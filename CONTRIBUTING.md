# Contributing to Flowfunc

Thanks for your interest in contributing! This guide describes how to set up your environment, the coding standards we follow, how to run tests, and what to expect in reviews.

## Development setup

- Python 3.10+
- Recent Node LTS

Steps:

1. Create a virtualenv and install Python deps:
   - `python -m venv .venv && . .venv/bin/activate`
   - `pip install -r requirements.txt`
2. Install Node deps and build:
   - `npm install`
   - `npm run build`
3. Optional demo server:
   - `npm start` (serves demo from `src/demo/`)

## Coding standards

- Python: type hints preferred, 4‑space indents, snake_case for functions/vars, PascalCase for classes. Lint with `.pylintrc`.
- JavaScript/React: ESLint + Prettier (`.eslintrc`, `.prettierrc`: 4 spaces, single quotes, trailing commas). Components PascalCase; hooks `use*`.
- Do not edit generated wrappers (`flowfunc/Flowfunc.py`, `R/NAMESPACE`, `man/*.Rd`). Rebuild via `npm run build`.

## Tests

- Run all tests: `pytest -q`
- Focus tests: `pytest -k <expr> -q`
- Distributed tests require Redis running on localhost:6379:
  - `pytest -k distributed -vv`
- Cycle solver tests: `pytest -k "cycles or scc_adv or scc_publish" -q`

Keep tests deterministic. Avoid network and external state in unit tests.

## Features and architecture notes

- Cycles (recycle streams) are supported in `sync` and `async` modes via an SCC fixed‑point solver (Jacobi with optional under‑relaxation, plus Wegstein acceleration). Wegstein activates from the second iteration and clamps the mixing factor by default with `scc_wegstein_qmin=0.0`, `scc_wegstein_qmax=2.0`.
- Distributed modes do not support cycles; the runner raises a `QueueError` if a cyclic graph is detected.
- Non‑convergence errors include iteration count, max delta, and worst‑port samples; for non‑numeric internals, provide `scc_initial`.
 - Tolerances: `scc_tolerance` (global), optional `scc_rtol/scc_atol` for numeric/array‑like streams; overrides via `scc_port_tolerance`, `scc_type_tolerance`, and per‑edge `scc_edge_tolerance`.
 - DataFrames: when all columns are numeric and `scc_df_numeric_as_array=True`, they are treated as arrays (vector delta + linear mixing + Wegstein). `scc_df_align` controls strict vs reindex alignment.

## Pull requests

- Keep commits focused with short, imperative messages.
- In PR description, include: motivation, changes, tests added/updated, docs updates, and screenshots/GIFs for UI behavior when relevant.
- Ensure `pytest` passes locally. If your change touches distributed code, also run `-k distributed` with Redis running.

## Security & configuration

- Don’t commit secrets. Avoid large binary artifacts.
- Avoid naming files `dash.py` in your working dir (can shadow Dash).

Thanks again for contributing!

