# 지표 병렬 계산 삽질기: ProcessPool → ThreadPool → ProcessPool (2026-03-04)

## 배경

US 파이프라인의 indicators 단계가 2/26~2/28 기간 평균 **51초**에서, 3/4 실행 시 **160초**로 3.5배 느려졌다. "멀티스레딩을 확대해서 최적화한다"는 작업의 결과가 오히려 성능 저하였다.

audit_log 실측:

| 날짜 | 코드 버전 | Executor | 워커 | 청크 | indicators 소요 |
|---|---|---|---|---|---|
| 2/26 | `016aa98` | ProcessPool | 4 | 250 | 50.6초 |
| 2/27 | `88d0f95` | ProcessPool | 4 | 250 | 51.6초 |
| 2/28 | `88d0f95` | ProcessPool | 4 | 250 | 51.6초 |
| 3/3 | `994dba6` | ProcessPool | 4 | 250 | 45.5초 |
| **3/4** | **`be7e6a3`** | **ThreadPool** | **16** | **50** | **160.7초** |

---

## 1단계: ProcessPool에서 pickle 에러 발생

`994dba6`까지의 코드는 `ProcessPoolExecutor`를 사용했다:

```python
_MAX_WORKERS = min(4, os.cpu_count() or 4)
_CHUNK_SIZE = 250

def _compute_chunk(args):
    stock_batch, bench_ret, rf_rate, factor_betas = args
    ...

with ProcessPoolExecutor(max_workers=_MAX_WORKERS) as pool:
    for batch in pool.map(_compute_chunk, args):
        ...
```

문제: `bench_ret`(pd.Series)와 `factor_betas`(4,300개 dict)를 **매 청크마다** 인자로 전달했다. 4,300종목 / 250 = 17청크이므로 같은 데이터가 **17번** pickle 직렬화되었다.

