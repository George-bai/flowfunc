import dash
from dash import html

from flowfunc import Flowfunc
from flowfunc.config import Config

# Reuse example node functions to build a config
from nodes import all_functions


app = dash.Dash(__name__)

# Build Flume/Flowfunc config from the provided functions
fconfig = Config.from_function_list(all_functions)

# Scatter a few default nodes across the canvas so Fit-to-View is meaningful
default_nodes = [
    {"type": "nodes.enter_integer", "x": -600, "y": -200},
    {"type": "nodes.enter_string", "x": 520, "y": -320},
    {"type": "nodes.add_sync", "x": 220, "y": 260},
    {"type": "nodes.convert_to_markdown", "x": -460, "y": 460},
]

app.layout = html.Div(
    html.Div(
        Flowfunc(
            id="flow",
            config=fconfig.dict(),
            default_nodes=default_nodes,
            initial_scale=1.0,
            disable_zoom=False,
            disable_pan=False,
            space_to_pan=True,
        ),
        style={"position": "relative", "width": "100%", "height": "100vh"},
    )
)


if __name__ == "__main__":
    app.run(debug=True)
