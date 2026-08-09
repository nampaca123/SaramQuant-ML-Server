# SaramQuant calc-server AWS 마이그레이션 설계 (2026-08-09)

Supabase(PostgreSQL) + Railway 기반의 calc-server를 S3 + Iceberg + DuckDB + Fargate/Lambda 기반으로 이전한다.
회사 프로젝트(ontology-for-nabus-adtrigger)의 운영 패턴을 선례로 삼되, SaramQuant의 규모(개인 포트폴리오,
실사용자 극소수, 일일 수십만 행)에 맞게 축소한다.

## 0. 범위와 세션 경계

- 이 문서는 **calc-server 세션의 범위**만 다룬다. gateway, usa-fstatements-collector는 각자 세션에서 진행하며,
  이 문서의 §8 "타 서비스 계약"이 그 접점이다.
- 완주 기준(성공 조건):
  1. 국장(KR)·미장(US) 콜드 ETL(`kr-initial`, `us-initial`)이 AWS 위에서 완주하여 Iceberg 테이블이 채워진다.
  2. 4개 API가 배포 환경에서 실제 curl 요청으로 정상 응답한다.
- 기존 Supabase 데이터는 마이그레이션하지 않는다. 콜드 ETL 재완주로 채운다.

## 1. 확정 사안 (사용자 결정 로그)

| 항목 | 결정 |
|---|---|
| 테이블 포맷 | Iceberg v2 + Parquet, **ZSTD 압축 명시** (`write.parquet.compression-codec=zstd`, Iceberg 기본값 gzip 함정 회피) |
| 스키마 | 기존 컬럼 구조 유지, 변경 금지 |
| 읽기 | 전부 DuckDB (`iceberg_scan`) |
| 배치 쓰기 | Athena SQL (staging Parquet → MERGE/INSERT) |
| 배치 컴퓨트 | EventBridge → 미니멀 SFN → Fargate **Spot** (실패 시 온디맨드 폴백) |
| API | Lambda (Python), **단일 ECR 이미지**에 함수별 entrypoint 오버라이드 |
| 포트폴리오 데이터 | gateway가 요청 바디로 보유 종목 전달 (calc는 사용자 데이터 접근 제거) |
| US 재무제표 연동 | HTTP 트리거/폴링 제거, **스케줄 분리 + run-summary 신선도 게이트** |
| 리전 | calc 전체 ap-northeast-2 (버킷 `saramquant-bucket` 기존 존재) |
| S3 버저닝 | 사용 안 함 (Iceberg 스냅샷이 대체) |
| CI/CD | GitHub Actions + 액션 시크릿 자격증명 (OIDC 아님), Terraform은 CI에서만 plan/apply |
| 네트워크 | 퍼블릭 서브넷 + S3 Gateway 엔드포인트, NAT/프라이빗 서브넷 없음. Lambda는 VPC 밖 |
| 로그 | CloudWatch 구조화 로그 (try/finally 런 레코드) + S3 run-summary, 보존 30일 |

## 2. 스토리지 설계 (S3 + Glue + Iceberg)

버킷 1개(`saramquant-bucket`, ap-northeast-2)에 prefix로 구분:

```
s3://saramquant-bucket/
  warehouse/            # Iceberg 웨어하우스 (Glue 카탈로그 DB: saramquant)
  staging/              # 배치 쓰기용 임시 Parquet (라이프사이클 7일 만료)
  athena-results/       # Athena 쿼리 결과 (라이프사이클 14일 만료)
  run-summary/          # 런 레코드 JSON (파이프라인·US수집기 신선도 게이트용)
```

Glue 데이터베이스: `saramquant` 단일. 크롤러 없음(Iceberg가 카탈로그를 직접 갱신).

### 테이블별 파티션 스펙

**스키마·파티셔닝의 단일 기준 문서는 `docs/spec/lakehouse-schema.md`이다** (전체 컬럼 정의, 타입 매핑, 서비스 간 계약 포함). 아래는 요약.

데이터 규모가 작으므로(전 종목 ~9천 개, 일일 신규 ~1만 행/테이블) **과분할 방지**가 핵심이다.
파티션 프로젝션은 Hive 외부 테이블 전용 기능으로 Iceberg에는 해당 없음 — Iceberg 메타데이터 프루닝 + 파일 내 정렬로 대체한다.

