window.dash_clientside = Object.assign({}, window.dash_clientside, {
    flowfunc: {
        dynamic_ports: function (ports, inputData, connections, context) {
            // Example from flume.dev
            console.log(inputData);
            const template =
                (inputData &&
                    inputData.template &&
                    Object.values(inputData.template)[0]) ||
                '';
            const re = /\{(.*?)\}/g;
            let res, ids = []
            while ((res = re.exec(template)) !== null) {
                if (!ids.includes(res[1])) ids.push(res[1]);
            }
            return [
                ports.str({ name: "template", label: "Template", hidePort: true }),
                ...ids.map(id => ports.str({ name: id, label: id }))
            ];
        },
        increasing_ports: function (ports, inputData, connections, context) {
            const arr = [];
            connection_count = Object.keys(connections.inputs).length;
            for (let i = 0; i <= connection_count; i++) {
                arr.push(ports.str({ name: `port${i}`, label: `String ${i}` }));
            }
            return arr
        },
        // Dynamic inputs for selecting columns from a DataFrame input
        'utils.toolnodes.select_columns_inputs': function (ports, inputData, connections, context, Controls) {
            const dfPortName = 'df';
            // If Controls isn't passed by the component (older build), avoid crashing
            if (!Controls || typeof Controls.multiselect !== 'function') {
                return [
                    ports.DataFrame({ name: dfPortName, label: 'DataFrame' }),
                    // Fallback: allow user to type CSV list of columns
                    ports.str({
                        name: 'columns',
                        label: 'Columns (comma-separated)',
                        hidePort: false,
                    })
                ];
            }
            let columns = [];
            try {
                const connList = connections && connections.inputs && connections.inputs[dfPortName];
                if (Array.isArray(connList) && connList.length > 0) {
                    const sourceNodeId = connList[0].nodeId;
                    columns = (((context || {}).nodes || {})[sourceNodeId] || {}).columns || [];
                }
            } catch (e) {
                // ignore
            }
            const options = (columns || []).map(c => ({ label: String(c), value: c }));

            return [
                // Visible DataFrame input port
                ports.DataFrame({ name: dfPortName, label: 'DataFrame' }),
                // Hidden port carrying a multiselect control for columns
                ports.object({
                    name: 'columns',
                    label: 'Columns',
                    hidePort: true,
                    controls: [
                        Controls.multiselect({
                            name: 'columns',
                            label: 'Columns',
                            options
                        })
                    ]
                })
            ];
        }
    }
});