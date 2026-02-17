# SaramQuant ML Server 설계서

## 1. 프로젝트 개요

### 목적

주식 데이터를 수집하고, 퀀트 기법과 머신러닝을 통해 분석하여 투자 인사이트를 제공하는 시스템

### 기술 스택

| 레이어 | 기술 | 역할 |
|--------|------|------|
| Frontend | Next.js | 대시보드 UI |
| Gateway | Spring Boot (Kotlin) | API Gateway, 캐싱 |
| ML Server | Flask (Python) | 데이터 수집, 퀀트 분석, ML |
| US FS Collector | Nest.js (TypeScript) | US 재무제표 수집 마이크로서비스 (Railway US East) |
| Database | Supabase (PostgreSQL) | 데이터 저장 |
| Cache | Redis | API 응답 캐싱 |

### 마이크로서비스 구조

```
┌──────────────────┐   HTTP POST /collect   ┌─────────────────────────────────┐
│  ML Server       │ ────────────────────▶  │  saramquant-usa-fstatements-    │
│  (한국 리전)     │   GET /status/:jobId   │  collector (US East, Railway)   │
│                  │ ◀──── polling ────────  │                                │
└────────┬─────────┘                        └────────────┬────────────────────┘
         │                                               │
         │           ┌──────────────┐                    │
         └──────────▶│  Supabase DB │◀───────────────────┘
                     │  (공유)       │
                     └──────────────┘
```

ML Server가 US 재무제표 수집을 트리거하면, US East에 배포된 마이크로서비스가 SEC EDGAR에서 벌크 데이터를 다운로드·파싱·DB 적재까지 처리한다.
ML Server는 jobId로 상태를 폴링하여 완료를 확인한다.

---

## 2. 데이터 소스

### 종목 목록 (KIS 종목정보파일)

KIS에서 제공하는 마스터 파일. 인증 불필요, 매일 자동 업데이트.

| 시장 | URL |
|------|-----|
| KOSPI | `https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip` |
| KOSDAQ | `https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip` |
| NYSE | `https://new.real.download.dws.co.kr/common/master/nysmst.cod.zip` |
| NASDAQ | `https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip` |

### 일봉 OHLCV

| 시장 | 클라이언트 | 비고 |
|------|-----------|------|
| 한국 | pykrx (`PykrxClient`) | KRX에서 시장별 전 종목 일괄 조회 |
| 미국 | Alpaca (`AlpacaClient`) | 배치 50종목 단위, SDK 자동 페이지네이션 |

### 벤치마크 지수

| 벤치마크 | 클라이언트 | 식별자 |
|----------|-----------|--------|
| KOSPI | pykrx | `1001` |
| KOSDAQ | pykrx | `2001` |
| S&P500 | yfinance | `^GSPC` |
| NASDAQ | yfinance | `^IXIC` |

### 무위험 이자율

#### 한국 - ECOS (한국은행 경제통계시스템)

| 만기 | 통계코드 | 항목코드 |
|------|----------|----------|
| 91D | 817Y002 | 010502000 |
| 3Y | 817Y002 | 010200000 |
| 10Y | 817Y002 | 010210000 |

- API URL: `https://ecos.bok.or.kr/api/StatisticSearch`
- 환경변수: `ECOS_API_KEY`

#### 미국 - FRED (Federal Reserve Economic Data)

| 만기 | Series ID |
|------|-----------|
| 91D | DTB3 |
| 1Y | DGS1 |
| 3Y | DGS3 |
| 10Y | DGS10 |

- API URL: `https://api.stlouisfed.org/fred/series/observations`
- 환경변수: `FRED_API_KEY`

### 섹터 분류

| 시장 | 1차 소스 | 2차 소스 (fallback) | 비고 |
|------|----------|-------------------|------|
| 한국 | pykrx (`PykrxClient`) | - | KRX 업종 인덱스 구성종목으로 매핑 |
| 미국 | NASDAQ Screener API | Finnhub API | 1차 실패 시 개별 profile 조회 |

