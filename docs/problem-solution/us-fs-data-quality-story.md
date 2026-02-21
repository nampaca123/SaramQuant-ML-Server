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

## 검증 결과 (2026-02-21)

DB 초기화 → collector 전체 재수집 → `python -m app.pipeline us-initial` 실행.

### PER 채움률: 29.2% → 82.2%

| 항목 | 이전 (2/17) | 이후 (2/21) | 변화 |
|---|---|---|---|
| **PER** | **29.2%** (1,278/4,375) | **82.2%** (3,589/4,368) | **+53.0pp** |
| EPS | 29.2% | 82.7% | +53.5pp |
| PBR | 75.6% | 78.9% | +3.3pp |
| ROE | 33.8% | 85.1% | +51.3pp |
| 영업이익률 | 34.5% | 58.5% | +24.0pp |
| 부채비율 | 85.0% | 75.0% | -10.0pp (주1) |

**(주1)** 부분 TTM 도입으로 자본잠식 종목(468건, 10.7%)이 신규 편입됨. 이들의 부채비율은 null 처리되므로 분모만 증가 → 비율 하락. 데이터 악화가 아닌 구조적 변화.

### 예상 vs 실제

| 개선 항목 | 예상 PER 채움률 | 누적 |
|---|---|---|
| TTM 로직 수정 (원인 1) | 29% → 55~60% | — |
| 부분 TTM 허용 | → 62~65% | — |
| XBRL 매핑 확장 (원인 2) | → 70~73% | — |
| Frame 필터 완화 (원인 3) | → 75~78% | — |
| **최종** | **75~80%** | **82.2% (초과 달성)** |

### Coverage 분포

| Coverage | 건수 | 비율 | 설명 |
|---|---|---|---|
| FULL | 1,546 | 35.4% | 3필드(revenue, operating_income, net_income) 전부 존재 |
| PARTIAL | 1,297 | 29.7% | 일부 필드만 존재 (은행/보험 등 operating_income 미보고) |
| LOSS | 1,522 | 34.8% | EPS 음수 (적자 기업) |
| INSUFFICIENT | 3 | 0.1% | TTM 계산 불가 |

이전에는 FULL/INSUFFICIENT만 존재했으나, 부분 TTM + coverage 재정의 적용 후 PARTIAL과 LOSS가 신규 등장.

---

## 기술적 설명: 뭘 고쳤나

아래는 면접이나 기술 블로그 등에서 활용 가능한, 비개발자도 따라올 수 있는 수준의 설명이다.

### 상황

미국 주식 5,900여 종목의 재무 데이터를 SEC EDGAR(미국 증권거래위원회 공시 시스템)에서 수집하여 PER·ROE 등 투자 지표를 자동 계산하는 퀀트 시스템을 구축했다. 한국 시장에서는 PER 산출률이 89.9%였는데, 동일한 코드로 미국 시장을 처리하니 **29.2%**만 계산되었다. 즉 10개 종목 중 7개의 PER을 알 수 없는 상태로, 사실상 시스템이 동작하지 않는 것이나 마찬가지였다.

### 원인 분석

DB 직접 조회와 SEC 데이터 구조 분석을 통해 4가지 근본 원인을 특정했다:

**① 미국 Q4 보고서가 존재하지 않는다 (~2,400종목 영향)**

TTM(Trailing Twelve Months, 최근 12개월) 수치를 구하려면 최근 4분기를 합산해야 하는데, 미국은 Q4를 별도로 공시하지 않는다. 10-Q(분기보고서)가 Q1·Q2·Q3까지만 나오고, Q4는 10-K(연간보고서)에 포함된다. 기존 로직은 "분기 4개를 찾아 합산"하는 방식이라, Q4가 없으면 무조건 실패했다.

→ 업계 표준 공식 `TTM = FY(연간) + 금년분기합 − 전년동일분기합`으로 교체. Q4가 없어도 FY와 Q1~Q3만으로 정확한 12개월 수치를 역산한다.

**② 같은 매출이 기업마다 다른 이름으로 기록되어 있다 (~1,200종목 영향)**

SEC EDGAR의 XBRL 데이터는 동일한 재무 항목(예: 매출)을 기업마다 다른 태그명으로 보고한다. 기존에는 매출 태그 4개만 인식했는데, 실제로는 업종별로 수십 개의 태그가 사용된다. EdgarTools(SEC 파싱 표준 오픈소스)를 참조하여 매출 13개, 순이익 4개, 자본 3개, 주식수 4개로 매핑을 확장했다. 또한 여러 태그 중 어떤 것을 선택하는 기준을 "선착순"에서 "데이터가 가장 풍부한 것"으로 변경했다.

**③ SEC 메타데이터 누락이 정상 데이터를 버리고 있었다 (~34% 분기 데이터 영향)**

