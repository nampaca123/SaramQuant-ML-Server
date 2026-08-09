# SaramQuant calc-server AWS 마이그레이션 설계 (2026-08-09)

Supabase(PostgreSQL) + Railway 기반의 calc-server를 S3 + Iceberg + DuckDB + Fargate/Lambda 기반으로 이전한다.
회사 프로젝트(ontology-for-nabus-adtrigger)의 운영 패턴을 선례로 삼되, SaramQuant의 규모(개인 포트폴리오,
실사용자 극소수, 일일 수십만 행)에 맞게 축소한다.
**§2(스키마·파티셔닝)와 §8(타 서비스 계약)은 gateway / usa-fstatements-collector 세션의 단일 기준이다.**

## 0. 범위와 세션 경계

- 이 문서는 **calc-server 세션의 범위**만 다룬다. gateway, usa-fstatements-collector는 각자 세션에서 진행하며,
  §2·§8이 그 접점이다.
- 완주 기준(성공 조건):
  1. 국장(KR)·미장(US) 콜드 ETL(`kr-initial`, `us-initial`)이 AWS 위에서 완주하여 Iceberg 테이블이 채워진다.
  2. 4개 API가 배포 환경에서 실제 curl 요청으로 정상 응답한다.
- 기존 Supabase 데이터는 마이그레이션하지 않는다. 콜드 ETL 재완주로 채운다.

## 1. 확정 사안 (사용자 결정 로그)

| 항목 | 결정 |
|---|---|
| 테이블 포맷 | Iceberg v2 + Parquet, **ZSTD 압축 명시** (`write.parquet.compression-codec=zstd`, Iceberg 기본값 gzip 함정 회피) |
| 스키마 | 기존 컬럼 구조 유지 (불가피한 예외 2건은 §2.2) |
| 읽기 | 전부 DuckDB (`iceberg_scan`) |
| 배치 쓰기 | Athena SQL (staging Parquet → MERGE/INSERT) |
| 배치 컴퓨트 | EventBridge → 미니멀 SFN → Fargate **Spot** (실패 시 온디맨드 폴백). 주기는 기존과 동일 |
| API | Lambda (Python), **단일 ECR 이미지**에 함수별 entrypoint 오버라이드 |
| 포트폴리오 데이터 | gateway가 요청 바디로 보유 종목 전달 (calc는 사용자 데이터 접근 제거) |
| US 재무제표 연동 | HTTP 트리거/폴링 제거, **스케줄 분리 + run-summary 신선도 게이트** |
| 리전 | calc 전체 ap-northeast-2 (버킷 `saramquant-bucket` 기존 존재) |
| S3 버저닝 | 사용 안 함 (Iceberg 스냅샷이 대체) |
| CI/CD | GitHub Actions + 액션 시크릿 자격증명 (OIDC 아님), Terraform은 CI에서만 plan/apply |
| 네트워크 | 퍼블릭 서브넷 + S3 Gateway 엔드포인트, NAT/프라이빗 서브넷 없음. Lambda는 VPC 밖 |
| 로그 | CloudWatch 구조화 로그 (try/finally 런 레코드) + S3 run-summary, 보존 30일 |
| 태그 | 전 리소스 `project=saramquant` (provider default_tags, 사용자 확정) |

## 2. 스토리지 설계 — 스키마 & 파티셔닝 (타 세션 기준 문서)

### 2.1 공통 규약

