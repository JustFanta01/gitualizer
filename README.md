# Gitualizer

Gitualizer is a visual desktop inspector and command previewer for Git
repositories. It reads repository state through the real Git CLI, maps that
state into explicit Python dataclasses, renders it with PySide6, and turns
supported UI gestures into reviewed Git command plans before execution.

The project is intentionally small and layered:

1. `git` commands are isolated behind a runner.
2. repository facts are normalized into immutable model objects.
3. UI widgets render only the current `RepositoryState`.
4. user actions create `CommandPlan` objects before any Git command runs.
5. execution rechecks the repository fingerprint so stale previews are not run.

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

Run against any path inside a Git repository:

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

## Package Layout

```text
src/gitualizer/
  app/
    main.py                 CLI argument parsing and QApplication startup.
  git/
    runner.py               Low-level subprocess wrapper for the Git CLI.
    repository.py           RepositoryReader: converts Git output into model state.
  model/
    repository_state.py     Immutable dataclasses for commits, refs, remotes, files, HEAD, and operations.
  operations/
    command_plan.py         CommandPlan, CommandStep, and execution result dataclasses.
    planner.py              OperationPlanner: maps user intent to safe, previewable Git commands.
    executor.py             CommandExecutor: runs approved CommandPlan steps.
  ui/
    main_window.py          Main application window, menus, refresh loop, dialogs, and action wiring.
    graph_widget.py         Custom-painted commit/ref graph with drag and context-menu signals.
    file_status_widget.py   Working tree and staging area lists with drag/drop support.
tests/
  test_repository_reader.py Git-backed integration tests for RepositoryReader.
  test_operation_planner.py Unit tests for command planning behavior.
```

## Runtime Architecture

### Entry Point

`gitualizer.app.main:main` is the console script configured in
`pyproject.toml`. It parses an optional repository path, creates a
`QApplication`, constructs `MainWindow`, and starts the Qt event loop.

`src/gitualizer/__main__.py` delegates to the same entry point, which is why
`python -m gitualizer` and the installed `gitualizer` command behave the same
way.

### Git Access Layer

`GitRunner` in `git/runner.py` is the only low-level subprocess wrapper. It
accepts argument arrays, never shell strings, and returns `GitResult` objects.
Failures raise `GitError` when `check=True`.

`RepositoryReader` in `git/repository.py` uses `GitRunner` to collect the facts
the UI needs:

- repository root and absolute `.git` directory;
- `HEAD`, detached HEAD, and unborn repository state;
- local branches, remote-tracking branches, tags, upstreams, and ahead/behind;
- recent commits from `git log --all --date-order`;
- remotes from `git remote -v`;
- staged, working-tree, untracked, renamed, copied, and conflicted files from
  porcelain status;
- in-progress operations such as merge, rebase, cherry-pick, revert, and bisect.

The reader returns a complete `RepositoryState`. It does not update widgets
directly.

### Domain Model

`model/repository_state.py` contains frozen dataclasses that define the
application's internal contract:

- `Commit`
- `Reference`
- `Remote`
- `FileChange`
- `HeadState`
- `OperationState`
- `RepositoryState`

`RepositoryState` also exposes convenience properties such as
`local_branches`, `staged_changes`, and `conflicted_changes`. UI and planning
code should consume these models instead of parsing Git output again.

### UI Layer

`MainWindow` owns the top-level application state. Its refresh path is:

1. read the current repository path from the path field;
2. call `RepositoryReader.read(...)`;
3. store the returned `RepositoryState`;
4. pass the state to `CommitGraphWidget`;
5. populate file, reference, remote, and summary panels.

The window also owns timers:

- auto refresh every 2.5 seconds;
- optional `git fetch --all --prune` every 60 seconds when remotes exist.

`CommitGraphWidget` is a custom `QWidget` that paints commits, parent edges,
branch/tag labels, preview overlays, alternate branch layouts, and drop targets.
It does not execute operations. It emits Qt signals such as
`referenceDropped`, `commitDroppedOnReference`, and
`commitContextRequested`.

`FileStatusWidget` renders working-tree and staged changes. It serializes
dragged `FileChange` objects into a custom MIME payload, then emits signals for
stage, unstage, discard, and diff actions.

### Operation Planning

Gitualizer separates intent from execution. UI handlers call `OperationPlanner`
methods to produce a `CommandPlan`. A plan contains:

- a human title and explanation;
- concrete `git` command steps as argument arrays;
- expected graph effects;
- preview text;
- warnings for destructive or history-rewriting operations;
- remote impact notes;
- a `state_fingerprint`.

