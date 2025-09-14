from __future__ import annotations
import asyncio
import inspect
from copy import copy, deepcopy
from typing import Any, Callable, Dict, List, Optional
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

    def _build_signature(self, nodeid: str, out_node: OutNode, input_args: dict, mapped_dict: dict, method: Callable) -> tuple[str, str]:
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

    async def run_async(self, mapped_dict) -> Dict[str, OutNode]:
        """Run the flow asynchronously"""
        # Removed debug logging
        nodes_evaluted = []
        for nodeid, node in mapped_dict.items():
            # Storing the lock in the node itself so that dependent nodes
            # dont start a new job.
            node.run_event = asyncio.Event()
            nodes_evaluted.append(self.evaluate_node_async(nodeid, mapped_dict))
        await asyncio.gather(*nodes_evaluted)
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
