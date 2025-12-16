# AUTO GENERATED FILE - DO NOT EDIT

import typing  # noqa: F401
from typing_extensions import TypedDict, NotRequired, Literal # noqa: F401
from dash.development.base_component import Component, _explicitize_args

ComponentType = typing.Union[
    str,
    int,
    float,
    Component,
    None,
    typing.Sequence[typing.Union[str, int, float, Component, None]],
]

NumberType = typing.Union[
    typing.SupportsFloat, typing.SupportsInt, typing.SupportsComplex
]


class Flowfunc(Component):
    """A Flowfunc component.
Flowfunc: A node editor for dash
This component gives a flow based programming interface for dash users.
The developer can define the nodes using simple python functions and these
will be available as nodes which can be connected together to create a logic
at runtime.

Keyword arguments:

- id (string; optional):
    The ID used to identify this component in Dash callbacks.

- comments (dict; optional):
    Comments in the node editor.

- config (dict; optional):
    The available port types and node types.

- context (dict; optional):
    Pass extra data to nodes.

- default_nodes (list; optional):
    Default nodes present in the editor  A list of nodes from the
    config.

- disable_focus (boolean; optional):
    Disable automatic focusing behavior in the editor (Flume 1.1.0).

- disable_pan (boolean; optional):
    Disable zoom option.

- disable_zoom (boolean; optional):
    Disable zoom option.

- double_clicked_node (string; optional):
    Node on which a double click event was registered.

- editor_status (string; optional):
    A property denoting the status of the editor  Following statuses
    are possible.  [\"client\", \"server\"].

- initial_scale (number; optional):
    Initial zoom level of the editor.

- nodes (dict; optional):
    The nodes of the node editor.

- nodes_status (dict; optional):
    The status of each node on the editor.

- selected_nodes (list; optional):
    The nodes of the node editor.

- space_to_pan (boolean; optional):
    Disable zoom option.

- type_safety (boolean; optional):
    If any port can connect to any other port."""
    _children_props = []
    _base_nodes = ['children']
    _namespace = 'flowfunc'
    _type = 'Flowfunc'


    def __init__(
        self,
        id: typing.Optional[typing.Union[str, dict]] = None,
        style: typing.Optional[typing.Any] = None,
        nodes: typing.Optional[dict] = None,
        nodes_status: typing.Optional[dict] = None,
        editor_status: typing.Optional[str] = None,
        selected_nodes: typing.Optional[typing.Sequence] = None,
        double_clicked_node: typing.Optional[str] = None,
        comments: typing.Optional[dict] = None,
        type_safety: typing.Optional[bool] = None,
        default_nodes: typing.Optional[typing.Sequence] = None,
        context: typing.Optional[dict] = None,
        initial_scale: typing.Optional[NumberType] = None,
        disable_zoom: typing.Optional[bool] = None,
        disable_pan: typing.Optional[bool] = None,
        space_to_pan: typing.Optional[bool] = None,
        disable_focus: typing.Optional[bool] = None,
        config: typing.Optional[dict] = None,
        **kwargs
    ):
        self._prop_names = ['id', 'comments', 'config', 'context', 'default_nodes', 'disable_focus', 'disable_pan', 'disable_zoom', 'double_clicked_node', 'editor_status', 'initial_scale', 'nodes', 'nodes_status', 'selected_nodes', 'space_to_pan', 'style', 'type_safety']
        self._valid_wildcard_attributes =            []
        self.available_properties = ['id', 'comments', 'config', 'context', 'default_nodes', 'disable_focus', 'disable_pan', 'disable_zoom', 'double_clicked_node', 'editor_status', 'initial_scale', 'nodes', 'nodes_status', 'selected_nodes', 'space_to_pan', 'style', 'type_safety']
        self.available_wildcard_properties =            []
        _explicit_args = kwargs.pop('_explicit_args')
        _locals = locals()
        _locals.update(kwargs)  # For wildcard attrs and excess named props
        args = {k: _locals[k] for k in _explicit_args}

        super(Flowfunc, self).__init__(**args)

setattr(Flowfunc, "__init__", _explicitize_args(Flowfunc.__init__))