- NASDAQ Screener API: `https://api.nasdaq.com/api/screener/stocks?download=true&exchange={nasdaq|nyse}`
- Finnhub API: `https://finnhub.io/api/v1/stock/profile2?symbol={symbol}` (환경변수: `FINNHUB_API_KEY`)
- 12개 섹터 분류: Basic Materials, Consumer Discretionary, Consumer Staples, Energy, Finance, Health Care, Industrials, Miscellaneous, Real Estate, Technology, Telecommunications, Utilities
- `sector` 값 정책: 유효 문자열 = 퀀트 대상, `'N/A'` = 비보통주(SPAC 등) 제외, `NULL` = 미분류 제외
- 인증 불필요 (NASDAQ Screener), Free tier 60 req/min (Finnhub)

### 재무제표

| 시장 | 수집 방식 | 비고 |
|------|----------|------|
| 한국 | DART API (`DartClient`) | 로컬에서 직접 수집 |
| 미국 | 마이크로서비스 위임 | SEC EDGAR companyfacts.zip 벌크 다운로드, US East에서 처리 |

- US 마이크로서비스 환경변수: `USA_FS_COLLECTOR_URL`, `USA_FS_COLLECTOR_AUTH_KEY`

---

## 3. 파이프라인 아키텍처

KR과 US를 독립된 파이프라인으로 분리하여, 각 시장의 장 마감 시점에 맞춰 실행한다.

```
python -m app.pipeline kr      # KR 일일 (수집 + 지표 + 펀더멘털)
python -m app.pipeline us      # US 일일 (수집 + 지표 + 펀더멘털)
python -m app.pipeline all     # KR + US 순차 실행
python -m app.pipeline kr-fs   # KR 재무제표 수집만
python -m app.pipeline us-fs   # US 재무제표 수집만 (마이크로서비스 트리거)
python -m app.pipeline full    # 전체 (일일 + 재무제표 + 펀더멘털 재계산)
python -m app.pipeline sectors # 섹터 수집만 (수동 실행/디버깅용)
```

### 파이프라인 흐름

