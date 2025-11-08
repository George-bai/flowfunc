from __future__ import annotations
import asyncio
import inspect
from copy import copy, deepcopy
from typing import Any, Callable, Dict, List, Optional, Set
import uuid
import multiprocessing as mp
import time

from pydantic import validate_call, ConfigDict
from .cache import (
    CacheManager,
    make_redis_client,
    stable_json_dumps,
    code_hash,
    sha256_hex_bytes,
)

from .config import Config
from .exceptions import ErrorInDependentNode, QueueError
from .models import OutNode
from .utils import logger

# Optional numeric backends for vector policies
try:  # numpy is optional; array policies gracefully degrade if unavailable
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    np = None  # type: ignore
try:  # pandas is optional
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    pd = None  # type: ignore

try:
    from .distributed import NodeQueue
except ImportError:
    # Not opted for distributed
    pass


def default_meta_method(
    method,
    job_queue,
    job_runner,
    input_args,
    node,
    dependents,
    job_kwargs,
):
    """The default enqueuing method

    Parameters
    ----------
    method: Callable
        The function that should be submitted to the queue
    job_queue: NodeQueue
        The NodeQueue instance which should be used to submit the job
    input_args: dict
        The kwargs of method
    node: OutNode
        The pydantic node object
    config: Config
        Instance of the Config object which contains all the node functions
    dependents: List[str]
        List of dependent job IDs
    job_kwargs: dict
        Dict of keyword arguments for the rq enqueue function

    Returns
    -------
    job: NodeJob
        An instance of the rq job
    """
    # Provide cache-related metadata for worker-side short-circuiting
    cache_enabled = bool(getattr(job_runner, "cache_enabled", False))
    cache_obj: CacheManager | None = getattr(job_runner, "cache", None)
    session_id = cache_obj.session_id if cache_obj else None
    ttl_seconds = cache_obj.ttl if cache_obj else None
    # precomputed signature and code hash if available
    cache_signature = job_runner._signatures.get(node.id)
    code_hash_val = job_runner._code_hashes.get(node.id, code_hash(method))

    # Enqueue wrapper that resolves dependencies + cache in worker context
    try:
        from .distributed import run_node_wrapper as _ff_run_wrapper  # local import to avoid circulars
    except Exception:
        # Fallback: direct method (won't resolve deps)
        _ff_run_wrapper = method
    # Combine depends_on from upstream dependents and any user-provided depends in job_kwargs
    user_depends = job_kwargs.pop("depends_on", None)
    depends_param = [dependent.job_id for dependent in dependents]
    if user_depends:
        if isinstance(user_depends, (list, tuple)):
            depends_param.extend(list(user_depends))
        else:
            depends_param.append(user_depends)
    return job_queue.enqueue(
        _ff_run_wrapper,
        kwargs={
            "func": method,
            "literal_kwargs": input_args,
        },
        meta={
            "node_connections": node.connections.model_dump(),
            "result_keys": [
                x.name for x in (job_runner.flume_config.get_node(node.type).outputs or [])
            ],
            "node_id": node.id,
            "node_type": node.type,
            # cache control/meta
            "cache_enabled": cache_enabled,
            "cache_session": session_id,
            "cache_ttl_seconds": ttl_seconds,
            "cache_signature": cache_signature,
            "code_hash": code_hash_val,
            # cancellation
            "run_id": getattr(job_runner, "run_id", None),
            "cancel_key": getattr(job_runner, "_cancel_key", None),
            **job_runner.meta_data,
        },
        depends_on=depends_param,
        **job_kwargs,
    )


def run_in_same_worker(flume_config, out_dict):
    """Run the whole flow in the same worker"""
    runner = JobRunner(flume_config=flume_config)
    result = {}
    run_output = runner.run(out_dict)
    if not run_output or not isinstance(run_output, dict):
        return result
    for nodeid, node in run_output.items():
        result[nodeid] = node.model_dump(exclude={"job", "run_event", "settings"})
        # Converting results to hashable type
        node_error = result[nodeid]["error"]
        if node_error:
            result[nodeid]["error"] = str(node_error)
    return result


