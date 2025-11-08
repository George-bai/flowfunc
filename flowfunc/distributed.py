"""
RQ Utils
--------
This module defines redis-queue related classess and functions.
"""
from __future__ import annotations
from rq.job import Job, get_current_job
from rq.queue import Queue
from .models import OutConnections
from pydantic import validate_arguments, validate_call, ConfigDict
from .cache import CacheManager
import time
from typing import Any


class NodeJob(Job):
    """Custom job class which will modify the kwargs based on the dependencies
    of the current job

    There should be two meta variables, node_connections and result_keys which
    will define the connections to the current node and the variable names of
    the output of the current node.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        node_connections = self.get_meta().get("node_connections")
        if node_connections:
            self.node_connections = OutConnections(**node_connections)
        else:
            self.node_connections = None
        # keys for the result dict
        self.result_keys = self.get_meta().get("result_keys", ["result"])

    def update_kwargs(self):
        if not self.node_connections or not self.node_connections.inputs:
            return
        for key, node_connection in self.node_connections.inputs.items():
            # Assuming dependent job shares the same connection.
            # Also, dependent job should be complete before this job starts peforming.
            # Also assuming that there is only one connection in one port
            # as flume allows only one at this time.
            try:
                oc = node_connection[0]
                djid = getattr(oc, "job_id", None)
                pname = getattr(oc, "portName", None)
                if not djid:
                    continue
                dependent_job = NodeJob.fetch(djid, connection=self.connection)
                value = dependent_job.result_mapped[pname]
                # If we enqueued the run_node_wrapper, arguments are nested under 'literal_kwargs'
                if isinstance(self.kwargs, dict) and 'literal_kwargs' in self.kwargs and isinstance(self.kwargs['literal_kwargs'], dict):
                    self.kwargs['literal_kwargs'][key] = value
                else:
                    self.kwargs.update({key: value})
            except Exception:
                pass

    @property
    def func(self):
        """Overriding Job class' func method to include argument validation"""
        return validate_arguments(
            super().func, config=dict(arbitrary_types_allowed=True)
        )

    def perform(self):
        """Overriding the perform method of the parent class.

        Only resolves upstream kwargs; caching is handled exclusively in run_node_wrapper.
        """
        # Always ensure kwargs reflect upstream job results
        self.update_kwargs()
        return super().perform()

    @property
    def result_mapped(self):
        """Mapped result dictionary

        Creating an extra property which is a dictionary with keys equal to the
        output ports of the node.
        """
        # Prefer return_value; supports both property and method forms; fallback to .result
        res = None
        try:
            rv = getattr(self, "return_value")
            if callable(rv):
                try:
                    res = rv()
                except Exception:
                    res = None
            else:
                res = rv
        except Exception:
            res = None
        if res is None:
            res = self.result
        if not isinstance(res, tuple):
            # If there is only one result item and has to be converted
            # to a tuple to map it onto a dict and later to kwargs
            res = (res,)
        return {x: y for x, y in zip(self.result_keys, res)}


class NodeQueue(Queue):
    """Node Queue class is derived from the base Queue class in RQ"""

    job_class = NodeJob


def _result_mapped_for_job(job: Job) -> dict:
    """Compute a result mapping for a finished job based on its meta result_keys."""
    try:
        keys = (job.meta or {}).get("result_keys") or job.get_meta().get("result_keys")
    except Exception:
        keys = None
    if not keys:
        keys = ["result"]
    # Prefer return_value (supports property and method forms); fallback to .result
    res = None
    try:
        rv = getattr(job, "return_value")
        if callable(rv):
            try:
                res = rv()
            except Exception:
                res = None
        else:
            res = rv
    except Exception:
        res = None
    if res is None:
        res = job.result
    if not isinstance(res, tuple):
        res = (res,)
    return {k: v for k, v in zip(keys, res)}


def run_node_wrapper(func, literal_kwargs: dict | None = None):
    """RQ worker-safe wrapper to resolve upstream dependencies, support caching,
    then call the target function with complete kwargs.

    Enqueue this wrapper instead of the user function so no custom Job class is required.
    """
    job = get_current_job()  # type: ignore
    meta = {}
    try:
        meta = job.get_meta() or {}
    except Exception:
        pass
    # Start with literals passed by the scheduler
    kwargs = dict(literal_kwargs or {})

    # Resolve inputs from dependent jobs using meta.node_connections
    try:
        node_conns = meta.get("node_connections")
        if node_conns and node_conns.get("inputs"):
            for key, conns in node_conns["inputs"].items():
                if not conns:
                    continue
                oc = conns[0]
                dep_job_id = oc.get("job_id")
                port_name = oc.get("portName")
                if not dep_job_id:
                    continue
                dep_job = Job.fetch(dep_job_id, connection=getattr(job, "connection", None))
                mapping = _result_mapped_for_job(dep_job)
                if port_name not in mapping:
                    raise KeyError
                kwargs[key] = mapping[port_name]
    except Exception:
        pass

    # Cancellation short-circuit (session/run scoped)
    cancel_key = meta.get("cancel_key")
    try:
        conn = getattr(job, "connection", None)
        if cancel_key and conn:
            try:
                if conn.get(cancel_key):
                    # mark canceled in meta and return None
                    try:
                        job.meta = {**meta, **{"phase": "canceled", "exec_ms": 0}}
                        job.save_meta()
                    except Exception:
                        pass
                    return None
            except Exception:
                pass
    except Exception:
        pass

    # Caching short-circuit
    cache_enabled = bool(meta.get("cache_enabled"))
    cache_session = meta.get("cache_session")
    cache_signature = meta.get("cache_signature")
    node_id = meta.get("node_id")
    cache_ttl = meta.get("cache_ttl_seconds")
    node_type = meta.get("node_type")
    if cache_enabled and cache_session and cache_signature and node_id:
        try:
            cache = CacheManager(redis_client=getattr(job, "connection", None), session_id=str(cache_session), ttl_seconds=cache_ttl)
            cached = cache.get_if_fresh(node_id, cache_signature)
        except Exception:
            cached = None
        if cached is not None:
            try:
                job.meta = {**meta, **{"cache_hit": True, "phase": "cached", "exec_ms": 0}}
                job.save_meta()
            except Exception:
                pass
            return cached

    # Execute target
    t0 = time.perf_counter()
    try:
        call = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(func)
        result = call(**kwargs)
    except Exception:
        # Try without validation (async or special callables)
        result = func(**kwargs)
    dt_ms = int((time.perf_counter() - t0) * 1000)

    # Store in cache on success
    if cache_enabled and cache_session and cache_signature and node_id:
        try:
            code_hash_val = meta.get("code_hash") or ""
            cache = CacheManager(redis_client=getattr(job, "connection", None), session_id=str(cache_session), ttl_seconds=cache_ttl)
            blob_dig = cache.put(node_id, cache_signature, result, code_hash_val)
            try:
                job.meta = {**meta, **{"cache_hit": False, "phase": "computed", "exec_ms": dt_ms, "blob_dig": blob_dig}}
                job.save_meta()
            except Exception:
                pass
        except Exception:
            pass
    return result
