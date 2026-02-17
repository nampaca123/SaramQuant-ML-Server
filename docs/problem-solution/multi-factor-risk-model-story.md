# Barra 멀티팩터 리스크 모델 도입기

## 배경

기존 시스템은 베타를 단순 OLS로 계산했다:

```
β = Cov(R_i, R_m) / Var(R_m)
```

1년치 일별 수익률을 동일한 가중치로 넣고, 시장 수익률과의 공분산을 분산으로 나누면 끝이다. 블룸버그가 쓰는 방식이기도 하다. 계산은 간단하지만 근본적인 한계가 있다:

1. **과거 전체를 동일하게 취급한다.** 1년 전의 시장 변동과 어제의 변동이 같은 비중을 갖는다. 시장 구조가 바뀌었을 때 반응이 느리다.
2. **시장 팩터 하나로만 설명한다.** 실제 주가 변동은 섹터, 시가총액, 가치/성장 등 여러 요인의 합이다. OLS 베타는 이 모든 것을 "시장"이라는 하나의 변수에 우겨넣는다.
3. **예측력이 낮다.** 과거 회귀 계수일 뿐, 미래 리스크를 추정하려면 최신 데이터에 더 큰 가중치를 주는 것이 자연스럽다.

업계 표준인 Barra(MSCI) 모델은 이 문제를 **멀티팩터 리스크 모델**로 해결한다. 1970년대 Barr Rosenberg가 설계한 이 프레임워크는 50년이 지난 지금도 기관투자자의 리스크 관리 표준이다.

---

## 1. 멀티팩터 모델이란

### 핵심 아이디어

주식 i의 수익률 R_i를 이렇게 분해한다:

```
R_i = Σ (X_ik × f_k) + ε_i
```

- `X_ik`: 종목 i의 팩터 k에 대한 **노출도(exposure)**. "이 종목이 해당 팩터에 얼마나 민감한가"
- `f_k`: 팩터 k의 **수익률(return)**. "오늘 이 팩터가 시장에서 얼마나 기여했는가"
- `ε_i`: **고유 리스크(specific risk)**. 팩터로 설명되지 않는 종목 고유의 변동

OLS 베타가 "시장 수익률 = 종가 설명 변수"였다면, 멀티팩터 모델은 "시장 + 섹터 + 시가총액 + 가치 + 모멘텀 + ... = 종가 설명 변수들"인 셈이다.

### 이 프로젝트에서 사용하는 팩터

| 카테고리 | 팩터 | 산출 방식 | 데이터 출처 |
|----------|------|-----------|-------------|
| 시장 | Market | 절편 (intercept = 1) | - |
| 스타일 | Size | log(시가총액) | 종가 × 발행주식수 |
| 스타일 | Value | 1/PBR | stock_fundamentals |
| 스타일 | Momentum | 12개월 수익률 − 최근 1개월 수익률 | daily_prices |
| 스타일 | Volatility | 지수가중 표준편차 (half-life=63일) | daily_prices |
| 스타일 | Quality | ROE + 영업이익률 | stock_fundamentals |
| 스타일 | Leverage | 부채비율 | stock_fundamentals |
| 산업 | Industry | 원-핫 인코딩 (종목의 sector) | stocks.sector |

스타일 팩터 6개 + 산업 팩터 N개(섹터 수만큼) + 시장 팩터 1개로 설계 행렬(design matrix)을 구성한다.

---

## 2. 횡단면 회귀: 팩터 수익률 추정

### 매일 하는 일

매일 장 마감 후, 모든 종목을 한 줄로 세워 놓고 **횡단면 회귀(cross-sectional regression)**를 돌린다:

```
오늘의 종목별 수익률 = X (팩터 노출도 행렬) × f (팩터 수익률) + ε
```

이것은 시계열 회귀가 아니다. "오늘" 이라는 하나의 시점에서, 3,000개 종목의 수익률을 동시에 설명하는 팩터 수익률 벡터 f를 구하는 것이다.

### WLS (가중 최소제곱)