여기에 **Python 3.14의 변경**이 결정적으로 작용했다. Python 3.14부터 Linux/Unix(macOS 제외)의 기본 multiprocessing start method가 `fork`에서 `forkserver`로 바뀌었다 ([What's New in Python 3.14](https://docs.python.org/3/whatsnew/3.14.html)). `fork`는 부모 메모리를 copy-on-write로 상속하므로 인자를 pickle할 필요가 없었지만, `forkserver`는 별도 서버 프로세스를 경유하므로 **태스크 인자를 pickle로 직렬화**해야 한다.

| Python 버전 | Linux/Unix 기본 | macOS/Windows 기본 |
|---|---|---|
| ~3.13 | `fork` | `spawn` |
| 3.14 | **`forkserver`** | `spawn` (변경 없음) |

Docker 이미지가 `python:3.14-slim`이었으므로, 이전에 `fork` 모드에서 문제없이 돌아가던 코드가 `forkserver` 모드에서 pickle 에러를 일으킨 것이다.

---

## 2단계: ThreadPool로 전환 — 잘못된 해결

pickle 에러를 피하기 위해 `ProcessPoolExecutor`를 `ThreadPoolExecutor`로 교체했다 (`cd89b14`). 스레드는 같은 프로세스 안에서 메모리를 공유하므로 pickle 자체가 불필요하다.

"numpy/pandas는 GIL을 해제하니까 ThreadPool로도 병렬화 가능하다"는 논리였다.

**이 진단은 절반만 맞았다.** numpy의 C-level 연산과 pandas의 Cython rolling/ewm 구현체는 실제로 GIL을 해제한다. 하지만 이 파이프라인의 특성상 그것만으로는 부족했다.

### 왜 ThreadPool이 실패했나

이 파이프라인은 **300행짜리 작은 DataFrame** 4,300개를 처리한다. 종목 하나당 ~15개의 지표 함수를 호출하는데, 각 호출의 시간 분해:

1. Python 메서드 디스패치 (GIL 보유): `close.rolling(20)` → Rolling 객체 생성
2. C-level 연산 (GIL 해제): 300개 float의 rolling mean
3. 결과 래핑 (GIL 보유): C 결과 → pd.Series 변환

300행에 대한 C-level 연산은 **마이크로초** 단위로 끝난다. 전체 시간의 대부분은 1번과 3번의 Python 오버헤드다. 특히 `parabolic_sar`는 300회 순수 Python for-loop로, GIL을 100% 점유한다.

**추정: 전체 연산의 60~70%가 GIL-holding Python 코드.**

16개 스레드가 이 GIL을 번갈아 잡으면서 경합만 발생했고, 실질 병렬도는 1.x배에 불과했다.

> numpy/pandas가 GIL을 해제한다는 것은 **대형 배열**(수만~수십만 행)을 다룰 때 유효하다.
> 300행 수준의 소형 Series에서는 Python wrapper 오버헤드가 지배적이어서 GIL 해제의 이점이 거의 없다.

---

## 3단계: ProcessPool + `_init_worker` — 올바른 해결

pickle 에러의 원인은 "ProcessPool 자체"가 아니라 "매 태스크마다 대형 데이터를 pickle하는 패턴"이었다. `_init_worker` 패턴으로 해결 가능하다:

```python
_shared: dict = {}

def _init_worker(bench_ret, rf_rate, factor_betas):
    _shared["bench_ret"] = bench_ret
    _shared["rf_rate"] = rf_rate
    _shared["factor_betas"] = factor_betas

def _compute_chunk(stock_batch):
    bench_ret = _shared["bench_ret"]
    ...

with ProcessPoolExecutor(
    max_workers=_MAX_WORKERS,
    initializer=_init_worker,
    initargs=(bench_ret, rf_rate, factor_betas),
) as pool:
    for batch in pool.map(_compute_chunk, chunks):
        ...
```

`initializer`는 **워커 프로세스 생성 시 1회만** 호출된다. 16워커면 공유 데이터가 16번만 pickle된다 (태스크 수인 22번이 아니라). bench_ret ~5KB + factor_betas ~100KB = 워커당 ~105KB, 총 ~1.7MB. `forkserver`/`spawn` 어느 모드에서도 무시 가능한 오버헤드다.

태스크 큐에는 stock_batch(200종목의 가격 데이터)만 전송된다.

### DB 쓰기 방식도 복원

ThreadPool 전환 시 `DELETE + INSERT`를 `UPSERT`(ON CONFLICT)로 바꿨었다. 이 파이프라인은 마켓 전체 지표를 매번 새로 계산하는 **전체 교체** 시나리오이므로, 충돌 검사가 없는 DELETE + INSERT가 더 효율적이다.

---

## 워커 수 결정 기준

ProcessPool의 각 워커는 독립 프로세스(독립 GIL)이므로, vCPU 수만큼 워커를 쓰는 것이 최적이다.

| 설정 | 효과 |
|---|---|
| 워커 < vCPU | CPU 유휴 — 자원 낭비 |
| 워커 = vCPU | 최적 — 각 코어가 1개 프로세스 전담 |
| 워커 > vCPU | 컨텍스트 스위칭 오버헤드 발생 |

Railway Pro에서 16 vCPU를 사용하므로 `min(16, os.cpu_count() or 8)`로 설정했다.

ThreadPool에서는 이 원칙이 **적용되지 않는다.** GIL 때문에 한 시점에 1개 스레드만 Python 코드를 실행하므로, 워커를 아무리 늘려도 CPU-bound 작업의 실질 병렬도는 올라가지 않는다.

---

## 교훈

1. **"어떤 Executor를 쓸까"는 작업의 성격으로 결정한다.** CPU-bound(pandas/numpy 계산)는 ProcessPool, I/O-bound(API 호출, DB 대기)는 ThreadPool. GIL 해제 여부는 배열 크기와 Python wrapper 비율에 따라 달라진다.

2. **Python 3.14의 `forkserver` 전환에 대비하라.** `fork`에서 암묵적으로 동작하던 데이터 공유가 `forkserver`(Linux/Unix)에서는 깨진다. `_init_worker` 패턴은 `fork`/`forkserver`/`spawn` 어느 모드에서도 동작하는 안전한 공유 방법이다.

3. **에러의 원인과 해결을 혼동하지 마라.** pickle 에러의 원인은 "데이터 전달 패턴"이었지, "ProcessPool 자체"가 아니었다. 원인을 정확히 짚지 못하면 해결책도 엇나간다.
