"""Tushare 客户端封装：加载项目环境、复用连接、限流、重试并记录调用摘要。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import random
import time
from typing import Any, Callable

from stock_selection.data.tushare_limiter import RateLimiter


def _default_env_candidates() -> list[Path]:
    """按“当前目录优先、项目根目录兜底”返回可能的 .env 文件。"""
    project_root = Path(__file__).resolve().parents[2]
    candidates = [Path.cwd() / ".env", project_root / ".env"]
    unique: list[Path] = []
    seen: set[Path] = set()
    # resolve 后去重，避免从项目根执行时对同一 .env 重复加载。
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            unique.append(candidate)
            seen.add(resolved)
    return unique


def _clean_env_value(value: str) -> str:
    """去除简单 .env 值的空白、非引号注释和外层引号。"""
    value = value.strip()
    if "#" in value and not value.startswith(("'", '"')):
        value = value.split("#", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def load_project_env(env_file: str | Path | None = None, *, override: bool = False) -> Path | None:
    """加载简单 KEY=VALUE 格式的项目 .env；默认不覆盖已有进程环境变量。"""

    candidates = [Path(env_file)] if env_file else _default_env_candidates()
    for path in candidates:
        if not path.exists():
            continue
        # 只实现本项目需要的简单语法，避免引入额外 dotenv 依赖。
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


# 导入模块时仅加载环境变量，不发起网络请求；真实连接在 get_pro 中延迟创建。
load_project_env()
DEFAULT_HTTP_URL = os.getenv("TUSHARE_HTTP_URL", "https://tx.xiaodefa.top/")
DEFAULT_MAX_CALLS_PER_MINUTE = int(os.getenv("TUSHARE_MAX_CALLS_PER_MINUTE", "90"))
DEFAULT_RETRY_WAIT_SECONDS = (30.0, 60.0)

_DEFAULT_LIMITER = RateLimiter(max_calls=DEFAULT_MAX_CALLS_PER_MINUTE, window_seconds=60.0)
_PRO_CACHE: dict[tuple[str, str | None], Any] = {}


@dataclass(frozen=True)
class TushareCallResult:
    """单次成功调用的无敏感信息审计摘要。"""
    api_name: str
    params: dict[str, Any]
    rows: int | None
    elapsed_seconds: float
    attempts: int


def get_pro(token: str | None = None, http_url: str | None = DEFAULT_HTTP_URL) -> Any:
    """返回配置完成的 Tushare Pro 客户端；令牌默认取自 ``TUSHARE_TOKEN``。"""

    load_project_env()
    resolved_token = token or os.getenv("TUSHARE_TOKEN")
    if not resolved_token:
        raise RuntimeError("TUSHARE_TOKEN is not set")

    # 同一令牌与 HTTP 地址复用客户端，避免批量采集反复初始化连接。
    cache_key = (resolved_token, http_url)
    if cache_key in _PRO_CACHE:
        return _PRO_CACHE[cache_key]

    ts = _import_tushare()
    pro = ts.pro_api(resolved_token)
    # 保留项目配置的镜像/自定义地址；调用方显式传 None 才使用 SDK 默认地址。
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
    """通过限流、重试和结果审计调用 ``pro.<api_name>(**params)``。"""

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
    """通过同一保护层调用 ``ts.pro_bar``；默认按 2 次额度计入限流。"""

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
    """执行受限调用：成功时记录摘要，可重试错误采用随机等待后重试。"""
    active_limiter = limiter or _DEFAULT_LIMITER
    attempts = 0
    started = time.monotonic()

    while True:
        attempts += 1
        # 限流必须发生在每次重试前，防止网络抖动导致短时间内集中重放。
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
            # 权限、令牌和接口名错误不会因等待而恢复，应立即向上抛出。
            if attempts > max_retries + 1 or not _is_retryable_error(exc):
                raise
            time.sleep(_retry_wait(retry_wait_seconds))


def _import_tushare() -> Any:
    """延迟导入可选的 tushare 依赖，并把安装提示转换为项目级错误。"""
    try:
        import tushare as ts
    except ImportError as exc:
        raise RuntimeError(
            "Tushare is not installed. Install the data extra with "
            '`pip install -e ".[data]"`.'
        ) from exc
    return ts


def _retry_wait(wait_range: tuple[float, float]) -> float:
    """在给定区间内随机选择等待时间，减少多个任务同时重试的碰撞。"""
    low, high = wait_range
    if high < low:
        low, high = high, low
    return random.uniform(low, high)


def _row_count(result: Any) -> int | None:
    """尽量从 DataFrame 或普通容器提取返回行数，不支持时返回空值。"""
    shape = getattr(result, "shape", None)
    if shape:
        return int(shape[0])
    try:
        return len(result)
    except TypeError:
        return None


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    """删除可能含令牌的参数后再写入审计日志。"""
    return {key: value for key, value in params.items() if "token" not in key.lower()}


def _is_retryable_error(exc: Exception) -> bool:
    """按错误文本区分不可恢复的权限问题与可恢复的网络/频率问题。"""
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