| 항목 | 값 |
|---|---|
| 리전 | ap-northeast-2 (usa-fstatements-collector의 컴퓨트만 us-east-1, 데이터는 여기로 기록) |
| 버킷 | `s3://saramquant-bucket` (버저닝 없음) |
| Glue 데이터베이스 | `saramquant` (단일). 크롤러 없음(Iceberg가 카탈로그 직접 갱신) |
| 웨어하우스 위치 | `s3://saramquant-bucket/warehouse/<table>/` |
| staging | `s3://saramquant-bucket/staging/<table>/<run_id>/` (라이프사이클 7일 만료) |
| Athena 결과 | `s3://saramquant-bucket/athena-results/` (라이프사이클 14일 만료) |
| 런 레코드 | `s3://saramquant-bucket/run-summary/<서비스>_<작업>.json` (§6.1 포맷) |
| 읽기 | DuckDB만. Glue `GetTable` → `Parameters["metadata_location"]` → `iceberg_scan()`. **Parquet 경로 직접 글롭 금지** |
| 배치 쓰기 | Athena SQL. 워크그룹 `saramquant`, `bytes_scanned_cutoff_per_query` 설정 |
| 쓰기 후 정리 | 행 단위 MERGE/DELETE를 쓴 테이블은 파이프라인 말미에 Athena `OPTIMIZE ... REWRITE DATA` + `VACUUM` (delete file 제거로 DuckDB 호환 유지, 스냅샷 만료) |
| Terraform 상태 | `s3://saramquant-tfstate` 공유 버킷, 리포별 key (`calc-server/`, `gateway/`, `usa-fstatements/` + `terraform.tfstate`), `use_lockfile=true` |

### 2.2 Postgres → Iceberg 타입 매핑 규칙

| Postgres | Iceberg |
|---|---|
| bigserial / bigint | long |
| int | int |
| numeric(p,s) | decimal(p,s) 그대로 유지 |
| varchar(n) / text / enum | string (enum 값 문자열은 기존 그대로: 예 `KR_KOSPI`) |
| date | date |
| timestamptz | timestamp with time zone (UTC 저장) |
| boolean | boolean |
| jsonb | string (JSON 직렬화) |
| uuid | string |

- **제약/인덱스/트리거는 Iceberg에 없다.** unique 제약 → MERGE 키로 대체, check 제약 → writer 검증,
  `set_updated_at` 트리거 → writer가 직접 세팅, 인덱스 → 파티션 + 파일 내 정렬로 대체.
- **"스키마 불변" 원칙의 불가피한 예외 2건**:
  1. **Surrogate id**: 자동증가 시퀀스는 Iceberg에 없다. 조인 키인 `stocks.id`(long)는 **유지** —
     신규 종목 upsert 시 배치(단일 작성자)가 `max(id)+1`로 채번. 자연키가 있는 팩트 테이블의 `id` 컬럼은
     **제거**: daily_prices, benchmark_daily_prices, risk_free_rates, financial_statements, exchange_rates
     (코드 어디에서도 이 id들을 읽지 않음을 확인. 자연키는 §2.3의 MERGE 키).
  2. **`market` 컬럼 비정규화**: daily_prices와 financial_statements에 `market`(string, `KR`|`US`) 컬럼 추가 —
     KR/US 파이프라인이 서로의 파티션을 건드리지 않게 하는 파티션 프루닝 키 (market_group 수준, market_type 아님).
- gateway 소유 테이블의 id 채번 방식은 gateway 세션이 결정(uuid 컬럼은 문자열로 유지 가능).

### 2.3 마켓 데이터 테이블 (calc-server 소유)

쓰기 주체: calc 배치 (financial_statements의 US 행만 usa-fstatements-collector).
파티셔닝 원칙: 데이터가 작으므로(전 종목 ~9천, 일일 ~1만 행/테이블) **과분할 방지**.
파티션 프로젝션은 Hive 외부 테이블 전용 개념으로 Iceberg에는 해당 없음 — Iceberg 메타데이터 프루닝 + 파일 내 정렬이 그 역할을 한다.

