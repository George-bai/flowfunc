import json
import gzip
import pickle
import hashlib
import inspect
from typing import Any, Optional

try:
    from redis import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore


PREFIX = "ff:v1"


def _default_json(obj: Any):
    """Best-effort JSON serializer for small control values.
    Do NOT serialize large payloads here – large data should be represented by upstream digests.
    Aim for determinism: for unknown objects, fall back to a stable digest of a pickle when feasible.
    """
    try:
        import numpy as np  # optional
        import pandas as pd  # optional
    except Exception:  # pragma: no cover
        np = None
        pd = None

    if isinstance(obj, (set, frozenset)):
        return sorted(list(obj))
    if hasattr(obj, "__json__"):
        return obj.__json__()
    # Minimal handling for numpy/pandas small values
    if "np" in locals() and hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except Exception:
            pass
    if "pd" in locals() and hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            pass
    # Fallback to a stable digest of a pickle (if possible), else repr
    try:
        buf = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        return {"__py_obj_sha256__": sha256_hex_bytes(buf)}
    except Exception:
        return {"__py_repr__": repr(obj)}


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=_default_json, ensure_ascii=False)


def sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_hash(func: Any) -> str:
    try:
        src = inspect.getsource(func)  # may fail for builtins
    except Exception:
        src = repr(func)
    payload = f"{getattr(func, '__qualname__', str(func))}|{src}".encode()
    return sha256_hex_bytes(payload)


class Serializer:
    """Serialize arbitrary Python objects for storage in Redis.
    Uses pickle with gzip compression and a small header.
    """

    RAW_PREFIX = b"raw:"
    GZ_PREFIX = b"gz:"

    @staticmethod
    def serialize(obj: Any) -> bytes:
        data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        # compress if worth it
        gz = gzip.compress(data, compresslevel=5)
        if len(gz) < len(data):
            return Serializer.GZ_PREFIX + gz
        return Serializer.RAW_PREFIX + data

    @staticmethod
    def deserialize(buf: bytes) -> Any:
        if buf.startswith(Serializer.GZ_PREFIX):
            data = gzip.decompress(buf[len(Serializer.GZ_PREFIX):])
        elif buf.startswith(Serializer.RAW_PREFIX):
            data = buf[len(Serializer.RAW_PREFIX):]
        else:
            # backward compat: assume raw pickle
            data = buf
        return pickle.loads(data)


class CacheManager:
    def __init__(
        self,
        redis_client: Optional["Redis"],
        session_id: str,
        ttl_seconds: Optional[int] = 1800,
        max_blob_bytes: Optional[int] = 512 * 1024 * 1024 - 1024 * 1024,
    ):
        self.redis: Optional["Redis"] = redis_client
        self.session_id = session_id
        self.ttl = ttl_seconds
        # Guardrail for Redis value size (<= 512MiB). Default keeps 1MiB margin.
        self.max_blob_bytes = max_blob_bytes

    # Key builders
    def _node_meta_key(self, node_id: str) -> str:
        return f"{PREFIX}:{self.session_id}:node:{node_id}"

    def _blob_key(self, blob_dig: str) -> str:
        return f"{PREFIX}:blob:{blob_dig}"

    # API
    def get_if_fresh(self, node_id: str, signature: str) -> Optional[Any]:
        if not self.redis:
            return None
        meta_key = self._node_meta_key(node_id)
        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.hget(meta_key, "signature")
            pipe.hget(meta_key, "blob_dig")
            sig_val, blob_dig = pipe.execute()
        except Exception:
            return None
        if not sig_val or not blob_dig:
            return None
        if sig_val.decode() != signature:
            return None
        try:
            blob = self.redis.get(self._blob_key(blob_dig.decode()))
        except Exception:
            return None
        if not blob:
            return None
        try:
            return Serializer.deserialize(blob)
        except Exception:
            return None

    def put(self, node_id: str, signature: str, result: Any, code_hash_val: str) -> str:
        if not self.redis:
            return ""
        blob = Serializer.serialize(result)
        # Size guard: skip caching oversized payloads
        if self.max_blob_bytes is not None and len(blob) > int(self.max_blob_bytes):
            return ""
        blob_dig = sha256_hex_bytes(blob)
        blob_key = self._blob_key(blob_dig)
        meta_key = self._node_meta_key(node_id)
        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.set(blob_key, blob)
            if self.ttl:
                pipe.expire(blob_key, self.ttl)
            pipe.hset(meta_key, mapping={
                "signature": signature,
                "blob_dig": blob_dig,
                "size_b": str(len(blob)),
                "stored_at": str(int(__import__('time').time())),
                "code_hash": code_hash_val,
                "status": "hot",
            })
            if self.ttl:
                pipe.expire(meta_key, self.ttl)
            pipe.execute()
        except Exception:
            return ""
        return blob_dig

    def clear_node(self, node_id: str):  # pragma: no cover
        if not self.redis:
            return
        meta_key = self._node_meta_key(node_id)
        self.redis.delete(meta_key)

    def ping(self) -> bool:
        try:
            return bool(self.redis and self.redis.ping())
        except Exception:
            return False


def make_redis_client(url: Optional[str]) -> Optional["Redis"]:
    if not Redis:
        return None
    try:
        if url:
            return Redis.from_url(url)
        return Redis()  # defaults to localhost
    except Exception:
        return None
