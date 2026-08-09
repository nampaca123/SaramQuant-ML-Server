# SaramQuant 레이크하우스 스키마 & 파티셔닝 스펙 (기준 문서)

작성: 2026-08-09, calc-server 세션. **gateway / usa-fstatements-collector 세션은 이 문서를 스키마의 단일 기준으로 삼는다.**
설계 배경은 `docs/superpowers/specs/2026-08-09-aws-migration-design.md` 참조.

## 1. 공통 규약

| 항목 | 값 |
|---|---|
| 리전 | ap-northeast-2 (usa-fstatements-collector의 컴퓨트만 us-east-1, 데이터는 여기로 기록) |
| 버킷 | `s3://saramquant-bucket` (버저닝 없음) |
| Glue 데이터베이스 | `saramquant` (단일) |
| 웨어하우스 위치 | `s3://saramquant-bucket/warehouse/<table>/` |
| 테이블 포맷 | Iceberg v2, Parquet, `write.parquet.compression-codec=zstd` (기본값 gzip이므로 반드시 명시) |
| 읽기 | DuckDB만. Glue `GetTable` → `Parameters["metadata_location"]` → `iceberg_scan()`. **Parquet 경로 직접 글롭 금지** |
| 배치 쓰기 | Athena SQL (staging Parquet → `MERGE INTO` / `INSERT INTO`). 워크그룹 `saramquant` |
| 쓰기 후 정리 | 행 단위 MERGE/DELETE를 쓴 테이블은 파이프라인 말미에 Athena `OPTIMIZE ... REWRITE DATA` + `VACUUM` (delete file 제거로 DuckDB 호환 유지) |
| 태그 | 전 리소스 `project=saramquant` (provider default_tags로 부여, 사용자 확정) |
| Terraform 상태 | `s3://saramquant-tfstate` 공유 버킷, 리포별 key (`calc-server/`, `gateway/`, `usa-fstatements/` + `terraform.tfstate`), `use_lockfile=true` |
| 런 레코드 | `s3://saramquant-bucket/run-summary/<서비스>_<작업>.json` (§5) |
| staging | `s3://saramquant-bucket/staging/<table>/<run_id>/` (라이프사이클 7일 만료) |
| Athena 결과 | `s3://saramquant-bucket/athena-results/` (라이프사이클 14일 만료) |

### Postgres → Iceberg 타입 매핑 규칙

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

- **제약/인덱스/트리거는 Iceberg에 없다.** unique 제약 → MERGE 키로 대체, check 제약 → writer 검증, `set_updated_at` 트리거 → writer가 직접 세팅, 인덱스 → 파티션 + 파일 내 정렬로 대체.
- **surrogate id 처리(유일한 의도적 스키마 변경)**: 자동증가 시퀀스는 Iceberg에 없다.
  - `stocks.id` (long)는 **유지** — 전 테이블의 조인 키. 신규 종목 upsert 시 배치(단일 작성자)가 `max(id)+1`로 채번.
  - 자연키가 있는 팩트 테이블의 `id` 컬럼은 **제거**: daily_prices, benchmark_daily_prices, risk_free_rates, financial_statements, exchange_rates. (코드 어디에서도 이 id들을 읽지 않음을 확인. 자연키는 아래 표의 MERGE 키)
  - gateway 소유 테이블의 id 채번 방식은 gateway 세션이 결정(uuid 컬럼은 문자열로 유지 가능).

## 2. 마켓 데이터 테이블 (calc-server 소유)

쓰기 주체: calc 배치 (financial_statements의 US 행만 usa-fstatements-collector).
파티셔닝 원칙: 데이터가 작으므로(전 종목 ~9천, 일일 ~1만 행/테이블) **과분할 방지**. 파티션 프로젝션은 Hive 전용 개념으로 Iceberg에는 해당 없음 — Iceberg 메타데이터 프루닝 + 파일 내 정렬이 그 역할을 한다.

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