| 테이블 | 파티션 | 쓰기 시 정렬 | MERGE 키 (기존 unique) | 쓰기 패턴 |
|---|---|---|---|---|
| stocks | 없음 | market, symbol | (symbol, market) | MERGE |
| daily_prices | market†, months(date) | stock_id, date | (stock_id, date) | MERGE (증분 append 위주) |
| benchmark_daily_prices | 없음 | benchmark, date | (benchmark, date) | MERGE |
| risk_free_rates | 없음 | country, maturity, date | (country, maturity, date) | MERGE |
| exchange_rates | 없음 | pair, date | (pair, date) | MERGE |
| financial_statements | market† | stock_id, fiscal_year | (stock_id, fiscal_year, report_type) | MERGE |
| stock_fundamentals | months(date) | stock_id | (stock_id, date) | MERGE |
| stock_indicators | 없음 | stock_id | (stock_id, date) | 전체 DELETE 후 INSERT (최신 스냅샷만 유지, 기존 동작 그대로) |
| factor_exposures | months(date) | stock_id | (stock_id, date) | MERGE |
| factor_returns | months(date) | market, factor_name | (market, date, factor_name) | MERGE |
| factor_covariance | 없음 | market, date | (market, date) | MERGE |
| sector_aggregates | 없음 | market, sector, date | (market, sector, date) | MERGE |
| risk_badges | 없음 | stock_id | (stock_id) | 전체 DELETE 후 INSERT (최신 스냅샷만 유지) |

† = §2.2 예외 2번의 추가 컬럼. (전체 DELETE는 메타데이터 연산이라 delete file이 남지 않는다.)

#### 컬럼 정의 (Postgres `db_table.sql` 기준, market† 외 추가/삭제/개명 없음)

**stocks** — id long, symbol string, name string, market string(market_type: KR_KOSPI|KR_KOSDAQ|US_NYSE|US_NASDAQ), is_active boolean, dart_corp_code string, sector string, created_at/updated_at timestamptz

**daily_prices** — market† string, stock_id long, date date, open/high/low/close decimal(15,2), volume long, created_at timestamptz

**benchmark_daily_prices** — benchmark string(benchmark_type: KR_KOSPI|KR_KOSDAQ|US_SP500|US_NASDAQ), date date, close decimal(15,2), created_at timestamptz

**risk_free_rates** — country string(KR|US), maturity string(91D|1Y|3Y|10Y), date date, rate decimal(6,4), created_at timestamptz

**exchange_rates** — pair string(7) (예 `USD/KRW`), date date, rate decimal(12,4)

**financial_statements** — market† string, stock_id long, fiscal_year int, report_type string(Q1|Q2|Q3|FY), revenue/operating_income/net_income/total_assets/total_liabilities/total_equity decimal(20,2), shares_outstanding long, created_at timestamptz

**stock_fundamentals** — stock_id long, date date, per/pbr decimal(12,4), eps/bps decimal(15,4), roe/debt_ratio/operating_margin decimal(10,4), data_coverage string(FULL|LOSS|PARTIAL|INSUFFICIENT|NO_FS), created_at timestamptz

**stock_indicators** — stock_id long, date date, sma_20/ema_20/wma_20 decimal(15,4), rsi_14 decimal(8,4), macd/macd_signal/macd_hist decimal(15,4), stoch_k/stoch_d decimal(8,4), bb_upper/bb_middle/bb_lower decimal(15,4), atr_14 decimal(15,4), adx_14/plus_di/minus_di decimal(8,4), obv long, vma_20 long, sar decimal(15,4), beta/alpha/sharpe decimal(8,4), created_at timestamptz

**factor_exposures** — stock_id long, date date, size_z/value_z/momentum_z/volatility_z/quality_z/leverage_z decimal(8,4)

**factor_returns** — market string(market_type), date date, factor_name string(50), return_value decimal(12,8)

**factor_covariance** — market string(market_type), date date, matrix string(JSON)

**sector_aggregates** — market string(market_type), sector string(100), date date, stock_count int, median_per/median_pbr decimal(12,4), median_roe/median_operating_margin/median_debt_ratio decimal(12,6)

**risk_badges** — stock_id long, market string(market_type), date date, summary_tier string(10), dimensions string(JSON), updated_at timestamptz

#### 레이크하우스로 가져가지 않는 테이블

- `predictions`, `ml_models` — 코드 어디에도 레포지토리/사용처가 없는 레거시. 생성하지 않는다.
- `audit_log`의 calc 기록(API/PIPELINE) — CloudWatch 구조화 로그 + run-summary로 대체. 테이블 자체의 존폐는 gateway 세션이 결정.

