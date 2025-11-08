import math
from flowfunc.config import Config
from flowfunc.jobrunner import JobRunner
from tests.methods import recycle_affine, recycle_a, recycle_b


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


def test_self_loop_affine_converges():
    # Node A: r = a*x + c, with x fed by its own result (recycle)
    nodes = {}
    nodes["A"] = make_outnode(
        id="A",
        type_name="tests.methods.recycle_affine",
        x=0,
        y=0,
        inputs_map={
            "x": [{"nodeId": "A", "portName": "result"}],
        },
        outputs_map={},
        input_data={
            "a": {"a": 0.4},
            "c": {"a": 1.0},
        },
    )
    config = Config.from_function_list([recycle_affine])
    runner = JobRunner(config, method="sync", enable_cycles=True, scc_tolerance=1e-8, scc_max_iters=200)
    result = runner.run(nodes)
    # Expected fixed point: x = a*x + c => x = c/(1-a)
    expected = 1.0 / (1.0 - 0.4)
    assert math.isfinite(result["A"].result)
    assert abs(result["A"].result - expected) < 1e-6
    assert result["A"].status == "finished"


def test_two_node_cycle_converges():
    # A: y = a*x + c, B: x = b*y + d. Connect A.result -> B.y, B.result -> A.x
    nodes = {}
    nodes["A"] = make_outnode(
        id="A",
        type_name="tests.methods.recycle_a",
        x=0,
        y=0,
        inputs_map={
            "x": [{"nodeId": "B", "portName": "result"}],
        },
        outputs_map={
            "result": [{"nodeId": "B", "portName": "y"}],
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
            "y": [{"nodeId": "A", "portName": "result"}],
        },
        outputs_map={
            "result": [{"nodeId": "A", "portName": "x"}],
        },
        input_data={
            "b": {"a": 0.5},
            "d": {"a": 2.0},
        },
    )
    config = Config.from_function_list([recycle_a, recycle_b])
    runner = JobRunner(config, method="sync", enable_cycles=True, scc_tolerance=1e-8, scc_max_iters=200)
    result = runner.run(nodes)
    x_expected = (0.5 * 1.0 + 2.0) / (1.0 - 0.4 * 0.5)
    y_expected = 0.4 * x_expected + 1.0
    assert abs(result["A"].result - y_expected) < 1e-6
    assert abs(result["B"].result - x_expected) < 1e-6
    assert result["A"].status == "finished"
    assert result["B"].status == "finished"
