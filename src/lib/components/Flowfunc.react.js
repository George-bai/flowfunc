import React, { Component } from 'react';
import * as R from 'ramda'
import { NodeEditor } from 'flume';
import { FlumeConfig, Colors, Controls } from 'flume'
import PropTypes, { string } from 'prop-types';
import { standardControls } from '../utils/Controls';
import "./nodeeditor.css"

/**
 * Flowfunc: A node editor for dash
 * This component gives a flow based programming interface for dash users.
 * The developer can define the nodes using simple python functions and these
 * will be available as nodes which can be connected together to create a logic
 * at runtime.
 */
export default class Flowfunc extends Component {

  constructor(props) {
    super(props)
    this.nodeEditor = React.createRef();
    this.container = React.createRef();
    this.ukey = (new Date()).toISOString();
    this.localSelectedNodes = new Set();
    this.fitToViewScale = this.props.initial_scale || 1;
    this.updateConfig();
  }

  updateConfig = () => {
    // Function to convert the python based config data to a FlumeConfig object
    const config = this.props.config;
    this.flconfig = new FlumeConfig();
    // Adding all standard ports first
    for (const port of config.portTypes) {
      const { color, controls, ...port_obj } = port;
      if (!R.isNil(color) && !R.isEmpty(color)) {
        port_obj.color = Colors[color];
      }
      if (!R.isNil(controls) && !R.isEmpty(controls)) {
        port_obj.controls = controls.map(control => {
          const { type, ...others } = control;
          return standardControls[type]({
            ...others
          })
        })
      }
      else {
        port_obj.controls = [
          Controls.custom({
            name: port_obj.type,
            label: port_obj.label,
            defaultValue: null,
            render: (data, onChange, context, redraw, portProps, inputData) => {
              return <label data-flume-component="port-label" className="IoPorts_portLabel__qOE7y"> {portProps.inputLabel}</label>;
            }
          })
        ];
      }
      try {
        //The standard ports are already added and hence will cause an error here
        this.flconfig.addPortType(port_obj);
      } catch (e) {
      }
    }
    for (const node of config.nodeTypes) {
      const { inputs, outputs, label, category, ...node_obj } = node;
      if (!R.isNil(inputs) && !R.isEmpty(inputs)) {
        if (R.hasIn("source", inputs)) {
          var func = new Function(inputs.source);
          node_obj.inputs = ports => (inputData, connections, context) => {
            return func(ports, inputData, connections, context)
          }
        }
        else if (R.hasIn("path", inputs)) {
          try{
            node_obj.inputs = ports => (inputData, connections, context) => {
              var func = window.dash_clientside.flowfunc[inputs.path];
              return func(ports, inputData, connections, context)
            }
          }
          catch (e){
            console.log("Error in evaluating function from path", e);
          }
        }
        else {
          node_obj.inputs = (ports) => inputs.map(input => {
            const { type, controls, ...input_data } = input;
            // console.log(input, type, controls, input_data);
            return ports[type](input_data);
          })
        }
      }
      if (!R.isNil(outputs) && !R.isEmpty(outputs)) {
        node_obj.outputs = (ports) => outputs.map(output => {
          const { type, controls, ...output_data } = output;
          return ports[type](output_data);
        })
      }
      if (!R.isNil(category) && !R.isEmpty(category)) {
        node_obj.label = `${category}: ${label}`;
      } else {
        node_obj.label = label;
      }
      this.flconfig.addNodeType(node_obj);
    }
    // console.log(this.flconfig);
    if (!this.props.type_safety) {
      // Use acceptTypes from the object port
      const allPortTypes = this.flconfig.portTypes.object.acceptTypes;
      for (const [type, obj] of Object.entries(this.flconfig.portTypes)) {
        obj.acceptTypes = allPortTypes;
      }
    }
  }

  handleChange = () => {
    // Dash function which will raise the nodes properties
    this.props.setProps({
      editor_status: "client",
      nodes: this.nodeEditor.current.getNodes(),
      comments: this.nodeEditor.current.getComments(),
    })
    // console.log(this.props.comments);
    // console.log(this.props.nodes);
  }

  componentDidMount() {
    this.addEventListners(); // Adding on click event listners to nodes
    // console.log("Adding listeners")
  }

  componentDidUpdate(prevProps) {
    if (this.props.config !== prevProps.config) {
      this.updateConfig();
    }
    if (this.props.editor_status === "server") {
      // console.log("Pushing new nodes", this.props.nodes)
      this.ukey = (Math.random() + 1).toString(36).substring(7);
    }
    if (this.props.fit_to_view !== prevProps.fit_to_view && this.props.fit_to_view) {
      this.performFitToView();
    }
    this.setNodesStatus();
  }

