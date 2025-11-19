import time
from flowfunc.Flowfunc import Flowfunc
from flowfunc.config import Config
from flowfunc.jobrunner import JobRunner
from flowfunc.models import Node, Port, PortFunction
import dash
from dash.dependencies import Input, Output, State
from dash import html, dcc
import dash_bootstrap_components as dbc
import json
import base64

from flowfunc.models import OutNode
from nodes import all_functions

app = dash.Dash(external_stylesheets=[dbc.themes.SLATE])


def convert_template(template: str, **kwargs):
    """Testing dynamic ports"""
    return template.format(**kwargs)


def convert_to_list(**kwargs):
    return list(kwargs.values())


increasing_ports_function = PortFunction(path="increasing_ports")

dynamic_port_function = PortFunction(path="dynamic_ports")
# "dynamic_ports" should be defined in /assets/*.js at the
# path window.dash_clientside.flowfunc.dynamic_ports
 

def split_csv_outputs(csv: str):
    parts = (csv or "").split(",")
    return {f"item_{i}": p.strip() for i, p in enumerate(parts)}


dynamic_outputs_function = PortFunction(path="dynamic_outputs.split_csv_outputs")


def splitter(value, n_outputs, ratios):
    """Split a value into N parts based on ratios.

    Behaviour:
    - n_outputs in [0, 50].
    - If no ratios are provided, the value is split equally across outputs.
    - If exactly n-1 ratios are provided, the last ratio is auto-computed as
      1 - sum(ratios), as long as the sum is <= 1.
    - If exactly n ratios are provided, they are used directly as long as
      they are non-negative and sum <= 1.
    - Otherwise a ValueError is raised.
    """

    n = int(n_outputs or 0)
    if n < 0 or n > 50:
        raise ValueError("Number of outputs must be between 0 and 50.")
    if n == 0:
        return {}

    # Parse ratios into a list of floats
    ratio_vals: list[float] = []
    if isinstance(ratios, str):
        text = ratios.strip()
        if text:
            for part in text.split(","):
                if not part.strip():
                    continue
                ratio_vals.append(float(part.strip()))
    else:
        try:
            for x in ratios:
                if x is None:
                    continue
                ratio_vals.append(float(x))
        except TypeError:
            ratio_vals = []

    if any(r < 0 for r in ratio_vals):
        raise ValueError("Ratios must be non-negative.")

    ratios_full: list[float]
    if not ratio_vals:
        # Equal split if no ratios provided
        ratios_full = [1.0 / n] * n
    elif len(ratio_vals) == n - 1:
        ratio_sum = sum(ratio_vals)
        if ratio_sum > 1:
            raise ValueError("Sum of ratios cannot exceed 1.")
        last_ratio = 1.0 - ratio_sum
        ratios_full = list(ratio_vals) + [last_ratio]
    elif len(ratio_vals) == n:
        ratio_sum = sum(ratio_vals)
        if ratio_sum > 1:
            raise ValueError("Sum of ratios cannot exceed 1.")
        ratios_full = list(ratio_vals)
    else:
        raise ValueError(
            f"Expected {n - 1} or {n} ratios for {n} outputs (got {len(ratio_vals)})."
        )

    if value is None:
        raise ValueError("Splitter node requires a value input.")
    value_float = float(value)

    result = {}
    for idx, r in enumerate(ratios_full):
        result[f"part_{idx}"] = value_float * r
    return result


splitter_outputs_function = PortFunction(path="dynamic_outputs.splitter_outputs")


template_node = Node(
    type="dynamic_ports",
    label="Dynamic Ports",
    description="Testing dynamic ports",
    method=convert_template,
    inputs=dynamic_port_function,
    outputs=[Port(type="str", name="template", label="Template")],
)

list_node = Node(
    type="increasing_list",
    label="Auto increasing list",
    description="Auto increasing list",
    method=convert_to_list,
    inputs=increasing_ports_function,
    outputs=[Port(type="object", name="object", label="List")],
)