단순 OLS 대신 **시가총액 가중 WLS**를 사용한다. 삼성전자의 수익률과 시가총액 100억 소형주의 수익률을 동일하게 취급하면 안 되기 때문이다. 시가총액이 클수록 시장에 미치는 영향이 크므로, 회귀 시 더 큰 가중치를 부여한다.

WLS의 핵심은 가중치를 적용하는 방식이다:

```python
# 나이브한 구현 — 종목 3,000개면 3000×3000 대각행렬 (72MB)
W = np.diag(w)
XtWX = X.T @ W @ X

# 실제 구현 — element-wise 곱으로 O(N²) → O(N×K)
w_norm = w / w.sum()
Xw = X * w_norm[:, None]   # 각 행에 가중치를 곱함
XtWX = Xw.T @ X            # (K×K) 행렬
XtWy = Xw.T @ y            # (K,) 벡터
```

수학적으로 완전히 동일한 결과를 내면서, 메모리는 N² → N×K로 줄어든다. K(팩터 수)가 20~30 수준이므로, 3,000종목 기준 약 100배 절약이다.

### 산업 제약: 라그랑주 승수법

WLS만 돌리면 산업 팩터 수익률의 합이 0이 안 된다. 그러면 시장 팩터가 "전체 시장의 평균 수익률"이 아니라 산업 효과를 일부 흡수해버린다. Barra 모델은 **시가총액 가중 산업 팩터 수익률의 합 = 0** 이라는 제약을 건다:

```
Σ (mcap_j × f_industry_j) = 0
```

이 제약을 구현하는 방법은 두 가지다:

**방법 A**: 가장 큰 산업을 하나 빼고 회귀 → 사후 조정 (실무에서 흔함)
**방법 B**: 라그랑주 승수법으로 정규방정식을 확장 (Barra 원논문 방식)

이 프로젝트는 방법 B를 택했다. 정규방정식에 제약 조건을 한 줄 추가하는 것이다:

```
| XtWX   C^T | | β |   | XtWy |
|            | |   | = |      |
| C      0   | | λ |   | 0    |
```

C는 제약 벡터(산업 위치에 시가총액 비중), λ는 라그랑주 승수다. numpy로는 단순히 행렬을 확장해서 `np.linalg.solve`를 호출하면 된다. 수학적으로 정확한 결과를 보장한다.

---

## 3. 팩터 공분산과 베타

### EWM 공분산 행렬

매일 추정된 팩터 수익률 f가 쌓이면, 이 시계열로 **팩터 공분산 행렬 F**를 만든다. 이때 지수가중이동평균(EWM)을 사용한다:

```python
weights = (1 - α)^(T-1), (1 - α)^(T-2), ..., 1    # 최근일수록 가중치가 큼
F = weighted_covariance(factor_returns, weights)
```

half-life 90일 기준, 90일 전 데이터의 가중치는 오늘의 절반이다. 이렇게 하면 시장 구조 변화에 더 빨리 반응한다. OLS 베타가 1년 전과 오늘을 동일하게 취급하는 것과 대조적이다.

pandas의 `ewm().cov()`를 쓸 수도 있지만, 버전에 따라 MultiIndex 처리가 달라지는 문제가 있어서 **직접 numpy로 구현**했다:

```python
def ewm_factor_covariance(returns: np.ndarray, halflife: int) -> np.ndarray:
    alpha = 1 - np.exp(-np.log(2) / halflife)
    T = len(returns)
    weights = np.array([(1 - alpha) ** (T - 1 - t) for t in range(T)])
    weights /= weights.sum()
    mean = weights @ returns
    centered = returns - mean
    return (centered * weights[:, None]).T @ centered
```

### 팩터 베타

이제 종목 i의 베타를 구하는 공식은:

```
β_i = (X_i^T × F × X_m) / (X_m^T × F × X_m)
```

- `X_i`: 종목 i의 팩터 노출도 벡터 (Size, Value, ..., Industry 원-핫)
- `X_m`: 시장 포트폴리오의 팩터 노출도 (전 종목의 시가총액 가중 평균)
- `F`: 팩터 공분산 행렬