| 테이블 | 파티션 | 정렬(파일 내) | 비고 |
|---|---|---|---|
| daily_prices | `market`, `months(date)` | `stock_id` | 월 ~19만 행. 종목 단위 조회 시 정렬로 로우그룹 프루닝 |
| benchmark_daily_prices | 없음 | `benchmark_code, date` | 소형 |
| risk_free_rates / exchange_rates | 없음 | `date` | 소형 |
| financial_statements | `market` | `stock_id, fiscal_date` | 분기당 수천 행. US는 usa-fstatements-collector가 기록 |
| stock_fundamentals | `months(date)` | `stock_id` | 이력 누적 |
| factor_exposures / factor_returns | `months(date)` | `stock_id` / `factor_name` | 이력 누적 |
| factor_covariance | 없음 | `date` | jsonb → string(JSON) 컬럼 유지 |
| sector_aggregates | 없음 | `sector, date` | 소형 |
| stock_indicators | 없음 | `stock_id` | 최신 스냅샷만 유지 (전체 DELETE 후 INSERT) |
| risk_badges | 없음 | `stock_id` | 최신 스냅샷만 유지 |
| stocks | 없음 | `market, symbol` | 마스터, ~9천 행 |

- 컬럼명/타입은 기존 `db_table.sql` 그대로 매핑한다 (Postgres enum → string, jsonb → string, uuid → string).
- `user_portfolios`/`portfolio_holdings`/`audit_log`는 calc 소유가 아니므로 **생성하지 않는다**.

### 쓰기 패턴 (Athena)

1. 파이프라인(Python)이 pyarrow로 staging Parquet 작성 → `staging/<table>/<run_id>/`
2. Glue staging 테이블(고정 스키마, location만 run별 교체 또는 파티션) 위에 Athena `MERGE INTO`
   — 기존 `ON CONFLICT DO UPDATE`와 동등한 멱등 upsert.
3. 스냅샷 테이블(stock_indicators, risk_badges)은 전체 `DELETE` 후 `INSERT INTO`
   (전체 삭제는 메타데이터 연산이라 delete file이 남지 않음).
4. **파이프라인 마지막 단계에서 당일 쓴 테이블에 `OPTIMIZE ... REWRITE DATA` + `VACUUM`** 실행
   — MERGE가 남긴 delete file을 제거해 DuckDB 읽기 호환성과 파일 수를 관리하고, 스냅샷을 만료시킨다.
5. Athena 워크그룹 `saramquant`에 `bytes_scanned_cutoff_per_query` 설정 (폭주 쿼리 비용 가드).

### 읽기 패턴 (DuckDB)

- Glue `GetTable` → `Table.Parameters["metadata_location"]` → `iceberg_scan('<metadata.json>')`.
  Parquet 경로 직접 글롭 금지(스냅샷 격리 우회 방지). 회사 프로젝트 검증 패턴 그대로.
- 배치 컨테이너 내 읽기(300일 가격 로딩 등)도 동일한 DuckDB 경로 사용 — Athena 스캔 비용 없이 무료.
- Lambda에서는 DuckDB 확장(httpfs, iceberg)을 이미지 빌드 시 오프라인 베이크하고 빌드에서 LOAD 검증.

## 3. 배치 아키텍처

### 스케줄 (기존 APScheduler를 EventBridge로 1:1 대체, KST 의미 유지)

| 작업 | 기존 (KST) | EventBridge cron (UTC) |
|---|---|---|
| `kr` 일일 | 월–금 18:00 | `cron(0 9 ? * MON-FRI *)` |
| `us` 일일 | 화–토 09:00 | `cron(0 0 ? * TUE-SAT *)` |
| `kr-fs` / `us-fs` 분기 | 4/7·5/22·8/21·11/21 03:00 | 전일 18:00 UTC로 환산한 8개 규칙 |
| `kr-initial` / `us-initial` | 수동 | 스케줄 없음, SFN 수동 실행 |

### 실행 (미니멀 SFN)

단일 상태 머신 `saramquant-calc-pipeline`, 입력 `{"command": "kr" | "us" | ...}`:

```
RunTaskSpot (ecs:runTask.sync, FARGATE_SPOT, TimeoutSeconds=상태 단위)
  ├─ Retry: 일시 오류 1회
  ├─ Catch → RunTaskOnDemand (FARGATE)   # Spot 중단·용량 부족 폴백
  └─ 성공 → Succeed
RunTaskOnDemand 실패 → Fail
```