class JobRunner:
    """Class which runs the flow

    Initiate an instance of this class with the config information. This instance
    can be used to run a flow dict which uses the nodes from the provided config.

    Use the 'run' method of this object to run the flow.

    Attributes
    ----------
    flume_config: Config
        The config object which contains all the nodes required to run the
        flow.
    method: str
        The way the JobRunner object should process the flow. There are three
        options as of now.
        sync: Synchronous, blocking run. If there are nodes which are async
            functions, they will be run asynchronously.
        async: Returns an awaitable when run
        distributed: Runs using python-rq. When run, returns the input dict, but
            updated with corresponding job objects for each node.
    default_queue: NodeQueue
        Required if the method is 'distributed'. default_queue is instance of
        NodeQueue class. If each node does not have a queue setting defined,
        this queue will be used.
    meta_map: Dict[Callable, Callable]
        Optional. A dictionary which matches the node function to a meta function.
        The meta function is responsible for enqueuing the job using the queue.
        Meta function gives better control in scheduling the job and use features
        from the python-rq library. Look at the default_meta_method to see how
        job is enqueued by default.
    meta_data: Dict[Any, Any]
        Optional. Any extra meta data to supply to the job
    """

    def __init__(
        self,
        flume_config: Config,
        method: str = "sync",
        same_worker: bool = False,
        # default_queue should be a NodeQueue instance but cannot annotate with
        # NodeQueue since it will make python-rq a required dependency
        default_queue: Optional[Any] = None,
        meta_map: Optional[Dict[Callable, Callable]] = None,
        meta_data: Optional[Dict[str, Any]] = None,
        # Caching options
        cache_enabled: bool = False,
        redis_url: Optional[str] = None,
        session_id: Optional[str] = None,
        cache_ttl_seconds: Optional[int] = 1800,
        # Cycles/SCC solver options
        enable_cycles: bool = False,
        scc_max_iters: int = 50,
        scc_tolerance: float = 1e-6,
        scc_relaxation: float = 1.0,
        scc_cache_policy: str = "final_only",
        scc_initial: Optional[Dict[str, Dict[str, Any]]] = None,
        # Advanced solvers
        scc_solver: str = "jacobi",  # 'jacobi' | 'wegstein'
        scc_wegstein_qmin: float = 0.0,
        scc_wegstein_qmax: float = 2.0,
        # Tolerance extensions and policies
        scc_rtol: float = 0.0,
        scc_atol: float = 0.0,
        scc_port_tolerance: Optional[Dict[tuple[str, str], float]] = None,
        scc_type_tolerance: Optional[Dict[type, float]] = None,
        scc_policy_overrides: Optional[Dict[tuple[str, str], str]] = None,  # {(node_type, port): 'auto'|'numeric'|'array'|'df_numeric'|'equality'}
        scc_df_numeric_as_array: bool = True,
        scc_df_align: str = "strict",  # 'strict' | 'allow_reindex'
        # Per-edge tolerance overrides: {(src_type, src_port, dst_type, dst_input): tol}
        scc_edge_tolerance: Optional[Dict[tuple[str, str, str, str], float]] = None,
    ):
        self.flume_config = flume_config
        self.method = method
        self.queue = default_queue
        self.meta_map = meta_map if meta_map else {}
        self.meta_data = meta_data if meta_data else {}
        if self.method == "distributed" and self.queue is None:
            raise QueueError(
                "If the method is distributed, the `default_queue` argument cannot be empty."
            )
        self.same_worker = same_worker
        # cache
        self.cache_enabled = cache_enabled
        self.cache: Optional[CacheManager] = None
        # map for signatures and function code hashes
        self._signatures: Dict[str, str] = {}
        self._code_hashes: Dict[str, str] = {}
        if cache_enabled:
            client = make_redis_client(redis_url)
            sid = session_id or "default"
            self.cache = CacheManager(client, sid, ttl_seconds=cache_ttl_seconds)
            if not (self.cache and self.cache.ping()):
                self.cache_enabled = False
        # store computed signatures per node id (for downstream)
        # (reinitialized at run boundaries as appropriate)
        # Cancellation state
        self.run_id: Optional[str] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._cancel_key: Optional[str] = None
        # Track active per-node processes for hard interrupts
        self._active_node_procs: Dict[str, mp.Process] = {}
        # SCC / cycles settings
        self.enable_cycles = enable_cycles
        self.scc_max_iters = int(scc_max_iters or 0) if isinstance(scc_max_iters, int) else 50
        self.scc_tolerance = float(scc_tolerance or 0.0)
        self.scc_relaxation = float(scc_relaxation or 1.0)
        self.scc_cache_policy = scc_cache_policy or "final_only"
        self.scc_initial: Dict[str, Dict[str, Any]] = scc_initial or {}
        self.scc_solver = (scc_solver or "jacobi").lower()
        self.scc_wegstein_qmin = float(scc_wegstein_qmin)
        self.scc_wegstein_qmax = float(scc_wegstein_qmax)
        # Extended tolerance / policy controls
        self.scc_rtol = float(scc_rtol or 0.0)
        self.scc_atol = float(scc_atol or 0.0)
        self.scc_port_tolerance: Dict[tuple[str, str], float] = dict(scc_port_tolerance or {})
        self.scc_type_tolerance: Dict[type, float] = dict(scc_type_tolerance or {})
        self.scc_policy_overrides: Dict[tuple[str, str], str] = dict(scc_policy_overrides or {})
        self.scc_df_numeric_as_array: bool = bool(scc_df_numeric_as_array)
        self.scc_df_align: str = (scc_df_align or "strict").lower()
        # Per-edge overrides
        self.scc_edge_tolerance: Dict[tuple[str, str, str, str], float] = dict(scc_edge_tolerance or {})

    # ---------- Cancellation API ----------
    def request_cancel(self, run_id: Optional[str] = None) -> bool:
        """Request cancellation for the current run (cooperative). In distributed mode,
        also sets a Redis cancel key so workers can short-circuit.
        """
        if run_id and getattr(self, "run_id", None) and run_id != self.run_id:
            return False
        try:
            if self._cancel_event and not self._cancel_event.is_set():
                self._cancel_event.set()
        except Exception:
            pass
        # Best-effort distributed cancel flag via Redis
        try:
            if self._cancel_key and self.cache and hasattr(self.cache, "client"):
                ttl = getattr(self.cache, "ttl", 1800) or 1800
                self.cache.client.set(self._cancel_key, "1", ex=ttl)  # type: ignore[attr-defined]
            elif self._cancel_key and self.cache and hasattr(self.cache, "redis"):
                ttl = getattr(self.cache, "ttl", 1800) or 1800
                self.cache.redis.set(self._cancel_key, "1", ex=ttl)  # type: ignore[attr-defined]
        except Exception:
            pass
        # Attempt to terminate any active subprocesses (hard cancel)
        try:
            for nid, proc in list(self._active_node_procs.items()):
                if proc and proc.is_alive():
                    try:
                        proc.terminate()
                        proc.join(timeout=1)
                    except Exception:
                        pass
            self._active_node_procs.clear()
        except Exception:
            pass
        return True

    def is_cancel_requested(self) -> bool:
        try:
            return bool(self._cancel_event and self._cancel_event.is_set())
        except Exception:
            return False

    def _build_signature(self, nodeid: str, out_node: OutNode, input_args: dict, mapped_dict: dict, method: Callable, ignore_node_ids: Optional[Set[str]] = None) -> tuple[str, str]:
        """Compute a deterministic signature for a node based on controls, upstream signatures and function code.

        Returns (signature_hex, func_code_hash).
        """
        # Gather upstream signatures if available
        upstream_sigs: List[str] = []
        if out_node.connections and out_node.connections.inputs:
            for _key, connections in out_node.connections.inputs.items():
                if not connections:
                    continue
                dep_id = connections[0].nodeId
                if ignore_node_ids and dep_id in ignore_node_ids:
                    continue
                dep_sig = self._signatures.get(dep_id, "")
                upstream_sigs.append(dep_sig)
        func_hash = code_hash(method)
        # Only include literal controls (exclude connected inputs which may be large objects)
        connected_keys = set((out_node.connections.inputs or {}).keys() if out_node.connections else [])
        literal_controls = {k: v for k, v in (input_args or {}).items() if k not in connected_keys}
        material = {
            "controls": literal_controls,
            "upstream": sorted(upstream_sigs),
            "func": func_hash,
            "node_type": out_node.type,
        }
        sig = sha256_hex_bytes(stable_json_dumps(material).encode())
        return sig, func_hash

    @validate_call
    def run(
        self,
        out_dict: Dict[str, OutNode],
        selected_node_ids: Optional[List[str]] = None,
    ):
        """Run the node map

        Parameters
        ----------
        out_dict: dict
            The output from the UI
        selected_nodes: List[str]
            The selected node IDs which should be run. The dependent nodes will
            automatically be identified from the out_dict and add to the list
            of nodes to be run.

        Returns
        -------
        mapped_dict: dict
            UI output dict converted to a dict with mapped pydantic OutNode objects
            for each node dictionary. Also, the results
            (if method is 'sync' or 'async') or job object (if method is
            'distributed') is attached to the Node.
        """
        if not out_dict:
            return
        mapped_dict = deepcopy(out_dict)
        self._signatures = {}
        self._code_hashes = {}
        try:
            self.run_id = uuid.uuid4().hex
            self._cancel_event = asyncio.Event()
            # precompute a cancel_key for distributed workers to consult
            if self.cache_enabled and self.cache and hasattr(self.cache, "session_id"):
                self._cancel_key = f"ff:v1:{self.cache.session_id}:cancel:{self.run_id}"  # type: ignore[attr-defined]
            else:
                self._cancel_key = None
        except Exception:
            self._cancel_key = None
        if selected_node_ids:
            dependent_node_ids = self.dependent_nodes(selected_node_ids, mapped_dict)
            mapped_dict = {nodeid: mapped_dict[nodeid] for nodeid in dependent_node_ids}

        # Dispatch by method for both full and filtered runs
        if self.method == "sync":
            return asyncio.run(self.run_async(mapped_dict))
        elif self.method == "async":
            return self.run_async(mapped_dict)
        elif self.method == "distributed" and self.same_worker:
            return asyncio.run(self.run_distributed_same_worker(out_dict))
        elif self.method == "async_distributed" and self.same_worker:
            return self.run_distributed_same_worker(out_dict)
        elif self.method == "distributed":
            return asyncio.run(self.run_distributed(mapped_dict))
        elif self.method == "async_distributed":
            return self.run_distributed(mapped_dict)
        else:
            raise ValueError(
                "The provided method is not identified."
                " It should be one of sync, async or distributed"
            )

    def dependent_nodes(self, selected_node_ids, mapped_dict):
        """Function to downselect only some nodes from the mapped_dict"""
        new_node_ids = []
        for nodeid, node in mapped_dict.items():
            if nodeid in selected_node_ids:
                new_node_ids.append(nodeid)
                if not node.connections.inputs:
                    continue
                for param, mapping in node.connections.inputs.items():
                    for connection in mapping:
                        new_node_ids.append(connection.nodeId)
        new_node_ids = list(set(new_node_ids))  # List of unique
        if len(new_node_ids) > len(selected_node_ids):
            return self.dependent_nodes(new_node_ids, mapped_dict)
        return new_node_ids

    def _toposort_nodes(self, mapped_dict: Dict[str, OutNode]) -> List[str]:
        """Topologically sort nodes so that dependencies come first."""
        indegree: Dict[str, int] = {nid: 0 for nid in mapped_dict}
        children: Dict[str, List[str]] = {nid: [] for nid in mapped_dict}
        for nid, node in mapped_dict.items():
            if node.connections and node.connections.inputs:
                for conns in node.connections.inputs.values():
                    if not conns:
                        continue
                    dep = conns[0].nodeId
                    if dep in indegree:
                        indegree[nid] += 1
                        children[dep].append(nid)
        # Kahn's algorithm
        queue = [nid for nid, deg in indegree.items() if deg == 0]
        order: List[str] = []
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for child in children.get(cur, []):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        # Fallback: if cycle/leftovers, append remaining nodes in arbitrary order
        if len(order) != len(mapped_dict):
            for nid in mapped_dict:
                if nid not in order:
                    order.append(nid)
        return order

    def _precompute_signatures(self, mapped_dict: Dict[str, OutNode]):
        """Compute deterministic signatures for all nodes in topological order.

        Populates self._signatures and self._code_hashes for use in distributed mode.
        """
        self._signatures = {}
        self._code_hashes = {}
        order = self._toposort_nodes(mapped_dict)
        for nodeid in order:
            out_node = mapped_dict[nodeid]
            method = self.flume_config.get_node(out_node.type).method
            # gather literal controls
            input_args: Dict[str, Any] = {}
            for key, values in (out_node.inputData or {}).items():
                if not values:
                    continue
                if len(values) > 1:
                    variable_value = values
                else:
                    variable_value = next(iter(values.values()))
                if variable_value is None:
                    continue
                input_args[key] = variable_value
            connected_keys = set((out_node.connections.inputs or {}).keys() if out_node.connections else [])
            literal_controls = {k: v for k, v in (input_args or {}).items() if k not in connected_keys}
            # upstream signatures (from already processed nodes)
            upstream_sigs: List[str] = []
            if out_node.connections and out_node.connections.inputs:
                for conns in out_node.connections.inputs.values():
                    if not conns:
                        continue
                    dep_id = conns[0].nodeId
                    upstream_sigs.append(self._signatures.get(dep_id, ""))
            func_hash = code_hash(method)
            material = {
                "controls": literal_controls,
                "upstream": sorted(upstream_sigs),
                "func": func_hash,
                "node_type": out_node.type,
            }
            sig = sha256_hex_bytes(stable_json_dumps(material).encode())
            self._signatures[nodeid] = sig
            self._code_hashes[nodeid] = func_hash

    # ---------- Graph / SCC utilities ----------
    def _build_graph(self, mapped_dict: Dict[str, OutNode]) -> tuple[Dict[str, List[str]], Dict[str, List[str]], Set[str]]:
        """Build adjacency and reverse-adjacency lists and detect self-loops.

        Returns (adj, rev_adj, self_loops).
        adj: edges u -> v if u feeds v.
        rev_adj: reverse edges v -> u.
        self_loops: nodes that directly depend on their own output.
        """
        adj: Dict[str, List[str]] = {nid: [] for nid in mapped_dict}
        rev_adj: Dict[str, List[str]] = {nid: [] for nid in mapped_dict}
        self_loops: Set[str] = set()
        for nid, node in mapped_dict.items():
            try:
                inputs = (node.connections.inputs or {}) if node.connections else {}
            except Exception:
                inputs = {}
            for conns in inputs.values():
                if not conns:
                    continue
                dep = conns[0].nodeId
                if dep == nid:
                    self_loops.add(nid)
                if dep in adj:
                    adj[dep].append(nid)
                if nid in rev_adj:
                    rev_adj[nid].append(dep)
        return adj, rev_adj, self_loops

    def _find_sccs(self, adj: Dict[str, List[str]]) -> tuple[List[Set[str]], Dict[str, int]]:
        """Tarjan's algorithm for strongly connected components.

        Returns (sccs, comp_id), where sccs is a list of node-id sets.
        """
        index = 0
        stack: List[str] = []
        on_stack: Set[str] = set()
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        sccs: List[Set[str]] = []

        def strongconnect(v: str):
            nonlocal index
            indices[v] = index
            lowlink[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)
            for w in adj.get(v, []):
                if w not in indices:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])
            if lowlink[v] == indices[v]:
                comp: Set[str] = set()
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    comp.add(w)
                    if w == v:
                        break
                sccs.append(comp)

        for v in adj.keys():
            if v not in indices:
                strongconnect(v)
        comp_id: Dict[str, int] = {}
        for i, comp in enumerate(sccs):
            for v in comp:
                comp_id[v] = i
        return sccs, comp_id

    def _build_component_dag(self, adj: Dict[str, List[str]], comp_id: Dict[str, int], sccs: List[Set[str]]) -> tuple[Dict[int, Set[int]], Dict[int, int]]:
        comp_adj: Dict[int, Set[int]] = {i: set() for i in range(len(sccs))}
        indegree: Dict[int, int] = {i: 0 for i in range(len(sccs))}
        for u, children in adj.items():
            cu = comp_id[u]
            for v in children:
                cv = comp_id[v]
                if cu != cv and cv not in comp_adj[cu]:
                    comp_adj[cu].add(cv)
                    indegree[cv] += 1
        return comp_adj, indegree

    def _topo_order_components(self, comp_adj: Dict[int, Set[int]], indegree: Dict[int, int]) -> List[int]:
        queue = [c for c, deg in indegree.items() if deg == 0]
        order: List[int] = []
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for child in comp_adj.get(cur, set()):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        # append leftovers if any (shouldn't happen if DAG)
        for cid in comp_adj.keys():
            if cid not in order:
                order.append(cid)
        return order

    def _is_cyclic_component(self, comp_nodes: Set[str], self_loops: Set[str]) -> bool:
        if len(comp_nodes) > 1:
            return True
        only = next(iter(comp_nodes))
        return only in self_loops

    def _build_literal_controls(self, out_node: OutNode) -> Dict[str, Any]:
        input_args: Dict[str, Any] = {}
        for key, values in (out_node.inputData or {}).items():
            if not values:
                continue
            if len(values) > 1:
                variable_value = values
            else:
                variable_value = next(iter(values.values()))
            if variable_value is None:
                continue
            input_args[key] = variable_value
        connected_keys = set((out_node.connections.inputs or {}).keys() if out_node.connections else [])
        return {k: v for k, v in (input_args or {}).items() if k not in connected_keys}

    async def _call_node_method_async(self, nodeid: str, out_node: OutNode, input_args: Dict[str, Any]) -> tuple[Any, Dict[str, Any]]:
        """Call a node's method with the provided inputs and return (result, result_mapped). No caching here."""
        method = self.flume_config.get_node(out_node.type).method
        if self.is_cancel_requested():
            raise asyncio.CancelledError()
        if inspect.iscoroutinefunction(method):
            call = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(method)
            coro = call(**input_args)
            cancel_wait = asyncio.create_task(self._cancel_event.wait()) if self._cancel_event else None
            task = asyncio.create_task(coro)
            done, pending = await asyncio.wait(
                {task, cancel_wait} if cancel_wait else {task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait and cancel_wait in done and self.is_cancel_requested():
                try:
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass
                except Exception:
                    pass
                raise asyncio.CancelledError()
            method_output = await task
        else:
            if (self.method or "sync").lower() == "async":
                result_q: mp.Queue = mp.Queue()
                proc = mp.Process(target=_ff_proc_call, args=(method, input_args, result_q))
                self._active_node_procs[nodeid] = proc
                proc.start()
                try:
                    while proc.is_alive():
                        if self.is_cancel_requested():
                            try:
                                proc.terminate()
                                proc.join(timeout=1)
                            except Exception:
                                pass
                            self._active_node_procs.pop(nodeid, None)
                            raise asyncio.CancelledError()
                        await asyncio.sleep(0.05)
                finally:
                    self._active_node_procs.pop(nodeid, None)
                if proc.exitcode and proc.exitcode != 0 and result_q.empty():
                    raise RuntimeError(f"Process exited with code {proc.exitcode}")
                method_output = result_q.get() if not result_q.empty() else None
            else:
                method_output = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(method)(**input_args)
        if not isinstance(method_output, tuple):
            method_output = (method_output,)
        output_args = [x.name for x in (self.flume_config.get_node(out_node.type).outputs or [])]
        result_mapped = {x: y for x, y in zip(output_args, method_output)}
        # Rebuild a scalar/tuple result consistent with output order
        if output_args:
            # keep tuple ordering
            result_ordered = tuple(result_mapped.get(x) for x in output_args)
            # if single output, collapse to scalar
            if len(result_ordered) == 1:
                result_value: Any = result_ordered[0]
            else:
                result_value = result_ordered
        else:
            result_value = None
        return result_value, result_mapped

    def _is_number(self, x: Any) -> bool:
        return isinstance(x, (int, float)) and not isinstance(x, bool)

    # ---------- Policy/delta/mix helpers ----------
    def _is_numpy_array(self, x: Any) -> bool:
        try:
            return np is not None and isinstance(x, np.ndarray)  # type: ignore[name-defined]
        except Exception:
            return False

    def _is_pandas_df(self, x: Any) -> bool:
        try:
            return pd is not None and isinstance(x, pd.DataFrame)  # type: ignore[name-defined]
        except Exception:
            return False

    def _df_is_all_numeric(self, df: Any) -> bool:
        try:
            if not self._is_pandas_df(df):
                return False
            import numpy as _np  # local guard
            return df.select_dtypes(include=[_np.number]).shape[1] == df.shape[1]
        except Exception:
            return False

    def _policy_kind(self, node_type: str, port: str, value: Any) -> str:
        # Override first
        try:
            override = self.scc_policy_overrides.get((node_type, port))
            if override:
                return override
        except Exception:
            pass
        # Auto detection
        if self._is_number(value):
            return "numeric"
        if self._is_numpy_array(value):
            return "array"
        if self.scc_df_numeric_as_array and self._df_is_all_numeric(value):
            return "df_numeric"
        return "equality"

    def _mix_value(self, kind: str, prev: Any, new: Any, r: float) -> Any:
        try:
            if kind == "numeric":
                return float(r) * (new if self._is_number(new) else float(new)) + (1.0 - float(r)) * (prev if self._is_number(prev) else float(prev))
            if kind == "array" and self._is_numpy_array(prev) and self._is_numpy_array(new):
                return (float(r) * new) + ((1.0 - float(r)) * prev)
            if kind == "df_numeric" and self._is_pandas_df(prev) and self._is_pandas_df(new):
                if self.scc_df_align == "allow_reindex":
                    new_al = new.reindex(index=prev.index, columns=prev.columns)
                else:
                    if not (prev.index.equals(new.index) and prev.columns.equals(new.columns)):
                        return new  # cannot mix; fall back to new
                    new_al = new
                if np is None:
                    return new_al
                arr = float(r) * new_al.to_numpy() + (1.0 - float(r)) * prev.to_numpy()
                return type(new_al)(arr, index=prev.index, columns=prev.columns)
        except Exception:
            pass
        # equality / fallback: use new
        return new

    def _delta_value(self, kind: str, a: Any, b: Any) -> float:
        try:
            if kind == "numeric":
                return abs(float(a) - float(b))
            if kind == "array" and self._is_numpy_array(a) and self._is_numpy_array(b):
                if a.shape != b.shape:
                    return float("inf")
                return float(np.nanmax(np.abs(b - a))) if np is not None else float("inf")
            if kind == "df_numeric" and self._is_pandas_df(a) and self._is_pandas_df(b):
                if self.scc_df_align == "allow_reindex":
                    b_al = b.reindex(index=a.index, columns=a.columns)
                else:
                    if not (a.index.equals(b.index) and a.columns.equals(b.columns)):
                        return float("inf")
                    b_al = b
                if np is None:
                    # Fallback: strict equality only
                    return 0.0 if a.equals(b_al) else float("inf")
                return float(np.nanmax(np.abs(b_al.to_numpy() - a.to_numpy())))
        except Exception:
            pass
        # equality fallback
        try:
            return 0.0 if a == b else float("inf")
        except Exception:
            return float("inf")

    def _scale_value(self, kind: str, v: Any) -> Optional[float]:
        try:
            if kind == "numeric":
                return abs(float(v))
            if kind == "array" and self._is_numpy_array(v):
                return float(np.nanmax(np.abs(v))) if np is not None else None
            if kind == "df_numeric" and self._is_pandas_df(v):
                if np is None:
                    return None
                return float(np.nanmax(np.abs(v.to_numpy())))
        except Exception:
            pass
        return None

    def _wegstein_mix_value(self, kind: str, xkm1: Any, xk: Any, gkm1: Any, gk: Any, qmin: Optional[float], qmax: Optional[float]) -> Optional[Any]:
        eps = 1e-12
        try:
            if kind == "numeric" and self._is_number(xk) and self._is_number(xkm1) and self._is_number(gk) and self._is_number(gkm1):
                dx = float(xk) - float(xkm1)
                if abs(dx) <= eps:
                    return None
                dg = float(gk) - float(gkm1)
                denom = (1.0 - (dg / dx))
                if abs(denom) <= eps:
                    return None
                q = 1.0 / denom
                if qmin is not None:
                    q = max(qmin, q)
                if qmax is not None:
                    q = min(qmax, q)
                return q * float(gk) + (1.0 - q) * float(xk)
            if kind == "array" and self._is_numpy_array(xk) and self._is_numpy_array(xkm1) and self._is_numpy_array(gk) and self._is_numpy_array(gkm1) and np is not None:
                dx = (xk - xkm1)
                safe = np.abs(dx) > eps
                if not np.any(safe):
                    return None
                dg = (gk - gkm1)
                a = np.zeros_like(dx, dtype=float)
                a[safe] = dg[safe] / dx[safe]
                denom = (1.0 - a)
                safe2 = np.abs(denom) > eps
                if not np.any(safe2):
                    return None
                q = np.zeros_like(dx, dtype=float)
                q[safe2] = 1.0 / denom[safe2]
                if qmin is not None:
                    q = np.maximum(qmin, q)
                if qmax is not None:
                    q = np.minimum(qmax, q)
                return (q * gk) + ((1.0 - q) * xk)
            if kind == "df_numeric" and self._is_pandas_df(xk) and self._is_pandas_df(xkm1) and self._is_pandas_df(gk) and self._is_pandas_df(gkm1) and np is not None:
                # align if allowed/needed
                if self.scc_df_align == "allow_reindex":
                    xkm1_al = xkm1.reindex(index=xk.index, columns=xk.columns)
                    gkm1_al = gkm1.reindex(index=gk.index, columns=gk.columns)
                    gk_al = gk.reindex(index=xk.index, columns=xk.columns)
                else:
                    if not (xk.index.equals(xkm1.index) and xk.columns.equals(xkm1.columns) and gk.index.equals(xk.index) and gk.columns.equals(xk.columns)):
                        return None
                    xkm1_al, gkm1_al, gk_al = xkm1, gkm1, gk
                mixed_arr = self._wegstein_mix_value("array", xkm1_al.to_numpy(), xk.to_numpy(), gkm1_al.to_numpy(), gk_al.to_numpy(), qmin, qmax)
                if mixed_arr is None:
                    return None
                return type(xk)(mixed_arr, index=xk.index, columns=xk.columns)
        except Exception:
            return None
        return None

    def _base_tol_for(self, node_type: str, port: str, value: Any) -> float:
        try:
            if (node_type, port) in self.scc_port_tolerance:
                return float(self.scc_port_tolerance[(node_type, port)])
        except Exception:
            pass
        try:
            t = type(value)
            if t in self.scc_type_tolerance:
                return float(self.scc_type_tolerance[t])
        except Exception:
            pass
        return float(self.scc_tolerance or 0.0)

    def _effective_tol(self, kind: str, base_tol: float, prev: Any, cur: Any) -> float:
        rt = float(self.scc_rtol or 0.0)
        at = float(self.scc_atol or 0.0)
        if (rt > 0.0 or at > 0.0):
            scale_prev = self._scale_value(kind, prev)
            scale_cur = self._scale_value(kind, cur)
            if scale_prev is not None or scale_cur is not None:
                m = max(scale_prev or 0.0, scale_cur or 0.0)
                denom = at + rt * m
                if denom > 0.0:
                    return max(base_tol, denom)
        return base_tol

    def _mix(self, prev: Any, new: Any, r: float) -> Any:
        try:
            if self._is_number(prev) and self._is_number(new):
                return r * float(new) + (1.0 - r) * float(prev)
        except Exception:
            pass
        return new

    def _delta(self, a: Any, b: Any) -> float:
        try:
            if self._is_number(a) and self._is_number(b):
                return abs(float(a) - float(b))
        except Exception:
            pass
        # non-numeric: 0 if equal, inf if not
        return 0.0 if a == b else float("inf")

    async def _solve_scc_async(self, comp_nodes: Set[str], mapped_dict: Dict[str, OutNode], rev_adj: Dict[str, List[str]], self_loops: Set[str]):
        """Fixed-point solve for a cyclic component. Publishes results and run_events on success/failure."""
        # Await external upstreams
        external_upstreams: Set[str] = set()
        for nid in comp_nodes:
            for up in rev_adj.get(nid, []):
                if up not in comp_nodes:
                    external_upstreams.add(up)
        # Wait for upstream run_events
        for up in external_upstreams:
            if self.is_cancel_requested():
                break
            try:
                await mapped_dict[up].run_event.wait()
            except Exception:
                pass
        if self.is_cancel_requested():
            # mark canceled
            for nid in comp_nodes:
                try:
                    mapped_dict[nid].status = "canceled"
                    mapped_dict[nid].run_event.set()
                except Exception:
                    pass
            return
        # Determine internal required outputs per node
        required_ports: Dict[str, Set[str]] = {nid: set() for nid in comp_nodes}
        # Also index edge schema for per-edge tolerance resolution
        edge_schema_map: Dict[tuple[str, str], List[tuple[str, str, str, str]]] = {}
        for dst in comp_nodes:
            node = mapped_dict[dst]
            inputs = (node.connections.inputs or {}) if node.connections else {}
            for in_key, conns in inputs.items():
                if not conns:
                    continue
                oc = conns[0]
                dep = oc.nodeId
                if dep in comp_nodes:
                    required_ports.setdefault(dep, set()).add(oc.portName)
                    src_type = mapped_dict[dep].type
                    dst_type = node.type
                    k = (dep, oc.portName)
                    edge_schema_map.setdefault(k, []).append((src_type, oc.portName, dst_type, in_key))
        # Initial guesses for internal edges
        prev_outputs: Dict[tuple[str, str], Any] = {}
        for nid, ports in required_ports.items():
            for port in ports:
                if self.scc_initial and nid in self.scc_initial and port in self.scc_initial[nid]:
                    prev_outputs[(nid, port)] = self.scc_initial[nid][port]
                else:
                    # numeric fallback
                    prev_outputs[(nid, port)] = 0.0
        # Iteration
        max_iters = max(int(self.scc_max_iters or 0), 1)
        tol = float(self.scc_tolerance or 0.0)
        relax = float(self.scc_relaxation or 1.0)
        solver = (self.scc_solver or "jacobi").lower()
        # History for advanced solvers
        prev_prev_outputs: Optional[Dict[tuple[str, str], Any]] = None
        prev_prev_new_outputs: Optional[Dict[tuple[str, str], Any]] = None
        # Track last complete node results for publishing
        last_node_results: Dict[str, Dict[str, Any]] = {nid: {} for nid in comp_nodes}
        last_deltas: Dict[tuple[str, str], float] = {}
        for it in range(max_iters):
            if self.is_cancel_requested():
                for nid in comp_nodes:
                    mapped_dict[nid].status = "canceled"
                    mapped_dict[nid].run_event.set()
                return
            new_outputs: Dict[tuple[str, str], Any] = {}
            # Evaluate each node once this iteration
            for nid in comp_nodes:
                out_node = mapped_dict[nid]
                try:
                    out_node.status = "started" if it == 0 else out_node.status or "started"
                except Exception:
                    pass
                # build inputs
                input_args: Dict[str, Any] = {}
                # literal controls
                input_args.update(self._build_literal_controls(out_node))
                # connected inputs
                inputs = (out_node.connections.inputs or {}) if out_node.connections else {}
                for key, conns in inputs.items():
                    if not conns:
                        continue
                    dep = conns[0].nodeId
                    src_port = conns[0].portName
                    if dep in comp_nodes:
                        # internal from previous iteration
                        if (dep, src_port) not in prev_outputs:
                            # guard missing guess
                            raise ValueError(f"Missing initial guess for internal edge {dep}->{nid}:{src_port}")
                        input_args[key] = prev_outputs[(dep, src_port)]
                    else:
                        # external from already completed upstream
                        dep_node = mapped_dict[dep]
                        if hasattr(dep_node, "error") and dep_node.error:
                            raise ErrorInDependentNode(f"Error in node {dep}")
                        input_args[key] = dep_node.result_mapped[src_port]
                # call method
                try:
                    result_value, result_mapped = await self._call_node_method_async(nid, out_node, input_args)
                except asyncio.CancelledError:
                    for xid in comp_nodes:
                        mapped_dict[xid].status = "canceled"
                        mapped_dict[xid].run_event.set()
                    return
                except Exception as e:
                    out_node.error = e
                    out_node.status = "failed"
                    # propagate failure to component
                    for xid in comp_nodes:
                        if xid != nid:
                            mapped_dict[xid].status = "failed"
                        mapped_dict[xid].run_event.set()
                    return
                # track outputs (only the required ones matter for convergence)
                last_node_results[nid] = result_mapped
                for port, val in (result_mapped or {}).items():
                    if port in required_ports.get(nid, set()):
                        new_outputs[(nid, port)] = val
            # Convergence and relaxation (per-port policy)
            mixed_outputs: Dict[tuple[str, str], Any] = {}
            max_delta_abs = 0.0
            converged_all = True
            qmin = self.scc_wegstein_qmin
            qmax = self.scc_wegstein_qmax
            for key, gk in new_outputs.items():
                nid, port = key
                xk = prev_outputs.get(key, gk)
                node_type = mapped_dict[nid].type
                kind = self._policy_kind(node_type, port, xk if xk is not None else gk)
                mixed_val = None
                if solver == "wegstein" and prev_prev_outputs is not None and prev_prev_new_outputs is not None:
                    xkm1 = prev_prev_outputs.get(key, xk)
                    gkm1 = prev_prev_new_outputs.get(key, gk)
                    attempt = self._wegstein_mix_value(kind, xkm1, xk, gkm1, gk, qmin, qmax)
                    mixed_val = attempt if attempt is not None else None
                if mixed_val is None:
                    mixed_val = self._mix_value(kind, xk, gk, relax)
                mixed_outputs[key] = mixed_val
                d_abs = self._delta_value(kind, xk, mixed_val)
                # Resolve base tolerance (port/type/global)
                base_tol = self._base_tol_for(node_type, port, xk)
                # Apply per-edge overrides if present (choose strictest among matches)
                try:
                    schema_keys = edge_schema_map.get((nid, port), [])
                    if schema_keys and self.scc_edge_tolerance:
                        matches = [self.scc_edge_tolerance[s] for s in schema_keys if s in self.scc_edge_tolerance]
                        if matches:
                            base_tol = min(matches)
                except Exception:
                    pass
                eff_tol = self._effective_tol(kind, base_tol, xk, mixed_val)
                last_deltas[key] = d_abs
                if d_abs > eff_tol:
                    converged_all = False
                if d_abs > max_delta_abs:
                    max_delta_abs = d_abs
            # shift history then update
            prev_prev_outputs = prev_outputs
            prev_prev_new_outputs = new_outputs
            prev_outputs = mixed_outputs
            if converged_all:
                break
        else:
            # did not converge: enrich error with diagnostics
            # Sort worst ports by delta (descending); include up to 5 samples
            try:
                worst_items = sorted(last_deltas.items(), key=lambda kv: kv[1] if kv[1] is not None else float("-inf"), reverse=True)
            except Exception:
                worst_items = list(last_deltas.items())
            worst_samples = []
            for (nid, port), d in worst_items[:5]:
                try:
                    d_str = ("inf" if d == float("inf") else f"{d:.6g}")
                except Exception:
                    d_str = str(d)
                worst_samples.append(f"{nid}.{port}={d_str}")
            non_numeric_ports = [f"{nid}.{port}" for (nid, port), d in last_deltas.items() if d == float("inf")]
            diag = ""
            if worst_samples:
                diag += f" worst_ports=[{', '.join(worst_samples)}]"
            if non_numeric_ports:
                diag += f" non_numeric_unstable_ports={non_numeric_ports}. Consider supplying scc_initial for these ports."
            err_msg = (
                f"Cyclic component did not converge within max iterations (iters={max_iters}, max_delta={max_delta_abs:.6g})." + diag
            )
            for nid in comp_nodes:
                mapped_dict[nid].status = "failed"
                mapped_dict[nid].error = RuntimeError(err_msg)
                mapped_dict[nid].run_event.set()
            return
        # Publish final results: recompute each node once more with the converged
        # mixed internal values so all outputs (including non-cyclic ports)
        # reflect the final fixed-point state.
        for nid in comp_nodes:
            out_node = mapped_dict[nid]
            # Build final inputs using mixed values for internal deps and
            # upstream mapped outputs for external deps
            try:
                input_args: Dict[str, Any] = {}
                input_args.update(self._build_literal_controls(out_node))
                inputs = (out_node.connections.inputs or {}) if out_node.connections else {}
                for key, conns in inputs.items():
                    if not conns:
                        continue
                    dep = conns[0].nodeId
                    src_port = conns[0].portName
                    if dep in comp_nodes:
                        input_args[key] = prev_outputs[(dep, src_port)]
                    else:
                        dep_node = mapped_dict[dep]
                        if hasattr(dep_node, "error") and dep_node.error:
                            raise ErrorInDependentNode(f"Error in node {dep}")
                        input_args[key] = dep_node.result_mapped[src_port]
                # Re-evaluate once at the mixed fixed-point
                result_value, result_mapped = await self._call_node_method_async(nid, out_node, input_args)
            except asyncio.CancelledError:
                for xid in comp_nodes:
                    mapped_dict[xid].status = "canceled"
                    mapped_dict[xid].run_event.set()
                return
            except Exception as e:
                # Treat failure as component failure
                out_node.error = e
                out_node.status = "failed"
                for xid in comp_nodes:
                    if xid != nid:
                        mapped_dict[xid].status = "failed"
                    mapped_dict[xid].run_event.set()
                return

            # Set final results from the recomputation
            out_node.result = result_value
            out_node.result_mapped = result_mapped
            out_node.status = "finished"

            # optional caching: final-only, ignore internal upstream signatures
            if self.cache and self.cache_enabled and (self.scc_cache_policy or "").lower() == "final_only":
                try:
                    method = self.flume_config.get_node(out_node.type).method
                    # Recompute signature using only literal controls and external upstream sigs
                    lit = self._build_literal_controls(out_node)
                    sig, func_hash = self._build_signature(nid, out_node, lit, mapped_dict, method, ignore_node_ids=comp_nodes)
                    self.cache.put(nid, sig, out_node.result, func_hash)
                    self._signatures[nid] = sig
                except Exception:
                    pass
            out_node.run_event.set()

    async def run_async(self, mapped_dict) -> Dict[str, OutNode]:
        """Run the flow asynchronously. If cycles are enabled, use SCC-based scheduler and solver."""
        # initialize run_events
        for _nid, node in mapped_dict.items():
            node.run_event = asyncio.Event()
        # Build SCCs
        adj, rev_adj, self_loops = self._build_graph(mapped_dict)
        sccs, comp_id = self._find_sccs(adj)
        has_cycle = any(self._is_cyclic_component(comp, self_loops) for comp in sccs)
        if not has_cycle:
            # fall back to original strategy: spawn all and wait
            nodes_evaluted = []
            for nodeid, _node in mapped_dict.items():
                nodes_evaluted.append(self.evaluate_node_async(nodeid, mapped_dict))
            await asyncio.gather(*nodes_evaluted)
            return mapped_dict
        if not self.enable_cycles:
            raise ValueError("Cycle detected in graph. Enable cycles with enable_cycles=True to solve cyclic components.")
        comp_adj, indegree = self._build_component_dag(adj, comp_id, sccs)
        comp_order = self._topo_order_components(comp_adj, indegree)
        tasks: List[asyncio.Task] = []
        for cid in comp_order:
            comp_nodes = sccs[cid]
            if self._is_cyclic_component(comp_nodes, self_loops):
                await self._solve_scc_async(comp_nodes, mapped_dict, rev_adj, self_loops)
            else:
                for nid in comp_nodes:
                    tasks.append(asyncio.create_task(self.evaluate_node_async(nid, mapped_dict)))
        if tasks:
            await asyncio.gather(*tasks)
        return mapped_dict

    async def evaluate_node_async(self, nodeid: str, mapped_dict: dict):
        """Evaluate the node and return the result"""
        out_node = mapped_dict[nodeid]
        out_node.status = "started"
        # Removed debug logging
        if hasattr(out_node, "result") and out_node.result:
            return
        config_node = self.flume_config.get_node(out_node.type)
        # method = validate_arguments(config_node.method)
        method = config_node.method
        
        out_node.result = None
        out_node.result_mapped = {}
        input_args = {}
        # Early cancel check
        if self.is_cancel_requested():
            out_node.status = "canceled"
            out_node.run_event.set()
            return
        for key, values in (out_node.inputData or {}).items():
            if not values:
                continue
            # If there are more than one control in this port return the dict
            if len(values) > 1:
                variable_value = values
            else:
                # else return the value of the first item in the dict
                # TODO: when flume implements option to have multiple inputs
                # address it here.
                variable_value = next(iter(values.values()))
            if variable_value is None:
                continue  # This is null coming from react for unset controls
            input_args[key] = variable_value
        if out_node.connections and out_node.connections.inputs:
            inputs_map = out_node.connections.inputs or {}
        else:
            inputs_map = {}
        for key, connections in inputs_map.items():
            # Now only one connection is supported by flume.
            # Hence using the first one
            dependent_nodeid = connections[0].nodeId
            dependent_node = mapped_dict[dependent_nodeid]
            out_node.status = "deferred"
            # Wait for dependency or cancel
            if self.is_cancel_requested():
                out_node.status = "canceled"
                out_node.run_event.set()
                return
            await dependent_node.run_event.wait()
            if self.is_cancel_requested():
                out_node.status = "canceled"
                out_node.run_event.set()
                return
            if hasattr(dependent_node, "error") and dependent_node.error:
                out_node.error = ErrorInDependentNode(
                    f"Error in node {dependent_node.id}"
                )
                out_node.status = "failed"
                out_node.run_event.set()
                return
            input_args[key] = dependent_node.result_mapped[connections[0].portName]
        # Removed debug logging
        # At this point inputs are fully resolved; try cache before executing
        if self.cache and self.cache_enabled:
            signature, func_hash = self._build_signature(nodeid, out_node, input_args, mapped_dict, method)
            cached = self.cache.get_if_fresh(nodeid, signature)
            if cached is not None:
                # Removed debug logging
                out_node.result = cached
                # map to outputs as usual
                method_output = out_node.result
                if not isinstance(method_output, tuple):
                    method_output = (method_output,)
                output_args = [
                    x.name for x in (self.flume_config.get_node(out_node.type).outputs or [])
                ]
                out_node.result_mapped = {x: y for x, y in zip(output_args, method_output)}
                out_node.status = "finished"
                self._signatures[nodeid] = signature
                try:
                    logger.info(f"ff: run_event.set node={nodeid}")
                except Exception:
                    pass
                out_node.run_event.set()
                return

        if self.is_cancel_requested():
            out_node.status = "canceled"
            out_node.run_event.set()
            return
        if inspect.iscoroutinefunction(method):
            try:
                call = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(method)
                coro = call(**input_args)
                cancel_wait = asyncio.create_task(self._cancel_event.wait()) if self._cancel_event else None
                task = asyncio.create_task(coro)
                done, pending = await asyncio.wait(
                    {task, cancel_wait} if cancel_wait else {task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_wait and cancel_wait in done and self.is_cancel_requested():
                    try:
                        task.cancel()
                        try:
                            await task
                        except Exception:
                            pass
                    except Exception:
                        pass
                    out_node.status = "canceled"
                    out_node.run_event.set()
                    return
                method_output = await task
            except Exception as e:
                out_node.error = e
                out_node.status = "failed"
        else:
            try:
                if (self.method or "sync").lower() == "async":
                    result_q: mp.Queue = mp.Queue()
                    proc = mp.Process(target=_ff_proc_call, args=(method, input_args, result_q))
                    self._active_node_procs[nodeid] = proc
                    t_start = time.perf_counter()
                    proc.start()
                    last_log = t_start
                    # Poll for cancel while process is running
                    while proc.is_alive():
                        if self.is_cancel_requested():
                            try:
                                proc.terminate()
                                proc.join(timeout=1)
                            except Exception:
                                pass
                            out_node.status = "canceled"
                            self._active_node_procs.pop(nodeid, None)
                            out_node.run_event.set()
                            return
                        now = time.perf_counter()
                        if (now - last_log) > 0.5:
                            last_log = now
                        await asyncio.sleep(0.05)
                    self._active_node_procs.pop(nodeid, None)
                    if proc.exitcode and proc.exitcode != 0 and result_q.empty():
                        out_node.error = RuntimeError(f"Process exited with code {proc.exitcode}")
                        out_node.status = "failed"
                        out_node.run_event.set()
                        return
                    method_output = result_q.get() if not result_q.empty() else None
                else:
                    method_output = validate_call(
                        config=ConfigDict(arbitrary_types_allowed=True)
                    )(method)(**input_args)
            except Exception as e:
                out_node.error = e
                out_node.status = "failed"
        if hasattr(out_node, "error") and out_node.error:
            out_node.run_event.set()
            return
        out_node.result = method_output

        if not isinstance(method_output, tuple):
            method_output = (method_output,)
        output_args = [
            x.name for x in (self.flume_config.get_node(out_node.type).outputs or [])
        ]
        out_node.result_mapped = {x: y for x, y in zip(output_args, method_output)}
        out_node.status = "finished"
        if self.cache and self.cache_enabled:
            signature, func_hash = self._build_signature(nodeid, out_node, input_args, mapped_dict, method)
            try:
                blob_dig = self.cache.put(nodeid, signature, out_node.result, func_hash)
                self._signatures[nodeid] = signature
            except Exception:
                pass
        out_node.run_event.set()


    async def run_distributed(
        self, mapped_dict: Dict[str, OutNode]
    ) -> Dict[str, OutNode]:
            """Run the flow using python rq"""
            # Precompute signatures for all nodes in a stable order so the worker can short-circuit.
            if self.cache_enabled:
                try:
                    self._precompute_signatures(mapped_dict)
                except Exception:
                    # Fall back silently if precompute fails; worker will still run jobs
                    pass
            # Detect cycles and gate distributed mode (not supported yet)
            try:
                adj, _rev, self_loops = self._build_graph(mapped_dict)
                sccs, _cid = self._find_sccs(adj)
                has_cycle = any(self._is_cyclic_component(comp, self_loops) for comp in sccs)
            except Exception:
                has_cycle = False
            if has_cycle:
                raise QueueError("Cyclic graphs are not yet supported in distributed mode. Run with method='sync'/'async' and enable_cycles=True.")
            nodes_evaluted = []
            for nodeid, node in mapped_dict.items():
                # Storing the lock in the node itself so that dependent nodes
                # dont start a new job.
                if not hasattr(node, "run_event") or not node.run_event:
                    node.run_event = asyncio.Event()
                nodes_evaluted.append(self.submit_node_job(nodeid, mapped_dict))
            await asyncio.gather(*nodes_evaluted)
            return mapped_dict

    async def run_distributed_same_worker(self, out_dict: dict) -> Dict[str, OutNode]:
            """Run the whole flow in the same worker using python-rq"""
            if (
                not hasattr(self, "queue")
                or not self.queue
                or not isinstance(self.queue, NodeQueue)
            ):
                raise QueueError(
                    "If the method is distributed, the `default_queue` argument cannot be empty."
                    " It should be an instance of NodeQueue."
                )
            # Best-effort cycle detection on plain dict to avoid enqueueing unsupported flows
            try:
                data = out_dict or {}
                indegree: Dict[str, int] = {nid: 0 for nid in data.keys()}
                children: Dict[str, List[str]] = {nid: [] for nid in data.keys()}
                for nid, node in data.items():
                    conns = ((node or {}).get("connections") or {}).get("inputs") or {}
                    for arr in conns.values():
                        if not arr:
                            continue
                        dep = (arr[0] or {}).get("nodeId")
                        if dep in indegree:
                            indegree[nid] += 1
                            children[dep].append(nid)
                q = [k for k, deg in indegree.items() if deg == 0]
                seen = 0
                while q:
                    cur = q.pop(0)
                    seen += 1
                    for ch in children.get(cur, []):
                        indegree[ch] -= 1
                        if indegree[ch] == 0:
                            q.append(ch)
                if seen != len(indegree):
                    raise QueueError("Cyclic graphs are not yet supported in distributed mode. Run with method='sync'/'async' and enable_cycles=True.")
            except QueueError:
                raise
            except Exception:
                pass
            return self.queue.enqueue(
                run_in_same_worker,
                kwargs={
                    "flume_config": self.flume_config,
                    "out_dict": out_dict,
                },
            )

    async def submit_node_job(self, nodeid: str, mapped_dict: dict):
            """Enqueue the node in the queue"""
            node = mapped_dict[nodeid]
            if hasattr(node, "job_id") and node.job_id:
                try:
                    node.run_event.set()
                except Exception:
                    pass
                return
            method = self.flume_config.get_node(node.type).method
            input_args = {}
            for key, values in node.inputData.items():
                if not values:
                    continue
                # TODO: when flume implements multiple inputs for a node, address
                # it here
                variable_value = next(iter(values.values()))
                if variable_value is None:
                    continue  # This is null coming from react
                input_args[key] = variable_value
            dependents = []
            for key, connections in node.connections.inputs.items():
                # Now only one connection is supported by flume.
                # Hence using the first one
                dependent_nodeid = connections[0].nodeId
                dependent_node = mapped_dict[dependent_nodeid]
                await dependent_node.run_event.wait()
                connections[0].job_id = dependent_node.job_id
                dependents.append(dependent_node)

            if hasattr(node, "settings") and isinstance(node.settings, dict):
                job_kwargs = copy(node.settings)
            else:
                job_kwargs = {}
            job_queue = job_kwargs.pop("queue", self.queue)

            meta_method = self.meta_map.get(method, default_meta_method)

            node.job = meta_method(
                method,
                job_queue,
                job_runner=self,
                input_args=input_args,
                node=node,
                dependents=dependents,
                job_kwargs=job_kwargs,
            )
            node.job_id = node.job.id
            # Setting the current job's output connection job id
            # This may not be required
            if node.connections.outputs:
                for key, conns in node.connections.outputs.items():
                    for conn in conns:
                        conn.job_id = node.job.id
            node.run_event.set()

    def dict(self, mapped_dict: Dict[str, OutNode], *args, **kwargs) -> dict:
            ret_dict = {}
            for nodeid, node in mapped_dict.items():
                ret_dict[nodeid] = node.model_dump(*args, **kwargs)
            return ret_dict


# Helper for process-based execution (must be top-level picklable)
def _ff_proc_call(func: Callable, kwargs: dict, result_q: mp.Queue):
    try:
        call = validate_call(config=ConfigDict(arbitrary_types_allowed=True))(func)
        res = call(**(kwargs or {}))
    except Exception:
        res = func(**(kwargs or {}))
    try:
        result_q.put(res)
    except Exception:
        # If result not picklable, put None to avoid deadlock
        try:
            result_q.put(None)
        except Exception:
            pass

    
