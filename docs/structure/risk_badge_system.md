# 리스크 뱃지 시스템 — 설계 문서

## 1. 목적 및 대상 사용자

리스크 뱃지 시스템은 개별 종목에 대한 **5차원 리스크 평가**를 제공합니다.
주식 시장에 처음 입문하는 20~30대 사회 초년생이 주요 대상이며, 위험을 하나의 숫자로 축소하지 않고
"성적표" 형태로 여러 측면의 리스크를 이해하기 쉽게 전달합니다.

**핵심 설계 원칙:**
- 요약 뱃지 하나 (STABLE / CAUTION / WARNING) 로 빠르게 훑기
- 5개 세부 차원 점수로 상세 파악
- 매수/매도 추천이 아닌, 순수한 "현재 상태 보고서"
- 백엔드는 구조화된 데이터(ENUM, 점수, 원본 수치)만 반환 → 메시지 렌더링은 프론트엔드 i18n에 위임

---

## 2. 업계 벤치마크 참고

### Barra (MSCI) 리스크 모델
- **적용 대상**: 변동성 차원 (`factor_exposures`의 `volatility_z`)
- **방법**: 실현 변동성의 횡단면 z-score 정규화. |z| > 2인 종목은 고변동성 이상치로 분류.
- **근거**: MSCI Barra 리스크 모델은 팩터 노출도 z-score로 투자 유니버스 대비 상대 위험을 정량화함.

### MSCI 섹터 상대 비교
- **적용 대상**: 회사 체력(company_health) 및 밸류에이션(valuation) 차원
- **방법**: 각 종목의 펀더멘털 비율(PER, PBR, ROE, 부채비율, 영업이익률)을 동일 섹터 중앙값과 비교. 섹터 규범에서 크게 벗어나면 점수 상승.
- **폴백**: 섹터 내 종목 수 < 5 또는 중앙값이 NULL이면 마켓 전체 중앙값으로 대체.

### Morningstar 불확실성 등급 (Uncertainty Rating)
- **적용 대상**: 전체 프레임워크 설계
- **개념**: 리스크를 "좋다/나쁘다"가 아닌 "불확실성"으로 프레이밍. WARNING 뱃지는 "이 주식을 피하라"가 아니라 "불확실성이 높으니 신중히 평가하라"는 의미.
- **다차원 분해**: 단일 불확실성 점수 대신 5개 해석 가능한 차원으로 분해. Morningstar가 별점과 함께 복수의 리스크 지표를 제공하는 방식과 유사.

### CAPM 베타
- **적용 대상**: 변동성 차원
- **방법**: 베타는 시장 민감도를 측정. |beta| > 2.0이면 시장 움직임을 크게 증폭. 계산 아티팩트 방지를 위해 [-5, 5]로 클램핑.
- **근거**: 표준 CAPM 프레임워크 — beta = Cov(종목, 벤치마크) / Var(벤치마크).

---

## 3. 5개 차원 — 상세 설계

### 차원 1: 가격 과열도 (`price_heat`)

**입력 지표**: RSI-14 (60%), 볼린저밴드 %B (40%)

**RSI 점수 산정** (구간 선형):

| RSI 범위 | 점수 범위 | 해석 |
|----------|----------|------|
| 0–30 | 70–100 | 과매도 영역 |
| 30–50 | 0–30 | 정상 하단 |
| 50–70 | 0–30 | 정상 상단 |
| 70–100 | 30–100 | 과매수 영역 |

**BB %B 점수 산정**: 유사한 구간 선형 커브. %B < 0 또는 > 1이면 밴드 돌파.

**BB 평탄화 방어**: `bb_upper == bb_lower`인 경우(미국 데이터 67종목) BB 점수를 건너뛰고 RSI가 100% 가중치를 가짐. 거래량이 극히 적은 종목에서 발생.

**방향성**: OVERHEATED (RSI ≥ 70), OVERSOLD (RSI ≤ 30), NEUTRAL (30–70)

**가중치 근거**: RSI는 독립 오실레이터로서 더 안정적. BB %B는 최근 변동성 밴드 대비 가격 극단의 보조 확인 역할.

