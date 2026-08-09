# SaramQuant calc-server AWS 마이그레이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Supabase/Railway 기반 calc-server를 S3+Iceberg+DuckDB(읽기)+Athena(쓰기)+Fargate Spot 배치+Lambda API로 이전하고, 콜드 ETL 완주와 배포 환경 API 검증까지 끝낸다.

**Architecture:** 스펙 `docs/superpowers/specs/2026-08-09-aws-migration-design.md`가 유일한 기준이다. 저장소 계층(`app/db/`)만 교체하고 수집기·퀀트 로직은 손대지 않는다. 쓰기는 staging Parquet→Athena MERGE, 읽기는 DuckDB `iceberg_scan`, 배치는 EventBridge→SFN→Fargate Spot(온디맨드 폴백), API는 단일 ECR 이미지 Lambda 5함수.

**Tech Stack:** Terraform(플랫 루트, S3 `use_lockfile`), GitHub Actions(액세스 키), Athena(Iceberg v2/ZSTD), DuckDB(httpfs+iceberg 확장), pyarrow, boto3, Fargate(x86_64), Lambda 컨테이너 이미지(python3.12), CloudWatch.

## Global Constraints

- 커밋 메시지: `260809_TaskNameCamelCase_kyoungin` 형식(날짜는 실제 작업일로) + Co-Authored-By/Claude-Session 트레일러.
- 주석: 파일당 최대 2줄, 한국어. 로그·오류 등 시스템 문자열은 영어.
- 파일당 ~300줄 이하. `verb-object` 네이밍.
- 모든 리소스 태그 `project=saramquant` (provider default_tags).
- Terraform: 로컬은 `make check`만. plan/apply/import/state류는 CI 전용. 로컬에서 절대 실행 금지.
- gh CLI는 반드시 `$env:GH_CONFIG_DIR='C:/Users/a/.config/gh-personal'` 설정 후 사용.
- AWS 자격증명: 로컬 검증은 `.env`의 `SARAMQUANT_IAM_KEY_ACCESS/SECRET`만 사용 (`saramquant-aws-managed`). 데스크톱 기본 AWS CLI 프로필 사용 절대 금지.
- 리전 ap-northeast-2. Glue DB `saramquant`, 버킷 `saramquant-bucket`, 워크그룹 `saramquant`.
- `app/collectors/`와 `app/quant/`의 계산·수집 로직은 수정 금지(임포트 경로 수정 등 기계적 변경만 허용).
- 통합 테스트는 `pytest -m integration`으로 분리하고 실제 AWS를 사용한다. 유닛 테스트는 AWS 없이 돈다.
- Iceberg 테이블 속성: `format-version=2`, `write.parquet.compression-codec=zstd` 반드시 명시.

---

### Task 1: Terraform 골격 + 로컬 가드

**Files:**
- Create: `infra/backend.tf`, `infra/providers.tf`, `infra/variables.tf`, `infra/locals.tf`, `infra/tf` (bash), `Makefile`
- Modify: `.gitignore` (`.terraform/`, `*.tfstate*`, `tfplan` 추가)

**Interfaces:**
- Produces: `local.app_name = "saramquant-calc"`, `local.bucket = "saramquant-bucket"`, `local.glue_db = "saramquant"`, `local.athena_workgroup = "saramquant"`, `var.region`(기본값 없음, GH Variable `AWS_REGION_APNE2`), 시크릿류 `var.alpaca_api_key` 등 §7 유지 목록 전부 (validation 블록에 GH Secret/Variable 이름 명시)

- [ ] **Step 1: tfstate 버킷 부트스트랩 (1회, CLI 허용 예외)**

```powershell
$env:AWS_ACCESS_KEY_ID='<.env SARAMQUANT_IAM_KEY_ACCESS>'; $env:AWS_SECRET_ACCESS_KEY='<.env SARAMQUANT_IAM_KEY_SECRET>'; $env:AWS_DEFAULT_REGION='ap-northeast-2'
aws s3api create-bucket --bucket saramquant-tfstate --create-bucket-configuration LocationConstraint=ap-northeast-2
aws s3api put-public-access-block --bucket saramquant-tfstate --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket saramquant-tfstate --tagging 'TagSet=[{Key=project,Value=saramquant}]'
```

- [ ] **Step 2: backend/providers/locals 작성**

```hcl
# infra/backend.tf
terraform {
  backend "s3" {
    bucket       = "saramquant-tfstate"
    key          = "calc-server/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}

# infra/providers.tf
terraform {
  required_version = ">= 1.10"
  required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }
}
provider "aws" {
  region = var.region
  default_tags { tags = { project = "saramquant" } }
}

# infra/locals.tf
locals {
  app_name         = "saramquant-calc"
  bucket           = "saramquant-bucket"
  glue_db          = "saramquant"
  athena_workgroup = "saramquant"
}
```