```
┌─────────────────────────────────────────────────────────────┐
│              KR 파이프라인 (18:00 KST, Mon-Fri)             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Collect]                                                  │
│      ├──▶ KIS mst 파일 → stocks 갱신 (KOSPI, KOSDAQ)       │
│      ├──▶ pykrx 업종 인덱스 → stocks.sector 갱신           │
│      ├──▶ pykrx → daily_prices 저장                        │
│      ├──▶ pykrx → benchmark_daily_prices 저장              │
│      └──▶ ECOS API → risk_free_rates 저장                  │
│                                                             │
│  [Deactivate] 가격 없는 종목 is_active=false               │
│                                                             │
│  [Compute Fundamentals]                                     │
│      └──▶ PER, PBR, EPS, BPS, ROE, 부채비율, 영업이익률    │
│                                                             │
│  [Compute Factors] ★ 멀티팩터 리스크 모델                   │
│      ├──▶ 팩터 노출도 계산 + 표준화 (MAD Winsorization)    │
│      ├──▶ 시가총액 가중 WLS 횡단면 회귀 → 팩터 수익률      │
│      └──▶ EWM 팩터 공분산 행렬 갱신                        │
│                                                             │
│  [Compute Indicators]                                       │
│      ├──▶ 팩터 베타 (or OLS fallback) + 알파 + 샤프        │
│      └──▶ 이동평균, RSI, MACD 등 23개 지표                 │
│                                                             │
│  [Sector Aggregates] 섹터별 중위수 PER, PBR, ROE 등        │
│                                                             │
│  [Integrity Check] 데이터 품질 보고 (읽기 전용)            │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              US 파이프라인 (09:00 KST, Tue-Sat)             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Collect]                                                  │
│      ├──▶ KIS cod 파일 → stocks 갱신 (NYSE, NASDAQ)        │
│      ├──▶ NASDAQ Screener API → stocks.sector 갱신         │
│      │    (NULL 잔여 → Finnhub fallback)                    │
│      ├──▶ Alpaca → daily_prices 저장                       │
│      ├──▶ yfinance → benchmark_daily_prices 저장           │
│      └──▶ FRED API → risk_free_rates 저장                  │
│                                                             │
│  [Deactivate] 가격 없는 종목 is_active=false               │
│                                                             │
│  [Compute Fundamentals]                                     │
│      └──▶ PER, PBR, EPS, BPS, ROE, 부채비율, 영업이익률    │
│                                                             │
│  [Compute Factors] ★ 멀티팩터 리스크 모델                   │
│      ├──▶ 팩터 노출도 계산 + 표준화 (MAD Winsorization)    │
│      ├──▶ 시가총액 가중 WLS 횡단면 회귀 → 팩터 수익률      │
│      └──▶ EWM 팩터 공분산 행렬 갱신                        │
│                                                             │
│  [Compute Indicators]                                       │
│      ├──▶ 팩터 베타 (or OLS fallback) + 알파 + 샤프        │
│      └──▶ 이동평균, RSI, MACD 등 23개 지표                 │
│                                                             │
│  [Sector Aggregates] 섹터별 중위수 PER, PBR, ROE 등        │
│                                                             │
│  [Integrity Check] 데이터 품질 보고 (읽기 전용)            │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              재무제표 수집 (별도 실행 가능)                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [KR] DART API → financial_statements 저장                  │
│                                                             │
│  [US] ML Server → POST /collect → 마이크로서비스 트리거     │
│       마이크로서비스: EDGAR ZIP 다운로드 → 파싱 → DB 저장   │
│       ML Server ← GET /status/:jobId 폴링으로 완료 확인    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 지표 계산 목록

| 카테고리 | 지표 | 컬럼명 |
|----------|------|--------|
| 이동평균 | SMA(20), EMA(20), WMA(20) | sma_20, ema_20, wma_20 |
| 모멘텀 | RSI(14), MACD, Stochastic | rsi_14, macd/macd_signal/macd_hist, stoch_k/stoch_d |
| 변동성 | Bollinger Bands, ATR(14), ADX(14) | bb_upper/bb_middle/bb_lower, atr_14, adx_14/plus_di/minus_di |
| 거래량 | OBV, VMA(20) | obv, vma_20 |
| 추세 | Parabolic SAR | sar |
| 리스크 | Beta, Jensen's Alpha, Sharpe Ratio | beta, alpha, sharpe |

> Beta는 Barra 멀티팩터 베타를 우선 사용하며, 팩터 수익률 90일 미만 시 OLS 베타로 fallback한다. Alpha와 Sharpe는 선택된 Beta 값을 기반으로 계산된다.

### 펀더멘털 지표 목록

| 카테고리 | 지표 | 컬럼명 |
|----------|------|--------|
| 밸류에이션 | PER, PBR, EPS, BPS | per, pbr, eps, bps |
| 수익성 | ROE, 영업이익률 | roe, operating_margin |
| 안정성 | 부채비율 | debt_ratio |

### 멀티팩터 리스크 모델 (Barra 방식)

| 팩터 | 산출 방식 | 출처 |
|------|-----------|------|
| Market | 절편 (intercept = 1) | - |
| Size | log(종가 × 발행주식수) | daily_prices + financial_statements |
| Value | 1 / PBR | stock_fundamentals |
| Momentum | 12개월 수익률 − 최근 1개월 수익률 | daily_prices |
| Volatility | EWM 표준편차 (half-life=63일) | daily_prices |
| Quality | ROE + 영업이익률 | stock_fundamentals |
| Leverage | 부채비율 | stock_fundamentals |
| Industry | 종목 sector 원-핫 인코딩 | stocks.sector |

- 스타일 팩터는 MAD Winsorization 후 Z-score 표준화
- 횡단면 WLS 회귀 (시가총액 가중, 라그랑주 산업 제약)
- EWM 공분산 (half-life=90일, numpy 직접 구현)
- 팩터 수익률 90일 미만 시 OLS 베타 fallback

---

## 4. 데이터베이스 스키마

### ERD

```
┌────────────────┐    ┌─────────────────┐
│   stocks       │    │  daily_prices   │
├────────────────┤    ├─────────────────┤
│ id (PK)        │─┐  │ id (PK)         │
│ symbol         │ │  │ stock_id (FK)   │
│ name           │ ├─▶│ date            │
│ market         │ │  │ open            │
│ is_active      │ │  │ high            │
│ dart_corp_code │ │  │ low             │
│ sector         │ │  │ close           │
│ created_at     │ │  │ volume          │
│ updated_at     │ │  │ created_at      │
└────────────────┘ │  └─────────────────┘
                   │
                   │  ┌─────────────────────┐
                   │  │  stock_indicators   │
                   │  ├─────────────────────┤
                   ├─▶│ stock_id (PK, FK)   │
                   │  │ date (PK)           │
                   │  │ sma_20 .. sharpe    │
                   │  │ created_at          │
                   │  └─────────────────────┘
                   │
                   │  ┌───────────────────────────┐
                   │  │   financial_statements    │
                   │  ├───────────────────────────┤
                   ├─▶│ id (PK)                   │
                   │  │ stock_id (FK)             │
                   │  │ fiscal_year               │
                   │  │ report_type               │
                   │  │ revenue .. shares_out      │
                   │  │ created_at                │
                   │  └───────────────────────────┘
                   │
                   │  ┌───────────────────────────┐
                   │  │   stock_fundamentals      │
                   │  ├───────────────────────────┤
                   ├─▶│ stock_id (PK, FK)         │
                   │  │ date (PK)                 │
                   │  │ per .. operating_margin    │
                   │  │ created_at                │
                   │  └───────────────────────────┘
                   │
                   │  ┌─────────────────────┐
                   │  │    predictions      │
                   │  ├─────────────────────┤
                   └─▶│ id (PK)             │
                      │ stock_id (FK)       │
                      │ date                │
                      │ direction           │
                      │ confidence          │
                      │ actual_direction    │
                      │ is_correct          │
                      │ created_at          │
                      └─────────────────────┘

                   │
                   │  ┌───────────────────────────┐
                   │  │   factor_exposures        │
                   │  ├───────────────────────────┤
                   ├─▶│ stock_id (PK, FK)         │
                   │  │ date (PK)                 │
                   │  │ size_z .. leverage_z       │
                   │  └───────────────────────────┘
                   │
                   │  ┌───────────────────────────┐
                   │  │   factor_returns          │
                   │  ├───────────────────────────┤
                   │  │ market (PK)               │
                   │  │ date (PK)                 │
                   │  │ market_ret .. industry_*   │
                   │  └───────────────────────────┘
                   │
                   │  ┌───────────────────────────┐
                   │  │   factor_covariance       │
                   │  ├───────────────────────────┤
                   │  │ market (PK)               │
                   │  │ date (PK)                 │
                   │  │ matrix (JSONB)            │
                   │  │ factor_names (TEXT[])      │
                   │  └───────────────────────────┘