split_outputs_node = Node(
    type="dynamic_outputs.split_csv",
    label="Dynamic Outputs (Split CSV)",
    description="Split a CSV string into multiple dynamic output ports.",
    method=split_csv_outputs,
    inputs=[Port(type="str", name="csv", label="CSV")],
    outputs=dynamic_outputs_function,
)


splitter_node = Node(
    type="dynamic_outputs.splitter",
    label="Splitter",
    description="Split a value into N outputs according to ratios.",
    method=splitter,
    inputs=[
        Port(type="float", name="value", label="Value"),
        Port(type="int", name="n_outputs", label="Outputs (0-50)"),
        Port(type="str", name="ratios", label="Ratios (comma-separated)"),
    ],
    outputs=splitter_outputs_function,
)


app = dash.Dash(external_stylesheets=[dbc.themes.SLATE])

fconfig = Config.from_function_list(
    all_functions, extra_nodes=[template_node, list_node, split_outputs_node, splitter_node]
)
# fconfig = Config.from_function_list(all_functions)
job_runner = JobRunner(fconfig)

node_editor = html.Div(
    [
        dbc.ButtonGroup(
            [
                dbc.Button(id="run", children="Run"),
                dbc.Button(id="save", children="Save"),
                dbc.Button(id="clear", children="Clear"),
                dcc.Upload(
                    id="uploader", children=dbc.Button(id="load", children="Load")
                ),
                dash.dcc.Download(id="download"),
            ],
            style={
                "position": "absolute",
                "top": "15px",
                "left": "15px",
                "zIndex": "15",
            },
        ),
        html.Div(
            id="nodeeditor_container",
            children=Flowfunc(
                id="input",
                # config=inconfig,
                config=fconfig.dict(),
                context={"context": "initial"},
            ),
            style={
                "position": "relative",
                "width": "100%",
                "height": "100vh",
            },
        ),
    ]
)

app.layout = html.Div(
    dbc.Row(
        [
            dbc.Col(width=8, children=node_editor),
            dbc.Col(
                id="output", width=4, style={"height": "100vh", "overflow": "auto"}
            ),
        ],
    ),
    style={"overflow": "hidden"},
)


def parse_uploaded_contents(contents):
    content_type, content_string = contents.split(",")

    decoded = base64.b64decode(content_string)
    data = json.loads(decoded.decode("utf-8"))
    try:
        for key, value in data.items():
            node = OutNode(**value)
        # Parsing succeeded
        return data
    except Exception as e:
        print(e)
        print("The uploaded file could not be parsed as a flow file.")


@app.callback(
    [
        Output("output", "children"),
        Output("input", "nodes_status"),
    ],
    [
        Input("run", "n_clicks"),
        State("input", "nodes"),
    ],
)
def display_output(runclicks, nodes):
    if not nodes:
        return [], {}
    starttime = time.perf_counter()
    # output_dict = job_runner.run(nodes)
    nodes_output = job_runner.run(nodes)
    # nodes_output = {node_id: OutNode(**node) for node_id, node in output_dict.items()}
    endtime = time.perf_counter()
    outdiv = html.Div(children=[])
    for node in nodes_output.values():
        if node.error:
            outdiv.children.append(str(node.error))
        if "display" in node.type:
            outdiv.children.append(node.result)

    return outdiv, {node_id: node.status for node_id, node in nodes_output.items()}


@app.callback(
    Output("download", "data"),
    [Input("save", "n_clicks"), State("input", "nodes")],
    prevent_initial_call=True,
)
def func(n_clicks, nodes):
    return dict(content=json.dumps(nodes), filename="nodes.json")


@app.callback(
    [
        Output("input", "nodes"),
        Output("input", "editor_status"),
    ],
    [
        Input("uploader", "contents"),
        Input("clear", "n_clicks"),
        State("input", "nodes"),
    ],
    prevent_initial_call=True,
)
def update_output(contents, nclicks, nodes):
    ctx = dash.callback_context
    if not ctx.triggered:
        return nodes, "server"
    control = ctx.triggered[0]["prop_id"].split(".")[0]
    if control == "uploader":
        newnodes = parse_uploaded_contents(contents)
        return newnodes, "server"
    return {}, "server"


if __name__ == "__main__":
    app.run(debug=True)