### 2.4 사용자/서비스 테이블 (gateway 소유 — 권고안)

최종 결정권은 gateway 세션에 있다. 실사용자가 극소수이므로 **전부 무파티션**을 권고(아래 예외만 파티션).
타입 매핑은 §2.2 규칙 동일.

| 테이블 | 파티션 권고 | 비고 |
|---|---|---|
| users, user_profiles, user_preferred_markets | 없음 | 소형 마스터 |
| user_portfolios, portfolio_holdings | 없음 | **calc는 더 이상 이 테이블을 읽지 않는다** — gateway가 calc API 호출 시 보유 종목을 요청 바디로 전달 (§8) |
| llm_usage_logs, portfolio_recommendations | 없음 | 소형 |
| stock_llm_analyses, portfolio_llm_analyses | 없음 (커지면 months(date)) | append 위주 |
| audit_log | months(created_at) | append-only 로그. gateway 방문 통계용 |
| refresh_tokens, email_verification_codes | — | **주의**: 로그인/인증마다 커밋이 발생하는 고빈도 변경 테이블로 Iceberg에 부적합. gateway 세션에서 별도 저장소(예: DynamoDB 온디맨드) 검토 권고 |

### 2.5 쓰기·읽기 패턴

1. 파이프라인(Python)이 pyarrow로 staging Parquet 작성 → `staging/<table>/<run_id>/`
2. Glue staging 테이블 위에 Athena `MERGE INTO` — 기존 `ON CONFLICT DO UPDATE`와 동등한 멱등 upsert.
3. 스냅샷 테이블(stock_indicators, risk_badges)은 전체 `DELETE` 후 `INSERT INTO`.
4. 파이프라인 마지막 단계에서 당일 쓴 테이블에 `OPTIMIZE` + `VACUUM`.
5. 배치 컨테이너 내 읽기(300일 가격 로딩 등)도 DuckDB `iceberg_scan` 사용 — Athena 스캔 비용 없이 무료.
6. Lambda에서는 DuckDB 확장(httpfs, iceberg)을 이미지 빌드 시 오프라인 베이크하고 빌드에서 LOAD 검증.

## 3. 배치 아키텍처

### 스케줄 (기존 APScheduler를 EventBridge로 1:1 대체, 주기 동일·KST 의미 유지)

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
| analysis | `POST /internal/portfolios/full-analysis` | **계약 변경**: 요청 바디로 보유 종목 수신 (§8). DB의 user_portfolios 조회 제거 |
| simulation(포트폴리오) | `POST /internal/portfolios/simulation` | 위와 동일한 계약 변경 (경로에서 portfolio_id 제거) |
| simulation(단일 종목) | `GET /internal/stocks/{symbol}/simulation` | 데이터 접근만 DuckDB로 교체 |
| price-lookup | `POST /internal/portfolios/price-lookup` | DuckDB 조회 + 기존 pykrx/alpaca/yfinance 라이브 폴백 유지. 환율 write-back은 제거 — 응답만 반환, 적재는 다음 배치 |
| health | `GET /health` | API Gateway 라우트 + 경량 응답 |

- 메모리 2048MB 내외(짧은 numpy 연산, DuckDB 스캔 소형), `DUCKDB_MEMORY_LIMIT`·`temp_directory=/tmp` 설정.
- Flask 앱은 Lambda 핸들러로 대체(엔드포인트 4개뿐이므로 어댑터 라이브러리 없이 얇은 핸들러 작성).
- matplotlib(미사용), psycopg2, gunicorn, apscheduler는 의존성에서 제거.

## 5. IaC / CI/CD

- Terraform 플랫 루트 모듈 `infra/` (회사 프로젝트 구조 축소판). 모듈/워크스페이스/환경 분리 없음.
- 상태 백엔드: S3 + `use_lockfile = true` (DynamoDB 없음). 상태 버킷은 §2.1의 `saramquant-tfstate`.
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

### 6.1 run-summary 포맷 (타 세션 공통)

