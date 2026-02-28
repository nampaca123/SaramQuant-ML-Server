# Compute Pipeline 성능 개선기: UNNEST → 병렬화 → LATERAL (2026-02-28)

## 배경

파이프라인의 compute 구간(DB에서 가격을 읽고, 지표/팩터/리스크 등을 계산해서 다시 DB에 쓰는 구간)이 느렸다. audit_log 테이블에 기록된 실측 데이터:

| 파이프라인 | 평균 소요 | indicators step | 미측정 구간 |
|---|---|---|---|
| US daily | 115초 | 51초 (44%) | 43초 (37%) |
| KR daily | 61초 | 29초 (47%) | 17초 (28%) |

"미측정 구간"은 `_safe_step`으로 감싸지 않아 audit_log에 안 찍히던 부분이다. 나중에 확인해보니 이것의 대부분은 DB에서 가격 데이터를 읽어오는 `_load_prices` 쿼리였다.

문제는 크게 세 층위로 나뉜다:

1. **DB 쓰기**: 계산 결과를 DB에 저장할 때 round-trip이 많음 → UNNEST 배치
2. **CPU 계산**: 종목 2,000개를 한 줄로 순차 처리 → ProcessPoolExecutor 병렬화
3. **DB 읽기**: 220만 행 테이블에서 비효율적인 윈도우 함수 → LATERAL JOIN

---

## 1. UNNEST 배치: 쓰기 round-trip 제거

### 문제

계산 결과를 DB에 저장할 때, 행 하나마다 INSERT를 보내면 종목 2,000개 기준 2,000번의 네트워크 왕복이 발생한다. 이전 문서([indicator-optimization-story](./indicator-optimization-story.md))에서 다룬 Bulk INSERT의 연장선인데, PostgreSQL에서는 **UNNEST**라는 더 효율적인 방법이 있다.

### 일반적인 Bulk INSERT vs UNNEST

멀티 VALUES 방식:

```sql
INSERT INTO stock_indicators (stock_id, date, sma_20, rsi_14, ...)
VALUES (1, '2026-02-28', 50000, 65.3, ...),
       (2, '2026-02-28', 32000, 48.1, ...),
       -- ... 2,000행
;
```

이 방식은 SQL 문자열 자체가 거대해진다. 2,000행 × 23컬럼이면 파라미터가 46,000개. PostgreSQL의 파라미터 파싱 비용이 커지고, 드라이버(psycopg2)에서 SQL 문자열을 조립하는 것 자체가 느려진다.

UNNEST 방식:

```sql
INSERT INTO stock_indicators (stock_id, date, sma_20, rsi_14, ...)
SELECT * FROM UNNEST(
    %s::bigint[],     -- stock_id 배열: [1, 2, ..., 2000]
    %s::date[],       -- date 배열: ['2026-02-28', '2026-02-28', ...]
    %s::numeric[],    -- sma_20 배열: [50000, 32000, ...]
    %s::numeric[],    -- rsi_14 배열: [65.3, 48.1, ...]
    ...
)
```

파라미터가 46,000개에서 **23개(컬럼 수)**로 줄어든다. 각 파라미터는 Python 리스트 하나이고, psycopg2가 이를 PostgreSQL 배열로 변환한다. DB 엔진은 배열을 한 번에 해체(unnest)해서 행으로 만드므로, 파싱 비용이 극적으로 줄어든다.

### 코드 패턴

이 프로젝트의 5개 repository가 모두 동일한 구조를 따른다:

```python
# 1. 컬럼 정의 (타입 포함)
_COL_TYPES = [
    ("stock_id", "bigint"), ("date", "date"),
    ("open", "numeric"), ("high", "numeric"), ...
]

# 2. SQL 조각 자동 생성
_COLS = [c for c, _ in _COL_TYPES]
_UNNEST = ", ".join(f"%s::{t}[]" for _, t in _COL_TYPES)

# 3. 행→열 전치 후 UNNEST 실행
def insert_batch(self, rows: list[tuple]) -> int:
    cols = [list(c) for c in zip(*rows)]   # 행 리스트 → 컬럼 리스트로 전치
    cur.execute(
        f"INSERT INTO table ({', '.join(_COLS)}) "
        f"SELECT * FROM UNNEST({_UNNEST})",
        cols,
    )
```