`infra/variables.tf`: `region`, `alpaca_api_key`, `alpaca_secret_key`, `dart_api_key`, `ecos_api_key`, `fred_api_key`, `finnhub_api_key`, `krx_id`, `krx_password`, `calc_auth_key`, `batch_image_tag`, `api_image_tag` — 전부 기본값 없음 + validation(`length(var.x) > 0`, error_message에 "Set GitHub secret/variable <NAME>" 명시). 민감값은 `sensitive = true`.

- [ ] **Step 3: `infra/tf` 래퍼 + `Makefile` 작성** (회사 프로젝트 `infra/tf` 로직 이식: BLOCKED="plan apply destroy refresh import state force-unlock taint untaint", `GITHUB_ACTIONS` 미설정 시 차단)

```make
check:
	cd infra && terraform init -backend=false -input=false >/dev/null \
	  && terraform fmt -check -recursive && terraform validate
```

- [ ] **Step 4: 검증** — Run: `make check` → Expected: `Success! The configuration is valid.`
- [ ] **Step 5: Commit** — `git add infra Makefile .gitignore && git commit` (`260809_TerraformSkeleton_kyoungin`)

---

### Task 2: 데이터 플레인 Terraform (S3/Glue/Athena/ECR/로그/SNS/SSM)

**Files:**
- Create: `infra/s3.tf`, `infra/glue_athena.tf`, `infra/ecr.tf`, `infra/logs_sns.tf`, `infra/ssm.tf`

**Interfaces:**
- Produces: `aws_glue_catalog_database.saramquant`, `aws_athena_workgroup.saramquant`, `aws_ecr_repository.batch`("saramquant-calc-batch")/`api`("saramquant-calc-api"), 로그 그룹 `/saramquant-calc/batch`·`/aws/lambda/saramquant-calc-*`(retention 30), `aws_sns_topic.alerts`, SSM `/saramquant/calc/*` SecureString

- [ ] **Step 1: 기존 버킷 import 블록 + 라이프사이클** — `saramquant-bucket`은 이미 존재하므로 `import { to = aws_s3_bucket.lake, id = "saramquant-bucket" }` 블록 사용(CI plan/apply에서 처리). 라이프사이클: `staging/` 7일, `athena-results/` 14일, 전 prefix `abort_incomplete_multipart_upload` 7일. public access block 전체 차단.
- [ ] **Step 2: Glue DB + Athena 워크그룹** — 워크그룹: `result_configuration.output_location = s3://saramquant-bucket/athena-results/`, `bytes_scanned_cutoff_per_query = 10737418240`(10GB), `publish_cloudwatch_metrics_enabled = false`.
- [ ] **Step 3: ECR 2개** — lifecycle policy `imageCountMoreThan 3 → expire`.
- [ ] **Step 4: 로그 그룹 + SNS** — 로그 그룹 명시 생성(retention_in_days=30): `/saramquant-calc/batch`, `/aws/lambda/saramquant-calc-analysis|-portfolio-simulation|-stock-simulation|-price-lookup|-health`. `aws_sns_topic.alerts` + `aws_sns_topic_subscription`(protocol=email, endpoint=`nampaca123@gmail.com`).
- [ ] **Step 5: SSM SecureString** — `/saramquant/calc/{alpaca_api_key,alpaca_secret_key,dart_api_key,ecos_api_key,fred_api_key,finnhub_api_key,krx_id,krx_password,calc_auth_key}` ← 대응 var.
- [ ] **Step 6: 검증** — Run: `make check` → PASS. **Commit** (`260809_DataPlaneTf_kyoungin`)

---

### Task 3: IAM + 네트워크 Terraform

**Files:**
- Create: `infra/iam.tf`, `infra/network.tf`, `infra/ecs.tf`

**Interfaces:**
- Produces: `aws_iam_role.batch_task`/`batch_exec`/`lambda_api`/`sfn`/`events`, `aws_ecs_cluster.calc`, `aws_ecs_task_definition.pipeline`(family `saramquant-calc-pipeline`, cpu=4096, memory=8192, x86_64, 컨테이너명 `pipeline`), 기본 VPC 데이터 소스 + S3 Gateway 엔드포인트, SG `saramquant-calc-batch`(egress all)

- [ ] **Step 1: network.tf** — `data "aws_vpc" "default" { default = true }`, `data "aws_subnets" "default"`, `aws_vpc_endpoint`(Gateway, `com.amazonaws.ap-northeast-2.s3`, 기본 라우트 테이블), SG(egress 0.0.0.0/0, ingress 없음).
- [ ] **Step 2: iam.tf** — 인라인 정책, sid 명명:
  - `batch_task`: S3(`arn:aws:s3:::saramquant-bucket` + `/warehouse/*`,`/staging/*`,`/athena-results/*`,`/run-summary/*` — List/Get/Put/Delete), Athena(해당 워크그룹 Start/Get/Stop), Glue(`saramquant` DB의 Get*/Create*/Update*/BatchCreatePartition 등), `logs:PutLogEvents/CreateLogStream`, SSM GetParameter(`/saramquant/calc/*`).
  - `batch_exec`: `AmazonECSTaskExecutionRolePolicy` + SSM GetParameters(`/saramquant/calc/*`) + kms:Decrypt(ViaService=ssm).
  - `lambda_api`: `AWSLambdaBasicExecutionRole` + S3 읽기(`/warehouse/*`) + `glue:GetTable` + SSM GetParameter(`/saramquant/calc/*`).
  - `sfn`: `ecs:RunTask/StopTask/DescribeTasks`(태스크 정의·클러스터 스코프), `iam:PassRole`(batch_task, batch_exec, condition `iam:PassedToService=ecs-tasks.amazonaws.com`), `events:PutTargets/PutRule/DescribeRule`(`StepFunctionsGetEventsForECSTaskRule`).
  - `events`: `states:StartExecution`(해당 상태머신 ARN).