- 회사 프로젝트의 "증거 기반 분류 후 에스컬레이션"은 런당 비용(~수백 원)이 작아 채택하지 않고
  단순 Catch→온디맨드로 축소한다. 상태 단위 `TimeoutSeconds`를 두어 침묵 타임아웃을 방지한다.
- 실패 알림: CloudWatch 알람(`ExecutionsFailed >= 1`) → SNS → 이메일 구독.
- 배치 컨테이너: 기존 `python -m app.pipeline <command>` 진입점 유지, x86_64
  (Fargate Spot은 ARM 미지원), 4 vCPU / 8GB (지표 계산 ProcessPool 병렬), 배치 전용 ECR 이미지.
- 코드 변경 범위: `app/db/` 계층을 DuckDB 읽기 + staging/Athena 쓰기 구현으로 교체하고,
  수집기·퀀트 계산 로직(§9 안정성 원칙)은 손대지 않는다. APScheduler·gunicorn은 배치 이미지에서 제거.

### US 재무제표 신선도 게이트

- `us-fs`(및 `us-initial`의 FS 단계)는 usa-fstatements-collector를 호출하지 않는다.
- 대신 `run-summary/usa_fstatements.json`을 읽어 `status == ok AND age < 72h`를 확인:
  통과 시 fundamentals 재계산, 실패 시 경고 로그 후 해당 단계 중단(soft-fail, 나머지 파이프라인 정상 종료).
- `fundamental_collection_service.py`의 HTTP 트리거/폴링 코드는 제거된다.

## 4. API 아키텍처 (Lambda + API Gateway)

- API Gateway HTTP API(v2) → Lambda. 기존 `x-api-key == CALC_AUTH_KEY` 검증 로직 유지(Lambda 내부 검증).
- **단일 ECR 이미지**(python3.12 base)에 DuckDB+확장 베이크, 함수별 `image_config.command` 오버라이드:

| 함수 | 엔드포인트 | 변경 사항 |
|---|---|---|
| analysis | `POST /internal/portfolios/full-analysis` | **계약 변경**: 요청 바디로 보유 종목 `[{stock_id 또는 symbol+market, quantity, ...}]` 수신. DB의 user_portfolios 조회 제거 |
| simulation(포트폴리오) | `POST /internal/portfolios/simulation` | 위와 동일한 계약 변경 (경로에서 portfolio_id 제거) |
| simulation(단일 종목) | `GET /internal/stocks/{symbol}/simulation` | 데이터 접근만 DuckDB로 교체 |
| price-lookup | `POST /internal/portfolios/price-lookup` | DuckDB 조회 + 기존 pykrx/alpaca/yfinance 라이브 폴백 유지. 환율 write-back은 Athena 경유가 과하므로 **폴백 시 응답만 하고 적재는 생략** (다음 배치가 채움) |
| health | `GET /health` | API Gateway 라우트 + 경량 응답 |

- 메모리 2048MB 내외(짧은 numpy 연산, DuckDB 스캔 소형), `DUCKDB_MEMORY_LIMIT`·`temp_directory=/tmp` 설정.
- Flask 앱은 Lambda 핸들러로 대체(엔드포인트 4개뿐이므로 어댑터 라이브러리 없이 얇은 핸들러 작성).
- matplotlib(미사용), psycopg2, gunicorn, apscheduler는 의존성에서 제거.

## 5. IaC / CI/CD

- Terraform 플랫 루트 모듈 `infra/` (회사 프로젝트 구조 축소판). 모듈/워크스페이스/환경 분리 없음.
- 상태 백엔드: S3 + `use_lockfile = true` (DynamoDB 없음).
  상태 버킷은 3개 서비스 세션이 공유할 `saramquant-tfstate` (key: `calc-server/terraform.tfstate`).
- `infra/tf` 래퍼: CI 밖에서 plan/apply/destroy/state 등 차단. 로컬은 `make check`(`terraform init -backend=false` + fmt + validate)만.
- 변수는 기본값 없이 `validation` 블록으로 GitHub Variable/Secret 이름을 명시 (누락 시 plan 실패).
- `deploy.yml` (main push = plan+apply, PR = plan only, `concurrency.cancel-in-progress: false`):
  1. `make check` → 자격증명 설정(시크릿 `SARAMQUANT_IAM_KEY_ACCESS/SECRET`) → `sts get-caller-identity`
  2. ECR 리포지토리만 targeted apply → 이미지 빌드/푸시 (태그 = Dockerfile+소스 해시, 존재 시 스킵)
  3. `terraform plan -out` → `apply`