`zip(*rows)`가 핵심이다. `[(1, 'a'), (2, 'b')]`를 `[(1, 2), ('a', 'b')]`로 뒤집는다. 이렇게 하면 PostgreSQL이 "stock_id 배열, date 배열, ..." 형태로 받아서 한 번에 행으로 풀 수 있다.

### 교훈

> **PostgreSQL에서 대량 INSERT를 할 때는 UNNEST가 가장 효율적이다.** 멀티 VALUES는 파라미터 수가 행 수 × 컬럼 수로 폭증하지만, UNNEST는 파라미터 수가 컬럼 수로 고정된다. 컬럼 정의를 `_COL_TYPES`로 한 곳에 모아두면 SQL 조각도 자동 생성되어 오타 위험도 없다.

---

## 2. ProcessPoolExecutor 병렬화: CPU 병목 해소

### 문제

`indicator_compute.py`의 핵심 루프:

```python
for stock_id, raw_prices in price_map.items():      # 2,000종목 순차
    df = IndicatorService.build_dataframe(raw_prices) # DataFrame 생성
    rows.append(IndicatorService.compute(stock_id, df, ...))  # 지표 23개 계산
```

종목 간에 의존성이 전혀 없다. A 종목의 RSI를 계산하는 데 B 종목의 데이터가 필요하지 않다. 이런 작업을 **embarrassingly parallel**(당혹스러울 정도로 병렬화가 쉬운) 문제라고 부른다. 그런데 단일 스레드로 순차 처리하고 있었다.

### 왜 ThreadPoolExecutor가 아니라 ProcessPoolExecutor인가

Python에는 **GIL(Global Interpreter Lock)**이라는 제약이 있다. 한 프로세스 안에서는 아무리 스레드를 많이 만들어도, Python 코드를 실행하는 스레드는 **한 번에 하나뿐**이다.

pandas/numpy 연산은 내부적으로 C 코드를 실행할 때 GIL을 풀어주기는 한다. 하지만 이 파이프라인의 종목당 계산은 "작은 pandas 호출 20개+의 연쇄"다. 각 호출 사이마다 Python 레벨로 돌아와서 GIL을 다시 잡는다.

```
[GIL 잡기] → sma() → [GIL 풀기/잡기] → ema() → [GIL 풀기/잡기] → rsi() → ...
```

이 GIL 잡기/풀기가 종목 2,000개 × 호출 20개 = 40,000번 반복된다. ThreadPoolExecutor에서는 이 경합 때문에 실질 병렬도가 낮다.

ProcessPoolExecutor는 아예 **별도 프로세스**를 띄우므로 GIL이 각자 독립적이다. 진짜 동시에 4개 종목을 처리할 수 있다.

### 구현

```python
# 모듈 레벨에 정의 — 자식 프로세스가 pickle로 이 함수를 찾아야 하므로
def _compute_chunk(args):
    stock_batch, bench_ret, rf_rate, factor_betas = args
    rows = []
    for stock_id, raw_prices in stock_batch:
        df = IndicatorService.build_dataframe(raw_prices)
        if df is not None:
            fb = factor_betas.get(stock_id)
            rows.append(IndicatorService.compute(stock_id, df, bench_ret, rf_rate, fb))
    return rows

# _process_market 안에서
items = list(price_map.items())
chunks = [items[i:i + 250] for i in range(0, len(items), 250)]

with ProcessPoolExecutor(max_workers=4) as pool:
    for batch in pool.map(_compute_chunk, args):
        rows.extend(batch)
```

세 가지 설계 결정이 있다:

**1) 왜 종목 단위가 아니라 청크(250개) 단위인가**

ProcessPoolExecutor는 작업을 자식 프로세스에 넘길 때 **pickle 직렬화**를 한다. 종목 하나씩 넘기면 2,000번의 직렬화/역직렬화가 발생한다. 250개씩 묶으면 8번으로 줄어든다. DB의 batch 처리와 같은 원리 — 단위 작업의 오버헤드를 줄이려면 묶어서 보내라.