- [ ] **Step 3: ecs.tf** — 클러스터(containerInsights 비활성), 태스크 정의: 이미지 `${aws_ecr_repository.batch.repository_url}:${var.batch_image_tag}`, 로그 드라이버 awslogs(`/saramquant-calc/batch`), secrets로 SSM 9종 주입, env `LAKE_BUCKET`,`GLUE_DATABASE`,`ATHENA_WORKGROUP`,`AWS_REGION_NAME=ap-northeast-2`.
- [ ] **Step 4: 검증** — `make check` PASS. **Commit** (`260809_IamNetworkEcsTf_kyoungin`)

---

### Task 4: GitHub Actions deploy.yml + 최초 CI apply

**Files:**
- Create: `.github/workflows/deploy.yml`
- Create: `docker/batch/Dockerfile`(임시 최소 버전 — 현행 루트 Dockerfile 복사, CMD `python -m app.pipeline`), `docker/api/Dockerfile`(임시: python3.12 lambda base + `CMD placeholder` 아님 — Task 15 전까지 빌드 스킵 조건으로 처리)

**Interfaces:**
- Produces: main push 시 plan+apply, PR 시 plan. `BATCH_IMAGE_TAG`/`API_IMAGE_TAG` = `md5(Dockerfile+소스)[:12]`, `TF_VAR_batch_image_tag`/`TF_VAR_api_image_tag`로 전달

- [ ] **Step 1: deploy.yml 작성** — 골자:

```yaml
on:
  pull_request:
  push: { branches: [main] }
concurrency: { group: terraform-state-${{ github.repository }}, cancel-in-progress: false }
env:
  AWS_ACCESS_KEY_ID: ${{ secrets.SARAMQUANT_IAM_KEY_ACCESS }}
  AWS_SECRET_ACCESS_KEY: ${{ secrets.SARAMQUANT_IAM_KEY_SECRET }}
  AWS_DEFAULT_REGION: ap-northeast-2
  TF_VAR_region: ap-northeast-2
  TF_VAR_alpaca_api_key: ${{ vars.ALPACA_API_KEY }}
  # ... §7 유지 목록 전부 (KRX_PASSWORD 등 vars/secrets 매핑)
steps:
  - checkout → aws sts get-caller-identity → make check
  - 이미지 태그 계산: batch = md5(docker/batch/Dockerfile + app/** + requirements.txt), api = md5(docker/api/Dockerfile + app/**)
  - terraform init
  - terraform apply -target=aws_ecr_repository.batch -target=aws_ecr_repository.api -auto-approve   # 이미지 선행 생성
  - ecr describe-images로 태그 존재 시 빌드 스킵, 없으면 docker build/push (batch, api 각각)
  - terraform plan -out=tfplan (PR이면 여기서 종료)
  - terraform apply tfplan (main push만)
```

- [ ] **Step 2: 원격 GH 변수 확인/보완** — `$env:GH_CONFIG_DIR='C:/Users/a/.config/gh-personal'; gh variable list -R nampaca123/saramquant-calc-server` — 부족한 값(`AWS_REGION_APNE2` 등 워크플로에서 참조하는 이름)이 있으면 `gh variable set`으로 추가.
- [ ] **Step 3: 브랜치 push → PR 생성** — CI plan green 확인 (`gh run watch`).
- [ ] **Step 4: main 머지 대신 이 단계에서는 PR 유지** — 이후 태스크들이 같은 브랜치에 쌓인다. 단, 인프라가 실제로 필요해지는 Task 6부터는 **main에 머지되어 apply가 돌아야 한다**. 따라서 Task 5까지 완료 후 1차 머지(§주의: 머지 전 커밋들이 태스크 게이트를 통과했는지 확인).
- [ ] **Step 5: Commit** (`260809_DeployWorkflow_kyoungin`)

---

### Task 5: 테이블 스펙 모듈 (단일 소스)

**Files:**
- Create: `app/db/lake_schemas.py`
- Test: `tests/db/test_lake_schemas.py`

