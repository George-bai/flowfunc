import math
import asyncio
from typing import Annotated, Literal, List, Optional
import dash
from dash import Input, Output, State, html
from flowfunc import config, jobrunner
from flowfunc.Flowfunc import Flowfunc
from flowfunc.models import PortFunction, Port, Control, ControlType
import uuid
import numpy as np
import logging
from enum import Enum
import pandas as pd

def add(a: int, b: int):
    """Add two numbers"""
    return a + b


def subtract(
    a: Annotated[int | float, {"label": "First number"}],
    b: Annotated[int | float, {"label": "Second number"}],
):
    """Subtract one number from another"""
    return a - b

class MathFunction(str, Enum):
    sin = "sin"
    cos = "cos"
    tan = "tan"
    asin = "asin"
    acos = "acos"
    atan = "atan"


def trig_function(
    x: float, func: Annotated[MathFunction, {"label": "Function", "hidePort": True}]
):
    """Trigonometric function"""
    return getattr(math, func)(x)


# Example DataFrame source for testing
def make_df(
    rows: Annotated[int, {"label": "Rows"}] = 1_000_000,
    cols: Annotated[int, {"label": "Columns"}] = 10,
    dtype: Annotated[Literal["float32", "float64"], {"label": "DType"}] = "float32",
) -> pd.DataFrame:
    """Create a large DataFrame for performance testing.

    Defaults generate ~40 MB (1e6 x 10 x 4 bytes) plus pandas overhead. Increase parameters to stress test (e.g., rows=5_000_000, cols=10 for ~200 MB with float32).
    """
    dt = np.float32 if dtype == "float32" else np.float64
    # seed for deterministic results
    rng = np.random.default_rng(42)
    data = rng.standard_normal(size=(rows, cols), dtype=dt)
    df = pd.DataFrame(data, columns=[f"c{i}" for i in range(cols)])
    return df


def heavy_groupby_stats(
    df: pd.DataFrame,
    groups: Annotated[int, {"label": "Groups"}] = 1000,
) -> pd.DataFrame:
    """Expensive groupby aggregation across all columns.

    Groups rows by index % groups and computes mean/std/sum, returning a wide aggregated frame.
    """
    key = (df.index % groups)
    agg = df.groupby(key).agg(["mean", "std", "sum"])  # multi-index columns
    # flatten multi-index columns for clarity
    agg.columns = ["_".join([str(a) for a in tup]) for tup in agg.columns.to_flat_index()]
    return agg


def select_columns(df: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
    """Return a copy of the DataFrame with only selected columns"""
    # Fallback: if user entered a comma-separated string in the text input
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(',') if c.strip()]
    if not columns:
        return df.copy()
    return df[columns].copy()


flist = [
    add,
    subtract,
    trig_function,
    make_df,
    select_columns,
    heavy_groupby_stats,
]
# Configure logging so backend cache messages appear in console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = dash.Dash(__name__, assets_folder="examples/assets")

# Ensure 'str' port type exists so the fallback text input renders correctly
extra_ports = [
    Port(
        type="str",
        name="str",
        label="str",
        acceptTypes=["str"],
        controls=[Control(type=ControlType.str, name="value", label="Value")],
    )
]

fconfig = config.Config.from_function_list(flist, extra_ports=extra_ports)
# Override inputs of select_columns to use clientside dynamic inputs
_sel_type = ".".join([select_columns.__module__, select_columns.__name__])
fconfig.get_node(_sel_type).inputs = PortFunction(path="utils.toolnodes.select_columns_inputs")
# Enable caching (session-scoped) using Redis
SESSION_ID = str(uuid.uuid4())
# Two runners to compare modes against the same cache/session
runner_sync = jobrunner.JobRunner(
    fconfig,
    method="sync",
    cache_enabled=True,
    redis_url="redis://localhost:6379/0",
    session_id=SESSION_ID,
    cache_ttl_seconds=1800,
)
runner_async = jobrunner.JobRunner(
    fconfig,
    method="async",
    cache_enabled=True,
    redis_url="redis://localhost:6379/0",
    session_id=SESSION_ID,
    cache_ttl_seconds=1800,
)

app.layout = html.Div(
    [
        html.Div([
            html.Button(id="btn_run", children=["Run"], style={"marginRight": "8px"}),
            html.Span("Mode:", style={"marginRight": "6px"}),
            html.Span(id="mode_label", children="sync", style={"fontFamily": "monospace", "marginRight": "8px"}),
            html.Div(
                id="mode_container",
                children=dash.dcc.RadioItems(
                    id="mode",
                    options=[
                        {"label": "sync", "value": "sync"},
                        {"label": "async", "value": "async"},
                    ],
                    value="sync",
                    inline=True,
                ),
                style={"display": "inline-block"},
            ),
        ]),
        html.Div(
            Flowfunc(
                id="nodeeditor",
                config=fconfig.dict(),
                disable_zoom=True,
                type_safety=False,
                context={},
            ),
            style={"height": "500px", "width": "100%"},
        ),
        html.Div(id="output"),
    ]
)


@app.callback(
    [
        Output("output", "children"),
        Output("nodeeditor", "nodes_status"),
        Output("nodeeditor", "context"),
    ],
    Input("btn_run", "n_clicks"),
    State("nodeeditor", "nodes"),
    State("mode", "value"),
)
def run(nclicks, nodes, mode):
    print(f"[CALLBACK] Run clicked n_clicks={nclicks}, mode={mode}, nodes={len(nodes) if nodes else 0}", flush=True)
    if not nodes:
        print("[CALLBACK] No nodes provided from editor", flush=True)
        return [], {}, {}
    if mode == "async":
        print("[CALLBACK] Executing in ASYNC mode (cache enabled)", flush=True)
        output = asyncio.run(runner_async.run(nodes))
    else:
        print("[CALLBACK] Executing in SYNC mode (cache enabled)", flush=True)
        output = runner_sync.run(nodes)
    print("[CALLBACK] Runner finished", flush=True)
    output_html = []
    nodes_status = {}
    context = {"nodes": {}}
    for node in output.values():
        try:
            print(f"[NODE] id={node.id} type={node.type} status={node.status} error={bool(node.error)}", flush=True)
        except Exception:
            pass
        output_html.append(html.Div(f"{node.type}: {node.result}"))
        if node.error:
            output_html.append(html.Div(f"{node.error}"))
        nodes_status[node.id] = node.status
        # Populate context with DataFrame columns if available
        cols = None
        try:
            obj = node.result
            if hasattr(obj, "columns"):
                cols_attr = getattr(obj.columns, "tolist", None)
                if callable(cols_attr):
                    cols = list(map(str, cols_attr()))
                else:
                    cols = list(map(str, list(obj.columns)))
            elif isinstance(obj, (tuple, list)):
                for v in obj:
                    if hasattr(v, "columns"):
                        cols_attr = getattr(v.columns, "tolist", None)
                        if callable(cols_attr):
                            cols = list(map(str, cols_attr()))
                        else:
                            cols = list(map(str, list(v.columns)))
                        break
            elif isinstance(obj, dict):
                for v in obj.values():
                    if hasattr(v, "columns"):
                        cols_attr = getattr(v.columns, "tolist", None)
                        if callable(cols_attr):
                            cols = list(map(str, cols_attr()))
                        else:
                            cols = list(map(str, list(v.columns)))
                        break
        except Exception:
            pass
        if cols:
            context["nodes"][node.id] = {"columns": cols}
    return output_html, nodes_status, context


if __name__ == "__main__":
    app.run(debug=True, port=8050, use_reloader=False)
