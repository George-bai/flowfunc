import math
from typing import Annotated, Literal, List, Optional
import dash
from dash import Input, Output, State, html
from flowfunc import Flowfunc, config, jobrunner
from flowfunc.models import PortFunction, Port, Control, ControlType
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
def make_df() -> pd.DataFrame:
    """Create a sample DataFrame"""
    return pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6], "c": [7, 8, 9]})


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
]
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
print(_sel_type)
fconfig.get_node(_sel_type).inputs = PortFunction(path="utils.toolnodes.select_columns_inputs")
runner = jobrunner.JobRunner(fconfig)

app.layout = html.Div(
    [
        html.Button(id="btn_run", children=["Run"]),
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
)
def run(nclicks, nodes):
    if not nodes:
        return [], {}, {}
    output = runner.run(nodes)
    output_html = []
    nodes_status = {}
    context = {"nodes": {}}
    for node in output.values():
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
    app.run(debug=True, port=8050, use_reloader=True)
