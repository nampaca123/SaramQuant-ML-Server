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
| Database | Supabase (PostgreSQL) | 데이터 저장 |
| Cache | Redis | API 응답 캐싱 |

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

---

## 3. 파이프라인 아키텍처

KR과 US를 독립된 파이프라인으로 분리하여, 각 시장의 장 마감 시점에 맞춰 실행한다.

```
python -m app.pipeline kr      # KR 전체 (수집 + 지표 계산)
python -m app.pipeline us      # US 전체 (수집 + 지표 계산)
python -m app.pipeline all     # KR + US 순차 실행
```

### 파이프라인 흐름

```
┌─────────────────────────────────────────────────────────────┐
│              KR 파이프라인 (18:00 KST, Mon-Fri)             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Collect]                                                  │
│      │                                                      │
│      ├──▶ KIS mst 파일 → stocks 갱신 (KOSPI, KOSDAQ)       │
│      ├──▶ pykrx → daily_prices 저장                        │
│      ├──▶ pykrx → benchmark_daily_prices 저장              │
│      │    (KOSPI, KOSDAQ 지수)                              │
│      └──▶ ECOS API → risk_free_rates 저장                  │
│           (91D, 3Y, 10Y)                                    │
│                                                             │
│  [Compute]                                                  │
│      │                                                      │
│      ├──▶ 마켓별 전 종목 가격 벌크 로드                    │
│      ├──▶ 벡터화 지표 일괄 계산 (23개 지표)                │
│      └──▶ stock_indicators 테이블 DELETE → INSERT          │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              US 파이프라인 (09:00 KST, Tue-Sat)             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [Collect]                                                  │
│      │                                                      │
│      ├──▶ KIS cod 파일 → stocks 갱신 (NYSE, NASDAQ)        │
│      ├──▶ Alpaca → daily_prices 저장                       │
│      ├──▶ yfinance → benchmark_daily_prices 저장           │
│      │    (S&P500, NASDAQ 지수)                             │
│      └──▶ FRED API → risk_free_rates 저장                  │
│           (91D, 1Y, 3Y, 10Y)                                │
│                                                             │
│  [Compute]                                                  │
│      │                                                      │
│      ├──▶ 마켓별 전 종목 가격 벌크 로드                    │
│      ├──▶ 벡터화 지표 일괄 계산 (23개 지표)                │
│      └──▶ stock_indicators 테이블 DELETE → INSERT          │
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

---

## 4. 데이터베이스 스키마

### ERD

```
┌─────────────┐       ┌─────────────────┐
│   stocks    │       │  daily_prices   │
├─────────────┤       ├─────────────────┤
│ id (PK)     │──┐    │ id (PK)         │
│ symbol      │  │    │ stock_id (FK)   │
│ name        │  ├───▶│ date            │
│ market      │  │    │ open            │
│ is_active   │  │    │ high            │
│ created_at  │  │    │ low             │
│ updated_at  │  │    │ close           │
└─────────────┘  │    │ volume          │
       │         │    │ created_at      │
       │         │    └─────────────────┘
       │         │
       │         │    ┌─────────────────────┐
       │         │    │  stock_indicators   │
       │         │    ├─────────────────────┤
       │         └───▶│ stock_id (PK, FK)   │
       │              │ date (PK)           │
       │              │ sma_20 .. sharpe    │
       │              │ created_at          │
       │              └─────────────────────┘
       │
       │              ┌─────────────────────┐
       │              │    predictions      │
       │              ├─────────────────────┤
       └─────────────▶│ id (PK)             │
                      │ stock_id (FK)       │
                      │ date                │
                      │ direction           │
                      │ confidence          │
                      │ actual_direction    │
                      │ is_correct          │
                      │ created_at          │
                      └─────────────────────┘

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

---

## 5. 스케줄러

### 파이프라인 스케줄

| 시간 (KST) | 명령어 | 요일 | 설명 |
|------------|--------|------|------|
| 18:00 | `python -m app.pipeline kr` | Mon-Fri | KR 수집 + 지표 계산 |
| 09:00 | `python -m app.pipeline us` | Tue-Sat | US 수집 + 지표 계산 |

### KR 파이프라인 (18:00 KST)

| 순서 | 작업 | 설명 |
|------|------|------|
| 1 | 종목 목록 갱신 | KIS mst 파일 (KOSPI, KOSDAQ) |
| 2 | KR 일봉 수집 | pykrx로 당일 전 종목 OHLCV |
| 3 | KR 벤치마크 수집 | pykrx로 KOSPI/KOSDAQ 지수 |
| 4 | KR 무위험금리 수집 | ECOS API (91D, 3Y, 10Y) |
| 5 | KR 지표 계산 | 전 종목 23개 지표 벌크 계산 → stock_indicators |

### US 파이프라인 (09:00 KST)

| 순서 | 작업 | 설명 |
|------|------|------|
| 1 | 종목 목록 갱신 | KIS cod 파일 (NYSE, NASDAQ) |
| 2 | US 일봉 수집 | Alpaca API 배치 수집 |
| 3 | US 벤치마크 수집 | yfinance로 S&P500/NASDAQ 지수 |
| 4 | US 무위험금리 수집 | FRED API (91D, 1Y, 3Y, 10Y) |
| 5 | US 지표 계산 | 전 종목 23개 지표 벌크 계산 → stock_indicators |

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
| GET | `/health` | 헬스 체크 |

데이터 수집과 지표 일괄 계산은 API가 아닌 CLI 파이프라인으로 처리:

```bash
python -m app.pipeline kr|us|all
```

---

## 7. Redis 캐시

| 키 패턴 | TTL | 설명 |
|---------|-----|------|
| `stock:list:{market}` | 1시간 | 종목 목록 |
| `price:daily:{symbol}:{date}` | 24시간 | 일봉 데이터 |
| `indicator:{symbol}:{date}` | 24시간 | 기술적 지표 |
| `prediction:{symbol}:{date}` | 24시간 | ML 예측 결과 |