### 차원 2: 변동성 (`volatility`)

**입력 지표**: 베타 (50%), 팩터 모델 volatility_z (50%)

**베타 점수 산정** (구간 선형):

| |베타| 범위 | 점수 범위 | 해석 |
|-------------|----------|------|
| 0–0.8 | 0–20 | 낮은 시장 민감도 |
| 0.8–1.2 | 20–40 | 시장 추종 |
| 1.2–2.0 | 40–70 | 움직임 증폭 |
| 2.0–5.0 | 70–100 | 극단적 민감도 |

**베타 클램핑**: `clamp_beta()` — NaN/Inf → None 반환, [-5, 5]로 제한. BENFW 1종목 NaN, 33종목 극단 이상치(예: -64.68, 67.19) 확인됨.

**volatility_z 점수 산정**: |z| ≤ 1.0 → 0–30, |z| ≤ 2.0 → 30–60, |z| > 2.0 → 60–100.

**폴백**: 한쪽 지표가 없으면 다른 쪽이 100% 가중치. 양쪽 모두 없으면 `data_available=False`.

**가중치 근거**: 베타는 시장 대비 상대 민감도(지수 움직임 증폭 정도), volatility_z는 횡단면 맥락의 절대 변동성(Barra 방식). 상호 보완적 관점 제공.

### 차원 3: 추세 강도 (`trend`)

**입력 지표**: ADX-14, +DI, -DI

**비대칭 가중치**:
- 상승 추세 (plus_di > minus_di): 기본 점수 × 0.6
- 하락 추세 (minus_di > plus_di): 기본 점수 × 1.0

**근거**: 강한 상승 추세(높은 ADX + 상승 방향)는 강한 하락 추세보다 덜 위험함. 0.6 배율로 상승 추세에 대한 과잉 경고를 방지하면서, 하락 추세는 전체 가중치로 강한 매도세를 제대로 포착.

**ADX 점수 산정** (구간 선형):

| ADX 범위 | 기본 점수 | 해석 |
|----------|----------|------|
| 0–20 | 0–20 | 추세 없음 (횡보) |
| 20–40 | 20–50 | 보통 추세 |
| 40–60 | 50–75 | 강한 추세 |
| 60+ | 75–100 | 극단적 추세 |

**방향성**: UPTREND, DOWNTREND, NEUTRAL (DI 비교 기반)

### 차원 4: 회사 체력 (`company_health`)

**입력 지표**: 부채비율 (40%), ROE (30%), 영업이익률 (30%)

**섹터 상대 비교**: 각 지표를 `sector_or_market_fallback()`으로 섹터 중앙값과 비교:
1. 섹터 내 종목 ≥ 5개 + 중앙값 non-null → 섹터 중앙값 사용
2. 미충족 시 → 마켓 전체 중앙값으로 폴백
3. 마켓 전체 중앙값도 null/음수 → 절대 기준 사용

**음수 중앙값 방어** (`safe_ratio()`): 섹터 중앙값이 ≤ 0인 경우(예: NASDAQ 헬스케어 ROE) 비율 비교가 무의미. 절대 기준으로 전환:
- ROE: ≥ 15% → 10점, ≥ 5% → 30점, ≥ 0% → 55점, < 0% → 80점
- 영업이익률, 부채비율에도 동일 패턴 적용

**방향성**: 없음 (펀더멘털에 방향 개념 불필요)

### 차원 5: 밸류에이션 (`valuation`)

**입력 지표**: PER (50%), PBR (50%)

**음수 PER 처리**:
- PER < 0 + 영업이익률 > 0 (영업은 흑자, 회계상 적자): 점수 = 50 (CAUTION — 일회성 비용 가능성)
- PER < 0 + 영업이익률 ≤ 0 (구조적 적자): 점수 = 70 (WARNING 근접)

**섹터 상대 점수 산정**: company_health와 동일한 `sector_or_market_fallback()` 사용.

**PER/PBR 중앙값 대비 비율 점수**:

| 중앙값 대비 비율 | 점수 | 해석 |
|----------------|------|------|
| ≤ 0.5 | 0 | 큰 폭 할인 |
| 0.5–1.0 | 0–25 | 섹터 평균 이하 |
| 1.0–1.5 | 25–50 | 약간 평균 이상 |
| 1.5–2.5 | 50–75 | 고평가 |
| > 2.5 | 75–100 | 매우 고평가 |

---

## 4. 종합 뱃지 로직

### Critical vs. Signal 차원 구분

| 유형 | 차원 | 동작 |
|------|------|------|
| **Critical** | company_health, valuation | WARNING이 항상 요약에 전파 |
| **Signal** | price_heat, volatility, trend | 단일 WARNING은 복합적이지 않으면 완화 |

### 규칙 (우선순위 순):
1. **Critical WARNING이 하나라도 있으면** → 요약 = WARNING
2. **Signal WARNING 2개 이상** → 요약 = WARNING
3. **Signal WARNING 1개 + 다른 CAUTION 이상 차원 존재** → 요약 = WARNING (복합 위험)
4. **Signal WARNING 1개만 단독** → 요약 = CAUTION (완화)
5. **상승 추세 단독 WARNING** → 요약 = CAUTION (강한 상승 모멘텀은 그 자체로 위험하지 않음)
6. **그 외** → 유효 차원 중 최악 등급

### 설계 근거:
- **Critical 차원**(펀더멘털)은 지속적인 구조적 위험. 재무가 악화되거나 극단적 고평가인 회사는 실질적으로 우려할 만함.
- **Signal 차원**(기술적)은 일시적 시장 상태. 단일 기술적 극단(예: 높은 변동성만)은 일시적일 수 있음. 그러나 다른 우려 신호와 결합되면 복합 위험으로 WARNING을 부여.
- **상승 추세 완화**는 강한 상승 모멘텀에 대한 과잉 경고를 방지 — 강한 상승세는 단독으로는 본질적으로 위험하지 않음.

---

## 5. 방어 로직 및 엣지 케이스

| 문제 | 발생 건수 | 방어 방법 |
|------|----------|----------|
| 베타 NaN/Infinity | 1건 (BENFW) | `clamp_beta()` → None 반환, 베타 점수 스킵 |
| 베타 이상치 (\|beta\| > 5) | 33건 | [-5, 5]로 클램핑 |
| BB 평탄화 (upper == lower) | 67건 | BB 점수 스킵, RSI 100% 가중치 |
| 소규모 섹터 (< 5종목) | 미국 12개 섹터 | 마켓 전체 중앙값으로 폴백 |
| 섹터 중앙값 NULL | 미국 3개 섹터 | 마켓 전체 중앙값으로 폴백 |
| 음수 섹터 중앙값 | NASDAQ HC 등 | `safe_ratio()` → 절대 기준으로 전환 |
| RSI 극단값 (0 또는 100) | 16건 | 점수 100 — 유효한 극단 신호 |
| ADX > 80 | 69건 | 점수 상한 100 — 유효한 극단 |
| 전체 펀더멘털 NULL | 변동적 | `data_available=False`, `unavailable_dimensions`에 나열 |
| 음수 PER + 영업 흑자 | 변동적 | 점수 = 50 (일회성 비용 가능성) |
| sector_aggregates 미존재 | 초기 배포 시 | 전 종목 market_agg 폴백; 펀더멘털도 비면 `data_available=False` |
| 신규 상장 (재무제표 없음) | 변동적 | 기술적 3개 차원만 계산 |
| 거래일 부족 (< 60일) | 변동적 | 전체 차원 불가 → CAUTION |
| 파이프라인 진행 중 | - | API는 캐시 테이블에서 읽기; 파이프라인 완료 후 갱신 |
| KR/US 시간대 차이 | - | 마켓별 독립 캐시; `data_as_of` 필드로 프론트에 안내 |

---

## 6. 표시 점수 반전 (Display Score Inversion)

### 내부 점수 vs. 표시 점수

| 구분 | 범위 | 방향 | 사용 위치 |
|------|------|------|----------|
| **내부 점수** | 0–100 | 높을수록 위험 | calc-server 계산, DB 저장, LLM 프롬프트 |
| **표시 점수** | 0–100 | 높을수록 안정 | Gateway API 응답 → 프론트엔드 |

