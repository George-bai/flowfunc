import React, { Component } from 'react';
import * as R from 'ramda'
import { NodeEditor, FlumeConfig, Colors, Controls } from 'flume'
import PropTypes from 'prop-types';
import { standardControls } from '../utils/Controls';
import { usePortHighlighter } from '../hooks/usePortHighlighter';
import "./nodeeditor.css"

const RANDOM_KEY_RADIX = 36;
const RANDOM_KEY_SUBSTRING_START = 7;
const DISMISS_CONTEXT_MENU_DELAY_MS = 50;
const FIT_TO_VIEW_MIN_SCALE = 0.1;
const FIT_TO_VIEW_MAX_SCALE = 7;
const FIT_TO_VIEW_EPSILON = 1e-3;
const FIT_TO_VIEW_STEP = 0.05;
const FIT_TO_VIEW_DENOM_EPSILON = 1e-6;
const FIT_TO_VIEW_WHEEL_SENSITIVITY = 0.005;

/**
 * Flowfunc: A node editor for dash
 * This component gives a flow based programming interface for dash users.
 * The developer can define the nodes using simple python functions and these
 * will be available as nodes which can be connected together to create a logic
 * at runtime.
 */
// Wrapper component to use hooks with class component
const FlowfuncWithPortHighlighter = (props) => {
  const containerRef = React.useRef(null);
  
  // Use the port highlighter hook properly
  usePortHighlighter(props.config, props.nodes, props.type_safety, containerRef);
  
  return <FlowfuncClass {...props} containerRef={containerRef} />;
};

class FlowfuncClass extends Component {

  constructor(props) {
    super(props)
    this.nodeEditor = React.createRef();
    this.container = this.props.containerRef || React.createRef();
    this.ukey = (new Date()).toISOString();
    this.localSelectedNodes = new Set();
    this.state = { contextMenu: { visible: false, x: 0, y: 0, nodeId: null } };
    this.nodeLabels = (this.props.node_labels || {});
    this.fitToView = this.fitToView.bind(this);
    this.handleChange = this.handleChange.bind(this);
    this.updateConfig();
  }

  // Compute and apply a transform that fits all nodes in view
  fitToView() {
    try {
      const container = this.container && this.container.current;
      if (!container) {
        return;
      }
      const stage = container.querySelector('[data-flume-stage="true"], [data-flume-component="stage"]');
      if (!stage) {
        return;
      }

      // Collect all node rects
      const nodeEls = container.querySelectorAll('[data-flume-component="node"]');
      if (!nodeEls || nodeEls.length === 0) {
        return;
      }

      // Stage viewport rect
      const rect = stage.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) {
        return;
      }

      // Get current scale and translate from inline transforms
      const translateWrapper = stage.children && stage.children[0];
      const scaleWrapper = translateWrapper && translateWrapper.children && translateWrapper.children[0];

      const parseScale = (el) => {
        if (!el) {
          return 1;
        }
        const t = el.style && el.style.transform ? el.style.transform : '';
        const m = t.match(/scale\(([^)]+)\)/);
        const s = m ? parseFloat(m[1]) : 1;
        return isFinite(s) && s > 0 ? s : 1;
      };

      const parseTranslate = (el) => {
        if (!el) {
          return { x: 0, y: 0 };
        }
        const t = el.style && el.style.transform ? el.style.transform : '';
        const m = t.match(/translate\(([-0-9.]+)px,\s*([-0-9.]+)px\)/);
        if (m) {
          const tx = parseFloat(m[1]);
          const ty = parseFloat(m[2]);
          // transform is translate(-Tx, -Ty), so invert sign
          return { x: isFinite(tx) ? -tx : 0, y: isFinite(ty) ? -ty : 0 };
        }
        return { x: 0, y: 0 };
      };

      const s0 = parseScale(scaleWrapper);
      const T0 = parseTranslate(translateWrapper);

