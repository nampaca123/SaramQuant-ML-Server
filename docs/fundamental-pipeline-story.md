# US 재무제표 수집 파이프라인 — 마이크로서비스 분리 실전기

## 배경

미국 상장사 약 7,500개의 재무제표를 SEC EDGAR에서 수집하고, 이를 기반으로 PER·PBR·ROE 등 펀더멘털 지표를 계산하는 파이프라인을 만들어야 했다. 이미 일봉 데이터 수집과 기술적 지표 계산은 잘 돌아가고 있었고, 재무제표 수집을 붙이면 "전체 퀀트 데이터 파이프라인"이 완성되는 상황이었다.

문제는 데이터 소스였다. SEC EDGAR의 `companyfacts.zip`(~1.3GB)은 **미국 동부 서버**에 있고, 이 서비스는 **한국에서** 돌아간다. 다운로드만 3~4시간이 걸렸다. 이건 느린 게 아니라 **위험한** 것이다 — 4시간짜리 다운로드는 중간에 끊길 확률이 높고, 끊기면 처음부터 다시 받아야 한다.

SEC 공식 문서에 따르면, `companyfacts.zip`은 이미 "대량 수집을 위한 가장 효율적인 방법"으로 권장되는 방식이다. 즉, 더 빠른 무료 API는 존재하지 않는다. 병목은 코드가 아니라 **물리적인 네트워크 거리**에 있었다.

---

## 1. 마이크로서비스 분리

### 발상

서버를 데이터 소스 옆에 두면 된다. Railway에 Nest.js 마이크로서비스를 하나 띄워서, **US East 리전에서** ZIP을 다운로드하고, 파싱하고, DB에 저장하는 일을 전담시킨다. 기존 Python ML 서버는 "수집해라"는 명령만 보내고 결과를 기다리면 된다.

```
[Python ML 서버 (한국)]                [Nest.js 마이크로서비스 (US East)]
        │                                        │
        │─── POST /collect ──────────────────────▶│
        │                                        │─── companyfacts.zip 다운로드 (로컬)
        │                                        │─── 7,459개 JSON 파싱
        │◀── { jobId } ─────────────────────────│─── DB에 46,138건 UPSERT
        │                                        │
        │─── GET /status/:jobId (30초 간격) ────▶│
        │◀── { progress: "parsing 2500/7459" } ──│
        │      ...반복...                         │
        │◀── { status: "completed" } ────────────│
        │                                        │
        │─── 펀더멘털 지표 계산 (로컬)            │
```

### 왜 Nest.js인가

이 서비스의 주 작업은 **HTTP 다운로드 → 파일 I/O → DB 쓰기**다. CPU 연산은 거의 없고, 대부분의 시간을 "네트워크 응답 대기"와 "디스크 읽기 대기"에 쓴다. Node.js의 비동기 I/O 모델은 이런 작업에 잘 맞는다 — 한 파일의 디스크 읽기를 기다리는 동안 다른 파일의 읽기 요청을 동시에 보낼 수 있기 때문이다. Python의 `asyncio`로도 가능하지만, 이 마이크로서비스는 단일 책임의 작은 서비스이므로 Nest.js의 모듈 구조 정도면 충분했다.

### 왜 웹훅이 아니라 폴링인가

처음에는 작업 완료 시 웹훅으로 Python 서버에 알리는 구조를 고려했다. 하지만 Python ML 서버는 Flask 서버가 아니라 **CLI 파이프라인**이다. 웹훅을 받으려면 별도의 HTTP 서버를 띄우거나 `threading.Event`로 메인 스레드를 블록해야 한다.

폴링이 더 단순하다. 30초마다 "아직이야?" 하고 물어보는 것뿐이다. 네트워크 비용은 30초당 1KB 미만의 JSON 응답이니 무시할 수 있고, 구현도 `while` 루프 하나면 된다.

---

## 2. 진행률을 보여줘야 불안하지 않다

### 문제

마이크로서비스에 작업을 맡기고 4분을 기다리는 동안, Python 서버의 로그에는 아무것도 안 나온다. "지금 뭐 하고 있는 거지?" 하는 불안감이 생긴다. 장애인지, 진행 중인지 구분이 안 된다.

### 해결

마이크로서비스가 `JobStatus` 객체에 현재 단계와 진행률을 기록하고, 폴링 응답에 포함시키도록 했다:

```
17:58:06 [FundCollection] US remote: downloading
17:58:37 [FundCollection] US remote: downloading
17:59:39 [FundCollection] US remote: parsing (500/7459)
18:00:10 [FundCollection] US remote: parsing (2500/7459)
18:00:41 [FundCollection] US remote: parsing (5000/7459)
18:01:12 [FundCollection] US (remote) complete: {'success': 46138, 'failed': 0}
```

