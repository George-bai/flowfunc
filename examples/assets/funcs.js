window.dash_clientside = Object.assign({}, window.dash_clientside, {
    flowfunc: {
        dynamic_ports: function (ports, inputData, _connections, _context) {
            // Example from flume.dev
            console.log(inputData);
            const template =
                (inputData &&
                    inputData.template &&
                    Object.values(inputData.template)[0]) ||
                '';
            const re = /\{(.*?)\}/g;
            let res;
            const ids = [];
            while ((res = re.exec(template)) !== null) {
                if (!ids.includes(res[1])) {
                    ids.push(res[1]);
                }
            }
            return [
                ports.str({ name: 'template', label: 'Template', hidePort: true }),
                ...ids.map(id => ports.str({ name: id, label: id })),
            ];
        },
        increasing_ports: function (ports, _inputData, connections, _context) {
            const arr = [];
            const connection_count = Object.keys(connections.inputs).length;
            for (let i = 0; i <= connection_count; i++) {
                arr.push(ports.str({ name: `port${i}`, label: `String ${i}` }));
            }
            return arr;
        },
        'dynamic_outputs.split_csv_outputs': function (ports, inputData, _connections, _context) {
            const csv = inputData && inputData.csv && Object.values(inputData.csv)[0];
            const parts = (csv || '').split(',');
            const arr = [];
            for (let i = 0; i < parts.length; i++) {
                const name = `item_${i}`;
                arr.push(ports.str({ name, label: `Item ${i + 1}` }));
            }
            return arr;
        },
        'dynamic_outputs.splitter_outputs': function (ports, inputData, _connections, _context) {
            const rawN = inputData && inputData.n_outputs && Object.values(inputData.n_outputs)[0];
            let n = parseInt(rawN, 10);
            if (!Number.isFinite(n)) {
                n = 0;
            }
            if (n < 0) {
                n = 0;
            }
            const MAX_OUTPUTS = 50;
            if (n > MAX_OUTPUTS) {
                n = MAX_OUTPUTS;
            }

            const rawRatios = inputData && inputData.ratios && Object.values(inputData.ratios)[0];
            const ratioVals = [];
            if (typeof rawRatios === 'string') {
                const text = rawRatios.trim();
                if (text) {
                    text.split(',').forEach(part => {
                        const v = parseFloat(part.trim());
                        if (!Number.isNaN(v)) {
                            ratioVals.push(v);
                        }
                    });
                }
            }

            // Mirror backend semantics for display labels where possible
            let ratiosForPorts = null;
            const nonNegative = ratioVals.every(r => r >= 0);
            if (n > 0 && nonNegative) {
                if (ratioVals.length === 0) {
                    // Equal split
                    ratiosForPorts = Array.from({ length: n }, () => 1 / n);
                } else if (ratioVals.length === n - 1) {
                    const sum = ratioVals.reduce((a, b) => a + b, 0);
                    if (sum <= 1) {
                        const last = 1 - sum;
                        ratiosForPorts = [...ratioVals, last];
                    }
                } else if (ratioVals.length === n) {
                    const sum = ratioVals.reduce((a, b) => a + b, 0);
                    if (sum <= 1) {
                        ratiosForPorts = ratioVals.slice(0, n);
                    }
                }
            }

            const arr = [];
            for (let i = 0; i < n; i++) {
                const baseLabel = `Part ${i + 1}`;
                let r = null;
                if (ratiosForPorts && ratiosForPorts.length === n) {
                    r = ratiosForPorts[i];
                } else if (i < ratioVals.length) {
                    r = ratioVals[i];
                }
                const label = r !== null ? `${baseLabel} (${r})` : baseLabel;
                arr.push(ports.float({ name: `part_${i}`, label }));
            }
            return arr;
        },

        // Dynamic inputs for selecting columns from a DataFrame input
        'utils.toolnodes.select_columns_inputs': function (ports, inputData, connections, context, controls) {
            const dfPortName = 'df';
            // If controls isn't passed by the component (older build), avoid crashing
            if (!controls || typeof controls.multiselect !== 'function') {
                return [
                    // eslint-disable-next-line new-cap
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
                // eslint-disable-next-line new-cap
                ports.DataFrame({ name: dfPortName, label: 'DataFrame' }),

                // Hidden port carrying a multiselect control for columns
                ports.object({
                    name: 'columns',
                    label: 'Columns',
                    hidePort: true,
                    controls: [
                        // eslint-disable-next-line new-cap
                        controls.multiselect({
                            name: 'columns',
                            label: 'Columns',
                            options,
                        }),
                    ],
                }),
            ];
        }
    }
});