### 변환 공식

```
표시 점수 = round(100 - 내부 점수, 1)
```

### 변환 위치

- **Gateway** `StockService.invertDimensionScores()` — `StockDetailResponse`를 만들 때 적용
- Calc 서버, DB, LLM 서비스는 기존 내부 점수 체계를 그대로 유지

### 변환 예시

| 차원 | 내부 점수 | 표시 점수 | 등급 |
|------|-----------|-----------|------|
| 가격 과열도 | 20.7 | **79.3** | 안정 |
| 기업 건전성 | 54.9 | **45.1** | 주의 |
| 밸류에이션 | 75.0 | **25.0** | 경고 |

### 근거

사용자 대상(20~30대 사회 초년생)에게 "시험 점수"처럼 높을수록 좋다는 직관이 자연스러움.
내부 계산 로직 변경 없이 Gateway BFF 계층에서 표현만 변환하므로 기존 파이프라인에 영향 없음.

---

## 7. 캐싱 전략

- **배치 계산**: `RiskBadgeService.compute_batch(market)` — 파이프라인 오케스트레이터의 마지막 단계로 실행
- **저장**: `risk_badges` 테이블 — `stock_id` (PK), `market`, `date`, `summary_tier`, `dimensions` (JSONB)
- **인덱스**: `(market, summary_tier)` 복합 인덱스 (스크리너 쿼리용), `(date DESC)` (최신성 확인용)
- **Upsert 패턴**: `INSERT ... ON CONFLICT (stock_id) DO UPDATE` — 파이프라인 실행 시 전체 교체
- **API 읽기**: Gateway가 JPA로 `risk_badges` 테이블 직접 조회 (Calc 서버 경유 불필요)
- **`data_as_of`**: 응답에 포함하여, KR 데이터는 최신이지만 US가 아직 갱신 전일 때 프론트에서 안내 가능

---

## 8. 프론트엔드 툴팁 투명성

각 차원의 툴팁(RiskScoreInfoPopover)에는 다음 정보가 포함됩니다:
- **설명**: 차원이 무엇을 측정하는지 (반전된 점수 기준 안내)
- **측정 기준 + 가중치**: 각 입력 지표와 비율 (예: RSI 60%, BB %B 40%)
- **점수 스케일**: "100점 = 안정 / 0점 = 위험"
- **참조 방법론**: 사용된 학술/업계 모델 (Barra, CAPM, MSCI 등)

---

## 9. 아키텍처 요약

### Calc 서버 (Flask/Python) — 연산 전용
- 파이프라인: 데이터 수집 → 지표/펀더멘털/팩터 계산 → 리스크 뱃지 배치 계산 → DB 저장
- API: `/internal/stocks/<symbol>/simulation` (몬테카를로 시뮬레이션만, x-api-key 인증)

### Gateway 서버 (Spring Boot/Kotlin) — BFF
- DB 직접 조회: 리스크 뱃지, 종목 정보, 가격 등 모든 읽기 데이터는 JPA로 직접 접근
- Calc 호출: 몬테카를로 시뮬레이션 같은 Python 연산이 필요한 경우에만

### 프론트엔드 — i18n 메시지 렌더링
- 백엔드가 반환하는 구조화 데이터(tier, direction, components)를 기반으로 메시지 조립
- 아래 템플릿 명세 참고

---

## 10. 프론트엔드 i18n 메시지 템플릿

백엔드는 `(dimension, tier, direction, components)`만 반환합니다.
프론트엔드가 자체 i18n 프레임워크로 사용자 언어에 맞는 메시지를 렌더링합니다.

### price_heat