      // Compute union bbox of nodes in screen coords
      let left = Infinity, right = -Infinity, top = Infinity, bottom = -Infinity;
      nodeEls.forEach((el) => {
        const r = el.getBoundingClientRect();
        left = Math.min(left, r.left);
        right = Math.max(right, r.right);
        top = Math.min(top, r.top);
        bottom = Math.max(bottom, r.bottom);
      });
      if (!isFinite(left) || !isFinite(right) || !isFinite(top) || !isFinite(bottom)) {
        return;
      }

      const bboxW = Math.max(1, right - left);
      const bboxH = Math.max(1, bottom - top);

      // pixels
      const margin = 40;
      const targetScaleByW = s0 * ((rect.width - 2 * margin) / bboxW);
      const targetScaleByH = s0 * ((rect.height - 2 * margin) / bboxH);
      const sTarget = Math.max(FIT_TO_VIEW_MIN_SCALE, Math.min(FIT_TO_VIEW_MAX_SCALE, Math.min(targetScaleByW, targetScaleByH)));

      // Center in world-space and compute target translate
      const cx = (left + right) / 2;
      const cy = (top + bottom) / 2;
      const wcx = (cx - rect.x - rect.width / 2 + T0.x) / s0;
      const wcy = (cy - rect.y - rect.height / 2 + T0.y) / s0;
      const TTarget = { x: sTarget * wcx, y: sTarget * wcy };

      // Prefer patched Flume API if available
      const api = this.nodeEditor && this.nodeEditor.current;
      if (api && typeof api.setStageTransform === 'function') {
        api.setStageTransform({ scale: sTarget, translate: TTarget });
        return;
      }

      // Fallback: animate with wheel events if zoom is enabled
      // cannot animate without wheel handler
      if (this.props.disable_zoom) {
        return;
      }

