![Flowfunc](./docs/source/images/logo.png)

# Flowfunc

Flowfunc is a Dash component that brings a node-based programming surface to Python apps. Nodes are defined from regular Python callables, composed visually with the React/Flume editor, and executed by a Python runtime that understands dependencies, async functions, distributed queues and caching. The project includes both the front-end component and the Python helpers required to build a complete workflow UI.

## Highlights

- **Dash-first node editor** built on [Flume](https://flume.dev) with custom styling, live port highlighting and toolbar toggles for pan/zoom to keep complex graphs manageable inside Dash layouts. A new Fit‑to‑View action zooms and centers all nodes to the viewport for quick orientation.【F:src/lib/components/Flowfunc.react.js†L18-L210】【F:src/lib/components/nodeeditor.css†L1-L37】
- **Python-native node definitions** generated from function signatures, docstrings and annotations, including support for `Annotated` metadata, enums, dataclasses, Pydantic models, optional/union types and multi-output functions.【F:flowfunc/config.py†L70-L214】【F:tests/test_config.py†L31-L94】
- **Extensible graph schema** via `Node`, `Port`, `PortFunction` and extra port definitions, allowing bespoke controls or clientside JavaScript to shape dynamic ports (e.g. column selectors driven by editor context).【F:flowfunc/models.py†L46-L139】【F:examples/dynamic.py†L18-L96】【F:examples/assets/funcs.js†L1-L52】
- **Flexible execution engine** powered by `JobRunner` with synchronous, asynchronous, distributed and hybrid modes, partial re-execution, structured node status reporting and graceful error propagation.【F:flowfunc/jobrunner.py†L102-L348】【F:flowfunc/jobrunner.py†L349-L637】
- **Distributed & cached runs** backed by `python-rq` and Redis. Jobs can be enqueued with custom queues/metadata, cancelled mid-flight, and short-circuited when cached results are valid across runs.【F:flowfunc/jobrunner.py†L26-L204】【F:flowfunc/jobrunner.py†L449-L637】【F:flowfunc/cache.py†L1-L140】
- **Ready-to-run examples** showcasing synchronous flows, Redis-backed caching, dynamic nodes and RQ workers for distributed execution in the `examples/` folder.【F:examples/usage.py†L1-L215】【F:examples/usage_rq.py†L1-L120】【F:examples/README.md†L1-L34】

## Installation

Flowfunc targets Python 3.10+ and is currently under active development. Install it directly from PyPI:

```bash
pip install flowfunc
```

Optional extras are available:

- `flowfunc[distributed]` – adds `python-rq` for queue-based execution.【F:setup.py†L23-L29】
- `flowfunc[full]` – includes Dash and RQ in addition to the core package.【F:setup.py†L23-L29】

Redis is required when using caching or distributed runners.

## Quick start

The snippet below wires the component into a Dash application, generates nodes from Python functions, runs them when the user clicks a button and renders both results and node statuses.

```python
from typing import Dict
import dash
from dash import html, Input, Output, State

from flowfunc import Flowfunc
from flowfunc.config import Config
from flowfunc.jobrunner import JobRunner
from flowfunc.models import OutNode

app = dash.Dash(__name__)

# Functions become nodes based on their type hints and docstrings

def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


def subtract(a: int, b: int) -> int:
    """Subtract one number from another"""
    return a - b

config = Config.from_function_list([add, subtract])
runner = JobRunner(config, method="sync")

app.layout = html.Div(
    [
        html.Button("Run", id="btn_run"),
        Flowfunc(id="nodeeditor", config=config.dict()),
        html.Div(id="output"),
    ],
    style={"height": "600px"},
)


@app.callback(
    Output("output", "children"),
    Output("nodeeditor", "nodes_status"),
    Input("btn_run", "n_clicks"),
    State("nodeeditor", "nodes"),
)
def run_nodes(nclicks: int, nodes: Dict[str, OutNode]):
    if not nodes:
        return [], {}
    results = runner.run(nodes)
    output = [
        html.Div([html.H4(f"{node.type}"), html.P(str(node.result))])
        for node in results.values()
    ]
    return output, {node_id: node.status for node_id, node in results.items()}


if __name__ == "__main__":
    app.run_server(debug=True)
```

Every callback run returns a dictionary of `OutNode` instances with `result`, `result_mapped`, `status`, `error` and (for distributed runs) `job` metadata so you can display progress, store results or trigger follow-up work.【F:flowfunc/models.py†L88-L135】【F:tests/test_jobrunner.py†L11-L73】 The example also updates `nodes_status` so the editor highlights each node according to its state.【F:src/lib/components/nodeeditor.css†L15-L36】

## Running flows with `JobRunner`

`JobRunner` interprets the node graph returned by the component, resolves dependencies, and executes nodes in topological order.【F:flowfunc/jobrunner.py†L253-L406】 Key capabilities include:

- **Execution modes** – choose `sync`, `async`, `distributed` or `async_distributed` depending on whether you want blocking, coroutine-returning, RQ-backed or hybrid behaviour.【F:flowfunc/jobrunner.py†L299-L336】 Setting `same_worker=True` lets distributed runs execute inside the worker process for debugging.【F:flowfunc/jobrunner.py†L490-L527】
- **Selective runs** – provide `selected_node_ids` to re-evaluate only the chosen outputs and their dependencies, which is ideal when editing large graphs.【F:flowfunc/jobrunner.py†L321-L334】【F:tests/test_jobrunner.py†L75-L97】
- **Status + errors** – each node tracks `status` transitions (`started`, `deferred`, `finished`, `failed`, `canceled`, etc.) and propagates exceptions from upstream nodes as `ErrorInDependentNode`.【F:flowfunc/jobrunner.py†L366-L494】【F:flowfunc/models.py†L108-L134】
- **Cancellation** – call `request_cancel()` to cooperatively stop in-flight runs; distributed workers check a Redis flag and local subprocesses are terminated when possible.【F:flowfunc/jobrunner.py†L132-L204】
- **Per-node settings** – attach an optional `settings` dict to a node to forward custom keyword arguments to `rq.Queue.enqueue`, such as a specific `job_id` or queue.【F:flowfunc/jobrunner.py†L563-L612】【F:tests/test_distributed.py†L39-L71】

### Caching

Enable caching by passing `cache_enabled=True`, a Redis URL, session identifier and TTL. Flowfunc hashes each node using its literal controls, upstream signatures and the function source so results are reused when nothing relevant changed.【F:flowfunc/jobrunner.py†L205-L282】【F:flowfunc/jobrunner.py†L406-L494】【F:flowfunc/cache.py†L72-L140】 Worker-side helpers short-circuit execution when a cached payload is available and automatically refresh cache entries after successful runs.【F:flowfunc/distributed.py†L83-L179】

## Distributed execution

When `method` is `distributed` or `async_distributed`, Flowfunc enqueues nodes onto `python-rq` using the custom `NodeQueue`/`NodeJob` classes. Dependencies are resolved automatically, results are exposed through `job_id` metadata, and jobs inherit cache and cancellation settings.【F:flowfunc/distributed.py†L1-L134】【F:flowfunc/jobrunner.py†L507-L637】 Use the included CLI snippet from `examples/README.md` to start a worker with the correct queue and job classes.【F:examples/README.md†L18-L34】

## Defining nodes and ports

- **Automatic generation** – `Config.from_function_list()` inspects each callable’s signature, docstring and annotations to create `Node`/`Port` models and default controls.【F:flowfunc/config.py†L69-L214】 Docstrings seed node descriptions and return annotations define the number and type of outputs.【F:flowfunc/config.py†L147-L194】
- **Rich types** – enums become dropdowns, optional/union types expand accepted connections, dataclasses and Pydantic models generate nested controls, and multi-value returns create multiple output ports.【F:flowfunc/config.py†L200-L294】【F:tests/test_config.py†L45-L94】 The `typing.Annotated` metadata lets you override labels, defaults and port visibility without leaving Python.【F:usage.py†L11-L70】
- **Custom nodes** – instantiate `Node`, `Port` and `PortFunction` manually to introduce bespoke behaviours (e.g. dynamic display nodes or incremental port lists). Client-side helpers can live in Dash’s `assets/` directory and receive editor context to render dynamic controls.【F:examples/dynamic.py†L18-L123】【F:examples/assets/funcs.js†L1-L52】 You can also extend the config with `extra_ports` to expose additional control widgets or composite inputs.【F:examples/usage.py†L85-L111】

## Dash component API

The `Flowfunc` component exposes several properties you can drive from callbacks:

- `config` (required) – serialized `Config` dict generated on the server.【F:flowfunc/Flowfunc.py†L23-L78】
- `nodes` / `comments` – current graph state emitted from the editor on every change.【F:src/lib/components/Flowfunc.react.js†L231-L272】
- `nodes_status` – map of node IDs to statuses for styling the editor (classes are provided in `nodeeditor.css`).【F:flowfunc/models.py†L108-L130】【F:src/lib/components/nodeeditor.css†L15-L36】
- `selected_nodes`, `double_clicked_node` – UI events raised by built-in listeners for selections and double clicks.【F:src/lib/components/Flowfunc.react.js†L282-L330】
- `context` – arbitrary JSON data pushed from callbacks back into the editor; dynamic port functions can read it to populate controls (e.g. DataFrame column lists).【F:src/lib/components/Flowfunc.react.js†L108-L210】【F:examples/assets/funcs.js†L21-L52】
- `type_safety`, `disable_zoom`, `disable_pan`, `space_to_pan`, `disable_focus`, `initial_scale` – runtime toggles that let end users adapt the editing experience.【F:flowfunc/Flowfunc.py†L23-L78】【F:src/lib/components/Flowfunc.react.js†L174-L210】【F:src/lib/components/Flowfunc.react.js†L340-L383】

### View controls and Fit‑to‑View

- The editor includes a small control cluster (bottom‑left) to toggle Zoom and Pan, and a new Fit‑to‑View button that automatically zooms and centers all nodes in the current canvas.
- Programmatic trigger: set `fit_to_view_request` to a changing number (e.g., a button’s `n_clicks`) to trigger the same action from Dash callbacks.
- The Fit‑to‑View feature computes a viewport‑aware transform with a margin and updates the Flume stage. When Flume’s stage setters are available (see optional patch below), the transform applies instantly; otherwise a smooth wheel‑based animation is used.

Optional, recommended: expose Flume stage setters for instant Fit‑to‑View

1. Install dependencies: `npm install`
2. Apply the local patch once per install: `npm run patch:flume`
3. Rebuild wrappers: `npm run build`

This exposes `setStageTransform`, `setScale`, and `setTranslate` on the Flume `NodeEditor` ref. Fit‑to‑View uses the direct API when available (works even if zoom is disabled).

## Examples and demo apps

- `examples/usage.py` – Dash app comparing synchronous vs asynchronous runners with Redis caching, plus context-driven column selectors.【F:examples/usage.py†L1-L215】
- `examples/dynamic.py` – demonstrates dynamic port generation and serialization helpers.【F:examples/dynamic.py†L18-L181】
- `examples/usage_rq.py` – runs the same graph in distributed mode with `rqworker` workers.【F:examples/usage_rq.py†L1-L120】
- `examples/fit_to_view.py` – quick manual test of the new Fit‑to‑View toolbar button.
- `examples/fit_to_view_trigger.py` – programmatic Fit‑to‑View via the `fit_to_view_request` prop.

Launch the demo development server with `npm start` (after `npm install`) and rebuild the component bundle with `npm run build`. Python tests live in `tests/` and can be executed via `pytest` once dependencies from `requirements.txt` are installed.【F:AGENTS.md†L8-L20】【F:tests/test_jobrunner.py†L1-L97】