  setNodeStatus = (id, status) => {
    const nodeDiv = this.container.current.querySelector('[data-node-id="' + id + '"]');
    if (status) {
      // Removing any existing classes
      const classes = ["started", "queued", "deferred", "finished", "canceled", "stopped", "scheduled", "failed"];
      nodeDiv.classList.remove(...classes);
      nodeDiv.classList.add(status); // Status itself is added as the class
    }
  }

  setNodesStatus = () => {
    if (R.isNil(this.props.nodes_status) | R.isEmpty(this.props.nodes_status)) {
      return
    }
    for (const [id, node] of Object.entries(this.props.nodes_status)) {
      try {
        this.setNodeStatus(id, node);
      } catch (error) {
        // console.log(error);
      }
    }
  }

  performFitToView = () => {
    if (!this.props.nodes || Object.keys(this.props.nodes).length === 0) {
      return;
    }
    // Calculate bounds of all nodes including their dimensions
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    
    Object.values(this.props.nodes).forEach(node => {
      // Include the node's full dimensions in boundary calculations
      minX = Math.min(minX, node.x);
      minY = Math.min(minY, node.y);
      maxX = Math.max(maxX, node.x);
      maxY = Math.max(maxY, node.y);
    });
    
    // Add padding
    const padding = 150;
    minX -= padding;
    minY -= padding;
    maxX += padding;
    maxY += padding;
    
    // Get viewport dimensions
    const viewportWidth = this.container.current.clientWidth;
    const viewportHeight = this.container.current.clientHeight;
    
    // Calculate content size and scale to fit
    const contentWidth = maxX - minX;
    const contentHeight = maxY - minY;
    const scaleX = viewportWidth / contentWidth;
    const scaleY = viewportHeight / contentHeight;
    
    // Use the smaller scale to ensure everything fits
    // Don't cap at 1 - we want to zoom out if needed
    const scale = Math.min(scaleX, scaleY);
    const finalScale = Math.max(scale, 0.1); // Minimum scale of 0.1
    
    // Calculate center position of the content
    const centerX = (maxX + minX) / 2;
    const centerY = (maxY + minY) / 2;
    
    // Calculate translation values to center the content in the viewport
    const translateX = (viewportWidth / 2) - (centerX * finalScale);
    const translateY = (viewportHeight / 2) - (centerY * finalScale);
    
    // Apply both scale and translation to the NodeEditor
    if (this.nodeEditor.current && this.nodeEditor.current.setTransform) {
      this.nodeEditor.current.setTransform({
        x: translateX,
        y: translateY,
        scale: finalScale
      });
    }
    
    // Store the scale for future renders
    this.fitToViewScale = finalScale;
    
    // Generate a new key to force proper re-render
    this.ukey = (Math.random() + 1).toString(36).substring(7);
    
    // Trigger re-render to apply changes
    this.forceUpdate();
  }

  addEventListners = () => {
    const comp = this;
    const stage = this.container.current
    var containerEventListenerAdded = stage.getAttribute("data-event-click");
    if (containerEventListenerAdded !== "true") {
      stage.addEventListener('click', function (e) {
        // console.log("Clicked", e);
        if (!e.ctrlKey) {
          comp.localSelectedNodes = new Set();
          for (const [id, node] of Object.entries(comp.props.nodes)) {
            try {
              const nodeDiv = stage.querySelector('[data-node-id = "' + id + '"]');
              nodeDiv.classList.remove("active")
            } catch (error) {
              // console.log("error", error, node);
            }
          }
        }
        const nodeDiv = e.target.closest('[class^=Node_wrapper]')
        if (nodeDiv) {
          var nodeId = nodeDiv.getAttribute('data-node-id');
          comp.localSelectedNodes.add(nodeId);
          nodeDiv.classList.add("active")
        }
        comp.props.setProps({ selected_nodes: [...comp.localSelectedNodes] });
      })
      stage.addEventListener('dblclick', function (e) {
        const nodeDiv = e.target.closest('[class^=Node_wrapper]')
        if (nodeDiv) {
          var nodeId = nodeDiv.getAttribute('data-node-id');
          comp.props.setProps({ double_clicked_node: nodeId });
        }
      })
      stage.setAttribute("data-event-click", "true");
    }
  }