이것이 OLS 베타와의 핵심적인 차이다. OLS 베타는 과거 가격 움직임만 본다. 팩터 베타는 **"이 종목의 현재 특성(섹터, 시가총액, 가치 성향 등)이 시장 전체와 얼마나 비슷한가"** 를 팩터 공분산 구조를 통해 계산한다. 과거 데이터뿐 아니라 현재 펀더멘털 정보까지 반영되는 것이다.

### Warm-up 기간과 Fallback

팩터 공분산이 의미 있으려면 최소 90일의 팩터 수익률 시계열이 필요하다. 시스템 최초 가동 시에는 이 데이터가 없으므로, **90일 미만이면 기존 OLS 베타를 fallback으로 사용**한다. 90일이 쌓이면 자동으로 팩터 베타로 전환된다.

```python
# factor_model_service.py → get_betas()
# 90일 이상: factor_beta 계산 → {stock_id: beta} 반환
# 90일 미만: 빈 dict 반환 → indicator_service가 ols_beta로 fallback
```

---

## 4. 팩터 노출도 표준화

### 왜 표준화가 필요한가

Size(로그 시가총액)는 25~35 범위이고, Leverage(부채비율)는 0~5 범위다. 이대로 회귀하면 Scale이 큰 팩터가 결과를 지배한다. 모든 스타일 팩터를 동일한 척도로 맞추기 위해 **Z-score 표준화**를 한다:

```
z_i = (x_i - μ) / σ
```

표준화 후 모든 팩터의 평균은 0, 표준편차는 1이 된다.

### Winsorization (이상치 처리)

단순 Z-score는 이상치에 취약하다. PBR이 -500인 종목 하나가 전체 분포를 왜곡한다. Barra에서는 **MAD(Median Absolute Deviation)** 기반 Winsorization을 사용한다:

```python
def winsorize(series, n_mad=3):
    median = series.median()
    mad = (series - median).abs().median()
    lower = median - n_mad * 1.4826 * mad
    upper = median + n_mad * 1.4826 * mad
    return series.clip(lower, upper)
```

1.4826은 MAD를 표준편차와 동등하게 만들어주는 상수(정규분포 가정)다. 중앙값 ± 3MAD 밖의 값을 경계로 잘라낸다. 평균과 표준편차 기반보다 이상치에 훨씬 강건하다.

---

## 5. 섹터 집계 지표

팩터 모델과 함께, 섹터별 펀더멘털 집계 지표를 매일 계산한다:

| 지표 | 집계 방식 |
|------|-----------|
| PER | 중위수 (PERCENTILE_CONT) |
| PBR | 중위수 |
| ROE | 중위수 |
| 영업이익률 | 중위수 |
| 부채비율 | 중위수 |

평균 대신 **중위수**를 사용한다. PER은 적자 기업에서 음수가 되고, 저수익 기업에서 수백 배까지 올라가는데, 평균은 이런 이상치에 크게 흔들린다. 중위수는 분포의 중심을 안정적으로 나타낸다.

SQL의 `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ...)` 함수를 사용해서 DB 레벨에서 직접 계산한다. 한국(~3,300종목)과 미국(~7,500종목)의 전 섹터 중위수를 단일 쿼리로 산출한다.

---

## 6. 데이터 무결성 체크

### 문제

KIS 마스터 파일에는 보통주가 아닌 종목(SPAC, Warrant, Unit 등)이 섞여 있다. 이들은 재무제표도 없고 섹터 분류도 안 되는데, 퀀트 분석에 포함되면 UX를 해친다. 또한 일봉이 없거나 재무제표가 미수집된 종목이 팩터 모델에 들어가면 Singular matrix 등의 수학적 오류를 유발한다.

### 해결: `is_active`를 주 게이트로 활용 (점진적 비활성화)

파이프라인이 데이터 수집 후, compute 단계 진입 전에 **점진적으로** 분석 불가 종목을 `is_active=false`로 비활성화한다:

```
[Reactivate]     마스터 파일에 있는 종목 재활성화     ─┐
[Deactivate]     no-price → no-sector → no-FS       ├ 단일 트랜잭션
[Safety Check]   active < 10% → rollback + 중단      ─┘
```