30초 간격이지만 "지금 어디까지 했는지"가 보이면 충분하다. 숫자가 올라가고 있으면 정상이고, 멈춰 있으면 뭔가 문제가 있는 것이다.

---

## 3. ZIP 추출 경로 버그

### 증상

배포 후 첫 테스트에서 `success: 0, failed: 0`이 돌아왔다. 에러도 없고, 파싱도 정상적으로 끝났는데, 결과가 0건이다.

### 원인

`companyfacts.zip`을 `/tmp/edgar/` 에 추출했는데, ZIP 내부 파일들이 `CIK0000001234.json` 형태로 루트에 바로 풀렸다. 즉 파일은 `/tmp/edgar/CIK0000001234.json`에 있었다. 그런데 코드는 `/tmp/edgar/companyfacts/CIK0000001234.json`을 찾고 있었다.

파일이 없으면 `.catch(() => null)`로 조용히 넘어가도록 되어 있었기 때문에, 7,459개 파일 전부 "없음"으로 처리되었다. 에러 0, 성공 0.

### 수정

추출 경로를 `companyfacts` 서브디렉토리로 명시적으로 지정했다:

```typescript
const dest = join(DATA_DIR, 'companyfacts');
mkdirSync(dest, { recursive: true });
// ...
await execFileAsync('unzip', ['-o', '-q', zipPath, '-d', dest]);
```

### 교훈

> **조용히 실패하는 코드는 위험하다.** `.catch(() => null)` 같은 에러 무시 패턴은 편하지만, 전체 파이프라인이 "성공적으로 아무것도 안 하는" 상황을 만들 수 있다.

---

## 4. Node.js 비동기 I/O를 실제로 활용하기

### 최초 구현: 순차 처리

처음에는 7,459개 JSON 파일을 하나씩 읽었다:

```
for (const stock of stocks) {
    const raw = await readFile(filePath, 'utf-8');  // 한 번에 하나씩
    JSON.parse(raw);
}
```

파일 하나를 읽을 때 Node.js는 OS에 읽기 요청을 보내고, 디스크가 응답할 때까지 **아무것도 안 하고 기다린다**. Node.js의 이벤트 루프는 이 시간에 다른 작업을 처리할 수 있는데, 순차 `for`문 안에서는 그 능력을 쓰지 못한다.

### 개선: `p-map`으로 동시 I/O

`p-map` 라이브러리를 사용해서 동시에 50개 파일을 읽도록 변경했다:

```typescript
await pMap(
  stocks,
  async ({ stockId, symbol }) => {
    const raw = await readFile(filePath, 'utf-8');
    // ...파싱...
  },
  { concurrency: 50 },
);
```

이제 50개의 `readFile` 호출이 동시에 OS에 전달된다. 디스크가 파일 1을 읽는 동안, 파일 2~50의 읽기 요청이 이미 큐에 들어가 있다. OS의 I/O 스케줄러가 이 요청들을 효율적으로 처리하고, Node.js 이벤트 루프는 응답이 오는 대로 즉시 파싱을 시작한다.

Python에서 같은 일을 하려면 `asyncio` + `aiofiles` 또는 `concurrent.futures.ThreadPoolExecutor`를 써야 한다. 불가능하지는 않지만, Node.js에서는 `fs/promises`의 `readFile`이 기본적으로 비동기이므로 추가 라이브러리 없이 자연스럽게 구현된다.

### 동시성 50인 이유

10,000개 파일을 동시에 열면 빠를 것 같지만, OS의 파일 디스크립터 한계와 메모리 폭발 위험이 있다. 50은 "충분히 빠르되 안전한" 경험적 수치다. Railway 환경에서 테스트했을 때, 50 정도면 디스크 I/O 파이프라인이 거의 포화 상태였다.

---

## 5. DB 쓰기: UNNEST 벌크 Upsert

### 문제

46,000건의 재무제표를 DB에 넣어야 한다. 한 건씩 INSERT하면 지표 최적화 글에서 다뤘던 것과 정확히 같은 문제가 발생한다 — 네트워크 왕복이 46,000회.

### 해결

PostgreSQL의 `UNNEST`를 사용해서 2,000건씩 한 번에 UPSERT한다:

```sql
INSERT INTO financial_statements (stock_id, fiscal_year, report_type, ...)
SELECT * FROM unnest($1::bigint[], $2::int[], $3::report_type[], ...)
ON CONFLICT (stock_id, fiscal_year, report_type) DO UPDATE SET ...
```

`unnest`는 PostgreSQL 배열을 테이블 형태로 "펼치는" 함수다. TypeScript에서 각 컬럼의 값들을 배열로 모아서 파라미터로 넘기면, DB 측에서 한 번의 쿼리로 2,000건의 INSERT + 충돌 시 UPDATE를 처리한다. 46,000건이 23번의 네트워크 왕복으로 끝난다.

