from __future__ import annotations

import json
import time
from typing import Any

import redis


class CacheBackend:
    def get_json(self, key: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class RedisCache(CacheBackend):
    def __init__(self, redis_url: str):
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def get_json(self, key: str) -> dict[str, Any] | None:
        raw = self.client.get(key)
        if not raw:
            return None
        return json.loads(raw)

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        self.client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))

    def delete(self, key: str) -> None:
        self.client.delete(key)


class MemoryCache(CacheBackend):
    def __init__(self):
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}

    def get_json(self, key: str) -> dict[str, Any] | None:
        pair = self._store.get(key)
        if not pair:
            return None
        expire_at, value = pair
        if expire_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        self._store[key] = (time.time() + ttl_seconds, value)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