**Interfaces:**
- Produces:
  - `TABLES: dict[str, TableSpec]` — 스펙 §2.3의 13개 테이블. `TableSpec = dataclass(columns: list[tuple[name, athena_type]], partition: list[str], sort: list[str], merge_keys: list[str], snapshot: bool)` (snapshot=True: stock_indicators, risk_badges)
  - `build_create_ddl(name) -> str` — Athena `CREATE TABLE IF NOT EXISTS saramquant.<name> (...) PARTITIONED BY (...) LOCATION 's3://saramquant-bucket/warehouse/<name>/' TBLPROPERTIES ('table_type'='ICEBERG','format'='parquet','write_compression'='zstd')`
  - `build_staging_ddl(name, s3_prefix) -> str` — 외부 Parquet staging 테이블 `saramquant.stg_<name>` (`CREATE EXTERNAL TABLE ... STORED AS PARQUET LOCATION <s3_prefix>`)
  - `arrow_schema(name) -> pyarrow.Schema` — athena_type→pyarrow 매핑 (`decimal(p,s)`→`pa.decimal128(p,s)`, `bigint`→`pa.int64()`, `int`→`pa.int32()`, `string`→`pa.string()`, `date`→`pa.date32()`, `timestamp`→`pa.timestamp('us', tz='UTC')`, `boolean`→`pa.bool_()`)
- 파티션 컬럼: daily_prices/financial_statements의 `market`(string)과 `months(date)`는 Athena DDL에서 `PARTITIONED BY (market, month(date))` 형태.

- [ ] **Step 1: 실패 테스트 작성** — `test_daily_prices_ddl_contains_partition_and_zstd`(DDL에 `month(date)`·`'write_compression'='zstd'` 포함), `test_arrow_schema_decimal_mapping`(daily_prices open → decimal128(15,2)), `test_merge_keys`(financial_statements → [stock_id, fiscal_year, report_type]), `test_all_13_tables_defined`.
- [ ] **Step 2: `pytest tests/db/test_lake_schemas.py -v` → FAIL 확인**
- [ ] **Step 3: 구현** — 스펙 §2.3 컬럼 정의를 그대로 옮긴다(id 제거·market† 추가 포함).
- [ ] **Step 4: 테스트 PASS 확인**
- [ ] **Step 5: requirements.txt에 `pyarrow`, `duckdb`, `boto3` 추가, `matplotlib` 제거. Commit** (`260809_LakeSchemas_kyoungin`)

---

### Task 6: Athena 러너 + DDL 부트스트랩

**Files:**
- Create: `app/db/athena_runner.py`, `app/db/create_tables.py`
- Test: `tests/db/test_athena_runner.py`(유닛), `tests/db/test_create_tables_integration.py`(`-m integration`)

**Interfaces:**
- Produces:
  - `run_query(sql: str) -> str` — boto3 StartQueryExecution(워크그룹 env `ATHENA_WORKGROUP`) + 폴링(1s 간격, 기본 타임아웃 300s), 실패 시 `AthenaQueryError(state_change_reason)` raise, 성공 시 QueryExecutionId 반환
  - `create_all_tables() -> None` — 13개 본 테이블 DDL 실행(멱등)
- 자격증명: 로컬 실행 시 `.env`의 SARAMQUANT 키를 `app/utils`의 dotenv 로딩 경유로 사용(`AWS_ACCESS_KEY_ID` 미설정이고 `SARAMQUANT_IAM_KEY_ACCESS` 존재 시 boto3 세션에 주입하는 `app/db/aws_session.py::build_session()` 헬퍼 포함, ECS/Lambda에서는 역할 자격증명 자동 사용)

- [ ] **Step 1: 유닛 테스트** — `run_query` 폴링 로직을 boto3 클라이언트 스텁(성공/FAILED 시나리오)으로 검증.
- [ ] **Step 2: FAIL 확인 → 구현 → PASS**
- [ ] **Step 3: (main 머지 후 인프라 apply 완료 상태에서) 통합 테스트** — `pytest -m integration tests/db/test_create_tables_integration.py`: `create_all_tables()` 실행 → Glue `get_table`로 13개 테이블 존재 + `metadata_location` 파라미터 확인.
- [ ] **Step 4: Commit** (`260809_AthenaRunnerDdl_kyoungin`)

---

### Task 7: DuckDB 리더

**Files:**
- Create: `app/db/lake_reader.py`
- Test: `tests/db/test_lake_reader_integration.py`(`-m integration`)

**Interfaces:**
- Produces:
  - `get_connection() -> duckdb.Connection` — 모듈 전역 재사용. httpfs/iceberg LOAD, `SET unsafe_enable_version_guessing=false`, S3 시크릿은 현재 자격증명으로 `CREATE OR REPLACE SECRET`(Lambda/ECS 역할 키·세션 토큰, 로컬은 .env 키)
  - `resolve_metadata_location(table: str) -> str` — Glue GetTable → `Parameters["metadata_location"]`, TTL 300s 캐시
  - `scan(table: str) -> str` — `f"iceberg_scan('{resolve_metadata_location(table)}')"` 반환(쿼리 조립용)
  - `query_df(sql: str, params: list | None = None) -> pandas.DataFrame`
- **Parquet 경로 직접 글롭 금지** — 모든 읽기는 `scan()` 경유.

- [ ] **Step 1: 통합 테스트 작성** — 빈 `stocks` 테이블 `SELECT count(*) FROM {scan('stocks')}` == 0; `resolve_metadata_location` 캐시 히트 검증(두 번째 호출 Glue 미호출 — 클라이언트 호출 카운트 몽키패치).
- [ ] **Step 2: FAIL → 구현 → `pytest -m integration` PASS**
- [ ] **Step 3: Commit** (`260809_DuckdbLakeReader_kyoungin`)