| 단계 | 대상 | 사유 |
|---|---|---|
| `reactivate_listed_stocks` | 마스터 파일에 존재하는 비활성 종목 | 전일 비활성화된 종목이 오늘 데이터가 채워졌을 수 있음 |
| `deactivate_no_price_stocks` | `daily_prices`에 레코드 없는 종목 | 거래정지/신규상장 직후 등 |
| `deactivate_no_sector_stocks` | `sector`가 NULL이거나 'N/A' | SPAC, 워런트, 셸컴퍼니 등 비보통주 |
| `deactivate_no_fs_stocks` | `financial_statements`에 레코드 없는 종목 | FS 미수집 시 펀더멘털/팩터 모델 계산 불가 |

이 전체 흐름은 **단일 트랜잭션** 안에서 실행된다. safety check가 실패하면(active 비율 10% 미만) 트랜잭션을 롤백하여 DB에 비정상 상태를 남기지 않는다. 이는 초기 파이프라인 실행 없이 일일 파이프라인만 돌린 경우를 조기 감지한다.

**`upsert_batch`와의 관계**: `upsert_batch()`는 기존 종목의 `is_active` 상태를 변경하지 않는다(INSERT 시에만 기본값 `true`). 따라서 전일 비활성화 상태가 다음 날 자동으로 되살아나지 않으며, `reactivate_listed_stocks`가 명시적으로 재활성화를 담당한다.

**방어적 중복 검증**: `get_eligible_for_factors()`는 `is_active=true` 외에 `sector IS NOT NULL AND sector != 'N/A'` 조건을 추가로 유지한다. 비활성화 단계가 에러로 스킵되거나 compute가 단독 실행되는 경우를 방어한다.

### Finnhub Fallback

NASDAQ Screener가 섹터를 반환하지 않는 종목에 대해 Finnhub API를 2차 소스로 사용한다:

```
1. NASDAQ Screener 벌크 조회 (NYSE + NASDAQ 전체)
2. sector가 여전히 NULL인 종목 → Finnhub /stock/profile2 개별 조회
3. Finnhub 응답이 'N/A' → sector = 'N/A' (비보통주 마킹)
4. Finnhub 응답이 유효한 산업명 → sector = 해당 산업명
5. Finnhub도 실패 → sector = NULL (미분류로 남음)
```

### IntegrityCheckEngine

파이프라인 마지막에 읽기 전용 무결성 보고서를 출력한다. DB를 수정하지 않고, 현황만 로그에 남긴다:

```
[IntegrityCheck] US_NYSE: active=2847, quant_eligible=2650, sector_null=12, sector_na=185, no_fs=45, no_price=3
```

제외 비율이 20%를 넘으면 경고를 띄운다. 운영 중 데이터 소스 장애를 조기에 감지하기 위함이다.

---

## 7. 파이프라인 실행 순서

### CLI 명령어

| 명령어 | 용도 | 주기 |
|---|---|---|
| `kr-initial` / `us-initial` | 초기실행 (전체 수집 + FS 수집 + compute) | 최초 1회 |
| `kr` / `us` | 일일실행 (가격/섹터/벤치마크/RFR 수집 + compute) | 매일 |
| `kr-fs` / `us-fs` | 재무제표 수집 + 펀더멘털 재계산 | 분기별 |

### 초기실행 흐름 (kr-initial / us-initial)

```
[Collect]                 종목 목록, 가격, 벤치마크, 무위험금리, 섹터
    ↓
[Collect FS]             재무제표 (DART / Railway)
    ↓
[Reactivate]             마스터 파일에 있는 종목 재활성화     ─┐
[Deactivate]             no-price → no-sector → no-FS       ├ 단일 트랜잭션
[Safety Check]           active < 10% → rollback + 중단      ─┘
    ↓
[Compute Fundamentals]   PER, PBR, ROE 등 → 팩터 노출도의 입력
    ↓
[Compute Factors]        횡단면 회귀 → 팩터 수익률 → 공분산 → 팩터 베타
    ↓
[Compute Indicators]     베타(팩터 or OLS), 알파, 샤프 등 23개 지표
    ↓
[Sector Aggregates]      섹터별 중위수 PER, PBR 등
    ↓
[Integrity Check]        데이터 품질 보고 (읽기 전용)
```

