# KR 파이프라인 20일간 무중단 장애: KRX 세션 만료 (2026-03-25)

## 배경

3월 6일부터 KR 파이프라인이 매일 정상 실행되는 것처럼 보이지만 실제로는 새 데이터를 수집하지 못하는 **사일런트 장애** 상태였다. US 파이프라인은 정상 가동 중이었고, Railway 로그에는 fundamentals 경고만 출력되어 이상 징후가 드러나지 않았다.

audit_log 파이프라인 메타데이터:

| 날짜 | command | collection (ms) | fundamentals | 비고 |
|---|---|---|---|---|
| 3/5 | kr | 68,942 | success | 마지막 정상 (최종 배포일) |
| 3/6 | kr | 65,001 | **failed** "connection already closed" | 데이터 0건 |
| 3/7 | kr | 464,529 | success | stale 가격 기반 |
| 3/10~25 | kr | 9,000~12,000 | success | stale 가격 기반 |

DB 최신 날짜 비교 (조사 시점):

| 데이터 | 소스 | 마지막 날짜 |
|---|---|---|
| KR daily_prices | pykrx | **2026-03-05** (중단) |
| KR benchmarks | pykrx | **2026-03-05** (중단) |
| US daily_prices | Alpaca | 2026-03-24 (정상) |
| KR risk_free_rates | ECOS | 2026-03-24 (정상) |
| Exchange rates | ExchangeRateCollector | 2026-03-24 (정상) |

pykrx에 의존하는 데이터만 정확히 마지막 배포일에 멈춰있었다.

---

## 원인: KRX 인증 정책 변경 + 세션 만료 후 재인증 부재

### KRX 정책 변경

2026년 2월 27일경, KRX(한국거래소)가 `data.krx.co.kr` API에 대해 로그인 인증을 필수화했다 (pykrx GitHub Issue [#276](https://github.com/sharebook-kr/pykrx/issues/276)). 비인증 요청에 대해 KRX는 다음을 반환한다:

```
HTTP 400, Content-Type: text/html, Body: "LOGOUT"
```

pykrx 라이브러리(v1.2.4)는 이 응답을 JSON/DataFrame으로 파싱하려 시도하여 `KeyError: '지수명'` 등의 에러를 발생시킨다.

### 로그인 구현과 그 한계

3월 4일 커밋(`be58ba9 krxLogin`)에서 KRX 로그인을 추가했다. `requests.Session`에 인증 쿠키를 저장하고, pykrx의 `webio.Post.read`/`webio.Get.read`를 monkey-patch하여 인증된 세션을 사용하도록 했다:

```python
_session = requests.Session()
_logged_in = False

def _ensure_login() -> None:
    global _logged_in
    if _logged_in:      # ← 한번 True면 다시는 로그인 안 함
        return
    # ... KRX 로그인 → _session에 쿠키 저장 ...
    _logged_in = True
```

### 장애 메커니즘

1. **3/5 배포** → 프로세스 시작 → `_logged_in=False` → 로그인 성공 → 데이터 수집 정상
2. KRX 세션 쿠키 만료 (수시간 후)
3. **3/6 파이프라인** → `PykrxClient()` 생성 → `_ensure_login()` → `_logged_in=True`이므로 skip → 만료된 쿠키로 요청 → HTTP 400 "LOGOUT" → pykrx `KeyError` → 조용히 0건 반환
4. Gunicorn master 프로세스는 worker와 달리 재시작되지 않으므로(`max_requests`는 worker만 해당), 배포 없이는 재인증 기회가 없음

### 로컬 검증

```
# 로그인 없이
>>> stock.get_index_ohlcv('20260320', '20260325', '1001')
KeyError: '지수명'

# 로그인 후
>>> stock.get_index_ohlcv('20260320', '20260325', '1001')
4 rows × 7 columns  ✅
```

---

## 해결: HTTP 응답 기반 자동 재인증

### 설계 원칙

시간 기반 추정이나 파이프라인 레벨 리셋 대신, **HTTP 응답 레벨에서 인증 실패를 감지하고 즉시 재인증 후 재시도**하는 방식을 선택했다. 근거:

1. **신호가 결정적**: `status_code != 200` 은 KRX 비인증을 100% 판별 (오탐 없음)
2. **세션 만료 시간 추정 불필요**: KRX의 세션 타임아웃이 변경되어도 자동 대응
3. **업계 표준 패턴**: AWS SDK, OAuth2 interceptor, Google Cloud SDK 모두 동일한 detect → refresh → retry 패턴 사용
4. **변경 범위 최소화**: monkey-patch 내부에 캡슐화, 나머지 코드 변경 없음

### 구현 (`app/collectors/clients/pykrx.py`)

기존 `_ensure_login()`을 3개 함수로 분리:

```python
def _do_login() -> bool:
    """순수 로그인 함수. 쿠키 초기화 후 KRX 인증, 성공/실패 반환."""
    _session.cookies.clear()
    # ... KRX 로그인 ...
    return code == "CD001"

def _is_auth_failure(resp: requests.Response) -> bool:
    return resp.status_code != 200

def _refresh_and_retry(method, url, headers, params) -> Response:
    """Lock 획득 → 재확인 요청 → 여전히 실패 시 재인증 → 재시도."""
    with _login_lock:
        resp = request(...)          # 다른 스레드가 이미 갱신했을 수 있음
        if _is_auth_failure(resp):
            _do_login()
            resp = request(...)
    return resp
```

monkey-patched read 메서드에서 매 응답을 검사:

```python
def _post_read(self, **params):
    resp = _session.post(self.url, headers=self.headers, data=params)
    if _is_auth_failure(resp):
        resp = _refresh_and_retry("post", self.url, self.headers, params)
    return resp
```

`threading.Lock`은 KR 파이프라인이 순차 실행이더라도, 방어적으로 동시 재인증을 방지한다.

---

## 부수 조치: 오염된 데이터 정리

3/6~3/25 동안 stale 가격(3/5 기준)으로 계산된 KR 데이터를 삭제:

| 테이블 | 삭제 건수 |
|---|---|
| stock_fundamentals | 22,122 |
| factor_exposures | 22,122 |
| factor_returns | 504 |
| sector_aggregates | 378 |
| risk_badges | 2,428 |

`stock_indicators`는 가격 날짜 기준 PK이므로 3/5 데이터를 매일 덮어쓸 뿐, 새 오염 행이 생기지 않아 삭제 불필요.

---

## 배포 후 예상 동작

1. Railway 배포 → 프로세스 재시작 → `_ensure_login()` → 신규 로그인
2. `KrDailyPriceCollector` → DB 마지막 날짜 `2026-03-05`부터 금일까지 누락된 거래일 가격 자동 backfill
3. `BenchmarkCollector` → KR 벤치마크 동일 backfill
4. 이후 세션 만료 시 → `_post_read`/`_get_read`에서 자동 감지 → 재인증 → 재시도 → 무중단 운영