---

### Task 8: 레이크 라이터 (staging + MERGE / snapshot replace)

**Files:**
- Create: `app/db/lake_writer.py`
- Test: `tests/db/test_lake_writer.py`(SQL 빌더 유닛), `tests/db/test_lake_writer_integration.py`

**Interfaces:**
- Produces:
  - `write_staging(table: str, df: pandas.DataFrame, run_id: str) -> str` — `arrow_schema(table)`로 캐스팅해 `s3://saramquant-bucket/staging/<table>/<run_id>/part-0.parquet` 업로드(ZSTD), `stg_<table>` 외부 테이블 DDL 실행(LOCATION=해당 prefix), staging prefix 반환
  - `build_merge_sql(table: str) -> str` — `MERGE INTO saramquant.<t> t USING saramquant.stg_<t> s ON <merge_keys 등가조건> WHEN MATCHED THEN UPDATE SET <비키 컬럼 전부> WHEN NOT MATCHED THEN INSERT (<전 컬럼>) VALUES (...)`
  - `merge(table, df, run_id) -> int` — write_staging → run_query(merge) → 행 수 반환
  - `snapshot_replace(table, df, run_id) -> int` — `DELETE FROM saramquant.<t>` 후 staging에서 `INSERT INTO ... SELECT`
  - `optimize_and_vacuum(tables: list[str]) -> None` — 각각 `OPTIMIZE saramquant.<t> REWRITE DATA USING BIN_PACK` + `VACUUM saramquant.<t>`
  - 빈 df는 no-op(0 반환)
- Consumes: Task 5 `TABLES/arrow_schema/build_staging_ddl`, Task 6 `run_query`

- [ ] **Step 1: 유닛 테스트** — `build_merge_sql("daily_prices")`에 ON절 `t.stock_id = s.stock_id AND t.date = s.date`와 전 컬럼 INSERT 포함; snapshot 테이블(risk_badges)에 merge 호출 시 ValueError.
- [ ] **Step 2: FAIL → 구현 → 유닛 PASS**
- [ ] **Step 3: 통합 라운드트립** — stocks에 2행 merge → DuckDB로 재조회 일치 → 같은 키 재-merge(값 변경) → 갱신 확인 → `DELETE FROM saramquant.stocks` 원복.
- [ ] **Step 4: Commit** (`260809_LakeWriter_kyoungin`)

---

### Task 9: stocks 레포지토리 재작성 (채번 + progressive deactivate 데이터 준비)

**Files:**
- Modify: `app/db/repositories/stock.py` (전면 재작성, 공개 함수 시그니처 유지)
- Test: `tests/db/repositories/test_stock_repo.py`(유닛: id 채번·deactivate 계산), `tests/db/repositories/test_stock_repo_integration.py`

**Interfaces:**
- Consumes: `lake_reader.scan/query_df`, `lake_writer.merge`
- Produces(기존 시그니처 유지 — 호출부인 `StockListCollector`/`SectorCollector`/orchestrator가 그대로 쓰도록): `upsert_stocks(rows)`, `get_active_stocks(market_group)`, `update_sectors(pairs)`, `get_stocks_missing_sector(...)`, `update_dart_corp_codes(...)` 등 — **재작성 전 현재 파일을 읽고 공개 함수 목록을 그대로 보존한다.** 내부 구현만 교체:
  - upsert: 기존 (symbol,market)→id 매핑을 DuckDB로 읽어 신규 심볼에 `max(id)+1..` 순차 채번 후 MERGE
  - `compute_deactivation(market_group) -> pandas.DataFrame` 신설: 가격/섹터/재무 존재 여부를 DuckDB 조인으로 판정해 is_active 변경분만 반환(안전 임계 10% 판정용 카운트 포함) — orchestrator가 사용
- 트랜잭션이던 `_progressive_deactivate`는 "계산(파이썬) → 단일 MERGE(원자적 커밋)"로 대체된다.

- [ ] **Step 1: 유닛 테스트(채번·deactivate 계산을 순수 함수로 분리해 검증)** → FAIL → 구현 → PASS
- [ ] **Step 2: 통합: upsert 2행 → get_active_stocks 일치 → 정리**
- [ ] **Step 3: Commit** (`260809_StockRepoLake_kyoungin`)

---

### Task 10: 시세·환율·금리 레포지토리 재작성

**Files:**
- Modify: `app/db/repositories/daily_price.py`, `benchmark.py`, `risk_free_rate.py`, `exchange_rate.py`
- Test: `tests/db/repositories/test_price_repos_integration.py`

