# aioprom

Minimal asyncio HTTP server that exposes Prometheus metrics.

## Install

```bash
pip install aioprom
```

## Usage

```python
from aioprom import start_server

# See package docstrings for configuration.
```

## Development

Enable repo git hooks (optional; auto-bumps patch `VERSION` in `aioprom/aioprom.py` when unchanged vs last commit):

```bash
git config core.hooksPath .githooks
```

Build and check the distribution:

```bash
pip install -e ".[dev]"
pytest -q
python -m build
twine check dist/*
```

Debug a failing wheel build with **verbose** logs (uses **`.venv/bin/python`**, **`pip wheel -vv`**, and **`--no-build-isolation`** so setuptools runs in that env, not an ephemeral build env). **`make wheel-debug`** runs **`make clean-build`** first so a previous **`build/`** tree cannot trigger **`[Errno 17] File exists`** on **`aioprom-*.dist-info`**.

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -U pip
make wheel-debug
# or only: make clean-build
```

The package targets **Python 3.9+** (`requires-python` / build tooling: `setuptools>=77`, `[project].license-files`). **`prometheus-client`** is required at **≥ 0.9.0**.

CI on push to `main` runs via `.github/workflows/release.yml`.

Upload to PyPI (use API tokens, not your password):

```bash
twine upload dist/*
```

TestPyPI:

```bash
twine upload --repository testpypi dist/*
```
