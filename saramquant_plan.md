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
| NASDAQ | `https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip` |
| NYSE | `https://new.real.download.dws.co.kr/common/master/nysmst.cod.zip` |

### 일봉 OHLCV (FinanceDataReader)

| 시장 | 사용법 | 비고 |
|------|--------|------|
| 한국 | `fdr.DataReader("005930", "2024-01-01")` | OHLCV + Change |
| 미국 | `fdr.DataReader("AAPL", "2024-01-01")` | OHLCV |

---

## 3. 데이터 흐름

```
┌─────────────────────────────────────────────────────────────┐
│                      데이터 수집 흐름                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [매일 06:00 KST]                                           │
│                                                             │
│      Scheduler                                              │
│          │                                                  │
│          ├──▶ KIS mst/cod 파일 다운로드                     │
│          │         │                                        │
│          │         ▼                                        │
│          │    stocks 테이블 갱신                            │
│          │                                                  │
│          └──▶ FinanceDataReader                             │
│                    │                                        │
│                    ▼                                        │
│               daily_prices 저장                             │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  [매일 07:00 KST]                                           │
│                                                             │
│      Scheduler                                              │
│          │                                                  │
│          ├──▶ 기술적 지표 계산 (RSI, MACD, MA 등)           │
│          │                                                  │
│          ├──▶ ML 모델 학습/예측                             │
│          │                                                  │
│          └──▶ predictions 저장                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 데이터베이스 스키마

### ERD

```
┌─────────────┐       ┌─────────────────┐
│   stocks    │       │  daily_prices   │
├─────────────┤       ├─────────────────┤
│ id (PK)     │──┐    │ id (PK)         │
│ symbol      │  │    │ stock_id (FK)   │
│ name        │  └───▶│ date            │
│ market      │       │ open            │
│ is_active   │       │ high            │
│ created_at  │       │ low             │
│ updated_at  │       │ close           │
└─────────────┘       │ volume          │
       │              │ created_at      │
       │              └─────────────────┘
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

┌─────────────────┐
│   ml_models     │
├─────────────────┤
│ id (PK)         │
│ name            │
│ market          │
│ accuracy        │
│ path            │
│ is_active       │
│ created_at      │
└─────────────────┘
```

### 테이블 상세

#### stocks

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| symbol | VARCHAR(20) | 종목 코드 |
| name | VARCHAR(100) | 종목명 |
| market | VARCHAR(20) | KR_KOSPI, KR_KOSDAQ, US_NYSE, US_NASDAQ |
| is_active | BOOLEAN | 활성 여부 |
| created_at | TIMESTAMP | 생성일시 |
| updated_at | TIMESTAMP | 수정일시 |

#### daily_prices

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| stock_id | BIGINT | FK → stocks.id |
| date | DATE | 거래일 |
| open | DECIMAL(15,2) | 시가 |
| high | DECIMAL(15,2) | 고가 |
| low | DECIMAL(15,2) | 저가 |
| close | DECIMAL(15,2) | 종가 |
| volume | BIGINT | 거래량 |
| created_at | TIMESTAMP | 수집일시 |

#### predictions

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| stock_id | BIGINT | FK → stocks.id |
| date | DATE | 예측 대상일 |
| direction | VARCHAR(10) | UP, DOWN |
| confidence | DECIMAL(5,4) | 신뢰도 (0~1) |
| actual_direction | VARCHAR(10) | 실제 결과 (검증 시 업데이트) |
| is_correct | BOOLEAN | 예측 정확 여부 |
| created_at | TIMESTAMP | 생성일시 |

#### ml_models

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BIGSERIAL | PK |
| name | VARCHAR(50) | 모델명 |
| market | VARCHAR(20) | 대상 시장 |
| accuracy | DECIMAL(5,4) | 정확도 |
| path | VARCHAR(255) | 모델 파일 경로 |
| is_active | BOOLEAN | 현재 사용 여부 |
| created_at | TIMESTAMP | 생성일시 |

---

## 5. 스케줄러

| 시간 (KST) | 작업 | 설명 |
|------------|------|------|
| 06:00 | 종목 목록 갱신 | KIS mst/cod 파일 다운로드 |
| 06:30 | 미국 일봉 수집 | FDR로 전일 미국 데이터 수집 |
| 16:30 | 한국 일봉 수집 | FDR로 당일 한국 데이터 수집 |
| 17:00 | ML 예측 | 예측 결과 저장 |
| 17:30 | 예측 검증 | 전일 예측 vs 실제 결과 비교 |

---

## 6. API 명세

### Gateway API (Spring Boot)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/stocks` | 종목 목록 |
| GET | `/api/stocks/{symbol}` | 종목 상세 |
| GET | `/api/prices/daily/{symbol}` | 일봉 데이터 |
| GET | `/api/indicators/{symbol}` | 기술적 지표 |
| GET | `/api/predictions/{symbol}` | ML 예측 결과 |

### Internal API (Flask)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/collect/stocks` | 종목 목록 수집 |
| POST | `/collect/daily` | 일봉 수집 |
| POST | `/train` | ML 학습 |
| GET | `/health` | 헬스 체크 |

---

## 7. Redis 캐시

| 키 패턴 | TTL | 설명 |
|---------|-----|------|
| `stock:list:{market}` | 1시간 | 종목 목록 |
| `price:daily:{symbol}:{date}` | 24시간 | 일봉 데이터 |
| `indicator:{symbol}:{date}` | 24시간 | 기술적 지표 |
| `prediction:{symbol}:{date}` | 24시간 | ML 예측 결과 |