**Interfaces:**
- 공개 함수 시그니처 전부 유지(각 파일 재작성 전 현재 공개 함수 목록 확인). 핵심 치환:
  - 쓰기(각 upsert류) → `lake_writer.merge(<table>, df, run_id)` (daily_prices df에는 `market` 컬럼(KR|US) 추가 — 호출부가 주는 market 정보로 채움)
  - `get_last_stored_date(...)` → `SELECT max(date) FROM {scan(t)} WHERE ...`
  - 최근 N일 로딩(`load_price_maps` 소스) →
    ```sql
    SELECT stock_id, date, open, high, low, close, volume
    FROM {scan('daily_prices')} WHERE market = ?
    QUALIFY row_number() OVER (PARTITION BY stock_id ORDER BY date DESC) <= ?
    ```
  - `HistoricalPriceLookup`의 환율 단건 write-back 호출은 **exchange_rate.py에서 함수 제거가 아니라 no-op로 두지 않고**, 호출부(Task 15에서 price-lookup 핸들러)가 호출하지 않도록 변경(스펙 §4). exchange_rate.py 자체는 배치용 merge 유지.

- [ ] **Step 1: 통합 테스트(테이블별 1-2행 merge→재조회→정리)** → FAIL → 구현 → PASS
- [ ] **Step 2: Commit** (`260809_PriceReposLake_kyoungin`)

---

### Task 11: 재무·계산 결과 레포지토리 재작성 + 불용 파일 제거

**Files:**
- Modify: `app/db/repositories/financial_statement.py`, `fundamental.py`, `indicator.py`, `factor.py`, `risk_badge.py`
- Delete: `app/db/repositories/portfolio.py`, `app/db/repositories/audit_log.py`, `app/db/connection.py`
- Test: `tests/db/repositories/test_compute_repos_integration.py`

**Interfaces:**
- 공개 함수 시그니처 유지. 치환 규칙:
  - financial_statements: merge 시 `market` 컬럼 추가(KR). TTM 정렬 쿼리는 DuckDB로 동일 CASE 식 사용.
  - indicator: delete+insert → `lake_writer.snapshot_replace('stock_indicators', df, run_id)`
  - risk_badge: 동일하게 snapshot_replace
  - factor_covariance.matrix / risk_badges.dimensions: `json.dumps`로 문자열 저장, 읽을 때 `json.loads`
- portfolio.py 삭제에 따라 이를 임포트하는 서비스(`PortfolioAnalysisService`/`PortfolioSimulationService`)는 Task 15에서 holdings 인자를 받도록 수정된다 — 이 태스크에서는 **임포트 오류가 나지 않는 선까지만**(해당 서비스 파일의 임포트·포트폴리오 로딩 함수를 holdings 파라미터로 대체) 수정.
- audit_log.py 삭제에 따라 `app/log/service/audit_log_service.py`는 Task 12에서 교체.

- [ ] **Step 1: 통합 테스트 → FAIL → 구현 → PASS** (json 직렬화 라운드트립 포함)
- [ ] **Step 2: `grep -r "connection import\|psycopg2" app/` 결과 0건 확인, requirements에서 `psycopg2-binary` 제거**
- [ ] **Step 3: Commit** (`260809_ComputeReposLake_kyoungin`)

---

### Task 12: 런 레코드 로거 (audit 대체)

**Files:**
- Create: `app/log/run_record.py`
- Modify: `app/log/service/audit_log_service.py`(DB 의존 제거), `app/log/middleware/audit_middleware.py`(DB 호출 제거 — Task 15에서 Lambda 핸들러 공통 로깅으로 대체되므로 여기선 파이프라인 경로만 무결하게)
- Test: `tests/log/test_run_record.py`

**Interfaces:**
- Produces:
  - `write_run_record(service: str, command: str, status: str, started_at: datetime, counts: dict, cause: str | None, run_id: str) -> None` — 스펙 §6.1 JSON을 (1) `logger.info(json.dumps(...))` 1줄 + (2) `s3://saramquant-bucket/run-summary/calc_<command>.json` put_object. 실패해도 예외 전파 금지(try/except + logger.exception)
  - `read_run_summary(key: str) -> dict | None` — 신선도 게이트용(`run-summary/usa_fstatements.json`)
- `log_pipeline()`은 내부에서 `write_run_record` 호출로 교체, `log_api()`는 DB insert 제거하고 JSON 로그 1줄만.

- [ ] **Step 1: 유닛 테스트(boto3 스텁: put_object 페이로드가 §6.1 스키마와 일치, 필수 키 검증)** → FAIL → 구현 → PASS
- [ ] **Step 2: Commit** (`260809_RunRecordLogger_kyoungin`)

---

### Task 13: 오케스트레이터 통합 + 배치 이미지

**Files:**
- Modify: `app/pipeline/orchestrator.py`(deactivate → Task 9 `compute_deactivation`+merge, 안전 임계 유지, run_id=SFN 실행명 env `RUN_ID`, 말미 `optimize_and_vacuum`, audit→`write_run_record`), `app/pipeline/__main__.py`(close_pool 제거), `app/services/fundamental_collection_service.py`(HTTP 트리거/폴링 제거 → `read_run_summary` 신선도 게이트 `status=="ok" and age<72h`, 실패 시 soft-fail 경고)
- Delete: `app/scheduler.py`, `gunicorn.conf.py`, `run.py`, 루트 `Dockerfile`
- Create: `docker/batch/Dockerfile`(python:3.14-slim, pykrx `--no-deps` 선설치 패턴 유지, `ENTRYPOINT ["python","-m","app.pipeline"]`)
- Modify: `requirements.txt`(`apscheduler`,`gunicorn`,`flask` 제거)
- Test: `tests/pipeline/test_orchestrator_gates.py`(유닛: 안전 임계·신선도 게이트·soft-fail)