```json
{
  "run_id": "<SFN 실행명 또는 고유 ID>",
  "service": "calc | usa-fstatements",
  "command": "kr | us | kr-fs | us-fs | collect",
  "status": "ok | partial | error",
  "started_at_utc": "ISO8601",
  "written_at_utc": "ISO8601",
  "duration_ms": 0,
  "counts": {"<단계 또는 테이블>": {"ok": 0, "failed": 0}},
  "cause": "실패 시 원인 문자열, 성공 시 null"
}
```

- 항상 try/finally에서 기록(실패 포함 런당 1건). 같은 내용을 CloudWatch에 JSON 로그 1줄로도 남긴다.
- 신선도 게이트 소비자는 `run_id`가 자기 실행과 무관하게 **파일의 written_at_utc 기준 age**로 판정한다.

## 7. 환경변수 정리

| 구분 | 항목 |
|---|---|
| 제거 | `SUPABASE_DB_TRANSACTION_POOLER_URL`, `DB_POOL_MAX_CONN`, `USA_FS_COLLECTOR_URL`, `USA_FS_COLLECTOR_AUTH_KEY`, `PORT`, `WEB_CONCURRENCY` |
| 유지 (GH → SSM/task env) | `ALPACA_API_KEY/SECRET_KEY`, `DART_API_KEY`, `ECOS_API_KEY`, `FRED_API_KEY`, `FINNHUB_API_KEY`, `KRX_ID/PASSWORD`, `CALC_AUTH_KEY` |
| 신규 (Terraform 주입) | Glue DB명, 버킷명(plain name — 기존 `SARAMQUANT_S3_BUCKET`은 URL 형식이라 버킷명 변수 별도 필요), Athena 워크그룹, staging prefix |
| 해당 없음 | `AWS_SES_REGION`(gateway 사안), Naver Cloud(gateway 사안, calc 코드에 없음 확인) |

민감값(KRX_PASSWORD 등)은 SSM SecureString으로 저장하고 task definition / Lambda가 참조한다.

## 8. 타 서비스 계약 (다른 세션에 전달할 사항)

1. **gateway → calc API** (API Gateway HTTP API 신규 URL, `x-api-key: CALC_AUTH_KEY` 헤더 유지):
   - `POST /internal/portfolios/full-analysis`, `POST /internal/portfolios/simulation` — **계약 변경**:
     요청 바디에 보유 종목 배열 포함
     `{"market_group": "KR|US", "holdings": [{"symbol", "market", "shares", "avg_price", "currency", "purchased_at", "purchase_fx_rate"}]}`.
     calc는 사용자 테이블에 접근하지 않는다.
   - `GET /internal/stocks/{symbol}/simulation`, `POST /internal/portfolios/price-lookup` — 계약 불변.
     price-lookup의 라이브 폴백 시 환율 write-back은 제거됨(응답만 반환, 적재는 다음 배치).
2. **usa-fstatements-collector → 레이크하우스**:
   - `saramquant.financial_statements`에 US 행 기록 (§2.3 스키마·MERGE 키 준수, `market='US'`).
   - stock_id는 `saramquant.stocks`(calc가 매일 갱신)에서 symbol+market으로 조인해 해석.
   - 완료 시 `run-summary/usa_fstatements.json` 기록 (§6.1 포맷). calc의 `us-fs` 작업(03:00 KST)은
     이 파일의 `status=="ok" && age<72h`를 게이트로 fundamentals를 재계산한다. HTTP 트리거는 폐지.
3. **스케줄 (전부 기존 주기 유지, KST 의미 보존)**: KR 일일 월–금 18:00 / US 일일 화–토 09:00 /
   분기 FS 4·5·8·11월 지정일 03:00. usa-fstatements-collector는 calc `us-fs`보다 충분히 앞서 완료되도록 자체 스케줄 설정.
4. 공유 Terraform 상태 버킷 `saramquant-tfstate`는 최초 1회 부트스트랩 후 각 리포가 자기 key만 사용.

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