      const animateStep = () => {
        const s = animateStep._s;
        const T = animateStep._T;
        if (Math.abs(sTarget - s) < FIT_TO_VIEW_EPSILON) {
          return;
        }
        const dir = sTarget > s ? 1 : -1;
        const sNext = dir > 0 ? Math.min(s + FIT_TO_VIEW_STEP, sTarget) : Math.max(s - FIT_TO_VIEW_STEP, sTarget);
        const ratio = sNext / s;
        const denom = ratio - 1;
        if (Math.abs(denom) < FIT_TO_VIEW_DENOM_EPSILON) {
          return;
        }
        const alphaX = (TTarget.x - T.x) / denom - T.x;
        const alphaY = (TTarget.y - T.y) / denom - T.y;
        const clientX = rect.x + rect.width / 2 + alphaX;
        const clientY = rect.y + rect.height / 2 + alphaY;
        // will be within [-10, 10]
        const deltaY = (s - sNext) / FIT_TO_VIEW_WHEEL_SENSITIVITY;
        const evt = new WheelEvent('wheel', {
          clientX,
          clientY,
          deltaY,
          bubbles: true,
          cancelable: true
        });
        stage.dispatchEvent(evt);
        // Update local state for next iteration
        animateStep._s = sNext;
        animateStep._T = { x: TTarget.x, y: TTarget.y };
        window.requestAnimationFrame(animateStep);
      };
      animateStep._s = s0;
      animateStep._T = { x: T0.x, y: T0.y };
      window.requestAnimationFrame(animateStep);
    } catch (e) {
      // Silently ignore fit errors
    }
  }

  createDisplayNodePorts(ports, inputData, connections, _context) {
    // Auto-expanding display node with compacting behavior
    const connected_ports = new Set();
    
    if (connections.inputs) {
      for (const portName in connections.inputs) {
        if (portName.startsWith('input')) {
          const portIndex = parseInt(portName.replace('input', ''), 10);
          if (!isNaN(portIndex)) {
            connected_ports.add(portIndex);
          }
        }
      }
    }
    
    // Sort connected ports to maintain order
    const sorted_connected_ports = Array.from(connected_ports).sort((a, b) => a - b);
    
    // Store compacting information for later use in handleChange
    if (sorted_connected_ports.length > 0) {
      // Check if we need to compact connections (if there are gaps)
      let needsCompacting = false;
      for (let i = 0; i < sorted_connected_ports.length; i++) {
        if (sorted_connected_ports[i] !== i) {
          needsCompacting = true;
          break;
        }
      }
      
      if (needsCompacting) {
        // Store the compacting information for handleChange to process
        this.pendingDisplayCompacting = {
          originalPorts: sorted_connected_ports,
          targetPorts: Array.from({length: sorted_connected_ports.length}, (_, i) => i)
        };
      }
    }
    
    const arr = [];
    
    // Create sequential ports (compacted)
    for (let i = 0; i < sorted_connected_ports.length; i++) {
      arr.push(ports.object({ 
        name: `input${i}`,
        label: `Input ${i + 1} (connected)`,
        acceptTypes: ['str', 'int', 'float', 'bool', 'object', 'list', 'dict']
      }));
    }
    
    // Add one empty port at the end
    arr.push(ports.object({ 
      name: `input${sorted_connected_ports.length}`, 
      label: `Input ${sorted_connected_ports.length + 1}`,
      acceptTypes: ['str', 'int', 'float', 'bool', 'object', 'list', 'dict']
    }));
    
    return arr;
  }

  updateConfig() {
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
            render: (data, onChange, context, redraw, portProps, _inputData) => {
              return <label data-flume-component="port-label" className="IoPorts_portLabel__qOE7y"> {portProps.inputLabel}</label>;
            }
          })
        ];
      }
      try {
        // The standard ports are already added and hence will cause an error here
        this.flconfig.addPortType(port_obj);
      } catch (e) {
        void e;
      }
    }
    for (const node of config.nodeTypes) {
      const { inputs, outputs, label, category, ...node_obj } = node;
      if (!R.isNil(inputs) && !R.isEmpty(inputs)) {
        if (R.hasIn("source", inputs)) {
          console.error('PortFunction.source is not supported. Use PortFunction.path instead.');
          node_obj.inputs = () => () => [];
        }
        else if (R.hasIn("path", inputs)) {
          try{
            node_obj.inputs = ports => (inputData, connections, context) => {
              // Check if it's the display node
              if (inputs.path === "utils.toolnodes.display") {
                // Embedded display node dynamic port logic
                return this.createDisplayNodePorts(ports, inputData, connections, context);
              }
              
              // For other dynamic functions, try to find them in window
              const func = (window.dash_clientside && window.dash_clientside.flowfunc && window.dash_clientside.flowfunc[inputs.path]);
              if (!func) {
                return [];
              }
              
              return func(ports, inputData, connections, context, Controls);
            }
          }
          catch (e){
            // Handle errors silently
            void e;
          }
        }
        else {
          node_obj.inputs = ports => inputs.map(input => {
            const { type, controls, ...input_data } = input;
            void controls;
            return ports[type](input_data);
          })
        }
      }
      if (!R.isNil(outputs) && !R.isEmpty(outputs)) {
        if (R.hasIn("source", outputs)) {
          console.error('PortFunction.source is not supported. Use PortFunction.path instead.');
          node_obj.outputs = () => () => [];
        }
        else if (R.hasIn("path", outputs)) {
          node_obj.outputs = ports => (inputData, connections, context) => {
            const func = (window.dash_clientside && window.dash_clientside.flowfunc && window.dash_clientside.flowfunc[outputs.path]);
            if (!func) {
              return [];
            }
            return func(ports, inputData, connections, context, Controls);
          }
        }
        else {
          node_obj.outputs = ports => outputs.map(output => {
            const { type, controls, ...output_data } = output;
            void controls;
            return ports[type](output_data);
          })
        }
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
      for (const [, obj] of Object.entries(this.flconfig.portTypes)) {
        obj.acceptTypes = allPortTypes;
      }
    }
  }

  handleChange() {
    // Get current nodes before processing
    const currentNodes = this.nodeEditor.current.getNodes();
    
    // Handle display node compacting if needed
    if (this.pendingDisplayCompacting) {
      const { originalPorts, targetPorts } = this.pendingDisplayCompacting;
      
      // Find all display nodes that need compacting
      for (const [nodeId, node] of Object.entries(currentNodes)) {
        if (node.type === "utils.toolnodes.display") {
          // Create mapping from old port names to new port names
          const portMapping = {};
          for (let i = 0; i < originalPorts.length; i++) {
            portMapping[`input${originalPorts[i]}`] = `input${targetPorts[i]}`;
          }
          
          // Update connections for this display node
          if (node.connections && node.connections.inputs) {
            const newInputs = {};
            for (const [portName, connections] of Object.entries(node.connections.inputs)) {
              if (portMapping[portName]) {
                // Move connection to new port
                newInputs[portMapping[portName]] = connections;
              } else if (!portName.startsWith('input')) {
                // Keep non-input ports as is
                newInputs[portName] = connections;
              }
              // Note: old input connections that aren't remapped are automatically removed
            }
            node.connections.inputs = newInputs;
          }
          
          // Also need to update all outgoing connections TO this display node
          for (const [, otherNode] of Object.entries(currentNodes)) {
            if (otherNode.connections && otherNode.connections.outputs) {
              for (const [outputPort, outputConnections] of Object.entries(otherNode.connections.outputs)) {
                if (Array.isArray(outputConnections)) {
                  // Filter and update connections - remove old ones, update existing ones
                  const newConnections = [];
                  for (const connection of outputConnections) {
                    if (connection.nodeId === nodeId) {
                      if (portMapping[connection.portName]) {
                        // Update the connection to new port
                        const newConnection = { ...connection, portName: portMapping[connection.portName] };
                        newConnections.push(newConnection);
                      }
                      // Note: connections to unmapped ports (old ports) are not added = removed
                    } else {
                      // Keep connections to other nodes
                      newConnections.push(connection);
                    }
                  }
                  otherNode.connections.outputs[outputPort] = newConnections;
                }
              }
            }
          }
        }
      }
      
      // Clear the pending compacting
      this.pendingDisplayCompacting = null;
      
      // Schedule a re-render after the current update cycle
      this.needsForceRerender = true;
    }
    
    // Dash function which will raise the nodes properties
    this.props.setProps({
      editor_status: "client",
      nodes: currentNodes,
      comments: this.nodeEditor.current.getComments(),
    })
    // console.log(this.props.comments);
    // console.log(this.props.nodes);
  }

  componentDidMount() {
    // Adding on click event listners to nodes
    this.addEventListners();
    // console.log("Adding listeners")
    this.applyNodeLabels();
  }

  componentDidUpdate(prevProps) {
    if (this.props.config !== prevProps.config) {
      this.updateConfig();
    }
    if (this.props.editor_status === "server") {
      // console.log("Pushing new nodes", this.props.nodes)
      this.ukey = (Math.random() + 1).toString(RANDOM_KEY_RADIX).substring(RANDOM_KEY_SUBSTRING_START);
    }

    
    // Handle forced re-render after compacting
    if (this.needsForceRerender) {
      this.needsForceRerender = false;
      if (this.nodeEditor.current && this.nodeEditor.current.setNodes) {
        this.nodeEditor.current.setNodes(this.props.nodes);
      }
      // Generate new key to force React re-render
      this.ukey = (Math.random() + 1).toString(RANDOM_KEY_RADIX).substring(RANDOM_KEY_SUBSTRING_START);
    }
    
    this.setNodesStatus();

    // Programmatic fit-to-view trigger from Dash
    if (this.props.fit_to_view_request !== prevProps.fit_to_view_request &&
        typeof this.props.fit_to_view_request !== 'undefined') {
      this.fitToView();
    }
    // Sync node labels from Dash if they changed
    if (this.props.node_labels !== prevProps.node_labels && this.props.node_labels) {
      this.nodeLabels = { ...this.props.node_labels };
    }
    this.applyNodeLabels();
  }

  setNodeStatus(id, status) {
    // Removing any existing classes
    const nodeDiv = this.container.current.querySelector('[data-node-id="' + id + '"]');
    if (status) {
      const classes = ["started", "queued", "deferred", "finished", "canceled", "stopped", "scheduled", "failed"];
      nodeDiv.classList.remove(...classes);
      // Status itself is added as the class
      nodeDiv.classList.add(status);
    }
  }

  setNodesStatus() {
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



  addEventListners() {
    const comp = this;
    const stage = this.container.current
    var containerEventListenerAdded = stage.getAttribute("data-event-click");
    if (containerEventListenerAdded !== "true") {
      stage.addEventListener('click', function (e) {
        // console.log("Clicked", e);
        if (!e.ctrlKey) {
          comp.localSelectedNodes = new Set();
          for (const [id] of Object.entries(comp.props.nodes)) {
            try {
              const nodeDiv = stage.querySelector('[data-node-id = "' + id + '"]');
              nodeDiv.classList.remove("active")
            } catch (error) {
              // console.log("error", error);
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
          const header = e.target.closest('h2');
          if (header && nodeDiv.contains(header)) {
            e.stopPropagation();
            comp.openRenameEditorForNode(nodeId);
          } else {
            comp.props.setProps({ double_clicked_node: nodeId });
          }
        }
      })
      stage.addEventListener('contextmenu', function (e) {
        const nodeDiv = e.target.closest('[class^=Node_wrapper]');
        if (nodeDiv) {
          // Let Flume open its own menu, then inject our items into it
          comp._lastContextNodeId = nodeDiv.getAttribute('data-node-id');
          setTimeout(() => comp.injectRenameItems(), 0);
        }
      })
      stage.setAttribute("data-event-click", "true");
    }
  }

  applyNodeLabels() {
    try {
      if (!this.container || !this.container.current) {
        return;
      }
      const labels = this.nodeLabels || {};
      for (const [nid, lbl] of Object.entries(labels)) {
        if (!lbl) {
          continue;
        }
        const nodeDiv = this.container.current.querySelector('[data-node-id="' + nid + '"]');
        if (!nodeDiv) {
          continue;
        }
        const header = nodeDiv.querySelector('h2');
        if (header && header.textContent !== lbl) {
          header.textContent = lbl;
        }
      }
    } catch (e) {
      void e;
    }
  }

  dismissContextMenus() {
    try {
      const sels = [
        '[data-flume-component="context-menu"]',
        '[data-flume-component="menu"]',
        'div[class*="ContextMenu"]',
        'div[class*="Popover"]',
        'div[class*="Menu"]'
      ];
      for (const s of sels) {
        const els = Array.from(document.querySelectorAll(s));
        for (const el of els) {
          try {
            el.style.display = 'none';
            el.setAttribute('aria-hidden', 'true');
          } catch (e) {
            void e;
          }
        }
      }
      const stray = Array.from(document.querySelectorAll('div,span'));
      for (const el of stray) {
        const t = (el.textContent || '').trim();
        if (t === 'Node Options') {
          try {
            el.style.display = 'none';
            el.setAttribute('aria-hidden', 'true');
          } catch (e) {
            void e;
          }
        }
      }
      // Remove any native tooltip sources
      const titled = Array.from(document.querySelectorAll('[title]'));
      for (const el of titled) {
        const val = el.getAttribute('title');
        if (val && val.trim() === 'Node Options') {
          try {
            el.removeAttribute('title');
          } catch (e) {
            void e;
          }
        }
      }
      const aria = Array.from(document.querySelectorAll('[aria-label]'));
      for (const el of aria) {
        const val = el.getAttribute('aria-label');
        if (val && val.trim() === 'Node Options') {
          try {
            el.removeAttribute('aria-label');
          } catch (e) {
            void e;
          }
        }
      }
      // Nudge the browser tooltip to disappear
      try {
        document.body.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 0, clientY: 0 }));
      } catch (e) {
        void e;
      }
    } catch (e) {
      void e;
    }
  }

  openRenameEditorForNode(nodeId) {
    try {
      try {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
      } catch (e) {
        void e;
      }
      this.dismissContextMenus();
      const nodeDiv = this.container.current.querySelector('[data-node-id="' + nodeId + '"]');
      if (!nodeDiv) {
        return;
      }
      const header = nodeDiv.querySelector('h2');
      if (!header) {
        return;
      }
      const original = header.textContent || '';
      const initial = this.nodeLabels[nodeId] || original || '';
      header.setAttribute('contenteditable', 'true');
      header.setAttribute('spellcheck', 'false');
      header.textContent = initial;
      header.focus();
      // Select the full text and place the caret at the end
      const range = document.createRange();
      const node = header.firstChild || header;
      try {
        range.setStart(node, 0);
        range.setEnd(node, (header.textContent || '').length);
      } catch (err) {
        void err;
        try {
          range.selectNodeContents(header);
        } catch (e) {
          void e;
        }
      }
      const sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(range);
      }

      const comp = this;

      const onBlur = () => commit();
      const onKey = (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
        if (ev.key === 'Escape') { ev.preventDefault(); cancel(); }
      };

      function commit() {
        const newVal = (header.textContent || '').trim();
        header.removeAttribute('contenteditable');
        if (newVal && newVal !== original) {
          comp.nodeLabels[nodeId] = newVal;
          comp.applyNodeLabels();
          try {
            if (comp.props.setProps) {
              comp.props.setProps({ node_labels: { ...comp.nodeLabels } });
            }
          } catch (e) {
            void e;
          }
        } else {
          header.textContent = original;
        }
        header.removeEventListener('blur', onBlur);
        header.removeEventListener('keydown', onKey);
      }
      function cancel() {
        header.removeAttribute('contenteditable');
        header.textContent = original;
        header.removeEventListener('blur', onBlur);
        header.removeEventListener('keydown', onKey);
      }
      header.addEventListener('blur', onBlur);
      header.addEventListener('keydown', onKey);
    } catch (e) {
      void e;
    }
  }

  injectRenameItems() {
    try {
      // Try to find Flume's context menu container
      let menu = null;
      const menus = Array.from(document.querySelectorAll('[data-flume-component="ctx-menu"]'));
      if (menus.length) {
        menu = menus[menus.length - 1];
      }
      if (!menu) {
        const candidates = Array.from(document.querySelectorAll('div[class*="ContextMenu"], [data-flume-component="menu"], [data-flume-component="contextmenu"]'));
        if (candidates.length) {
          menu = candidates[candidates.length - 1];
        }
      }
      if (!menu) {
        return;
      }

      // Determine the list container and sample item class
      const list = menu.querySelector('[data-flume-component="ctx-menu-list"]') || menu.querySelector('ul') || menu;
      const sample = list.querySelector('[data-flume-component="ctx-menu-option"]') || list.firstElementChild;
      const itemTag = (list.tagName || '').toLowerCase() === 'ul' ? 'li' : 'div';

      // Try to detect the description styling from the built-in Delete item
      let descProto = null;
      try {
        for (const child of Array.from(list.children)) {
          const t = (child.textContent || '').toLowerCase();
          if (t.includes('delete node')) {
            const cand = Array.from(child.querySelectorAll('*')).find(el => /deletes\s+a\s+node/i.test(el.textContent || ''));
            if (cand) { descProto = cand; break; }
          }
        }
      } catch (e) {
        void e;
      }

      const ensureItem = (key, label, handler, descText) => {
        if (list.querySelector('[data-ff-action="' + key + '"]')) {
          return;
        }
        const el = document.createElement(itemTag);
        if (sample && sample.className) {
          el.className = sample.className;
        }
        el.setAttribute('data-ff-action', key);
        el.setAttribute('data-flume-component', 'ctx-menu-option');
        el.setAttribute('role', 'menuitem');
        el.style.cursor = 'pointer';
        const labelEl = document.createElement('label');
        labelEl.textContent = label;
        el.appendChild(labelEl);
        el.addEventListener('click', (ev) => {
          ev.stopPropagation();
          try {
            ev.preventDefault();
          } catch (e) {
            void e;
          }
          try {
            if (menu && menu.style) {
              menu.style.display = 'none';
            }
          } catch (e) {
            void e;
          }
          try {
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
          } catch (e) {
            void e;
          }
          this.dismissContextMenus();
          setTimeout(() => this.dismissContextMenus(), 0);
          setTimeout(() => this.dismissContextMenus(), DISMISS_CONTEXT_MENU_DELAY_MS);
          const nid = this._lastContextNodeId;
          handler(nid);
        });
        // Insert at top to make it visible immediately
        if (list.firstChild) {
          list.insertBefore(el, list.firstChild);
        } else {
          list.appendChild(el);
        }

        // Add description (clone style from Delete Node item if possible)
        if (descText) {
          let descEl;
          if (descProto) {
            descEl = document.createElement(descProto.tagName || 'p');
            if (descProto.className) {
              descEl.className = descProto.className;
            }
          } else {
            descEl = document.createElement('p');
          }
          descEl.textContent = descText;
          el.appendChild(descEl);
        }
      };

      ensureItem('rename-node', 'Rename node', (nid) => {
        if (nid) {
          setTimeout(() => this.openRenameEditorForNode(nid), 0);
        }
      }, 'Renames the node\'s display label.');
    } catch (e) {
      void e;
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
            initialScale={this.props.initial_scale}
            disableZoom={this.props.disable_zoom}
            disablePan={this.props.disable_pan}
            disableFocus={this.props.disable_focus}
            spaceToPan={this.props.space_to_pan}
            circularBehavior="allow"
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

          {/* Fit-to-View button */}
          <button
            onClick={this.fitToView}
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
            title={'Fit to View'}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M4 9V5H8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M16 5H20V9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M20 15V19H16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M8 19H4V15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>

        </div>
      </React.Fragment>
    );
    return output;
  }
}

FlowfuncClass.defaultProps = {};

FlowfuncClass.propTypes = {
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
   * Incrementing number to request a fit-to-view action.
   * Increase this value (e.g., n_clicks) to programmatically trigger Fit.
   */
  fit_to_view_request: PropTypes.number,

  /**
   * Disable automatic focusing behavior in the editor (Flume 1.1.0)
   */
  disable_focus: PropTypes.bool,

  /**
   * The available port types and node types
   */
  config: PropTypes.object,

  /**
   * Mapping of nodeId -> display label
   */
  node_labels: PropTypes.object,

  /**
   * Dash-assigned callback that should be called to report property changes
   * to Dash, to make them available for callbacks.
   */
  setProps: PropTypes.func,
  containerRef: PropTypes.object
};

// Copy PropTypes to wrapper component
FlowfuncWithPortHighlighter.defaultProps = {};

FlowfuncWithPortHighlighter.propTypes = {
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
   * Disable automatic focusing behavior in the editor (Flume 1.1.0)
   */
  disable_focus: PropTypes.bool,

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

// Export the wrapper component as default
export default FlowfuncWithPortHighlighter;