**Interfaces:**
- Consumes: Task 9 `compute_deactivation`, Task 12 `write_run_record`/`read_run_summary`, Task 8 `optimize_and_vacuum`
- Produces: `python -m app.pipeline <command>`가 Postgres 없이 완주 가능한 상태. `app/__init__.py`는 Flask 제거 후 빈 모듈로.

- [ ] **Step 1: 유닛 테스트(게이트 3종) → FAIL → 구현 → PASS**
- [ ] **Step 2: 전체 유닛 스위트 `pytest -m "not integration"` PASS + `docker build -f docker/batch/Dockerfile .` 성공**
- [ ] **Step 3: 로컬 스모크** — `.env` 자격증명으로 `python -m app.pipeline kr-fs` 실행: DART 수집→재무 merge→fundamentals 계산이 실제 레이크에 완주하는지 확인(가장 짧은 실전 명령). run-summary 객체 생성 확인.
- [ ] **Step 4: Commit** (`260809_OrchestratorLake_kyoungin`)

---

### Task 14: SFN + EventBridge + 알람 Terraform

**Files:**
- Create: `infra/sfn.tf`, `infra/sfn/pipeline.asl.json`, `infra/eventbridge.tf`, `infra/monitoring.tf`

**Interfaces:**
- Produces: 상태머신 `saramquant-calc-pipeline`(입력 `{"command": "..."}`), EventBridge 규칙 12개(kr: `cron(0 9 ? * MON-FRI *)`, us: `cron(0 0 ? * TUE-SAT *)`, kr-fs/us-fs 각 4개: `cron(0 18 6 4 ? *)`,`cron(0 18 21 5 ? *)`,`cron(0 18 20 8 ? *)`,`cron(0 18 20 11 ? *)` — KST 03:00의 전일 UTC 환산), 알람 `ExecutionsFailed>=1`→SNS

- [ ] **Step 1: ASL 작성**

```json
{
  "StartAt": "RunTaskSpot",
  "States": {
    "RunTaskSpot": {
      "Type": "Task", "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "${cluster_arn}", "TaskDefinition": "${task_def_arn}",
        "CapacityProviderStrategy": [{ "CapacityProvider": "FARGATE_SPOT", "Weight": 1 }],
        "NetworkConfiguration": { "AwsvpcConfiguration": { "Subnets": ${subnets}, "SecurityGroups": ["${sg}"], "AssignPublicIp": "ENABLED" } },
        "Overrides": { "ContainerOverrides": [{ "Name": "pipeline",
          "Command.$": "States.Array($.command)",
          "Environment": [{ "Name": "RUN_ID", "Value.$": "$$.Execution.Name" }] }] }
      },
      "TimeoutSeconds": 14400,
      "Retry": [{ "ErrorEquals": ["ECS.AmazonECSException"], "MaxAttempts": 2, "IntervalSeconds": 60 }],
      "Catch": [{ "ErrorEquals": ["States.ALL"], "Next": "RunTaskOnDemand" }],
      "End": true
    },
    "RunTaskOnDemand": {
      "Type": "Task", "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": { "Cluster": "${cluster_arn}", "TaskDefinition": "${task_def_arn}", "LaunchType": "FARGATE",
        "NetworkConfiguration": { "AwsvpcConfiguration": { "Subnets": ${subnets}, "SecurityGroups": ["${sg}"], "AssignPublicIp": "ENABLED" } },
        "Overrides": { "ContainerOverrides": [{ "Name": "pipeline", "Command.$": "States.Array($.command)",
          "Environment": [{ "Name": "RUN_ID", "Value.$": "$$.Execution.Name" }] }] } },
      "TimeoutSeconds": 14400, "End": true
    }
  }
}
```

(ENTRYPOINT가 `python -m app.pipeline`이므로 Command는 `[command]` 하나만 넘긴다. SFN 로깅 level=ALL → 로그 그룹 `/saramquant-calc/sfn` retention 30일.)
- [ ] **Step 2: `make check` PASS → push/머지 → CI apply green**
- [ ] **Step 3: 실전 검증** — `aws stepfunctions start-execution --input '{"command":"us-fs"}'` (신선도 게이트가 소프트 실패해 몇 분 내 성공 종료하는 가장 저렴한 경로) → 실행 SUCCEEDED + CloudWatch에 run record 확인.
- [ ] **Step 4: Commit** (`260809_SfnEventbridgeTf_kyoungin`)

---

### Task 15: API Lambda (단일 이미지 5함수) + API Gateway + 계약 변경