┌───────────────────────────┐
│   sector_aggregates       │
├───────────────────────────┤
│ market (PK)               │
│ sector (PK)               │
│ date (PK)                 │
│ stock_count               │
│ median_per .. median_*    │
└───────────────────────────┘

┌─────────────────┐   ┌───────────────────────────┐
│   ml_models     │   │  benchmark_daily_prices   │
├─────────────────┤   ├───────────────────────────┤
│ id (PK)         │   │ id (PK)                   │
│ name            │   │ benchmark                 │
│ market          │   │ date                      │
│ accuracy        │   │ close                     │
│ path            │   │ created_at                │
│ is_active       │   └───────────────────────────┘
│ created_at      │
└─────────────────┘   ┌───────────────────────────┐
                      │    risk_free_rates        │
                      ├───────────────────────────┤
                      │ id (PK)                   │
                      │ country                   │
                      │ maturity                  │
                      │ date                      │
                      │ rate                      │
                      │ created_at                │
                      └───────────────────────────┘
```

### 테이블 상세

#### stocks

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| symbol | VARCHAR(20) | 종목 코드 |
| name | TEXT | 종목명 |
| market | market_type | KR_KOSPI, KR_KOSDAQ, US_NYSE, US_NASDAQ |
| is_active | BOOLEAN | 활성 여부 |
| dart_corp_code | VARCHAR(8) | DART 고유번호 (KR만 사용) |
| sector | VARCHAR(100) | 섹터 분류 (KR: pykrx 업종, US: NASDAQ Screener) |
| created_at | TIMESTAMPTZ | 생성일시 |
| updated_at | TIMESTAMPTZ | 수정일시 |

#### daily_prices

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| stock_id | BIGINT | FK → stocks.id |
| date | DATE | 거래일 |
| open | NUMERIC(15,2) | 시가 |
| high | NUMERIC(15,2) | 고가 |
| low | NUMERIC(15,2) | 저가 |
| close | NUMERIC(15,2) | 종가 |
| volume | BIGINT | 거래량 |
| created_at | TIMESTAMPTZ | 수집일시 |

#### stock_indicators

| 컬럼 | 타입 | 설명 |
|------|------|------|
| stock_id | BIGINT | PK (복합), FK → stocks.id |
| date | DATE | PK (복합), 기준 거래일 |
| sma_20 | NUMERIC(15,4) | SMA 20일 |
| ema_20 | NUMERIC(15,4) | EMA 20일 |
| wma_20 | NUMERIC(15,4) | WMA 20일 |
| rsi_14 | NUMERIC(8,4) | RSI 14일 |
| macd | NUMERIC(15,4) | MACD 라인 |
| macd_signal | NUMERIC(15,4) | MACD 시그널 |
| macd_hist | NUMERIC(15,4) | MACD 히스토그램 |
| stoch_k | NUMERIC(8,4) | 스토캐스틱 %K |
| stoch_d | NUMERIC(8,4) | 스토캐스틱 %D |
| bb_upper | NUMERIC(15,4) | 볼린저 상단 |
| bb_middle | NUMERIC(15,4) | 볼린저 중앙 |
| bb_lower | NUMERIC(15,4) | 볼린저 하단 |
| atr_14 | NUMERIC(15,4) | ATR 14일 |
| adx_14 | NUMERIC(8,4) | ADX 14일 |
| plus_di | NUMERIC(8,4) | +DI |
| minus_di | NUMERIC(8,4) | -DI |
| obv | BIGINT | OBV |
| vma_20 | BIGINT | 거래량 이동평균 20일 |
| sar | NUMERIC(15,4) | Parabolic SAR |
| beta | NUMERIC(8,4) | 베타 (vs 벤치마크) |
| alpha | NUMERIC(8,4) | 젠센의 알파 |
| sharpe | NUMERIC(8,4) | 샤프 비율 |
| created_at | TIMESTAMPTZ | 생성일시 |

#### financial_statements

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| stock_id | BIGINT | FK → stocks.id |
| fiscal_year | INT | 회계연도 |
| report_type | report_type | ANNUAL, Q1, Q2, Q3 |
| revenue | NUMERIC(20,2) | 매출액 |
| operating_income | NUMERIC(20,2) | 영업이익 |
| net_income | NUMERIC(20,2) | 순이익 |
| total_assets | NUMERIC(20,2) | 총자산 |
| total_liabilities | NUMERIC(20,2) | 총부채 |
| total_equity | NUMERIC(20,2) | 총자본 |
| shares_outstanding | BIGINT | 발행주식수 |
| created_at | TIMESTAMPTZ | 수집일시 |

#### stock_fundamentals

| 컬럼 | 타입 | 설명 |
|------|------|------|
| stock_id | BIGINT | PK (복합), FK → stocks.id |
| date | DATE | PK (복합), 기준일 |
| per | NUMERIC(12,4) | PER |
| pbr | NUMERIC(12,4) | PBR |
| eps | NUMERIC(15,4) | EPS |
| bps | NUMERIC(15,4) | BPS |
| roe | NUMERIC(10,4) | ROE |
| debt_ratio | NUMERIC(10,4) | 부채비율 |
| operating_margin | NUMERIC(10,4) | 영업이익률 |
| data_coverage | data_coverage_type | FULL, PARTIAL, LOSS, NO_FS, INSUFFICIENT |
| created_at | TIMESTAMPTZ | 생성일시 |

#### predictions

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| stock_id | BIGINT | FK → stocks.id |
| date | DATE | 예측 대상일 |
| direction | direction_type | UP, DOWN |
| confidence | NUMERIC(5,4) | 신뢰도 (0~1) |
| actual_direction | direction_type | 실제 결과 (검증 시 업데이트) |
| is_correct | BOOLEAN | 예측 정확 여부 |
| created_at | TIMESTAMPTZ | 생성일시 |

#### ml_models

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| name | VARCHAR(50) | 모델명 |
| market | market_type | 대상 시장 |
| accuracy | NUMERIC(5,4) | 정확도 |
| path | VARCHAR(255) | 모델 파일 경로 |
| is_active | BOOLEAN | 현재 사용 여부 |
| created_at | TIMESTAMPTZ | 생성일시 |

#### benchmark_daily_prices

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| benchmark | benchmark_type | KR_KOSPI, KR_KOSDAQ, US_SP500, US_NASDAQ |
| date | DATE | 거래일 |
| close | NUMERIC(15,2) | 종가 |
| created_at | TIMESTAMPTZ | 생성일시 |

#### risk_free_rates

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| country | country_type | KR, US |
| maturity | maturity_type | 91D, 1Y, 3Y, 10Y |
| date | DATE | 기준일 |
| rate | NUMERIC(6,4) | 금리 (%) |
| created_at | TIMESTAMPTZ | 생성일시 |

#### factor_exposures

| 컬럼 | 타입 | 설명 |
|------|------|------|
| stock_id | BIGINT | PK (복합), FK → stocks.id |
| date | DATE | PK (복합), 기준일 |
| size_z | NUMERIC(8,4) | Size 팩터 Z-score |
| value_z | NUMERIC(8,4) | Value 팩터 Z-score |
| momentum_z | NUMERIC(8,4) | Momentum 팩터 Z-score |
| volatility_z | NUMERIC(8,4) | Volatility 팩터 Z-score |
| quality_z | NUMERIC(8,4) | Quality 팩터 Z-score |
| leverage_z | NUMERIC(8,4) | Leverage 팩터 Z-score |

#### factor_returns

| 컬럼 | 타입 | 설명 |
|------|------|------|
| market | market_type | PK (복합), 시장 |
| date | DATE | PK (복합), 기준일 |
| factor_name | TEXT | PK (복합), 팩터명 |
| return_value | NUMERIC(10,6) | 팩터 수익률 |

#### factor_covariance

| 컬럼 | 타입 | 설명 |
|------|------|------|
| market | market_type | PK (복합), 시장 |
| date | DATE | PK (복합), 기준일 |
| matrix | JSONB | 팩터 공분산 행렬 (K×K) |
| factor_names | TEXT[] | 팩터명 순서 배열 |

#### sector_aggregates

| 컬럼 | 타입 | 설명 |
|------|------|------|
| market | market_type | PK (복합), 시장 |
| sector | VARCHAR(100) | PK (복합), 섹터명 |
| date | DATE | PK (복합), 기준일 |
| stock_count | INT | 해당 섹터 종목 수 |
| median_per | NUMERIC(12,4) | PER 중위수 |
| median_pbr | NUMERIC(12,4) | PBR 중위수 |
| median_roe | NUMERIC(10,4) | ROE 중위수 |
| median_operating_margin | NUMERIC(10,4) | 영업이익률 중위수 |
| median_debt_ratio | NUMERIC(10,4) | 부채비율 중위수 |

---

## 5. 스케줄러

### 파이프라인 스케줄

| 시간 (KST) | 명령어 | 요일 | 설명 |
|------------|--------|------|------|
| 18:00 | `python -m app.pipeline kr` | Mon-Fri | KR 수집 + 지표 + 펀더멘털 |
| 09:00 | `python -m app.pipeline us` | Tue-Sat | US 수집 + 지표 + 펀더멘털 |

### KR 파이프라인 (18:00 KST)

| 순서 | 단계 | 작업 | 설명 |
|------|------|------|------|
| 1 | Collect | 종목 목록 갱신 | KIS mst 파일 (KOSPI, KOSDAQ) |
| 2 | Collect | KR 섹터 수집 | pykrx 업종 인덱스 매핑 |
| 3 | Collect | KR 일봉 수집 | pykrx로 당일 전 종목 OHLCV |
| 4 | Collect | KR 벤치마크 수집 | pykrx로 KOSPI/KOSDAQ 지수 |
| 5 | Collect | KR 무위험금리 수집 | ECOS API (91D, 3Y, 10Y) |
| 6 | Deactivate | 미가격 종목 비활성화 | 가격 없는 종목 is_active=false |
| 7 | Compute | KR 펀더멘털 계산 | PER, PBR, ROE 등 → stock_fundamentals |
| 8 | Compute | KR 팩터 모델 | 노출도 → WLS 회귀 → 공분산 → factor_* 테이블 |
| 9 | Compute | KR 지표 계산 | 팩터 베타 + 23개 지표 → stock_indicators |
| 10 | Compute | KR 섹터 집계 | 섹터별 중위수 PER, PBR 등 → sector_aggregates |
| 11 | Check | 무결성 보고 | 데이터 품질 로그 (읽기 전용) |

### US 파이프라인 (09:00 KST)

| 순서 | 단계 | 작업 | 설명 |
|------|------|------|------|
| 1 | Collect | 종목 목록 갱신 | KIS cod 파일 (NYSE, NASDAQ) |
| 2 | Collect | US 섹터 수집 | NASDAQ Screener 벌크 + Finnhub fallback |
| 3 | Collect | US 일봉 수집 | Alpaca API 배치 수집 |
| 4 | Collect | US 벤치마크 수집 | yfinance로 S&P500/NASDAQ 지수 |
| 5 | Collect | US 무위험금리 수집 | FRED API (91D, 1Y, 3Y, 10Y) |
| 6 | Deactivate | 미가격 종목 비활성화 | 가격 없는 종목 is_active=false |
| 7 | Compute | US 펀더멘털 계산 | PER, PBR, ROE 등 → stock_fundamentals |
| 8 | Compute | US 팩터 모델 | 노출도 → WLS 회귀 → 공분산 → factor_* 테이블 |
| 9 | Compute | US 지표 계산 | 팩터 베타 + 23개 지표 → stock_indicators |
| 10 | Compute | US 섹터 집계 | 섹터별 중위수 PER, PBR 등 → sector_aggregates |
| 11 | Check | 무결성 보고 | 데이터 품질 로그 (읽기 전용) |

### 개별 수집 (별도 실행)

| 명령어 | 설명 |
|--------|------|
| `python -m app.pipeline kr-fs` | KR 재무제표 수집 (DART API, 로컬) |
| `python -m app.pipeline us-fs` | US 재무제표 수집 (마이크로서비스에 위임, 폴링 대기) |
| `python -m app.pipeline sectors` | KR+US 섹터 수집만 (수동 실행/디버깅용) |
| `python -m app.pipeline full` | 전체 파이프라인 (일일 + 재무제표 + 펀더멘털 재계산) |

### 스케줄 시간 근거

- KR: 장 마감 15:30 KST → 데이터 확정 ~17:00 → 18:00 실행 → 다음 장 09:00까지 15시간 여유
- US: 장 마감 16:00 EST = 06:00 KST → 데이터 확정 ~08:00 KST → 09:00 실행 → 다음 장 23:30 KST까지 14.5시간 여유

### Cron 설정 (참고)

```bash
# KR pipeline: 18:00 KST, Mon-Fri
0 18 * * 1-5 cd /path/to/saramquant-ml-server && .venv/bin/python -m app.pipeline kr >> logs/pipeline_kr.log 2>&1