- 이미지: `calc-batch`(배치), `calc-api`(Lambda) 2개 리포, ECR 라이프사이클 최근 3개 유지.
- 태그: 전 리소스 `default_tags`로 `project=saramquant` 부여 (사용자 확정).

## 6. 로깅 / 운영

- 파이프라인: try/finally로 런 레코드 1건(JSON 로그 1줄 + `run-summary/calc_<command>.json`) —
  run_id(SFN 실행명), 단계별 성공/소요시간/오류, 입출력 건수, status(ok/partial/error). 기존 audit_log DB 쓰기는 제거.
- API: 요청당 JSON 로그 1줄(status, duration_ms, 쿼리 span 등). 기존 audit 미들웨어의 DB 쓰기 제거.
- 모든 Lambda·ECS 로그 그룹을 Terraform으로 명시 생성, 보존 30일 (자동 생성 무기한 보존 방지).
- IAM: 태스크/Lambda별 역할 분리, S3는 prefix 스코프, Glue는 `saramquant` DB 스코프.
  배포는 `saramquant-aws-managed` 액세스 키(AdministratorAccess) 사용.

## 7. 환경변수 정리

| 구분 | 항목 |
|---|---|
| 제거 | `SUPABASE_DB_TRANSACTION_POOLER_URL`, `DB_POOL_MAX_CONN`, `USA_FS_COLLECTOR_URL`, `USA_FS_COLLECTOR_AUTH_KEY`, `PORT`, `WEB_CONCURRENCY` |
| 유지 (GH → SSM/task env) | `ALPACA_API_KEY/SECRET_KEY`, `DART_API_KEY`, `ECOS_API_KEY`, `FRED_API_KEY`, `FINNHUB_API_KEY`, `KRX_ID/PASSWORD`, `CALC_AUTH_KEY` |
| 신규 (Terraform 주입) | Glue DB명, 버킷명(plain name — 기존 `SARAMQUANT_S3_BUCKET`은 URL 형식이라 버킷명 변수 별도 필요), Athena 워크그룹, staging prefix |
| 해당 없음 | `AWS_SES_REGION`(gateway 사안), Naver Cloud(gateway 사안, calc 코드에 없음 확인) |

민감값(KRX_PASSWORD 등)은 SSM SecureString으로 저장하고 task definition / Lambda가 참조한다.

## 8. 타 서비스 계약 (다른 세션에 전달할 사항)

1. **gateway**: calc API 신규 URL(API Gateway)로 교체. 포트폴리오 분석·시뮬레이션 호출 시
   보유 종목(symbol, market, quantity 등)을 요청 바디에 포함해야 함. `x-api-key` 헤더 유지.
2. **usa-fstatements-collector**: ap-northeast-2 Glue 카탈로그의 `saramquant.financial_statements`
   Iceberg 테이블(기존 스키마 유지)에 직접 기록하고, 완료 시
   `s3://saramquant-bucket/run-summary/usa_fstatements.json`에 런 레코드를 남길 것.
   분기 수집 스케줄은 calc의 `us-fs`(03:00 KST)보다 충분히 앞서도록 자체 설정.
3. 공유 Terraform 상태 버킷 `saramquant-tfstate`는 최초 1회 부트스트랩 후 각 리포가 자기 key만 사용.

## 9. 비용 추정 (월, 대략)

- Fargate Spot 일일 1–2h × 4vCPU/8GB ≈ $3–6
- Lambda + API Gateway: 실사용자 극소수 ≈ $0–1
- Athena: 일일 MERGE 스캔 수백 MB ≈ $1 미만
- S3 수 GB + Glue 카탈로그 + CloudWatch(보존 30일) ≈ $1 내외
- 합계 ≈ **$5–10/월** (Railway 상시 서버 대비 절감, 유휴 컴퓨트 0)

## 10. 구현 순서 (개요 — 상세는 implementation plan에서)

1. Terraform 골격(버킷 prefix/라이프사이클, Glue, Athena, ECR, IAM, 로그 그룹) + deploy.yml
2. 스토리지 계층 교체: DuckDB reader + staging/Athena writer, 테이블 DDL
3. 파이프라인 통합(레포지토리 교체, audit→CloudWatch, US 신선도 게이트) + 배치 이미지
4. SFN + EventBridge + 알람
5. API Lambda 이미지 + API Gateway + 계약 변경
6. 콜드 ETL 완주(kr-initial, us-initial) → 배포 환경 curl 검증 → PR
