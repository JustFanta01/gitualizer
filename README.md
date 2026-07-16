# Gitualizer

Gitualizer is a desktop Git client built around drag and drop.

Instead of starting with a Git command, you start with the graph:

- drag a branch onto another branch to merge, rebase, fast-forward, or push;
- drag a commit onto a branch to cherry-pick or revert it;
- drag a branch or commit to the trash to see the available removal options;
- drag a rectangle around commits to move or remove them as a group;
- drag files between the working tree and staging area.
- drag working-tree files onto the stash panel to save them;
- drag a stash onto a branch or the working tree to apply it, or onto the trash to delete it.

Gitualizer does not run a graph operation immediately. It first creates a plan,
shows the exact Git commands and their expected effects, and asks for
confirmation. Destructive, remote, and history-rewriting operations are marked
clearly.

## Run

Gitualizer requires Python 3.9 or newer.

Create a virtual environment and install the project:

```bash
python3 -m venv venv_gitualizer
source venv_gitualizer/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Open a repository:

```bash
python -m gitualizer /path/to/repository
```

The installed command works too:

```bash
gitualizer /path/to/repository
```

The path is optional. You can choose a repository from the application:

```bash
gitualizer
```

Run the tests with:

```bash
pytest
```

## How the application works

The main flow is:

```text
Git CLI
  -> RepositoryReader
  -> RepositoryState
  -> Qt widgets
  -> Qt signal
  -> MainWindow handler
  -> OperationPlanner
  -> CommandPlan and confirmation
  -> CommandExecutor
  -> Git CLI
```

### Signals and the observer pattern

PySide uses the Qt signal/slot system. It is an observer-style, publish/subscribe
mechanism.

A widget publishes a signal when something happens. For example, the graph
emits `commitDroppedOnReference`. The widget does not know what Git command will
be used and does not call the planner directly.

`MainWindow` connects that signal to a handler. The handler asks
`OperationPlanner` to create a command plan, then shows the preview and
confirmation dialog.

This keeps the parts separate:

- widgets detect user interaction;
- signals describe what the user did;
- `MainWindow` coordinates the response;
- the planner decides which Git commands represent the action;
- the executor runs only an approved plan.

It is similar to the Observer pattern, but the project uses Qt's built-in
signal/slot implementation rather than maintaining its own observer list.

## Code architecture

```text
src/gitualizer/
  app/main.py                 Application entry point
  git/runner.py               Safe subprocess wrapper for Git
  git/repository.py           Reads and normalizes repository data
  model/repository_state.py   Repository dataclasses
  operations/command_plan.py  Planned commands and execution results
  operations/planner.py       Converts user intent into Git plans
  operations/executor.py      Executes confirmed plans
  ui/main_window.py           Coordination, menus, and dialogs
  ui/graph_widget.py          Painted graph and graph interactions
  ui/file_status_widget.py    Working tree and staging interactions
  ui/stash_widget.py          Draggable stash list
```

### Git and model layers

`GitRunner` runs Git with argument lists instead of shell command strings.
`RepositoryReader` uses it to read HEAD, commits, reflogs, branches, tags,
remotes, file changes, and operations in progress.

The reader converts Git output into the frozen dataclasses in
`repository_state.py`. UI code should use these objects instead of parsing Git
output itself.

### UI layer

`MainWindow` owns the current `RepositoryState` and updates the panels after a
refresh.

`CommitGraphWidget` is a custom-painted PySide `QWidget`. It draws nodes, edges,
references, selections, previews, and drop targets. It performs hit testing and
emits signals, but it does not run Git commands.

`FileStatusWidget` handles working-tree and staging-area drag and drop. It also
emits model objects through signals.

### Planning and execution

`OperationPlanner` returns a `CommandPlan`. A plan contains:

- Git commands represented as argument lists;
- a plain-language explanation;
- expected effects and preview steps;
- warnings and destructive/history-rewrite flags;
- a fingerprint of the repository state.

Before execution, `MainWindow` reads the repository again. If its fingerprint
changed after the preview was created, execution is refused. This prevents a
stale plan from running against a different repository state.

`CommandExecutor` runs the confirmed steps, stops on failure, and returns the
results to the UI.

## Extending Gitualizer

Keep new features in the layer that owns the behavior.

### Add a graph interaction

1. Add a Qt `Signal` to `CommitGraphWidget`.
2. Detect the gesture in its mouse, hitbox, or drag/drop handling.
3. Emit model objects such as `Commit` or `Reference`.
4. Connect the signal in `MainWindow.__init__`.
5. Let the handler request a plan and use the existing preview workflow.

Do not execute Git from the graph widget.

### Add a Git operation

1. Add a method to `OperationPlanner`.
2. Validate whether the operation is available.
3. Return a complete `CommandPlan` with `CommandStep(["git", ...], ...)` entries.
4. Mark destructive, remote, or history-rewriting effects.
5. Add planner tests.
6. Connect the plan to a UI signal or menu action.

Most operations do not require changes to `CommandExecutor`.

### Add repository data

1. Add or extend a dataclass in `model/repository_state.py`.
2. Read the value in `RepositoryReader`.
3. Add a repository-reader test.
4. Pass the normalized value to the relevant widget.

Do not parse raw Git output inside UI code.

### Add a panel

Create a widget under `ui/`, feed it from `RepositoryState`, and update it from
`MainWindow.refresh()`. Use signals to report user actions back to the window.

## Testing

Repository-reader tests create temporary real Git repositories. Planner tests
use model objects and check the generated command arguments and safety metadata.

Prefer testing at the lowest responsible layer:

- Git parsing in repository-reader tests;
- command behavior in planner tests;
- pure formatting and UI helpers in focused UI tests.