**2) 왜 함수가 모듈 레벨에 있어야 하는가**

Python의 pickle은 클래스의 메서드나 람다 함수를 직렬화할 수 없다. 자식 프로세스가 함수를 실행하려면 "모듈 이름 + 함수 이름"으로 찾아갈 수 있어야 한다. 모듈 최상위에 정의된 일반 함수만 이 조건을 만족한다.

**3) `max_workers=min(4, os.cpu_count() or 4)`**

배포 환경(Docker 컨테이너)의 CPU 코어 수에 따라 자동 조절된다. 2코어 인스턴스에 워커 8개를 띄우면 컨텍스트 스위칭 오버헤드만 늘어난다.

### 예상 효과

| | 기존 (1 worker) | 병렬화 (4 workers) |
|---|---|---|
| US indicators | 51초 | ~14초 |
| KR indicators | 29초 | ~8초 |

### 교훈

> **종목 간 독립적인 계산은 병렬화의 최적 대상이다.** Python에서 CPU-bound 병렬화는 ProcessPoolExecutor를 쓰고, I/O-bound 병렬화는 ThreadPoolExecutor를 쓴다. 둘을 구분하는 기준은 "작업 시간의 대부분을 CPU가 쓰는가, 대기(네트워크/디스크)에 쓰는가"이다.

---

## 3. LATERAL JOIN: 읽기 쿼리 19배 개선

### 문제

파이프라인의 미측정 구간 43초(US) 중 대부분은 `_load_prices` — DB에서 종목별 최근 300일 가격을 가져오는 쿼리였다. 기존 쿼리:

```sql
SELECT stock_id, date, open, high, low, close, volume
FROM (
    SELECT dp.*, ROW_NUMBER() OVER (
        PARTITION BY dp.stock_id ORDER BY dp.date DESC
    ) AS rn
    FROM daily_prices dp
    JOIN stocks s ON dp.stock_id = s.id
    WHERE s.market = 'US_NASDAQ' AND s.is_active = true
) sub
WHERE rn <= 300
ORDER BY stock_id, date
```

`ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC)` — "종목별로 날짜 역순 번호를 매겨서 300번 이내만 가져와라"라는 뜻이다. 직관적이고 읽기 쉽다. 하지만 실행 계획(EXPLAIN ANALYZE)을 보면:

```
Index Scan using daily_prices_stock_date_idx on daily_prices dp
  (actual time=0.012..15695.567 rows=2,203,820 loops=1)
```

**220만 행 전체**를 인덱스 스캔한다. US_NASDAQ 종목만 필요한데, KR 종목의 가격까지 전부 읽은 뒤 Merge Join으로 걸러내는 구조다. 왜냐하면 `ROW_NUMBER`의 `PARTITION BY`가 전체 데이터에 걸려 있어서, DB 엔진이 "일단 다 읽어야 번호를 매길 수 있다"고 판단하기 때문이다.

실행 시간: **16,970ms** (US_NASDAQ 단일 마켓).

### 해결: LATERAL JOIN

```sql
SELECT s.id AS stock_id,
       dp.date, dp.open, dp.high, dp.low, dp.close, dp.volume
FROM stocks s
CROSS JOIN LATERAL (
    SELECT date, open, high, low, close, volume
    FROM daily_prices dp
    WHERE dp.stock_id = s.id
    ORDER BY dp.date DESC
    LIMIT 300
) dp
WHERE s.market = 'US_NASDAQ' AND s.is_active = true
ORDER BY s.id, dp.date
```

`LATERAL`은 "왼쪽 테이블(stocks)의 각 행에 대해, 오른쪽 서브쿼리를 실행하라"는 의미다. 쉽게 말하면 **"종목 목록을 먼저 뽑고, 각 종목에 대해 가격 300개씩 가져와라"**는 2단계 전략이다.

