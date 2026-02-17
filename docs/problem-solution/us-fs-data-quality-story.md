# US 재무제표 데이터 품질 개선 (2026-02-17)

## 배경

US 파이프라인 초회 실행 결과, PER 채움률이 **29.2%**에 불과했다. 한국은 89.9%인데 비해 비정상적으로 낮은 수치로, 퀀트 시스템이 정상 가동될 수 있는 상태가 아니었다. DB 직접 조회와 코드 분석을 통해 4가지 근본 원인을 특정하고, 업계 표준(EdgarTools, Wall Street Prep, CFI)을 조사하여 해법을 도출했다.

| 지표 | US | KR |
|---|---|---|
| PER 채움률 | 29.2% | 89.9% |
| ROE 채움률 | 34.3% | 89.5% |
| revenue NULL률 | 24.8% | 3.0% |
| net_income NULL률 | 22.7% | 0.9% |

---

## 원인 1: TTM 로직이 US Q4 부재를 처리 못 함 (영향 ~2,400종목)

### 문제

미국은 분기보고서(10-Q)가 Q1~Q3까지만 제출되고, Q4는 연간보고서(10-K)에 포함된다. 별도의 Q4 10-Q가 없다. 기존 TTM 로직은 분기 4개를 찾아 합산하는 방식이었으므로, Q4가 없으면 무조건 실패했다.

```python
# 기존: 분기 4개 합산 — Q4가 없으면 여기서 대량 탈락
quarters = [s for s in statements if s.report_type != ReportType.FY][:4]
if len(quarters) < 4:
    return None
```

2026년 2월 시점에서 78%의 종목(3,412개)이 Q3:2025가 최신이다. 이 종목들의 상위 5건은 `Q3:2025, Q2:2025, Q1:2025, FY:2024, Q3:2024`인데, FY를 필터링하면 `[Q3:2025, Q2:2025, Q1:2025, Q3:2024]`로 4개가 만들어지긴 하지만, Q3가 이중계상되는 오류가 발생한다.

### 해결

업계 표준 공식을 적용했다:

```
TTM = FY(직전) + sum(금년 분기) − sum(전년 동일분기)
    = FY:2024 + (Q1+Q2+Q3):2025 − (Q1+Q2+Q3):2024
```

FY 기준 상대 위치로 분기를 분류한다. statements가 `fiscal_year DESC, report_type DESC` 순서로 정렬되어 오므로, FY보다 앞(최신)에 있는 분기는 current, FY와 같은 연도의 분기는 prior로 분류된다. 이 방식은 비달력 회계연도(Apple 9월결산, Microsoft 6월결산 등)도 자연스럽게 처리한다.

```python
# fundamental_service.py — _ttm_income 핵심 로직
fy_stmt = None
current_qs, prior_qs = {}, {}

for s in statements:
    if s.report_type == ReportType.FY and fy_stmt is None:
        fy_stmt = s
    elif s.report_type != ReportType.FY:
        if fy_stmt is None:
            current_qs.setdefault(s.report_type, s)
        elif s.fiscal_year == fy_stmt.fiscal_year:
            prior_qs.setdefault(s.report_type, s)
```

연속 분기만 카운트한다 — Q1이 없는데 Q2만 있으면 TTM이 12개월이 되지 않으므로 break한다. 각 필드(revenue, operating_income, net_income)는 독립적으로 계산되어, 하나의 필드가 null이어도 나머지는 살릴 수 있다.

### 추가 변경

`financial_statement.py`의 두 조회 메서드(`get_ttm_by_stock`, `get_ttm_by_market`)에서 LIMIT/slice를 5 → 10으로 확장했다. 새 TTM 공식은 FY 1건 + 금년Q 3건 + 전년Q 3건 = 최소 7건이 필요하기 때문이다.

---

## 원인 2: XBRL 개념 매핑 부족 (영향 ~1,200종목)

### 문제

