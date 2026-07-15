# Gitualizer

Gitualizer is a visual desktop inspector for Git repositories.

V0 is intentionally read-only. It observes a repository through the real Git CLI,
builds an explicit `RepositoryState`, and displays:

- commit DAG;
- `HEAD` and current branch;
- local branches, remote-tracking branches, and tags;
- upstream and ahead/behind information;
- remotes and URLs;
- staged, unstaged, untracked, renamed, copied, and conflicted files.

No V0 UI action modifies the inspected repository.

## Run

Create and activate a virtual environment:

```bash
python3.9 -m venv venv_gitualizer
source venv_gitualizer/bin/activate
```

Install runtime and test dependencies:

```bash
python3 -m pip install -U pip setuptools wheel
python3 -m pip install -e '.[dev]'
```

On older distributions, editable installs may fail unless `pip` is upgraded.
This repository also includes a small `setup.py` compatibility shim for older
`pip` versions.

If PySide6 installation fails while byte-compiling one of its template files,
install with bytecode compilation disabled:

```bash
python3 -m pip install --no-compile -e '.[dev]'
```

```bash
python -m gitualizer /path/to/repo
```

or, after installing the package:

```bash
gitualizer /path/to/repo
```

## Test

```bash
pytest
```
