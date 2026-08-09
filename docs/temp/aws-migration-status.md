# AWS 마이그레이션 진행 현황 (calc-server)

갱신: 2026-08-10 (최종 상태 — 세션/서브에이전트 간 공유용)
기준 문서: `docs/superpowers/specs/2026-08-09-aws-migration-design.md` (스키마·계약은 §2·§8)
계획: `docs/superpowers/plans/2026-08-09-aws-migration.md` / 상세 렛저: `.superpowers/sdd/2026-08-09-aws-migration/progress.md`

## 완료 (Task 1–16)

- **인프라 배포 완료** (CI apply): S3(saramquant-bucket + 라이프사이클), Glue DB `saramquant`, Athena 워크그룹 `saramquant`(10GB 스캔 컷오프), ECR 2종(calc-batch/calc-api), ECS 클러스터+태스크 정의(4vCPU/8GB, 컨테이너명 `pipeline`), IAM 역할 5종, 기본 VPC+S3 게이트웨이 엔드포인트, 로그 그룹(30일), SNS(이메일), SSM SecureString 9종.
- **13개 Iceberg 테이블 생성 완료** (v2/parquet/zstd, `date` 예약어 이슈 없음 실증).
- **저장소 계층 전면 교체 완료**: DuckDB 리더(`iceberg_scan`+Glue 메타 캐시 300s), 라이터(staging Parquet→Athena MERGE / 시장 스코프 snapshot_replace / OPTIMIZE+VACUUM). 레포지토리 10개 재작성(공개 시그니처 보존), psycopg2/connection.py 완전 제거.
- **런 레코드**: `app/log/run_record.py` — §6.1 JSON 로그 + S3 run-summary, `read_run_summary` 신선도 게이트 리더.
- **오케스트레이터 통합**: safety 10% 게이트, US FS 신선도 게이트(72h), RUN_ID, counts. scheduler/gunicorn/flask 제거.
- **SFN + EventBridge**: 상태 머신 `saramquant-calc-pipeline`(Spot → 실패 시 온디맨드 폴백) 실행 검증 완료, EventBridge 10규칙.
- **API Lambda 5함수 + API Gateway** 배포 완료 — `https://slskn4jfqh.execute-api.ap-northeast-2.amazonaws.com`.
- **콜드 ETL 완주** (`kr-initial` / `us-initial`), 레이크에 실데이터 적재:

| 항목 | KR | US |
|---|---|---|
| daily_prices | 623k행 | 1.54M행 |
| stocks | 2,371 | 5,975 |
| stock_indicators | 2,364 | 4,348 |
| risk_badges | 2,364 | 4,348 |

  - exchange_rates 1,626행.
  - `financial_statements` US 45,810행은 usa-fstatements 세션이 적재했고, `run-summary/usa_fstatements.json` 신선도 게이트가 실동작으로 검증됨.
- **마무리 (Task 16)**: EventBridge 10규칙 `ENABLED` 전환(다음 apply에 반영), 파괴적 통합 테스트에 `LAKE_DESTRUCTIVE_TESTS` 가드 추가, README·상태 문서 갱신.
- 테스트: 비통합 270 그린, 실 AWS 통합 테스트 그린(파괴적 3건은 가드로 기본 skip).

## 남은 작업

1. 전체 브랜치 diff 최종 코드 리뷰 (사용자 지시로 1회)
2. 배포 환경 curl 스위트 (API 5엔드포인트)
3. 최종 PR — EventBridge 규칙 ENABLED가 이 PR의 apply와 함께 적용된다

## 타 세션 전달 사항 (스펙 §8 그대로 유효)

- usa-fstatements-collector: `saramquant.financial_statements`(market='US') 기록 + `run-summary/usa_fstatements.json` (§6.1 포맷). calc us-fs는 status==ok && age<72h 게이트. **적재·게이트 모두 실동작 확인 완료.**
- gateway: calc API 베이스 URL `https://slskn4jfqh.execute-api.ap-northeast-2.amazonaws.com`, `x-api-key: CALC_AUTH_KEY` 헤더. 포트폴리오 분석/시뮬레이션은 holdings 바디 전달 계약.

## 주의 사항

- 파괴적 통합 테스트(`test_compute_repos_integration.py` 전체, lake_writer의 risk_badges 스냅샷, price_repos의 환율 라운드트립)는 `LAKE_DESTRUCTIVE_TESTS=1` 없이는 skip된다. 실데이터 환경에서 켜지 말 것.
- 단일 작성자 전제(stg_* 테이블 공유, stocks id 채번) — 배치 동시 실행 금지(SFN이 보장).
- Terraform 상태 버킷 `saramquant-tfstate` 부트스트랩 완료(3개 리포 공유, 각자 key).
- `db_table.sql`은 레거시 Postgres 스키마 참조본 — 운영 스키마는 `app/db/lake_schemas.py`.