| 등급 | 방향 | 한국어 | English | 보간 변수 |
|------|------|--------|---------|----------|
| STABLE | NEUTRAL | 현재 과열이나 과매도 신호가 없어요 | No overbought or oversold signals detected | `rsi`, `bb_pct_b` |
| CAUTION | OVERHEATED | RSI가 {rsi:.0f}로, 과열 구간에 접근 중이에요 | RSI at {rsi:.0f}, approaching overbought territory | `rsi`, `bb_pct_b` |
| CAUTION | OVERSOLD | RSI가 {rsi:.0f}로, 과매도 구간에 접근 중이에요 | RSI at {rsi:.0f}, approaching oversold territory | `rsi`, `bb_pct_b` |
| WARNING | OVERHEATED | RSI {rsi:.0f}, 볼린저밴드 상단 돌파 — 과열 상태예요 | RSI {rsi:.0f}, broke above Bollinger upper band — overbought | `rsi`, `bb_pct_b` |
| WARNING | OVERSOLD | RSI {rsi:.0f}, 과매도 상태예요. 반등 가능성도 있지만 주의하세요 | RSI {rsi:.0f}, oversold. Possible bounce, but be cautious | `rsi`, `bb_pct_b` |

### volatility

| 등급 | 방향 | 한국어 | English | 보간 변수 |
|------|------|--------|---------|----------|
| STABLE | - | 변동성이 안정적이에요 | Volatility is stable | `beta`, `volatility_z` |
| CAUTION | - | 변동성이 다소 높아요 (베타 {beta:.2f}) | Somewhat elevated volatility (beta {beta:.2f}) | `beta`, `volatility_z` |
| WARNING | - | 변동성이 매우 높아요 (베타 {beta:.2f}). 시장 대비 큰 출렁임이 예상돼요 | Very high volatility (beta {beta:.2f}). Expect large swings vs. market | `beta`, `volatility_z` |

### trend

| 등급 | 방향 | 한국어 | English | 보간 변수 |
|------|------|--------|---------|----------|
| STABLE | NEUTRAL | 뚜렷한 추세 없이 횡보 중이에요 | No clear trend — trading sideways | `adx`, `plus_di`, `minus_di` |
| CAUTION | UPTREND | 상승 추세가 진행 중이에요 | Uptrend in progress | `adx`, `plus_di`, `minus_di` |
| CAUTION | DOWNTREND | 하락 추세가 감지되고 있어요 | Downtrend detected | `adx`, `plus_di`, `minus_di` |
| WARNING | UPTREND | 상승 추세가 매우 강해요. 급격한 방향 전환에 대비하세요 | Very strong uptrend. Prepare for potential reversal | `adx`, `plus_di`, `minus_di` |
| WARNING | DOWNTREND | 매우 강한 하락 추세예요. 신중한 판단이 필요해요 | Very strong downtrend. Careful evaluation needed | `adx`, `plus_di`, `minus_di` |

### company_health

| 등급 | 방향 | 한국어 | English | 보간 변수 |
|------|------|--------|---------|----------|
| STABLE | - | 재무 상태가 양호해요 | Financial condition is healthy | `debt_ratio`, `roe`, `operating_margin` |
| CAUTION | - | 일부 재무 지표가 업종 평균 대비 약해요 | Some financial metrics are below sector average | `debt_ratio`, `roe`, `operating_margin` |
| WARNING | - | 재무 건전성에 주의가 필요해요 | Financial health requires attention | `debt_ratio`, `roe`, `operating_margin` |

### valuation

| 등급 | 방향 | 한국어 | English | 보간 변수 |
|------|------|--------|---------|----------|
| STABLE | - | 업종 대비 적정 가격대에 있어요 | Priced reasonably vs. sector | `per`, `pbr` |
| CAUTION | - | 업종 평균보다 다소 비싸게 평가되고 있어요 | Slightly expensive vs. sector average | `per`, `pbr` |
| WARNING | - | 업종 대비 고평가 상태예요 (PER {per:.1f}) | Overvalued vs. sector (PER {per:.1f}) | `per`, `pbr` |

### composite (요약 뱃지)

| 등급 | 한국어 | English |
|------|--------|---------|
| STABLE | 전반적으로 안정적인 상태예요 | Overall condition is stable |
| CAUTION | 일부 지표에서 주의 신호가 감지됐어요 | Some indicators show caution signals |
| WARNING | 복수 지표에서 경고 신호가 감지됐어요. 신중하게 판단하세요 | Multiple warning signals detected. Evaluate carefully |