SEC EDGAR의 XBRL 데이터는 기업마다 같은 재무 항목을 서로 다른 태그로 보고한다. Microsoft만 해도 총 매출에 4개의 서로 다른 GAAP 태그를 사용했다. 기존 매핑은 revenue 4개, net_income 1개, total_equity 2개, shares 2개로 매우 제한적이었다.

EdgarTools(SEC EDGAR 파싱의 사실상 표준 오픈소스)를 조사한 결과, 2,067개 XBRL 태그를 95개 표준 개념으로 매핑하고 있었고, 그중 revenue만 139개 태그가 매핑되어 있었다.

### 해결

EdgarTools의 `gaap_mappings.json`을 참조하여 매핑을 확장했다:

| 필드 | 기존 | 변경 후 | 추가 태그 예시 |
|---|---|---|---|
| revenue | 4개 | 13개 | `SalesRevenueGoodsNet`, `InterestAndDividendIncomeOperating`(은행) |
| net_income | 1개 | 4개 | `ProfitLoss`, `IncomeLossAttributableToParent` |
| total_equity | 2개 | 3개 | `MembersEquity`(합자회사) |
| shares | 2개 | 4개 | `SharesOutstanding`, `WeightedAverageNumberOfSharesOutstandingBasic` |

`operating_income`은 `OperatingIncomeLoss` 1개만 유지했다 — EdgarTools도 1개만 매핑하고 있으며, 은행/보험/REIT는 이 태그를 구조적으로 사용하지 않는다. 대신 TTM에서 operating_income을 필수 조건에서 제외하는 것으로 대응했다(원인 1 해결과 연계).

---

## 원인 3: Q1/Q2 net_income의 이상 저조한 채움률 (영향 ~34% 분기 데이터)

### 문제

DB 조회 결과, 동일 종목에서 Q3의 net_income 채움률은 93.9%인데 Q1/Q2는 60.5%에 불과했다. revenue/operating_income은 ~76%로 일관적이었으므로, net_income 특유의 문제가 있었다.

원인은 SEC `companyfacts.zip`의 `frame` 필드 누락이다. SEC가 사후 부여하는 이 메타데이터가 일부 entry에 빠져있는데, 기존 파서는 income 필드에서 frame이 없으면 무조건 reject했다:

```typescript
// 기존: frame이 없으면 무조건 스킵
if (IS_FIELDS.has(field)) {
  if (!SINGLE_Q_RE.test(frame)) return;  // frame='' → 스킵
}
// 반면 balance sheet는 frame 없어도 통과
if (BS_FIELDS.has(field)) {
  if (frame && !INSTANT_RE.test(frame)) return;  // frame='' → OK
}
```

### 해결

income 필드도 balance sheet와 동일하게 frame이 없으면 통과시키되, frame이 있는 entry를 우선하도록 변경했다:

```typescript
if (IS_FIELDS.has(field)) {
  if (frame && !SINGLE_Q_RE.test(frame)) return;  // frame 있으면 검증, 없으면 통과
}
// ...
if (acc[field] === undefined || (frame && SINGLE_Q_RE.test(frame))) {
  acc[field] = val;  // frame-tagged entry가 frameless entry를 덮어씀
}
```

주의점: frame 없는 entry가 YTD(누적) 값일 수 있다. 하지만 SEC companyfacts의 각 entry는 `fp` 필드로 분기를 명시하므로, `fp=Q2`이고 `form=10-Q`인 entry는 해당 분기의 값으로 간주할 수 있다.

---

## 원인 4: 기타 데이터 오염 + 비효율적 concept 선택

### 문제 A: Fiscal year 이상값

TNET(44012), PRTH(43830) — Excel date serial 숫자가 fiscal_year로 파싱된 사례. 데이터를 오염시키진 않지만 불필요한 행이 생성된다.

### 해결 A

`placeEntry` 초입에 fiscal year 유효성 검증 추가:

```typescript
if (fy > 2100 || fy < 1900) return;
```

