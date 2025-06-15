# Flowfunc Development Guide

This document provides instructions for setting up a development environment for the Flowfunc project and guidelines for adding or modifying features.

## What is Flowfunc?

Flowfunc is a Plotly Dash component that provides a node editor interface based on the React package [Flume](https://flume.dev). It allows users to:

- Create nodes based on Python functions (using function signatures)
- Connect nodes together visually in a web interface
- Define logic during runtime
- Execute the workflow with the JobRunner in various modes (sync, async, distributed)

## Key Components

- **Config**: Manages nodes and ports available in the editor
- **JobRunner**: Processes the node editor output (sync, async, distributed)
- **Nodes**: Building blocks created from Python functions
- **Ports**: Inputs and outputs of nodes that render controls

## Development Environment Setup

### Prerequisites

- Python 3.10+
- Node.js 8.11.0+ and npm 6.1.0+ (newer versions recommended)

### Setup Steps

1. **Clone the repository**

   ```bash
   git clone https://github.com/idling-mind/flowfunc.git
   cd flowfunc
   ```

2. **Create a virtual environment and activate it**

   ```bash
   # Windows
   python -m venv venv
   .\venv\Scripts\activate
   
   # Unix/macOS
   python -m venv venv
   source venv/bin/activate
   ```

3. **Install the package in development mode with all dependencies**

   ```bash
   pip install -e .[full]
   ```

4. **Install Node.js dependencies**

   ```bash
   npm install
   ```

5. **Build the JavaScript components**

   ```bash
   npm run build:js
   ```

6. **Test the installation**

   Run one of the example applications to verify your setup:

   ```bash
   python examples/usage.py
   ```

   This should start a Dash server, and you can access the application at http://127.0.0.1:8050/

## Project Structure

- `/flowfunc/` - Python package core files
  - `__init__.py` - Package initialization
  - `Flowfunc.py` - Main component class
  - `config.py` - Configuration handling
  - `jobrunner.py` - Execution engine
  - `models.py` - Data models (nodes, ports)
  - `types.py` - Custom types
  - `utils.py` - Utility functions
  - `distributed.py` - Distributed execution support

- `/src/` - Frontend React components
  - `/lib/components/` - React components

- `/examples/` - Example Dash applications
  - `usage.py` - Basic example
  - `usage_rq.py` - Distributed execution example
  - `nodes.py` - Example node definitions

## Adding/Modifying Features

### Backend (Python)

1. **Add new node types**: 
   - Extend or modify the node creation logic in `config.py`
   - Update the `models.py` file if you need to add new data structures

2. **Enhance JobRunner**:
   - Modify `jobrunner.py` to add new execution modes or optimizations
   - For distributed computing features, update `distributed.py`

3. **Add custom types**:
   - Define new types in `types.py`
   - Update port handling in `config.py` to use these types

### Frontend (React/JavaScript)

1. **Modify UI components**:
   - Edit the React components in `/src/lib/components/`
   - The main component is likely in `/src/lib/components/Flowfunc.react.js` or similar

2. **Add node rendering improvements**:
   - Check how nodes are rendered and update the relevant components

3. **Build after changes**:
   ```bash
   npm run build:js
   ```

## Testing Your Changes

1. **Run examples**:
   ```bash
   python examples/usage.py
   ```

2. **Create your own test scripts**:
   - Create a new Python file in the examples directory
   - Import your modified flowfunc components
   - Test your new features

## Common Workflows

### Adding a New Node Type

1. Define a Python function with type annotations
2. Add it to the Config object using `Config.from_function_list([your_function])`
3. Test it in an example application

### Adding a New Port Control

1. Update the port type handling in `config.py`
2. Potentially update the frontend rendering in the React components
3. Build the JavaScript files using `npm run build:js`
4. Test the new control in an example

### Distribution & Publishing

To build a distribution package:

```bash
# Build JavaScript components
npm run build

# Create Python distribution
python setup.py sdist bdist_wheel
```

## Troubleshooting

- **Module not found errors**: Ensure your virtual environment is activated and the package is installed in development mode
- **JavaScript build errors**: Check Node.js and npm versions, and ensure all dependencies are installed
- **UI rendering issues**: Check browser console for React errors
- **Missing dash-generate-components**: This tool might not be available in newer versions, focus on building the JavaScript components manually with `npm run build:js`

## Contributing

- Follow the coding style of the existing codebase
- Add tests for new features
- Update documentation as needed
- Submit a pull request with a clear description of your changes

## Resources

- [Flowfunc Demo](https://najeem.pythonanywhere.com/)
- [Flume Documentation](https://flume.dev)
- [Dash Documentation](https://dash.plotly.com)
