from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import random
import time
from typing import Any, Callable

from stock_selection.data.tushare_limiter import RateLimiter


def _default_env_candidates() -> list[Path]:
    project_root = Path(__file__).resolve().parents[2]
    candidates = [Path.cwd() / ".env", project_root / ".env"]
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            unique.append(candidate)
            seen.add(resolved)
    return unique


def _clean_env_value(value: str) -> str:
    value = value.strip()
    if "#" in value and not value.startswith(("'", '"')):
        value = value.split("#", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def load_project_env(env_file: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load simple KEY=VALUE pairs from a project .env file."""

    candidates = [Path(env_file)] if env_file else _default_env_candidates()
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and (override or key not in os.environ):
                os.environ[key] = _clean_env_value(value)
        return path
    return None


DEFAULT_HTTP_URL = "http://minitick.top/"
load_project_env()
DEFAULT_MAX_CALLS_PER_MINUTE = int(os.getenv("TUSHARE_MAX_CALLS_PER_MINUTE", "90"))
DEFAULT_RETRY_WAIT_SECONDS = (30.0, 60.0)

_DEFAULT_LIMITER = RateLimiter(max_calls=DEFAULT_MAX_CALLS_PER_MINUTE, window_seconds=60.0)
_PRO_CACHE: dict[tuple[str, str | None], Any] = {}


@dataclass(frozen=True)
class TushareCallResult:
    api_name: str
    params: dict[str, Any]
    rows: int | None
    elapsed_seconds: float
    attempts: int


def get_pro(token: str | None = None, http_url: str | None = DEFAULT_HTTP_URL) -> Any:
    """Return a configured Tushare Pro client.

    The token is read from ``TUSHARE_TOKEN`` unless explicitly provided.
    Tushare is imported lazily so the rest of the project remains usable without
    the data extra installed.
    """

    load_project_env()
    resolved_token = token or os.getenv("TUSHARE_TOKEN")
    if not resolved_token:
        raise RuntimeError("TUSHARE_TOKEN is not set")

    cache_key = (resolved_token, http_url)
    if cache_key in _PRO_CACHE:
        return _PRO_CACHE[cache_key]

    ts = _import_tushare()
    pro = ts.pro_api(resolved_token)
    if http_url:
        pro._DataApi__http_url = http_url
    _PRO_CACHE[cache_key] = pro
    return pro


def call_api(
    api_name: str,
    params: dict[str, Any] | None = None,
    *,
    pro: Any | None = None,
    limiter: RateLimiter | None = None,
    max_retries: int = 2,
    retry_wait_seconds: tuple[float, float] = DEFAULT_RETRY_WAIT_SECONDS,
    on_result: Callable[[TushareCallResult], None] | None = None,
) -> Any:
    """Call ``pro.<api_name>(**params)`` through rate limiting and retry."""

    params = dict(params or {})
    client = pro or get_pro()
    api = getattr(client, api_name)
    return _call_with_retries(
        lambda: api(**params),
        api_name=api_name,
        params=params,
        cost=1,
        limiter=limiter,
        max_retries=max_retries,
        retry_wait_seconds=retry_wait_seconds,
        on_result=on_result,
    )


def call_pro_bar(
    params: dict[str, Any] | None = None,
    *,
    pro: Any | None = None,
    limiter: RateLimiter | None = None,
    max_retries: int = 2,
    retry_wait_seconds: tuple[float, float] = DEFAULT_RETRY_WAIT_SECONDS,
    on_result: Callable[[TushareCallResult], None] | None = None,
) -> Any:
    """Call ``ts.pro_bar(api=pro, ...)`` with a conservative cost of 2."""

    params = dict(params or {})
    client = pro or get_pro()
    ts = _import_tushare()
    return _call_with_retries(
        lambda: ts.pro_bar(api=client, **params),
        api_name="pro_bar",
        params=params,
        cost=2,
        limiter=limiter,
        max_retries=max_retries,
        retry_wait_seconds=retry_wait_seconds,
        on_result=on_result,
    )


def _call_with_retries(
    call: Callable[[], Any],
    *,
    api_name: str,
    params: dict[str, Any],
    cost: int,
    limiter: RateLimiter | None,
    max_retries: int,
    retry_wait_seconds: tuple[float, float],
    on_result: Callable[[TushareCallResult], None] | None,
) -> Any:
    active_limiter = limiter or _DEFAULT_LIMITER
    attempts = 0
    started = time.monotonic()

    while True:
        attempts += 1
        active_limiter.acquire(cost)
        try:
            result = call()
            if on_result is not None:
                on_result(
                    TushareCallResult(
                        api_name=api_name,
                        params=_safe_params(params),
                        rows=_row_count(result),
                        elapsed_seconds=time.monotonic() - started,
                        attempts=attempts,
                    )
                )
            return result
        except AttributeError:
            raise
        except Exception as exc:
            if attempts > max_retries + 1 or not _is_retryable_error(exc):
                raise
            time.sleep(_retry_wait(retry_wait_seconds))


def _import_tushare() -> Any:
    try:
        import tushare as ts
    except ImportError as exc:
        raise RuntimeError(
            "Tushare is not installed. Install the data extra with "
            '`pip install -e ".[data]"`.'
        ) from exc
    return ts


def _retry_wait(wait_range: tuple[float, float]) -> float:
    low, high = wait_range
    if high < low:
        low, high = high, low
    return random.uniform(low, high)


def _row_count(result: Any) -> int | None:
    shape = getattr(result, "shape", None)
    if shape:
        return int(shape[0])
    try:
        return len(result)
    except TypeError:
        return None


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if "token" not in key.lower()}


def _is_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    non_retryable_markers = (
        "permission",
        "auth",
        "token",
        "权限",
        "积分",
        "无权",
        "没有访问",
        "不存在",
        "invalid api",
    )
    if any(marker in message for marker in non_retryable_markers):
        return False

    retryable_markers = (
        "timeout",
        "timed out",
        "connection",
        "network",
        "too many",
        "rate",
        "frequency",
        "频率",
        "超时",
        "网络",
        "连接",
        "限流",
    )
    return any(marker in message for marker in retryable_markers)
