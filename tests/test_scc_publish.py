import math
from flowfunc.config import Config
from flowfunc.jobrunner import JobRunner
from tests.methods import recycle_a_ext, recycle_b, identity


def make_outnode(id, type_name, x, y, inputs_map, outputs_map, input_data):
    return {
        "id": id,
        "x": x,
        "y": y,
        "type": type_name,
        "width": 200,
        "connections": {
            "inputs": inputs_map,
            "outputs": outputs_map,
        },
        "inputData": input_data,
    }


def test_scc_relaxation_recompute_publishes_consistent_external_outputs():
    # Three-node graph with a 2-node cycle (A <-> B) and an external consumer C.
    # A returns (cycle_output, external_output). Only cycle_output participates
    # in the SCC; external_output feeds downstream C. With relaxation, the final
    # publish must recompute so that external_output is consistent with A's
    # published cycle_output.
    nodes = {}
    nodes["A"] = make_outnode(
        id="A",
        type_name="tests.methods.recycle_a_ext",
        x=0,
        y=0,
        inputs_map={
            "x": [{"nodeId": "B", "portName": "result"}],
        },
        outputs_map={
            "result_0": [{"nodeId": "B", "portName": "y"}],
            "result_1": [{"nodeId": "C", "portName": "e"}],
        },
        input_data={
            "a": {"a": 0.4},
            "c": {"a": 1.0},
        },
    )
    nodes["B"] = make_outnode(
        id="B",
        type_name="tests.methods.recycle_b",
        x=0,
        y=0,
        inputs_map={
            "y": [{"nodeId": "A", "portName": "result_0"}],
        },
        outputs_map={
            "result": [{"nodeId": "A", "portName": "x"}],
        },
        input_data={
            "b": {"a": 0.5},
            "d": {"a": 2.0},
        },
    )
    nodes["C"] = make_outnode(
        id="C",
        type_name="tests.methods.identity",
        x=0,
        y=0,
        inputs_map={
            "e": [{"nodeId": "A", "portName": "result_1"}],
        },
        outputs_map={},
        input_data={},
    )

    config = Config.from_function_list([recycle_a_ext, recycle_b, identity])
    # Use relaxation and a tolerance that stops before exact fixed point so
    # stale publish would be observable if recompute didn't happen.
    runner = JobRunner(
        config,
        method="sync",
        enable_cycles=True,
        scc_tolerance=0.7,
        scc_relaxation=0.5,
        scc_max_iters=50,
    )
    result = runner.run(nodes)

    a0 = result["A"].result_mapped["result_0"]
    a1 = result["A"].result_mapped["result_1"]
    c_out = result["C"].result

    assert result["A"].status == "finished"
    assert result["B"].status == "finished"
    assert result["C"].status == "finished"
    # External output must be consistent with the final published cycle output
    # for node A (result_1 = 2 * result_0)
    assert math.isfinite(float(a0)) and math.isfinite(float(a1))
    assert abs(a1 - (2.0 * a0)) < 1e-9
    # Downstream consumer must see the same external output
    assert abs(c_out - a1) < 1e-12


def test_scc_wegstein_recompute_publishes_consistent_external_outputs():
    nodes = {}
    nodes["A"] = make_outnode(
        id="A",
        type_name="tests.methods.recycle_a_ext",
        x=0,
        y=0,
        inputs_map={
            "x": [{"nodeId": "B", "portName": "result"}],
        },
        outputs_map={
            "result_0": [{"nodeId": "B", "portName": "y"}],
            "result_1": [{"nodeId": "C", "portName": "e"}],
        },
        input_data={
            "a": {"a": 0.4},
            "c": {"a": 1.0},
        },
    )
    nodes["B"] = make_outnode(
        id="B",
        type_name="tests.methods.recycle_b",
        x=0,
        y=0,
        inputs_map={
            "y": [{"nodeId": "A", "portName": "result_0"}],
        },
        outputs_map={
            "result": [{"nodeId": "A", "portName": "x"}],
        },
        input_data={
            "b": {"a": 0.5},
            "d": {"a": 2.0},
        },
    )
    nodes["C"] = make_outnode(
        id="C",
        type_name="tests.methods.identity",
        x=0,
        y=0,
        inputs_map={
            "e": [{"nodeId": "A", "portName": "result_1"}],
        },
        outputs_map={},
        input_data={},
    )

    config = Config.from_function_list([recycle_a_ext, recycle_b, identity])
    runner = JobRunner(
        config,
        method="sync",
        enable_cycles=True,
        scc_tolerance=0.7,
        scc_solver="wegstein",
        scc_wegstein_qmin=0.0,
        scc_wegstein_qmax=1.5,
        scc_max_iters=50,
    )
    result = runner.run(nodes)

    a0 = result["A"].result_mapped["result_0"]
    a1 = result["A"].result_mapped["result_1"]
    c_out = result["C"].result

    assert result["A"].status == "finished"
    assert result["B"].status == "finished"
    assert result["C"].status == "finished"
    assert math.isfinite(float(a0)) and math.isfinite(float(a1))
    assert abs(a1 - (2.0 * a0)) < 1e-9
    assert abs(c_out - a1) < 1e-12