† `market`/`market†` 파티션 컬럼: daily_prices와 financial_statements에는 원본에 market 컬럼이 없다(stocks 조인으로 유도).
파티션 프루닝을 위해 **`market` 컬럼(string)을 비정규화로 추가**한다 — KR/US 파이프라인이 서로의 파티션을 건드리지 않게 하는 분리 키. 값은 `KR` | `US` (market_group 수준, market_type 아님).

### 컬럼 정의 (Postgres 원본 → Iceberg, 기존 `db_table.sql` 기준)

`market†` = 위에서 설명한 추가 컬럼. 그 외 컬럼 추가/삭제/개명 없음(§1 surrogate id 제거 제외).

**stocks** — id long, symbol string, name string, market string(market_type), is_active boolean, dart_corp_code string, sector string, created_at/updated_at timestamptz

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

### 레이크하우스로 가져가지 않는 테이블

- `predictions`, `ml_models` — 코드 어디에도 레포지토리/사용처가 없는 레거시. 생성하지 않는다.
- `audit_log`의 calc 기록(API/PIPELINE) — CloudWatch 구조화 로그 + run-summary로 대체. 테이블 자체의 존폐는 gateway 세션이 결정(§3).

## 3. 사용자/서비스 테이블 (gateway 소유 — 권고안)

최종 결정권은 gateway 세션에 있다. 실사용자가 극소수이므로 **전부 무파티션**을 권고(아래 예외만 파티션).
타입 매핑은 §1 규칙 동일(uuid → string, enum → string, jsonb → string).

| 테이블 | 파티션 권고 | 비고 |
|---|---|---|
| users, user_profiles, user_preferred_markets | 없음 | 소형 마스터 |
| user_portfolios, portfolio_holdings | 없음 | **calc는 더 이상 이 테이블을 읽지 않는다** — gateway가 calc API 호출 시 보유 종목을 요청 바디로 전달 (§4) |
| llm_usage_logs, portfolio_recommendations | 없음 | 소형 |
| stock_llm_analyses, portfolio_llm_analyses | 없음 (커지면 months(date)) | append 위주 |
| audit_log | months(created_at) | append-only 로그. gateway 방문 통계용 |
| refresh_tokens, email_verification_codes | — | **주의**: 로그인/인증마다 커밋이 발생하는 고빈도 변경 테이블로 Iceberg에 부적합. gateway 세션에서 별도 저장소(예: DynamoDB 온디맨드) 검토 권고 |

## 4. 서비스 간 계약

1. **gateway → calc API** (API Gateway HTTP API, `x-api-key: CALC_AUTH_KEY` 유지):
   - `POST /internal/portfolios/full-analysis`, `POST /internal/portfolios/simulation` — **계약 변경**:
     요청 바디에 보유 종목 배열 포함 `{"market_group": "KR|US", "holdings": [{"symbol", "market", "shares", "avg_price", "currency", "purchased_at", "purchase_fx_rate"}]}`.
     calc는 사용자 테이블에 접근하지 않는다.
   - `GET /internal/stocks/{symbol}/simulation`, `POST /internal/portfolios/price-lookup` — 계약 불변.
     price-lookup의 라이브 폴백 시 환율 write-back은 제거됨(응답만 반환, 적재는 다음 배치).
2. **usa-fstatements-collector → 레이크하우스**:
   - `saramquant.financial_statements`에 US 행 기록 (§2 스키마·MERGE 키 준수, `market='US'`).
   - stock_id는 `saramquant.stocks`(calc가 매일 갱신)에서 symbol+market으로 조인해 해석.
   - 완료 시 `run-summary/usa_fstatements.json` 기록 (§5 포맷). calc의 `us-fs` 작업(03:00 KST)은
     이 파일의 `status=="ok" && age<72h`를 게이트로 fundamentals를 재계산한다. HTTP 트리거는 폐지.
3. **스케줄 (전부 기존 주기 유지, KST 의미 보존)**: KR 일일 월–금 18:00 / US 일일 화–토 09:00 /
   분기 FS 4·5·8·11월 지정일 03:00. usa-fstatements-collector는 calc `us-fs`보다 충분히 앞서 완료되도록 자체 스케줄 설정.

## 5. run-summary 포맷

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
