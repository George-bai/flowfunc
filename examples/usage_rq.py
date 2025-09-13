import os
import uuid
import time
from flowfunc.Flowfunc import Flowfunc
from flowfunc.config import Config
from flowfunc.jobrunner import JobRunner
import dash
from dash.dependencies import Input, Output, State
from dash import html, dcc
import dash_bootstrap_components as dbc
import json
import base64
from redis import Redis

from flowfunc.models import OutNode
from rq import Queue
import sys
from pathlib import Path

# Ensure project root on sys.path so workers can import examples.nodes
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    # Preferred: absolute import so function __module__ is 'examples.nodes'
    from examples.nodes import all_functions
except Exception:
    # Fallback for ad-hoc runs from inside examples/
    from nodes import all_functions

app = dash.Dash(external_stylesheets=[dbc.themes.SLATE])

rhost = os.environ.get("REDIS_HOST", "localhost")
rport = int(os.environ.get("REDIS_PORT", "6379"))
rdb = int(os.environ.get("REDIS_DB", "0"))
rurl = f"redis://{rhost}:{rport}/{rdb}"
print(f"[INIT] Connecting to Redis at {rurl}")
rconn = Redis(host=rhost, port=rport, db=rdb)
q = Queue(connection=rconn)

fconfig = Config.from_function_list(all_functions)

# Create a session id for caching
SESSION_ID = os.environ.get("FLOWFUNC_SESSION_ID", str(uuid.uuid4()))
print(f"[INIT] FLOWFUNC_SESSION_ID={SESSION_ID}")

job_runner = JobRunner(
    fconfig,
    method="distributed",
    default_queue=q,
    cache_enabled=True,
    redis_url=rurl,
    session_id=SESSION_ID,
    cache_ttl_seconds=1800,
)

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
                html.Div(id="run_info", style={"marginLeft": "8px", "fontSize": "12px"}),
            ],
            style={
                "position": "absolute",
                "top": "15px",
                "left": "15px",
                "z-index": "15",
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
    [
        dcc.Interval(
            id="status_interval",
            interval=60 * 60 * 1000,  # in milliseconds
            n_intervals=0,
        ),
        dcc.Store(id="job_store"),
        dbc.Row(
            [
                dbc.Col(width=8, children=node_editor),
                dbc.Col(
                    id="output", width=4, style={"height": "100vh", "overflow": "auto"}
                ),
            ],
        ),
    ],
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
    Output("job_store", "data"),
    [
        Input("run", "n_clicks"),
        State("input", "nodes"),
    ],
)
def display_output(runclicks, nodes):
    if not nodes:
        return {}
    print("[CALLBACK:RUN] Submitting distributed jobs...")
    nodes_output = job_runner.run(nodes)
    # Log signatures (precomputed during scheduling)
    try:
        print(f"[CALLBACK:RUN] Precomputed signatures for {len(job_runner._signatures)} nodes")
        for nid, sig in job_runner._signatures.items():
            print(f"  - node={nid} sig={sig[:12]}… type={nodes_output[nid].type}")
    except Exception as e:
        print(f"[CALLBACK:RUN] Signature log error: {e}")
    store = {}
    for nodeid, node in nodes_output.items():
        # Log job submission
        try:
            print(f"[ENQUEUED] node={nodeid} type={node.type} job_id={node.job_id}")
        except Exception:
            pass
        store[nodeid] = node.model_dump(exclude={"run_event", "job"})

    return store

@app.callback(
    [
        Output("output", "children"),
        Output("input", "nodes_status"),
        Output("status_interval", "interval"),
    ],
    [
        Input("status_interval", "n_intervals"),
        Input("job_store", "data"),
    ],
)
def get_status(ninterval, data):
    interval = 60 * 60 * 1000
    if not data:
        return "", {}, interval
    status = {}
    result_blocks = []
    rows = []
    for nodeid, node in data.items():
        node = OutNode.model_validate(node)
        try:
            # Base Job fetch is sufficient (wrapper handles kwargs resolution)
            from rq.job import Job
            job = Job.fetch(node.job_id, connection=rconn)
        except Exception as e:
            print(f"[STATUS] Fetch failed node={nodeid} job_id={node.job_id} err={e}")
            continue
        jstatus = job.get_status()
        status[nodeid] = jstatus
        jmeta = job.get_meta() or {}
        cache_hit = jmeta.get("cache_hit")
        phase = jmeta.get("phase")
        exec_ms = jmeta.get("exec_ms")
        sig = jmeta.get("cache_signature")
        rows.append(
            html.Div(
                f"node={nodeid} type={node.type} job={job.id} status={jstatus} cache_hit={cache_hit} phase={phase} exec_ms={exec_ms} sig={(sig[:12]+'…') if sig else None}",
                style={"fontFamily": "monospace", "marginBottom": "4px"},
            )
        )
        if job.result is not None and "display" in node.type:
            result_blocks.append(job.result)
    if any([x in ["started", "deferred"] for x in status.values()]):
        interval = 1000
    # Compose output panel
    output_panel = html.Div([
        html.H5("Job Status"),
        html.Div(rows),
        html.Hr(),
        html.H5("Display Results"),
        html.Div(result_blocks),
    ])
    return output_panel, status, interval


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
