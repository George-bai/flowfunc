import math
import asyncio
import pytest

from flowfunc.config import Config
from flowfunc.jobrunner import JobRunner
from tests.methods import (
    recycle_affine,
    affine_async,
    identity_str,
)


def make_outnode(id, type_name, inputs_map, outputs_map=None, input_data=None):
    return {
        "id": id,
        "x": 0,
        "y": 0,
        "type": type_name,
        "width": 200,
        "connections": {
            "inputs": inputs_map or {},
            "outputs": outputs_map or {},
        },
        "inputData": input_data or {},
    }


def test_cancel_mid_iteration():
    async def _go():
        # Self-loop with async function to allow cancel during iteration
        nodes = {
            "A": make_outnode(
                "A",
                "tests.methods.affine_async",
                inputs_map={"x": [{"nodeId": "A", "portName": "result"}]},
                input_data={"a": {"a": 0.9}, "c": {"a": 1.0}},
            )
        }
        config = Config.from_function_list([affine_async])
        runner = JobRunner(
            config,
            method="async",
            enable_cycles=True,
            scc_tolerance=1e-9,
            scc_max_iters=500,
        )
        # Start and cancel shortly after
        task = asyncio.create_task(runner.run(nodes))
        await asyncio.sleep(0.05)
        runner.request_cancel(runner.run_id)
        return await task

    result = asyncio.run(_go())
    assert result["A"].status == "canceled"


def test_non_numeric_internal_with_initial():
    # Non-numeric self-loop stabilized via scc_initial
    nodes = {
        "A": make_outnode(
            "A",
            "tests.methods.identity_str",
            inputs_map={"e": [{"nodeId": "A", "portName": "result"}]},
        )
    }
    config = Config.from_function_list([identity_str])
    runner = JobRunner(
        config,
        method="sync",
        enable_cycles=True,
        scc_tolerance=1e-9,
        scc_max_iters=50,
        scc_initial={"A": {"result": "seed"}},
    )
    result = runner.run(nodes)
    assert result["A"].status == "finished"
    assert result["A"].result == "seed"


def test_wegstein_clamp_stability_self_loop():
    # High contraction factor; Wegstein with clamping should converge
    nodes = {
        "A": make_outnode(
            "A",
            "tests.methods.recycle_affine",
            inputs_map={"x": [{"nodeId": "A", "portName": "result"}]},
            input_data={"a": {"a": 0.99}, "c": {"a": 1.0}},
        )
    }
    config = Config.from_function_list([recycle_affine])
    runner = JobRunner(
        config,
        method="sync",
        enable_cycles=True,
        scc_solver="wegstein",
        scc_tolerance=1e-8,
        scc_max_iters=1000,
    )
    result = runner.run(nodes)
    expected = 1.0 / (1.0 - 0.99)
    assert result["A"].status == "finished"
    assert math.isfinite(result["A"].result)
    assert abs(result["A"].result - expected) < 1e-4


def test_wegstein_two_iteration_requirement():
    # With max_iters=1, not enough iterations to converge a moderate contraction
    nodes = {
        "A": make_outnode(
            "A",
            "tests.methods.recycle_affine",
            inputs_map={"x": [{"nodeId": "A", "portName": "result"}]},
            input_data={"a": {"a": 0.5}, "c": {"a": 1.0}},
        )
    }
    config = Config.from_function_list([recycle_affine])
    # First, too few iterations
    runner1 = JobRunner(
        config,
        method="sync",
        enable_cycles=True,
        scc_solver="wegstein",
        scc_tolerance=1e-9,
        scc_max_iters=1,
    )
    res1 = runner1.run(nodes)
    assert res1["A"].status == "failed"
    assert "iters=1" in str(res1["A"].error)

    # Now enough iterations
    runner2 = JobRunner(
        config,
        method="sync",
        enable_cycles=True,
        scc_solver="wegstein",
        scc_tolerance=1e-9,
        scc_max_iters=50,
    )
    res2 = runner2.run(nodes)
    assert res2["A"].status == "finished"
    expected = 1.0 / (1.0 - 0.5)
    assert abs(res2["A"].result - expected) < 1e-6