---

## 6. 파이프라인 자동 연결

### 문제

재무제표 수집 후, 펀더멘털 지표 계산을 수동으로 따로 실행해야 했다. `us-fs` 명령을 치고, 끝나면 다시 펀더멘털 계산 명령을 쳐야 하는 구조였다. 까먹으면 지표가 갱신되지 않는다.

### 해결

파이프라인 오케스트레이터에서 재무제표 수집이 끝나면 자동으로 펀더멘털 계산을 호출하도록 연결했다:

```python
def run_collect_fs_us(self) -> None:
    self._fund_collector.collect_all("us")     # 마이크로서비스로 수집
    self._compute_fundamentals("us")           # 수집 끝나면 자동 계산
```

`us-fs` 한 번이면 수집부터 지표 계산까지 전부 끝난다.

---

## 7. Python 계산 효율 점검

마이크로서비스가 재무제표를 다 모아주면, Python 서버가 펀더멘털 지표를 계산한다. 이 단계에서도 비효율이 없는지 검토했다.

### Decimal → float 변환

DB에서 읽어온 가격 데이터는 `Decimal` 타입이다. 원래는 이렇게 변환했다:

```python
df[col] = df[col].apply(lambda v: float(v) if isinstance(v, Decimal) else v)
```

`.apply(lambda)`는 파이썬 for문과 다름없다. 매 행마다 Python 인터프리터가 lambda를 호출하고, 타입 체크를 하고, 변환한다. pandas의 벡터 연산으로 바꾸면:

```python
df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
```

pandas가 내부적으로 C 레벨에서 일괄 변환한다.

### Parabolic SAR의 NumPy 전환

기술적 지표 중 유일하게 for문이 필요한 Parabolic SAR에서, `pandas.iloc[]`을 `numpy` 배열 인덱싱으로 교체했다. `iloc`은 매번 인덱스 검증과 타입 체크를 수행하지만, numpy 배열의 `arr[i]`는 C 포인터 접근과 동일한 속도다.

---

## 최종 결과

| 항목 | 최적화 전 (한국 직접) | 최적화 후 (마이크로서비스) | 개선 |
|------|----------------------|--------------------------|------|
| ZIP 다운로드 | 3~4시간 | ~2분 | **~100x** |
| JSON 파싱 (7,459개) | - | ~1분 30초 | p-map 동시 50 |
| DB 쓰기 (46,138건) | - | ~수 초 | UNNEST 벌크 |
| 펀더멘털 계산 (4,618종목) | - | ~14초 | pandas 벡터 연산 |
| **총 소요 시간** | **3~4시간** | **~4분** | **~50x** |

### 아키텍처 요약

```
[CLI: python -m app.pipeline us-fs]
    │
    ▼
[PipelineOrchestrator]
    │
    ├──▶ FundamentalCollectionService._collect_us()
    │        │
    │        ├── POST /collect → Nest.js 마이크로서비스 (US East)
    │        │       │
    │        │       ├── BulkDownloadService: ZIP 다운로드 + unzip 추출
    │        │       ├── FactsReaderService: p-map(concurrency:50) 비동기 파싱
    │        │       ├── FactsParserService: XBRL JSON → 재무제표 구조화
    │        │       └── StatementWriterService: UNNEST 벌크 upsert
    │        │
    │        └── GET /status/:jobId (30초 폴링, 진행률 표시)
    │
    └──▶ FundamentalComputeEngine.run()
             │
             ├── TTM 재무데이터 + 최신 종가 로드
             ├── PER / PBR / ROE / ROA / 부채비율 / EPS 계산
             └── stock_fundamentals 테이블에 일괄 저장
```

---

## 정리: 마이크로서비스 분리의 3가지 원칙

1. **병목이 물리적이면, 코드가 아니라 인프라를 옮겨라.** 한국↔미국 동부의 네트워크 지연은 어떤 코드 최적화로도 줄일 수 없다. 서버를 데이터 소스 옆에 두는 것이 답이다.
2. **동시성은 도구가 아니라 모델에 맞춰 선택하라.** Node.js의 비동기 I/O는 "CPU 안 쓰고 기다리는 시간"이 많을 때 빛난다. 7,459개 파일을 읽는 이 서비스가 정확히 그 케이스다.
3. **작은 서비스일수록 빠르게 분리할 수 있다.** 이 마이크로서비스는 Nest.js 서버 한 대, 모듈 3개, 서비스 5개로 구성된다. 기존 Python 서버에서 호출하는 코드는 `requests.post` + `while` 루프가 전부다. 마이크로서비스 분리의 오버헤드가 충분히 낮으면, "일단 해보자"가 가능하다.