  render() {
    // this.nodeEditor.current.setNodes(this.props.nodes);
    const output = (
      <React.Fragment>
        <div id={this.props.id} style={{ height: "100%", position: 'relative' }} ref={this.container}>
          <NodeEditor
            ref={this.nodeEditor}
            portTypes={this.flconfig.portTypes}
            nodeTypes={this.flconfig.nodeTypes}
            nodes={this.props.nodes}
            defaultNodes={this.props.default_nodes}
            context={this.props.context}
            initialScale={this.fitToViewScale}
            disableZoom={this.props.disable_zoom}
            disablePan={this.props.disable_pan}
            spaceToPan={this.props.space_to_pan}
            onChange={this.handleChange}
            onCommentsChange={this.handleChange}
            key={this.ukey}
          />
        </div>
        <div 
          style={{
            position: 'fixed',
            bottom: '20px',
            left: '20px',
            zIndex: 10000,
            display: 'flex',
            flexDirection: 'column',
            gap: '2px',
            backgroundColor: 'rgba(30, 30, 30, 0.7)',
            padding: '3px',
            borderRadius: '4px',
            boxShadow: '0 2px 5px rgba(0, 0, 0, 0.2)'
          }}
        >
          {/* Zoom button */}
          <button
            onClick={() => this.props.setProps({ disable_zoom: !this.props.disable_zoom })}
            style={{
              padding: '6px',
              backgroundColor: this.props.disable_zoom ? '#555' : '#2a2a2a',
              color: this.props.disable_zoom ? '#aaa' : '#fff',
              border: 'none',
              borderRadius: '3px',
              cursor: 'pointer',
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
            title={this.props.disable_zoom ? "Enable Zoom" : "Disable Zoom"}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M20 20L16 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M11 8V14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M8 11H14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
          
          {/* Pan button */}
          <button
            onClick={() => this.props.setProps({ disable_pan: !this.props.disable_pan })}
            style={{
              padding: '6px',
              backgroundColor: this.props.disable_pan ? '#555' : '#2a2a2a',
              color: this.props.disable_pan ? '#aaa' : '#fff',
              border: 'none',
              borderRadius: '3px',
              cursor: 'pointer',
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
            title={this.props.disable_pan ? "Enable Pan" : "Disable Pan"}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M9 11.5V5.5C9 4.67157 9.67157 4 10.5 4C11.3284 4 12 4.67157 12 5.5V11" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <path d="M12 11V7.5C12 6.67157 12.6716 6 13.5 6C14.3284 6 15 6.67157 15 7.5V11" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <path d="M15 11V9.5C15 8.67157 15.6716 8 16.5 8C17.3284 8 18 8.67157 18 9.5V14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <path d="M6 12.4V14.5C6 17.5376 8.46243 20 11.5 20H12.5C15.5376 20 18 17.5376 18 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <path d="M6 12.5C6 11.6716 6.67157 11 7.5 11C8.32843 11 9 11.6716 9 12.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
          </button>
          
          {/* Fit to View button */}
          <button
            onClick={this.performFitToView}
            style={{
              padding: '6px',
              backgroundColor: '#2a2a2a',
              color: '#fff',
              border: 'none',
              borderRadius: '3px',
              cursor: 'pointer',
              width: '32px',
              height: '32px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
            title="Fit to View"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M4 8V4H8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M4 16V20H8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M16 4H20V8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M16 20H20V16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <rect x="8" y="8" width="8" height="8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        </div>
      </React.Fragment>
    );
    return output;
  }
}

Flowfunc.defaultProps = {};

Flowfunc.propTypes = {
  /**
   * The ID used to identify this component in Dash callbacks.
   */
  id: PropTypes.string,

  /**
   * The style of the container div
   */
  style: PropTypes.object,

  /**
   * The nodes of the node editor
   */
  nodes: PropTypes.object,

  /**
   * The status of each node on the editor
   */
  nodes_status: PropTypes.object,

  /**
   * A property denoting the status of the editor
   * Following statuses are possible.
   * ["client", "server"]
   */
  editor_status: PropTypes.string,

  /**
   * The nodes of the node editor
   */
  selected_nodes: PropTypes.array,
  /**
   * Node on which a double click event was registered
   */
  double_clicked_node: PropTypes.string,

  /**
   * Comments in the node editor
   */
  comments: PropTypes.object,
  /**
   * If any port can connect to any other port
   */
  type_safety: PropTypes.bool,

  /**
   * Default nodes present in the editor
   * A list of nodes from the config
   */
  default_nodes: PropTypes.array,

  /**
   * Pass extra data to nodes
   */
  context: PropTypes.object,

  /**
   * Initial zoom level of the editor
   */
  initial_scale: PropTypes.number,

  /**
   * Disable zoom option
   */
  disable_zoom: PropTypes.bool,

  /**
   * Disable zoom option
   */
  disable_pan: PropTypes.bool,

  /**
   * Disable zoom option
   */
  space_to_pan: PropTypes.bool,

  /**
   * Trigger fit to view when this prop changes to true
   */
  fit_to_view: PropTypes.bool,

  /**
   * The available port types and node types
   */
  config: PropTypes.object,

  /**
   * Dash-assigned callback that should be called to report property changes
   * to Dash, to make them available for callbacks.
   */
  setProps: PropTypes.func
};