### 일일실행 흐름 (kr / us)

초기실행과 동일하되 **[Collect FS] 단계가 없다.** DB에 이미 있는 재무제표 기준으로 no-FS 비활성화를 수행한다.

핵심은 **Fundamentals → Factors → Indicators** 순서다. Indicators가 팩터 베타를 쓰려면 Factors가 먼저 계산되어야 하고, Factors가 Size(시가총액), Value(PBR) 등을 쓰려면 Fundamentals가 먼저 계산되어야 한다.

---

## 8. 아키텍처 계층 분리

### 3계층 원칙

| 계층 | 역할 | 예시 |
|------|------|------|
| `pipeline/` | 오케스트레이션 (데이터 로드 → 서비스 호출 → 저장) | `factor_compute.py` → `FactorModelService.run()` 호출 |
| `services/` | 비즈니스 로직 (계산, 판단, 조합) | `FactorModelService` → 노출도, 회귀, 공분산 조율 |
| `db/repositories/` | 데이터 접근 (SQL 쿼리) | `FactorRepository.upsert_exposures()` |

**pipeline에 SQL이 있으면 안 되고, service에 raw SQL이 있으면 안 된다.** pipeline은 "무엇을 어떤 순서로 실행할지"만 알고, service는 "어떻게 계산할지"만 알고, repository는 "어떻게 저장/조회할지"만 안다.

### 베타 워크플로우 (정리)

```
FactorComputeEngine         →  FactorModelService.run()
  (pipeline, 오케스트레이션)       (service, 계산)
                                    ├─ exposure 계산 + 저장
                                    ├─ WLS 횡단면 회귀 → 팩터 수익률 저장
                                    └─ EWM 공분산 → 저장

IndicatorComputeEngine      →  FactorModelService.get_betas(market)
  (pipeline, 오케스트레이션)       (service, 조회 + 계산)
                                    ├─ 공분산 + 노출도 로드
                                    ├─ 종목별 factor_beta() 계산
                                    └─ {stock_id: beta} dict 반환
                            →  IndicatorService.compute(stock_id, df, bench, rf, factor_beta)
                                    ├─ factor_beta가 있으면 → 팩터 베타 사용
                                    ├─ factor_beta가 없으면 → ols_beta() fallback
                                    └─ alpha, sharpe 등은 선택된 beta로 계산
```

모든 베타 관련 함수는 `quant/factor_model/beta.py`에 모여 있다:
- `factor_beta()`: Barra 방식 (주력)
- `ols_beta()`: OLS 방식 (fallback)
- `build_exposure_vector()`: 노출도 벡터 조립 (공용)

---

## 정리: 멀티팩터 리스크 모델의 핵심 원칙

1. **팩터로 분해하라.** 주가 변동을 "시장" 하나로 설명하지 말고, 섹터/사이즈/가치/모멘텀 등 여러 요인으로 쪼개라. 설명력이 높아지고, 각 요인의 기여도를 따로 측정할 수 있다.

2. **최신 데이터에 더 큰 가중치를 줘라.** 시장은 변한다. EWM으로 최근 데이터를 더 중시하면, 구조 변화에 빠르게 적응하는 리스크 추정치를 얻는다.

3. **이상치에 강건하게 만들어라.** MAD 기반 Winsorization은 극단적 관측치 하나가 전체 모델을 흔드는 것을 방지한다.

4. **제약 조건을 명시하라.** 산업 팩터 수익률의 시가총액 가중합 = 0 제약이 없으면, 시장 팩터와 산업 팩터가 서로 정보를 훔친다. 수학적으로 정확한 제약(라그랑주)을 걸어야 각 팩터의 해석이 깨끗해진다.

5. **Fallback을 항상 준비하라.** 90일치 데이터가 없으면 팩터 베타를 쓸 수 없다. OLS 베타라는 단순한 fallback이 있어야 시스템이 첫날부터 동작한다.
