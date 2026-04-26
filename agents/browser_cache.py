import hashlib
import json
import os
import time
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Callable, TypeVar


T = TypeVar("T")

DEFAULT_BROWSER_CACHE_PATH = "/tmp/careloop-browser-use-cache.json"
DEFAULT_BROWSER_CACHE_TTL_SECONDS = 24 * 60 * 60

_cache_lock = RLock()
_key_locks: dict[str, Lock] = {}
_memory_cache: dict[str, dict[str, Any]] | None = None


def browser_cache_ttl_seconds() -> int:
    value = os.getenv("CARELOOP_BROWSER_CACHE_TTL_SECONDS")
    if not value:
        return DEFAULT_BROWSER_CACHE_TTL_SECONDS
    return max(0, int(value))


def browser_cache_path() -> Path:
    return Path(os.getenv("CARELOOP_BROWSER_CACHE_PATH", DEFAULT_BROWSER_CACHE_PATH))


def _normalize_payload(payload: dict[str, Any]) -> str:
    cleaned = {
        str(key): " ".join(str(value).lower().split())
        for key, value in payload.items()
        if value is not None
    }
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def browser_cache_key(namespace: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(_normalize_payload(payload).encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def _load_cache() -> dict[str, dict[str, Any]]:
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache

    path = browser_cache_path()
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        _memory_cache = loaded if isinstance(loaded, dict) else {}
    except Exception:
        _memory_cache = {}
    return _memory_cache


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    path = browser_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, sort_keys=True)
    temp_path.replace(path)


def _key_lock(key: str) -> Lock:
    with _cache_lock:
        lock = _key_locks.get(key)
        if lock is None:
            lock = Lock()
            _key_locks[key] = lock
        return lock


def get_browser_cache(key: str, ttl_seconds: int | None = None) -> Any | None:
    ttl = browser_cache_ttl_seconds() if ttl_seconds is None else ttl_seconds
    if ttl == 0:
        return None

    with _cache_lock:
        cache = _load_cache()
        entry = cache.get(key)
        if not entry:
            return None
        created_at = float(entry.get("created_at") or 0)
        if time.time() - created_at > ttl:
            cache.pop(key, None)
            _save_cache(cache)
            return None
        return entry.get("value")


def set_browser_cache(key: str, value: Any) -> None:
    with _cache_lock:
        cache = _load_cache()
        cache[key] = {"created_at": time.time(), "value": value}
        _save_cache(cache)


def cached_browser_call(
    *,
    namespace: str,
    payload: dict[str, Any],
    loader: Callable[[], T | None],
    ttl_seconds: int | None = None,
) -> tuple[T | None, bool]:
    key = browser_cache_key(namespace, payload)
    cached = get_browser_cache(key, ttl_seconds)
    if cached is not None:
        return cached, True

    with _key_lock(key):
        cached = get_browser_cache(key, ttl_seconds)
        if cached is not None:
            return cached, True

        value = loader()
        if value is not None:
            set_browser_cache(key, value)
        return value, False