SEC EDGAR의 XBRL 데이터에서 `frame`은 해당 수치가 어느 기간에 속하는지를 태깅하는 메타데이터다. 예를 들어 Apple의 2024년 Q3 매출 엔트리에는 `"frame": "CY2024Q3"`(Calendar Year 2024 Q3 기간 수치)가 붙고, 재무상태표 항목에는 `"frame": "CY2024Q3I"`(I = instant, 시점 수치)가 붙는다. `fp`(fiscal period)와 `fy`(fiscal year)는 기업이 제출 시 직접 기록하는 필드인 반면, frame은 SEC가 전체 기업의 데이터를 연도·분기별로 일괄 정리할 때 **사후에 부여하는 분류 라벨**이다.

문제는 SEC가 이 frame을 모든 엔트리에 빠짐없이 붙여주지 않는다는 것이다. 상당수 엔트리에서 frame이 비어 있는데, 기존 파서는 손익계산서 항목에서 frame이 없으면 무조건 버렸다. 반면 재무상태표 항목은 frame이 없어도 통과시키고 있었다 — 같은 "frame 없음" 상황을 두 재무제표에서 다르게 처리한 것이다. 그 결과 같은 종목이라도 Q3의 순이익은 93.9% 채워지는데 Q1/Q2는 60.5%만 채워지는 비대칭이 발생했다. frame이 없어도 통과시키되, frame이 있는 데이터를 우선하도록 변경했다.

**④ 기타 데이터 오염 + 비효율적 선택 로직**

Excel 날짜 시리얼 번호(44012 등)가 회계연도로 파싱되는 오염, 그리고 부분 데이터만 있는 태그가 먼저 선택되어 나머지 분기가 null이 되는 문제를 수정했다.

### 커밋 이력

3개 파일, 2개 리포지토리, 2개 커밋에 걸친 변경이다:

| 커밋 | 리포 | 파일 | 변경 내용 |
|---|---|---|---|
| [`0a35fa4`](https://github.com) edgarParsingIssue | calc-server | `fundamental_service.py` | TTM 공식 교체, 부분 TTM 허용 |
| [`0a35fa4`](https://github.com) edgarParsingIssue | calc-server | `financial_statement.py` | LIMIT 5→10 (새 공식에 필요한 데이터 확보) |
| [`329a2f3`](https://github.com) fundaSageLimit | calc-server | `fundamental_service.py` | sanity clamp, coverage FULL/PARTIAL/LOSS 재정의 |
| [`9a99341`](https://github.com) irregularQ4Issue | collector | `facts-parser.service.ts` | XBRL 매핑 확장, pickConcept 완전성 기준, frame 필터 완화, fiscal year 검증 |

### 핵심 코드 변경

**TTM 계산 (기존):** 분기 4개를 찾아 단순 합산 — Q4가 없으면 실패

```python
quarters = [s for s in statements if s.report_type != ReportType.FY][:4]
if len(quarters) < 4:
    return None
return sum(revenues), sum(op_incomes), sum(net_incomes)
```

**TTM 계산 (변경 후):** FY를 기준점으로 금년/전년 분기를 분류, 차분 계산

```python
for s in statements:                          # fiscal_year DESC 정렬된 입력
    if s.report_type == ReportType.FY:
        fy_stmt = s                           # 기준점: 직전 연간보고서
    elif fy_stmt is None:
        current_qs[s.report_type] = s         # FY 앞 = 금년 분기
    elif s.fiscal_year == fy_stmt.fiscal_year:
        prior_qs[s.report_type] = s           # FY 같은 해 = 전년 동일분기

# TTM = FY + sum(금년분기) - sum(전년동일분기)
ttm_revenue = fy_revenue + sum(current_q_revenues) - sum(prior_q_revenues)
```

**XBRL 태그 선택 (기존):** 첫 번째로 데이터가 "존재하는" 태그를 선택

```typescript
for (const concept of candidates) {
    if (entries.some((e) => e.fy >= MIN_RECENT_YEAR)) return entries;  // 선착순
}
```

**XBRL 태그 선택 (변경 후):** 최근 데이터가 가장 풍부한 태그를 선택

```typescript
for (const concept of candidates) {
    const recent = entries.filter((e) => e.fy >= MIN_RECENT_YEAR).length;
    if (recent > bestCount) { best = entries; bestCount = recent; }  // 완전성 기준
}
```

**frame 필터 (기존):** frame 없으면 무조건 거부

```typescript
if (IS_FIELDS.has(field)) {
    if (!SINGLE_Q_RE.test(frame)) return;  // frame='' → 거부
}
```

**frame 필터 (변경 후):** frame 없으면 통과, frame 있는 데이터 우선

```typescript
if (IS_FIELDS.has(field)) {
    if (frame && !SINGLE_Q_RE.test(frame)) return;  // frame='' → 통과
}
if (acc[field] === undefined || (frame && SINGLE_Q_RE.test(frame))) {
    acc[field] = val;  // frame 있는 값이 없는 값을 덮어씀
}
```

### 결과

자동화 기반 SEC EDGAR 데이터만으로 PER 82.2%를 달성. Bloomberg/Refinitiv 같은 상용 데이터 벤더도 100%가 아닌 점을 감안하면(비표준 보고, 금융업 구조, 기업별 확장 태그), 순수 자동화로 80% 이상은 매우 우수한 수준이다.