The fingerprint is computed from the loaded repository path, HEAD, refs, and
file changes. It lets the app detect when the repository changed after the
preview was built.

Supported plan families include:

- branch switching and detached checkout;
- branch creation at `HEAD` or a selected commit;
- staging, unstaging, discarding, and committing file changes;
- fetch, fast-forward, and push workflows;
- integrating remote-tracking branches by fast-forward, merge, or rebase;
- local branch merge and rebase workflows;
- cherry-pick, revert, commit replay, branch reset, and commit drop plans.

### Confirmation and Execution

`MainWindow` previews a plan in the command panel and opens a confirmation
dialog. If the user confirms, `_execute_plan` rereads the repository and
compares the current fingerprint with the plan fingerprint. If they differ, the
app refuses to execute and asks the user to refresh and review again.

`CommandExecutor` runs each approved step through `GitRunner`. It disables
interactive credential prompts with environment variables, applies a timeout,
and stops at the first failing command. The result is shown in the command
panel, and the repository view is refreshed afterward.

## Important Design Rules

- Keep Git subprocess calls in `git/` or `operations/executor.py`.
- Keep raw Git output parsing in `RepositoryReader`.
- Treat `RepositoryState` as the UI and planner boundary.
- Represent commands as argument arrays, not shell strings.
- Add command-producing behavior to `OperationPlanner` first, then wire it into
  the UI.
- Always surface destructive, remote-writing, or history-rewriting behavior in
  the `CommandPlan`.
- Prefer previewable workflows that avoid moving existing refs unless the user
  explicitly confirms that operation.

## Extending Functionality

### Add a New Repository Fact

1. Add or extend a dataclass in `model/repository_state.py`.
2. Read and normalize the Git output in `RepositoryReader`.
3. Add a focused test in `tests/test_repository_reader.py`.
4. Render the new data in `MainWindow`, `CommitGraphWidget`, or a dedicated UI
   widget.

Do not parse Git output inside widgets. Widgets should receive already-normalized
model objects.

### Add a New Git Operation

1. Add a method to `OperationPlanner`.
2. Validate input and raise `ValueError` with user-facing messages when the
   operation is not available.
3. Return a `CommandPlan` with explicit `CommandStep(["git", ...], ...)`
   entries.
4. Include `expected_effects`, `preview_steps`, warnings, `remote_impact`, and
   `history_rewrite` or `destructive` flags where relevant.
5. Set `state_fingerprint=state_fingerprint(state)`.
6. Add unit tests in `tests/test_operation_planner.py`.
7. Wire the planner method to a menu item, drag/drop handler, or dialog in
   `MainWindow`.

The executor already knows how to run any valid `CommandPlan`, so most new Git
behavior should not require executor changes.

### Add a New Graph Interaction

1. Add a `Signal` to `CommitGraphWidget`.
2. Extend the widget's hitbox, drag, drop, or context-menu handling to emit that
   signal with model objects.
3. Connect the signal in `MainWindow.__init__`.
4. Implement a `MainWindow` handler that builds one or more `CommandPlan`
   objects.
5. Reuse `_preview_and_confirm` or `_choose_preview_and_execute` so the user sees
   the generated commands before execution.

### Add a New File-State Interaction

1. Extend `FileStatusWidget` or `ChangeListWidget`.
2. Use `FileChange` objects as the data payload.
3. If drag/drop is involved, update the custom MIME encoding and decoding helpers
   in `file_status_widget.py`.
4. Connect the emitted signal in `MainWindow`.
5. Implement the underlying command as an `OperationPlanner` method.

### Add a New View or Panel

Create a widget under `ui/`, feed it from `RepositoryState`, and let
`MainWindow.refresh()` update it after each repository read. Avoid making the
new widget responsible for Git access or command execution.

### Add a New Command Dialog

Most operations should use the existing preview and confirmation dialogs:

- `OperationChoiceDialog` for choosing between multiple strategies;
- `OperationChoiceDialogLabels` for non-reference labels;
- `CommandPlanDialog` for final command confirmation.

Add a specialized dialog only when the operation needs extra user input that
cannot be captured by a simple `QInputDialog`.

## Testing Strategy

The tests mirror the architecture:

- `test_repository_reader.py` creates real temporary Git repositories and checks
  that Git output is converted into the expected `RepositoryState`.
- `test_operation_planner.py` builds model objects directly and checks generated
  command arrays, warnings, and operation metadata.

When adding functionality, prefer tests at the lowest layer that owns the
behavior. For example, parsing belongs in repository reader tests, while command
shape and safety metadata belong in planner tests.
