# Flowfunc Examples

## Additional requirements

To run these examples, you need to install couple of packages in addition to `flowfunc`.

```
pip install dash-bootstrap-components rq
```

## Usage.py

This example shows the basic usage of `flowfunc`.

To run, clone this repo and run the following

```
cd examples
python usage.py
```

## Usage_rq.py

Running `flowfunc` nodes in a distributed way using redis and rq. Look in to the
documentation of [python-rq](https://python-rq.org/]) to know more.

```
cd examples
python usage_rq.py
```

Start the worker as below from this folder.

```
rqworker --job-class=flowfunc.distributed.NodeJob --queue-class=flowfunc.distributed.NodeQueue
```

This method is going to be slow for simple functions because of the overhead of
communication between dash, worker and client for each function call.
But this will method gives a lot of new possibilities like being able to run
long running tasks, shared results between different runs (because the only
thing another flow run needs to know is the job id to retrieve that result),
scheduled tasks (using the scheduler feature of python-rq), retries, etc.

I am hoping to add more examples later.

## Dynamic nodes (dynamic.py)

`examples/dynamic.py` shows how to use `PortFunction` and clientside JavaScript to build dynamic nodes:

- Dynamic inputs driven by a template string or connection context.
- Dynamic outputs driven by user input, including:
  - A CSV splitter that exposes one output per parsed token.
  - A `Splitter` node that takes a value, a requested number of outputs and a set of ratios, then creates that many outputs and maps each output to a share of the value.

To run:

```bash
cd examples
python dynamic.py
```

## Fit-to-View tests

Two small apps exercise the new Fit‑to‑View feature in the editor:

```
python examples/fit_to_view.py            # Manual: click the toolbar Fit button
python examples/fit_to_view_trigger.py    # Programmatic: triggers via Dash prop
```

Notes:

- For best results, apply the local Flume patch once per install: `npm run patch:flume`, then `npm run build`.
- Without the patch, Fit‑to‑View falls back to a smooth zoom animation and requires zoom to be enabled.