실행 과정:
1. stocks 테이블에서 `market = 'US_NASDAQ' AND is_active = true` → 2,780개 종목 ID 확보
2. 각 종목 ID에 대해 `daily_prices_stock_date_idx` 인덱스를 **직접 탐색** → 300행만 읽기
3. 총 읽기량: 2,780 × 300 = ~834,000행 (필요한 만큼만)

기존 방식은 220만 행을 읽고 걸러냈지만, LATERAL은 83만 행만 읽는다. 그리고 각 탐색이 인덱스 seek(정확한 위치로 점프)이므로 순차 스캔보다 훨씬 빠르다.

EXPLAIN ANALYZE 결과:

```
Nested Loop (actual time=23.410..571.023 rows=742,435 loops=1)
  -> Index Scan using stocks_pkey on stocks s
       (actual time=21.996..24.339 rows=2,780 loops=1)
  -> Limit (actual time=0.016..0.164 rows=267 loops=2,780)
       -> Index Scan using daily_prices_stock_date_idx on daily_prices dp
            Index Cond: (stock_id = s.id)
```

실행 시간: **887ms**. 동일한 결과(742,435행), **19배 빠르다.**

| | ROW_NUMBER | LATERAL JOIN |
|---|---|---|
| 실행 시간 | 16,970ms | 887ms |
| 버퍼 읽기 | 724,604 페이지 | 38,468 페이지 |
| 결과 행 수 | 742,435 | 742,435 |

### 핵심: 인덱스가 이미 있었다

`daily_prices_stock_date_idx`라는 `(stock_id, date DESC)` 인덱스가 이미 존재했다. ROW_NUMBER 쿼리도 이 인덱스를 사용하긴 했지만, **전체 스캔** 방식으로 사용했다. LATERAL은 같은 인덱스를 **포인트 탐색** 방식으로 사용한다. 인덱스를 만드는 것만큼, 그 인덱스를 **어떤 쿼리 패턴으로 활용하느냐**가 중요하다.

### 교훈

> **"종목별 최근 N건"같은 top-N-per-group 쿼리에서 ROW_NUMBER는 직관적이지만, 그룹 수가 많고 테이블이 클 때는 LATERAL JOIN이 압도적으로 빠르다.** LATERAL은 "필요한 그룹만, 필요한 만큼만" 읽는 반면, ROW_NUMBER는 "전부 읽고 번호 매기고 걸러내는" 구조이기 때문이다.

---

## 전체 개선 효과 (예상)

### US daily pipeline

| 구간 | 개선 전 | 개선 후 | 기법 |
|---|---|---|---|
| load_prices | ~43초 | ~2초 | LATERAL JOIN |
| indicators | 51초 | ~14초 | ProcessPoolExecutor |
| 기타 steps | 19초 | 19초 | (변경 없음) |
| **합계** | **~115초** | **~35초** | |

### KR daily pipeline

| 구간 | 개선 전 | 개선 후 | 기법 |
|---|---|---|---|
| load_prices | ~17초 | ~1초 | LATERAL JOIN |
| indicators | 29초 | ~8초 | ProcessPoolExecutor |
| 기타 steps | 13초 | 13초 | (변경 없음) |
| **합계** | **~61초** | **~22초** | |

---

## 정리: 계층별 최적화 원칙

이 세 가지 최적화는 각각 다른 계층의 병목을 해결한다:

| 계층 | 병목 | 해법 | 원리 |
|---|---|---|---|
| DB 쓰기 | 행마다 INSERT = round-trip 폭증 | UNNEST 배치 | 파라미터를 배열로 묶어 1회 전송 |
| CPU 계산 | 단일 스레드 순차 처리 | ProcessPoolExecutor | 독립 작업을 여러 프로세스에 분산 |
| DB 읽기 | 전체 스캔 후 필터링 | LATERAL JOIN | 필요한 데이터만 인덱스로 직접 탐색 |

공통 원칙은 하나다: **불필요한 작업을 하지 마라.**
- UNNEST: 불필요한 네트워크 왕복을 하지 마라
- 병렬화: 불필요하게 다른 종목을 기다리지 마라
- LATERAL: 불필요한 행을 읽지 마라
