# saramquant-calc-server

한국/미국 주식의 시세·재무 데이터를 수집하고 지표·팩터·리스크 배지를 계산하는 퀀트 계산 서버.
AWS 레이크하우스(S3 + Iceberg + DuckDB/Athena) 위에서 배치와 API로 동작한다.

설계 기준 문서: `docs/superpowers/specs/2026-08-09-aws-migration-design.md`

## 아키텍처

| 계층 | 구성 |
|---|---|
| 저장소 | `s3://saramquant-bucket` + Glue DB `saramquant`의 Iceberg v2 테이블 13개 (Parquet/ZSTD) |
| 읽기 | DuckDB `iceberg_scan` (Glue `metadata_location` 해석, Athena 스캔 비용 없음) |
| 쓰기 | staging Parquet → Athena `MERGE INTO` / 스냅샷 테이블은 전체 DELETE + INSERT, 말미에 `OPTIMIZE` + `VACUUM` |
| 배치 | EventBridge(10규칙) → Step Functions `saramquant-calc-pipeline` → ECS Fargate **Spot** (실패 시 온디맨드 폴백), 4 vCPU / 8GB |
| API | API Gateway HTTP API → Lambda 5함수 (단일 ECR 이미지 `saramquant-calc-api`, `image_config.command`로 분기) |
| 로그/알람 | CloudWatch 구조화 런 레코드(보존 30일) + `s3://saramquant-bucket/run-summary/`, SFN 실패 시 SNS 이메일 |
| 리전 | ap-northeast-2 |

## 배치 운영

진입점은 `python -m app.pipeline <command>`이며, SFN 입력 `{"command": "..."}`로 전달된다.

| command | 스케줄 (KST) | EventBridge cron (UTC) |
|---|---|---|
| `kr` | 월–금 18:00 | `cron(0 9 ? * MON-FRI *)` |
| `us` | 화–토 09:00 | `cron(0 0 ? * TUE-SAT *)` |
| `kr-fs` / `us-fs` | 분기 4·5·8·11월 지정일 `kr-fs` 03:00 / `us-fs` 04:00 | 분기별 4규칙씩 |
| `kr-initial` / `us-initial` | 스케줄 없음 | SFN 수동 실행 (콜드 ETL) |

- `us-fs`는 `run-summary/usa_fstatements.json`의 `status == ok && age < 72h` 신선도 게이트를 통과해야 fundamentals를 재계산한다(미통과 시 fs 단계 soft-fail + fundamentals 스킵).
- 배치는 단일 작성자를 전제한다(staging 테이블 공유, `stocks.id` 채번). SFN Standard는 동시 실행을 막지 않으므로, 겹칠 수 있는 스케줄은 서로 오프셋해 둔다(`us-fs`가 `kr-fs`보다 1시간 뒤).
- 런당 1건의 런 레코드가 try/finally로 기록된다: `run_id`, 단계별 성공/소요시간, 입출력 건수, `status`.

## API

인증은 `x-api-key: CALC_AUTH_KEY` 헤더. 포트폴리오 보유 종목은 gateway가 요청 바디로 전달한다(calc는 사용자 테이블에 접근하지 않는다).

| 라우트 | 함수 |
|---|---|
| `POST /internal/portfolios/full-analysis` | analysis |
| `POST /internal/portfolios/simulation` | portfolio-simulation |
| `GET /internal/stocks/{symbol}/simulation` | stock-simulation |
| `POST /internal/portfolios/price-lookup` | price-lookup |
| `GET /health` | health |

## 배포 (IaC)

모든 AWS 리소스는 `infra/`의 Terraform 플랫 루트 모듈로만 관리하며, **apply는 GitHub Actions(`.github/workflows/deploy.yml`)의 main 브랜치에서만** 실행된다.

- 로컬: `make check` (= `terraform init -backend=false` + `fmt -check` + `validate`)만 허용. `plan/apply/destroy/state` 등 공유 S3 상태를 건드리는 명령은 `infra/tf` 래퍼가 CI 밖에서 차단한다(로컬 stale 락 방지).
- CI 순서: `make check` → 자격증명 확인 → ECR 리포지토리 targeted apply → 이미지 빌드/푸시(태그 = Dockerfile+소스 해시, 존재 시 스킵) → `terraform plan -out` → `apply`. PR은 plan까지만.
- 상태: `s3://saramquant-tfstate` (key `calc-server/terraform.tfstate`, `use_lockfile = true`).
- 이미지: ECR `saramquant-calc-batch`(`docker/batch/Dockerfile`), `saramquant-calc-api`(`docker/api/Dockerfile`).
- 시크릿은 GitHub Variables → Terraform → SSM SecureString → 태스크/Lambda 참조 경로로만 흐른다.

## 테스트

```bash
pytest                    # 비통합 전체 (기본 -m "not integration")
pytest -m integration     # 실 AWS 리소스 접촉
```

실데이터 레이크를 전체/광역 DELETE하는 통합 테스트는 `tests/lake_guard.py`의 가드로 기본 skip이며, `LAKE_DESTRUCTIVE_TESTS=1`을 명시할 때만 실행된다. 운영 데이터가 적재된 환경에서는 켜지 말 것.

## 참고

- `db_table.sql`은 마이그레이션 이전 Postgres 스키마의 **레거시 참조본**이다. 현재 운영 스키마가 아니며, 실제 테이블 정의는 `app/db/lake_schemas.py`와 설계 문서 §2.3이 기준이다.