### 문제 B: pickConcept 선착순 선택

기존 `pickConcept`은 첫 번째로 최근 데이터가 "존재하는" concept을 선택했다. 매핑 확장 후 `NetIncomeLoss`에 Q3만 있고 `ProfitLoss`에 Q1~Q3 모두 있는 종목이 있다면, `NetIncomeLoss`가 선택되어 Q1/Q2가 null이 되는 문제가 있었다.

### 해결 B

각 concept의 최근 entry 수를 비교하여 가장 풍부한 concept을 선택하도록 변경:

```typescript
function pickConcept(namespace, candidates): XbrlEntry[] {
  let best: XbrlEntry[] = [];
  let bestCount = 0;
  for (const concept of candidates) {
    // ...
    const recent = entries.filter((e) => (e.fy ?? 0) >= MIN_RECENT_YEAR).length;
    if (recent > bestCount) {
      best = entries;
      bestCount = recent;
    }
  }
  return best;
}
```

---

## 부분 TTM 허용 + coverage 판정 재정의

### 문제

기존에는 revenue, operating_income, net_income 중 하나라도 null이면 TTM 전체가 실패했다. 은행/보험사는 `OperatingIncomeLoss` 태그를 사용하지 않으므로, operating_income이 null이면 net_income이 있어도 PER/EPS/ROE를 계산할 수 없었다.

또한 coverage 판정이 부분 TTM과 맞지 않았다 — operating_income만 null인 경우에도 `FULL`로 표시되는 문제가 있었다.

### 해결

3가지 변경:

1. **FY 경로의 `any(None) → return None` 제거** — FY-latest 514종목도 부분 계산 혜택을 받도록
2. **`(None, None, None)` guard** — 3필드 전부 null이면 None 반환
3. **coverage 재정의** — 3필드 완전 시 `FULL`, 부분 시 `PARTIAL`, EPS 음수 시 `LOSS`

```python
# compute() 내 coverage 판정
all_present = all(v is not None for v in ttm)
if eps_val is not None and eps_val < 0:
    coverage = DataCoverage.LOSS
elif all_present:
    coverage = DataCoverage.FULL
else:
    coverage = DataCoverage.PARTIAL
```

---

## 변경 파일 요약

| 파일 | 리포지토리 | 변경 내용 |
|---|---|---|
| `app/services/fundamental_service.py` | calc-server | TTM 로직 전면 교체, 부분 TTM 허용, coverage 재정의 |
| `app/db/repositories/financial_statement.py` | calc-server | LIMIT/slice 5→10 |
| `src/process-save/service/facts-parser.service.ts` | usa-fstatements-collector | XBRL 매핑 확장, pickConcept 완전성 기준, frame 필터 완화, fiscal year 검증 |

## 기대 효과

| 개선 | 예상 PER 채움률 변화 |
|---|---|
| TTM 로직 수정 | 29% → 55~60% |
| 부분 TTM 허용 | → 62~65% |
| XBRL 매핑 확장 | → 70~73% |
| Frame 필터 완화 | → 75~78% |

현실적 목표는 75~80%. Bloomberg/Refinitiv도 100%가 아닌 이유는 SEC EDGAR 데이터 자체의 한계(비표준 보고, 금융업 구조, 기업별 XBRL extension 태그)이며, 순수 자동화 기반으로 80%를 달성하면 매우 우수한 수준이다.

## 검증 (미실행)

collector 전체 재수집 + `python -m app.pipeline us-initial` 재실행 후, 아래 쿼리로 효과 측정 예정:

```sql
SELECT
  COUNT(*) AS total,
  COUNT(per) AS per_filled,
  ROUND(COUNT(per)::numeric / COUNT(*) * 100, 1) AS per_rate
FROM stock_fundamentals sf
JOIN stocks s ON s.id = sf.stock_id
WHERE s.market IN ('US_NYSE', 'US_NASDAQ') AND s.is_active = true;
```
