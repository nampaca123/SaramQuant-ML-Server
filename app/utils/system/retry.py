import logging
import time
from functools import wraps
from typing import Callable, TypeVar, ParamSpec
from requests.exceptions import RequestException
from psycopg2 import OperationalError, InterfaceError

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

RETRYABLE_EXCEPTIONS = (
    RequestException,
    OperationalError,
    InterfaceError,
    TimeoutError,
    ConnectionError,
)


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = RETRYABLE_EXCEPTIONS,
) -> Callable[[Callable[P, R]], Callable[P, R | None]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R | None]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | None:
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        f"[Retry {attempt + 1}/{max_retries}] "
                        f"{func.__name__} failed: {e}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(delay)

            logger.error(
                f"[Failed] {func.__name__} after {max_retries} retries: "
                f"{last_exception}"
            )
            return None

        return wrapper
    return decorator