**Files:**
- Create: `app/api/lambda_handlers.py`(공통: auth 검증·JSON 로깅·라우팅 유틸 + 핸들러 5개), `docker/api/Dockerfile`
- Modify: `app/services/portfolio_analysis_service.py`·`portfolio_simulation_service.py`(portfolio_id 로딩 대신 `holdings: list[dict]` 인자), `app/services/historical_price_lookup.py`(환율 write-back 제거), `app/api/` Flask 블루프린트 4파일 삭제
- Create: `infra/api_lambda.tf`(함수 5개 — 이미지 1개, `image_config.command`로 `app.api.lambda_handlers.<handler>` 지정, memory 2048, timeout 30s, ephemeral 1024, env `DUCKDB_MEMORY_LIMIT=1GB`,`GLUE_DATABASE` 등), `infra/apigateway.tf`(HTTP API v2, 라우트 5개, `$default` 스테이지 auto-deploy)
- Test: `tests/api/test_lambda_handlers.py`(유닛: auth 401, 바디 검증 400, holdings 계약), `tests/api/test_handlers_integration.py`(실제 레이크 대상 로컬 호출)

**Interfaces:**
- Consumes: Task 7 `lake_reader`, 기존 quant 서비스들
- Produces(§8 계약):
  - `POST /internal/portfolios/full-analysis`·`POST /internal/portfolios/simulation` — 바디 `{"market_group":"KR|US","holdings":[{"symbol","market","shares","avg_price","currency","purchased_at","purchase_fx_rate"}]}`
  - `GET /internal/stocks/{symbol}/simulation?market=...`, `POST /internal/portfolios/price-lookup`, `GET /health`
  - 전 핸들러: 헤더 `x-api-key`가 SSM `calc_auth_key`와 불일치 시 401 (`hmac.compare_digest`), finally에서 JSON 로그 1줄(event, status, duration_ms)
- Dockerfile: `public.ecr.aws/lambda/python:3.12`, `requirements-api.txt`(duckdb, pyarrow, pandas, numpy, boto3, requests, pykrx `--no-deps`, alpaca-py, yfinance), DuckDB 확장 오프라인 베이크+LOAD 검증 RUN 스텝(회사 프로젝트 패턴).

- [ ] **Step 1: 유닛 테스트(핸들러 3종: 401/400/정상 라우팅) → FAIL → 구현 → PASS**
- [ ] **Step 2: 통합: 로컬에서 핸들러 직접 호출(실제 레이크의 kr-fs 데이터 활용) — full-analysis는 합성 holdings 2종목으로 응답 구조 검증**
- [ ] **Step 3: `docker build -f docker/api/Dockerfile .` 성공 → push/머지 → CI apply**
- [ ] **Step 4: 배포 검증** — `curl -H "x-api-key: ..." https://<api-id>.execute-api.ap-northeast-2.amazonaws.com/health` 200 확인(전체 curl 스위트는 Task 16 PR 게이트에서).
- [ ] **Step 5: Commit** (`260809_ApiLambda_kyoungin`)

---

### Task 16: 콜드 ETL 완주 + PR 게이트

**Files:**
- Modify: `docs/temp/aws-migration-status.md`(진행 현황 문서 — 각 태스크 완료 시점마다 갱신해왔어야 함), `README.md`(배포·운영 절차 §간결 갱신)

**Steps:**
- [ ] **Step 1: 콜드 ETL 실행** — `start-execution '{"command":"kr-initial"}'` → 완주 확인(수 시간, `gh`/`aws` 폴링) → `'{"command":"us-initial"}'` 완주. 실패 시 원인 수정 후 재실행(멱등 MERGE라 안전).
- [ ] **Step 2: 데이터 검증** — DuckDB로 `daily_prices` 종목 수·날짜 범위, `stock_indicators`/`risk_badges` 행 수가 활성 종목 수와 일치하는지, run-summary status=ok 확인.
- [ ] **Step 3: PR 게이트(순서 고정)** — ① `superpowers:requesting-code-review`(전체 diff) → 지적 해소 ② 배포 환경 curl 4종(분석·시뮬 2종·price-lookup, 유효/무효 키) ③ `superpowers:verification-before-completion`(명령·출력 캡처) ④ `superpowers:finishing-a-development-branch`(전체 테스트 → push → PR 한국어 작성 → 머지).
- [ ] **Step 4: 마무리** — Supabase/Railway 관련 잔재(문서 언급) 정리 커밋, 최종 상태 문서 갱신.

---

## Self-Review 결과

- 스펙 커버리지: §2(스키마/파티션→T5-6), §2.5(패턴→T7-8), §3(배치→T13-14), §4(API→T15), §5(IaC→T1-4), §6(로깅→T12), §7(env→T2·T4), §10 순서 일치. 갭 없음.
- 자리표시자: 반복 레포 재작성(T9-11)은 "현재 파일의 공개 함수 목록 보존"을 명시적 절차로 대체 — 실행자는 각 파일을 먼저 읽고 시그니처를 고정한다.
- 타입 일관성: `merge(table, df, run_id)`·`scan(table)`·`write_run_record(...)` 시그니처를 T8/T7/T12 정의 그대로 T9-13에서 사용.