# US pipeline: 09:00 KST, Tue-Sat
0 9 * * 2-6 cd /path/to/saramquant-ml-server && .venv/bin/python -m app.pipeline us >> logs/pipeline_us.log 2>&1
```

---

## 6. API 명세

### Gateway API (Spring Boot)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/stocks` | 종목 목록 |
| GET | `/api/stocks/{symbol}` | 종목 상세 |
| GET | `/api/prices/daily/{symbol}` | 일봉 데이터 |
| GET | `/api/indicators/{symbol}` | 기술적 지표 (온디맨드 계산) |
| GET | `/api/risk/{symbol}` | 리스크 지표 (온디맨드 계산) |
| GET | `/api/predictions/{symbol}` | ML 예측 결과 |

### Internal API (Flask)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/stocks` | 종목 목록 |
| GET | `/api/stocks/<symbol>` | 종목 상세 |
| GET | `/api/prices/daily/<symbol>` | 일봉 데이터 |
| GET | `/api/indicators/<symbol>` | 기술적 지표 (온디맨드 계산) |
| GET | `/api/risk/<symbol>` | 리스크 지표 (Beta, Alpha, Sharpe) |
| GET | `/api/stocks/<symbol>/simulation` | 몬테카를로 시뮬레이션 (GBM/Bootstrap) |
| GET | `/health` | 헬스 체크 |

### US Financial Statements Collector (Nest.js, 마이크로서비스)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/usa-financial-statements/collect` | 수집 작업 트리거 → `{ jobId }` 반환 |
| GET | `/usa-financial-statements/status/:jobId` | 작업 상태 조회 |
| GET | `/usa-financial-statements/health` | 헬스 체크 |

데이터 수집과 지표 일괄 계산은 API가 아닌 CLI 파이프라인으로 처리:

```bash
python -m app.pipeline kr|us|all|kr-fs|us-fs|full
```

---

## 7. Redis 캐시

| 키 패턴 | TTL | 설명 |
|---------|-----|------|
| `stock:list:{market}` | 1시간 | 종목 목록 |
| `price:daily:{symbol}:{date}` | 24시간 | 일봉 데이터 |
| `indicator:{symbol}:{date}` | 24시간 | 기술적 지표 |
| `prediction:{symbol}:{date}` | 24시간 | ML 예측 결과 |
