from __future__ import annotations

import html
from pathlib import Path
import shlex
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from gitualizer.git.repository import RepositoryReader
from gitualizer.git.runner import AUTH_SESSION_SECONDS, CommandEvent, GitError, GitResult, remote_auth_environment
from gitualizer.model.repository_state import Commit, FileChange, Reference, RepositoryState, Stash
from gitualizer.operations.command_plan import CommandPlan, ExecutionResult, StepResult
from gitualizer.operations.executor import CommandExecutor, REMOTE_AUTH_COMMANDS
from gitualizer.operations.planner import OperationPlanner, state_fingerprint
from gitualizer.ui.file_status_widget import FileStatusWidget
from gitualizer.ui.graph_widget import CommitGraphWidget
from gitualizer.ui.stash_widget import StashWidget


FETCH_INTERACTIVE_TIMEOUT_SECONDS = 45
FETCH_NONINTERACTIVE_TIMEOUT_SECONDS = 8


class MainWindow(QMainWindow):
    commandEventReceived = Signal(object)
    fetchFinished = Signal(object, bool)

    def __init__(self, initial_path: Optional[Path] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.reader = RepositoryReader()
        self.planner = OperationPlanner()
        self.executor = CommandExecutor()
        self.state: Optional[RepositoryState] = None
        self.auto_refresh_enabled = True
        self.auto_fetch_enabled = True
        self.refresh_in_progress = False
        self.commit_limit = 300
        self.fetch_in_progress = False
        self._terminal_alert: Optional[QMessageBox] = None
        self.network_state = "unknown"
        self.auth_state = "not_checked"
        self.last_auth_command_at: Optional[float] = None
        self.last_remote_command_at: Optional[float] = None
        self.ui_scale = 1.0
        self._base_application_font = QApplication.font()
        self.setWindowTitle("Gitualizer")
        self.resize(980, 640)
        self.setStyleSheet(APP_STYLE)

        self.path_edit = QLineEdit(str(initial_path or Path.cwd()))
        self.path_edit.setPlaceholderText("Path inside a Git repository")
        self.path_edit.textChanged.connect(self._clear_path_error)
        self.open_button = QPushButton("Browse")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("refreshButton")
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.setObjectName("fetchButton")
        self.fetch_button.setToolTip("Fetch remotes; Git/SSH may ask you to authenticate.")

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("Repository"))
        top.addWidget(self.path_edit, 1)
        top.addWidget(self.open_button)
        top.addWidget(self.refresh_button)
        top.addWidget(self.fetch_button)

        self.graph = CommitGraphWidget()
        graph_scroll = QScrollArea()
        graph_scroll.setWidgetResizable(True)
        graph_scroll.setWidget(self.graph)
        graph_scroll.setObjectName("graphScroll")

        self.file_status = FileStatusWidget()
        self.refs_table = self._table(["Kind", "Name", "Target", "Upstream", "Ahead/Behind"])
        self.remotes_table = self._table(["Remote", "Fetch URL", "Push URL"])
        self.stash_widget = StashWidget()
        self.summary = QLabel("No repository loaded.")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("summary")

        self.command_panel = QTextBrowser()
        self.command_panel.setOpenExternalLinks(False)
        self.command_panel.setMinimumHeight(115)
        self.command_panel.setHtml(_empty_preview_html())
        self.command_history = QTextEdit()
        self.command_history.setReadOnly(True)
        self.command_history.setMinimumHeight(115)
        self.command_history.setPlaceholderText("Completed Git commands will appear here.")
        self.command_history.setObjectName("commandHistory")
        self.command_history.document().setMaximumBlockCount(500)
        self.command_history_auto_scroll = True
        self.updating_command_history = False
        self.command_history.verticalScrollBar().valueChanged.connect(
            self._update_command_history_auto_scroll
        )

        self.working_panel = self._panel("Working Tree and Index", self.file_status)

        self.repo_panel = self._panel("Repository State", self.summary)
        self.details_tabs = QTabWidget()
        self.references_tab_index = self.details_tabs.addTab(self.refs_table, "References")
        self.stashes_tab_index = self.details_tabs.addTab(self.stash_widget, "Stashes")
        self.remotes_tab_index = self.details_tabs.addTab(self.remotes_table, "Remotes")
        self.details_tabs.setCurrentIndex(self.stashes_tab_index)
        self.right_panel = QWidget()
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(self.repo_panel)
        right_layout.addWidget(self.details_tabs, 1)

        self.graph_scroll = graph_scroll
        self.graph_scroll.verticalScrollBar().valueChanged.connect(self._maybe_load_more_commits)
        self.graph_scroll.verticalScrollBar().valueChanged.connect(lambda _value: self._sync_graph_viewport())
        self.graph_scroll.horizontalScrollBar().valueChanged.connect(lambda _value: self._sync_graph_viewport())
        self.main_splitter = QSplitter()
        self.main_splitter.addWidget(self.working_panel)
        self.main_splitter.addWidget(self.graph_scroll)
        self.main_splitter.addWidget(self.right_panel)
        self.main_splitter.setChildrenCollapsible(False)
        self.right_panel.setMinimumWidth(320)
        self.main_splitter.setSizes([220, 700, 360])

        self.command_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.command_splitter.addWidget(self.command_panel)
        self.command_splitter.addWidget(self.command_history)
        self.command_splitter.setChildrenCollapsible(False)
        self.command_splitter.setSizes([520, 520])
        self.command_panel_group = self._panel(
            "Operation / Preview / Commands  |  Command History",
            self.command_splitter,
        )
        self.command_panel_group.setCheckable(True)
        self.command_panel_group.setChecked(True)
        self.command_panel_group.setToolTip("Uncheck to collapse the command preview and give more space to repository panels.")
        self.command_panel_group.toggled.connect(self._set_command_panel_expanded)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addLayout(top)
        root_layout.addWidget(self.main_splitter, 1)
        root_layout.addWidget(self.command_panel_group)
        self.setCentralWidget(root)
        self.network_status_button = QPushButton("Network: Unknown")
        self.network_status_button.setObjectName("networkStatusButton")
        self.network_status_button.setProperty("networkState", "unknown")
        self.network_status_button.setToolTip("Remote network reachability is checked by fetch.")
        self.statusBar().addPermanentWidget(self.network_status_button)
        self.auth_status_button = QPushButton("Remote auth: Not checked")
        self.auth_status_button.setObjectName("authStatusButton")
        self.auth_status_button.setProperty("authState", "not_checked")
        self.auth_status_button.setToolTip("Remote SSH/HTTPS authentication is handled entirely by Git and the terminal.")
        self.statusBar().addPermanentWidget(self.auth_status_button)
        self.commandEventReceived.connect(self._append_command_event)
        self.fetchFinished.connect(self._finish_fetch)
        self.reader.runner.set_observer(self.commandEventReceived.emit)
        self.executor.runner.set_observer(self.commandEventReceived.emit)
        self._build_menus()

        self.open_button.clicked.connect(self._browse)
        self.refresh_button.clicked.connect(self.refresh)
        self.fetch_button.clicked.connect(self._interactive_fetch)
        self.auth_status_button.clicked.connect(self._show_auth_explanation)
        self.path_edit.returnPressed.connect(self.refresh)
        self.graph.referenceDropped.connect(self._handle_reference_drop)
        self.graph.referenceDroppedOnCommit.connect(self._handle_reference_drop_on_commit)
        self.graph.commitDroppedOnReference.connect(self._handle_commit_drop_on_reference)
        self.graph.stageDroppedOnBranch.connect(self._handle_stage_drop_on_branch)
        self.graph.commitDroppedOnCommit.connect(self._handle_commit_drop_on_commit)
        self.graph.commitDroppedToTrash.connect(self._handle_commit_drop_to_trash)
        self.graph.commitsDroppedOnReference.connect(self._handle_commits_drop_on_reference)
        self.graph.commitsDroppedOnCommit.connect(self._handle_commits_drop_on_commit)
        self.graph.commitsDroppedToTrash.connect(self._handle_commits_drop_to_trash)
        self.graph.stashDroppedOnBranch.connect(self._handle_stash_drop_on_branch)
        self.graph.stashDroppedToTrash.connect(self._handle_stash_drop_to_trash)
        self.stash_widget.changesDropped.connect(self._handle_changes_drop_to_stash)
        self.graph.referenceDroppedToTrash.connect(self._handle_reference_drop_to_trash)
        self.graph.commitContextRequested.connect(self._show_commit_context_menu)
        self.graph.commitsContextRequested.connect(self._show_commits_context_menu)
        self.graph.referenceContextRequested.connect(self._show_reference_context_menu)
        self.file_status.changesDroppedToStage.connect(self._handle_changes_drop_to_stage)
        self.file_status.changesDroppedToWorking.connect(self._handle_changes_drop_to_working)
        self.file_status.changesDroppedToTrash.connect(self._handle_changes_drop_to_trash)
        self.file_status.changeActivated.connect(self._show_file_diff)
        self.file_status.stashDroppedToWorking.connect(self._handle_stash_drop_to_working)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2500)
        self.refresh_timer.timeout.connect(self._auto_refresh)
        self.refresh_timer.start()
        self.fetch_timer = QTimer(self)
        self.fetch_timer.setInterval(60000)
        self.fetch_timer.timeout.connect(self._auto_fetch)
        self.fetch_timer.start()

        if initial_path is not None:
            self.refresh()
        else:
            self._set_enabled(False)
        self._sync_graph_viewport()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open Repository...", self)
        open_action.triggered.connect(self._browse)
        file_menu.addAction(open_action)
        file_menu.addAction("Refresh", self.refresh)
        file_menu.addAction("Fetch Remotes (Authenticate)...", self._interactive_fetch)
        file_menu.addSeparator()
        file_menu.addAction("Quit", QApplication.instance().quit)

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction("Copy Command Preview", self.command_panel.copy)

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction("Workspace", self._workspace_mode)
        view_menu.addAction("Graph Focus", self._graph_focus_mode)
        view_menu.addAction("Status Focus", self._status_focus_mode)
        view_menu.addAction("Command Focus", self._command_focus_mode)
        self.stashes_action = QAction("Open Stashes", self)
        self.stashes_action.triggered.connect(self._show_stashes_tab)
        view_menu.addAction(self.stashes_action)
        view_menu.addSeparator()
        view_menu.addAction("Graph Layout: Commits", self._commits_layout)
        view_menu.addAction("Graph Layout: Branches", self._branches_layout)
        view_menu.addAction("Graph Layout: Local vs Remote", self._local_remote_layout)
        view_menu.addSeparator()
        graph_options = view_menu.addMenu("Graph Visualization")
        self.lane_width_spin = self._graph_spacing_control(
            graph_options,
            "Lane width",
            value=72,
            minimum=40,
            maximum=400,
            step=8,
            changed=lambda value: self.graph.set_visualization(lane_spacing=value),
        )
        self.row_height_spin = self._graph_spacing_control(
            graph_options,
            "Row height",
            value=52,
            minimum=24,
            maximum=240,
            step=4,
            changed=lambda value: self.graph.set_visualization(row_spacing=value),
        )
        view_menu.addSeparator()
        scale_menu = view_menu.addMenu("Interface Scale")
        zoom_in = scale_menu.addAction("Increase Size", lambda: self._change_ui_scale(0.1))
        zoom_in.setShortcuts(["Ctrl++", "Ctrl+="])
        zoom_out = scale_menu.addAction("Decrease Size", lambda: self._change_ui_scale(-0.1))
        zoom_out.setShortcut("Ctrl+-")
        reset_zoom = scale_menu.addAction("Actual Size", self._reset_ui_scale)
        reset_zoom.setShortcut("Ctrl+0")
        graph_options.addAction("Reset Graph Spacing", self._reset_graph_visualization)
        view_menu.addSeparator()
        view_menu.addAction("Toggle Full Screen", self._toggle_fullscreen)
        view_menu.addAction("Maximized", self.showMaximized)
        view_menu.addAction("Normal Size", self.showNormal)

        preferences_menu = self.menuBar().addMenu("Preferences")
        auto_refresh = QAction("Auto Refresh", self)
        auto_refresh.setCheckable(True)
        auto_refresh.setChecked(True)
        auto_refresh.triggered.connect(self._set_auto_refresh)
        preferences_menu.addAction(auto_refresh)
        auto_fetch = QAction("Auto Fetch Remotes", self)
        auto_fetch.setCheckable(True)
        auto_fetch.setChecked(True)
        auto_fetch.triggered.connect(self._set_auto_fetch)
        preferences_menu.addAction(auto_fetch)

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("About Gitualizer", self._about)

    def _graph_spacing_control(
        self,
        menu: QMenu,
        label: str,
        *,
        value: int,
        minimum: int,
        maximum: int,
        step: int,
        changed: Callable[[int], None],
    ) -> QSpinBox:
        container = QWidget(menu)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.addWidget(QLabel(label))
        spin = QSpinBox(container)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setSuffix(" px")
        spin.setValue(value)
        spin.valueChanged.connect(changed)
        layout.addWidget(spin)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        return spin

    def _table(self, headers: list[str]):
        from PySide6.QtWidgets import QTableWidget

        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(24)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setWordWrap(False)
        return table

    def _panel(self, title: str, child: QWidget) -> QWidget:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(child)
        return group

    def _set_command_panel_expanded(self, expanded: bool) -> None:
        self.command_splitter.setVisible(expanded)
        self.command_panel_group.updateGeometry()

    def _update_command_history_auto_scroll(self, value: int) -> None:
        if self.updating_command_history:
            return
        scroll_bar = self.command_history.verticalScrollBar()
        self.command_history_auto_scroll = value == scroll_bar.maximum()

    def _append_command_event(self, event: CommandEvent) -> None:
        if event.phase == "started" and event.interactive:
            self.last_auth_command_at = time.monotonic()
            self.last_remote_command_at = self.last_auth_command_at
            if self.auth_state != "authenticated":
                self._set_auth_status("required")
            return
        if event.phase != "finished":
            return
        if event.interactive and event.returncode == 0:
            self._set_auth_status("authenticated")
        scroll_bar = self.command_history.verticalScrollBar()
        previous_value = scroll_bar.value()
        command = html.escape(shlex.join(event.command))
        status_color = "#1a7f37" if event.returncode == 0 else "#cf222e"
        line = (
            f"<span style='color:#667085;'>[{html.escape(event.timestamp)}]</span> "
            f"<span style='color:#0969da; font-weight:700;'>$ {command}</span> "
            f"<span style='color:{status_color}; font-weight:700;'>exit {event.returncode}</span>"
        )
        self.updating_command_history = True
        try:
            self.command_history.append(line)
            scroll_bar = self.command_history.verticalScrollBar()
            scroll_bar.setValue(
                scroll_bar.maximum() if self.command_history_auto_scroll else previous_value
            )
        finally:
            self.updating_command_history = False
        if self.command_history_auto_scroll:
            # QTextEdit may update its range after the document layout
            # pass, so pin again on the next event-loop iteration.
            QTimer.singleShot(0, self._scroll_command_history_to_bottom)

    def _scroll_command_history_to_bottom(self) -> None:
        if not self.command_history_auto_scroll:
            return
        scroll_bar = self.command_history.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def _set_auth_status(self, state: str) -> None:
        self.auth_state = state
        self.auth_status_button.setProperty("authState", state)
        self.auth_status_button.style().unpolish(self.auth_status_button)
        self.auth_status_button.style().polish(self.auth_status_button)
        self._update_auth_status_label()

    def _set_network_status(self, state: str) -> None:
        self.network_state = state
        self.network_status_button.setProperty("networkState", state)
        self.network_status_button.style().unpolish(self.network_status_button)
        self.network_status_button.style().polish(self.network_status_button)
        labels = {
            "unknown": "Unknown",
            "online": "Online",
            "offline": "Offline",
        }
        self.network_status_button.setText(f"Network: {labels[self.network_state]}.")

    def _update_auth_status_label(self) -> None:
        labels = {
            "not_checked": "Not checked",
            "required": "Required",
            "authenticated": "Authenticated",
            "unavailable": "Unavailable",
        }
        self.auth_status_button.setText(f"Remote auth: {labels[self.auth_state]}.")

    def _auth_details_html(self) -> str:
        def elapsed(timestamp: Optional[float]) -> str:
            if timestamp is None:
                return "Not used yet"
            seconds = max(0, int(time.monotonic() - timestamp))
            return f"{seconds} sec ago"

        return (
            "Gitualizer never reads, redirects, or stores an SSH passphrase, HTTPS password, or token. "
            "Interactive authentication belongs to Git, SSH, and the external terminal.<br><br>"
            f"Git's credential cache and SSH connection sharing may reuse authentication for "
            f"{AUTH_SESSION_SECONDS // 60} minutes.<br><br>"
            "<span style='color:#0969da; font-weight:700;'>Last terminal authentication command:</span> "
            f"{elapsed(self.last_auth_command_at)}<br>"
            "<span style='color:#8250df; font-weight:700;'>Last command using remote authentication:</span> "
            f"{elapsed(self.last_remote_command_at)}"
        )

    def _create_auth_alert(self, *, show_details: bool) -> QMessageBox:
        alert = QMessageBox(self)
        alert.setIcon(QMessageBox.Icon.Information)
        alert.setWindowTitle("Remote Authentication")
        alert.setText("Git may require authentication in the terminal.")
        alert.setInformativeText(
            self._auth_details_html()
            if show_details
            else "Use the terminal that launched Gitualizer. Gitualizer does not read or redirect credentials."
        )
        alert.setStandardButtons(QMessageBox.StandardButton.Close)
        return alert

    def _show_auth_explanation(self) -> None:
        alert = self._create_auth_alert(show_details=True)
        alert.setStandardButtons(QMessageBox.StandardButton.Ok)
        alert.exec()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Open Git Repository", self.path_edit.text())
        if selected:
            self.path_edit.setText(selected)
            self.refresh()

    def _auto_refresh(self) -> None:
        if self.auto_refresh_enabled and self.state is not None:
            self.refresh(show_errors=False)

    def _set_auto_refresh(self, enabled: bool) -> None:
        self.auto_refresh_enabled = enabled

    def _set_auto_fetch(self, enabled: bool) -> None:
        self.auto_fetch_enabled = enabled

    def _initial_fetch(self) -> None:
        if self.auto_fetch_enabled:
            self._interactive_fetch()

    def _interactive_fetch(self) -> None:
        self._start_fetch(interactive=True)

    def _auto_fetch(self) -> None:
        self._start_fetch(interactive=False)

    def _start_fetch(self, *, interactive: bool) -> None:
        if (
            (not interactive and not self.auto_fetch_enabled)
            or self.state is None
            or self.fetch_in_progress
            or not self.state.remotes
        ):
            return
        self.fetch_in_progress = True
        repository_path = self.state.path
        if interactive:
            self._terminal_alert = self._create_auth_alert(show_details=False)
            self._terminal_alert.finished.connect(lambda _result: setattr(self, "_terminal_alert", None))
            self._terminal_alert.show()
            self.statusBar().showMessage(
                "Fetching in the background; complete any Git/SSH prompt in the terminal."
            )
        else:
            self.statusBar().showMessage("Checking remotes in the background...")

        def run_fetch() -> None:
            try:
                if interactive:
                    # All standard streams remain inherited. Git, SSH, and
                    # credential helpers exclusively own authentication.
                    result = self.reader.runner.run_interactive(
                        ["fetch", "--all", "--prune"],
                        cwd=repository_path,
                        env=remote_auth_environment(interactive=True),
                        timeout=FETCH_INTERACTIVE_TIMEOUT_SECONDS,
                    )
                else:
                    result = self.reader.runner.run(
                        ["fetch", "--all", "--prune"],
                        cwd=repository_path,
                        check=False,
                        env={
                            "GIT_TERMINAL_PROMPT": "0",
                            "GIT_ASKPASS": "echo",
                            "SSH_ASKPASS": "echo",
                            **remote_auth_environment(interactive=False),
                        },
                        timeout=FETCH_NONINTERACTIVE_TIMEOUT_SECONDS,
                    )
            except OSError as exc:
                result = GitResult(
                    ["git", "fetch", "--all", "--prune"],
                    repository_path,
                    "",
                    str(exc),
                    126,
                )
            self.fetchFinished.emit(result, interactive)

        threading.Thread(target=run_fetch, name="gitualizer-fetch", daemon=True).start()

    def _finish_fetch(self, result: GitResult, interactive: bool) -> None:
        self.last_remote_command_at = time.monotonic()
        if interactive and self._terminal_alert is not None:
            alert = self._terminal_alert
            self._terminal_alert = None
            alert.hide()
            alert.deleteLater()
        self.fetch_in_progress = False
        self.fetch_timer.start()

        if result.returncode == 0:
            self._set_network_status("online")
            self._set_auth_status("authenticated")
            self.statusBar().showMessage("Remote-tracking branches updated.", 4000)
            if self.state is not None and result.cwd == self.state.path:
                self.refresh(show_errors=False)
            return

        exit_text = f"exit {result.returncode}"
        if result.returncode == 124:
            self._set_network_status("offline")
            self._set_auth_status("unavailable")
            message = f"Remote fetch timed out ({exit_text}). Gitualizer remains available offline."
        elif interactive:
            if result.returncode == 130:
                self._set_network_status("unknown")
                self._set_auth_status("required")
                message = (
                    f"Fetch canceled ({exit_text}); authentication was not completed. "
                    "Gitualizer remains available offline."
                )
            else:
                auth_failure = _looks_like_auth_failure(result.stderr)
                if auth_failure or not result.stderr.strip():
                    self._set_network_status("unknown")
                    self._set_auth_status("required")
                else:
                    self._set_network_status("offline")
                    self._set_auth_status("unavailable")
                message = f"Fetch failed ({exit_text}); see the terminal for Git's error. Gitualizer remains available offline."
        else:
            auth_failure = _looks_like_auth_failure(result.stderr)
            self._set_network_status("online" if auth_failure else "offline")
            self._set_auth_status("required" if auth_failure else "unavailable")
            if auth_failure:
                self.statusBar().showMessage(
                    f"Remote authentication required ({exit_text}); opening terminal prompt.",
                    12000,
                )
                self._start_fetch(interactive=True)
                return
            detail = result.stderr.strip().splitlines()
            reason = detail[-1] if detail else "the remote could not be reached"
            message = f"Remote unavailable ({exit_text}): {reason}. Gitualizer remains available offline."
        self.statusBar().showMessage(message, 12000)

    def refresh(self, show_errors: bool = True) -> None:
        if self.refresh_in_progress:
            return
        self.refresh_in_progress = True
        previous_path = self.state.path if self.state is not None else None
        requested_path = Path(self.path_edit.text()).expanduser()
        if not requested_path.exists() or not requested_path.is_dir():
            message = (
                f"The repository path does not exist: {requested_path}"
                if not requested_path.exists()
                else f"The repository path is not a directory: {requested_path}"
            )
            self._show_path_error(message)
            self._clear_repository_state()
            self.refresh_in_progress = False
            return
        try:
            self.state = self.reader.read(requested_path, commit_limit=self.commit_limit)
        except GitError as exc:
            self._show_path_error("The selected directory is not a readable Git repository.")
            self._clear_repository_state()
            if show_errors:
                QMessageBox.warning(self, "Unable to Open Repository", str(exc))
            self.refresh_in_progress = False
            return
        self._clear_path_error()
        full_repository_path = str(self.state.path.resolve())
        if self.path_edit.text() != full_repository_path:
            self.path_edit.setText(full_repository_path)
        self.path_edit.setToolTip(full_repository_path)
        self.graph.set_state(self.state)
        self._set_summary(self.state)
        self._set_changes(self.state.changes)
        self._set_refs(self.state.references)
        self._set_remotes(self.state)
        self.stash_widget.set_stashes(self.state.stashes)
        self._refresh_controls(self.state)
        self._set_enabled(True)
        self.refresh_in_progress = False
        if self.state.path != previous_path:
            self.last_auth_command_at = None
            self.last_remote_command_at = None
            self._set_network_status("unknown")
            self._set_auth_status("not_checked")
            # Authenticate through Git/SSH once when a repository is opened.
            # Later timer fetches are non-interactive and can never prompt.
            QTimer.singleShot(0, self._initial_fetch)

    def _show_path_error(self, message: str) -> None:
        self.path_edit.setProperty("invalid", True)
        self.path_edit.setToolTip(message)
        self.path_edit.style().unpolish(self.path_edit)
        self.path_edit.style().polish(self.path_edit)
        self.summary.setText(message)

    def _clear_path_error(self) -> None:
        if not self.path_edit.property("invalid"):
            return
        self.path_edit.setProperty("invalid", False)
        self.path_edit.setToolTip("")
        self.path_edit.style().unpolish(self.path_edit)
        self.path_edit.style().polish(self.path_edit)

    def _clear_repository_state(self) -> None:
        self.state = None
        self.last_auth_command_at = None
        self.last_remote_command_at = None
        self._set_network_status("unknown")
        self._set_auth_status("not_checked")
        self.graph.set_state(None)
        self.graph.set_preview_plan(None)
        self._set_changes([])
        self._set_refs([])
        self._set_remotes(None)
        self.stash_widget.set_stashes([])
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        self.graph.setEnabled(enabled)
        self.fetch_button.setEnabled(enabled)

    def _refresh_controls(self, state: RepositoryState) -> None:
        return

    def _set_summary(self, state: RepositoryState) -> None:
        head_label = "unborn"
        if state.head.oid:
            head_label = f"{state.head.short_oid}"
        branch = state.head.branch or ("detached HEAD" if state.head.detached else "no branch")
        operation = ", ".join(state.operation.active_labels) or "none"
        self.summary.setText(
            f"Path: {state.path}\n"
            f"HEAD: {head_label}\n"
            f"Current branch: {branch}\n"
            f"Commits loaded: {len(state.commits)}{'+' if state.commits_truncated else ''}\n"
            f"Operation in progress: {operation}"
        )

    def _maybe_load_more_commits(self, value: int) -> None:
        if self.state is None or not self.state.commits_truncated:
            return
        bar = self.graph_scroll.verticalScrollBar()
        if value >= bar.maximum() - 80:
            self.commit_limit += 300
            self.refresh(show_errors=False)

    def _sync_graph_viewport(self) -> None:
        if not hasattr(self, "graph_scroll"):
            return
        viewport = self.graph_scroll.viewport()
        self.graph.set_viewport(
            self.graph_scroll.horizontalScrollBar().value(),
            self.graph_scroll.verticalScrollBar().value(),
            viewport.width(),
            viewport.height(),
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_graph_viewport()

    def _set_changes(self, changes: list[FileChange]) -> None:
        self.file_status.set_changes(changes)

    def _set_refs(self, refs: list[Reference]) -> None:
        self.refs_table.setRowCount(len(refs))
        for row, ref in enumerate(refs):
            ahead_behind = ""
            if ref.ahead is not None or ref.behind is not None:
                ahead_behind = f"+{ref.ahead or 0} / -{ref.behind or 0}"
            self.refs_table.setItem(row, 0, QTableWidgetItem(ref.kind))
            self.refs_table.setItem(row, 1, QTableWidgetItem(ref.name))
            self.refs_table.setItem(row, 2, QTableWidgetItem(ref.target[:12]))
            self.refs_table.setItem(row, 3, QTableWidgetItem(ref.upstream or ""))
            self.refs_table.setItem(row, 4, QTableWidgetItem(ahead_behind))
        self.refs_table.resizeColumnsToContents()
        self.refs_table.setColumnWidth(0, 110)
        self.refs_table.setColumnWidth(1, 150)
        self.refs_table.setColumnWidth(2, 96)
        self.refs_table.setColumnWidth(3, 140)

    def _set_remotes(self, state: Optional[RepositoryState]) -> None:
        remotes = state.remotes if state else []
        self.remotes_table.setRowCount(len(remotes))
        for row, remote in enumerate(remotes):
            self.remotes_table.setItem(row, 0, QTableWidgetItem(remote.name))
            self.remotes_table.setItem(row, 1, QTableWidgetItem(remote.fetch_url or ""))
            self.remotes_table.setItem(row, 2, QTableWidgetItem(remote.push_url or ""))
        self.remotes_table.resizeColumnsToContents()
        self.remotes_table.setColumnWidth(0, 80)
        self.remotes_table.setColumnWidth(1, 220)

    def _execute_plan(self, plan: CommandPlan) -> None:
        assert self.state is not None
        try:
            current_state = self.reader.read(self.state.path)
        except GitError as exc:
            QMessageBox.warning(self, "Unable to Refresh Repository", str(exc))
            return
        if state_fingerprint(current_state) != plan.state_fingerprint:
            QMessageBox.warning(
                self,
                "Repository Changed",
                "The repository changed after the operation was planned. Refresh and review the command again.",
            )
            self.refresh()
            return
        result = self.executor.execute(plan, current_state.path)
        self._update_remote_status_from_execution(result, current_state.path)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=True) + _render_result_html(result))
        self.graph.set_preview_plan(None)
        self.refresh()
        if not result.success:
            QMessageBox.warning(self, "Git Command Failed", _render_result_text(result))

    def _update_remote_status_from_execution(self, result: ExecutionResult, repository_path: Path) -> None:
        remote_steps = [step for step in result.steps if _is_remote_step(step.args)]
        if not remote_steps:
            return
        failed = next((step for step in remote_steps if step.returncode != 0), None)
        if failed is None:
            self._set_network_status("online")
            self._set_auth_status("authenticated")
            self.statusBar().showMessage("Remote command completed; network online and remote auth available.", 5000)
            return
        self._update_remote_status_from_failed_step(failed, repository_path)

    def _update_remote_status_from_failed_step(self, step: StepResult, repository_path: Path) -> None:
        probe = None
        stderr = step.stderr
        if not stderr.strip() and step.returncode not in {124, 130}:
            remote = _remote_name_from_step(step.args)
            probe_args = ["ls-remote", "--heads"]
            if remote:
                probe_args.append(remote)
            probe = self.reader.runner.run(
                probe_args,
                cwd=repository_path,
                check=False,
                env={
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_ASKPASS": "echo",
                    "SSH_ASKPASS": "echo",
                    **remote_auth_environment(interactive=False),
                },
                timeout=FETCH_NONINTERACTIVE_TIMEOUT_SECONDS,
            )
            stderr = probe.stderr

        effective_returncode = probe.returncode if probe is not None else step.returncode
        exit_text = f"exit {effective_returncode}"
        if effective_returncode == 124:
            self._set_network_status("offline")
            self._set_auth_status("unavailable")
            self.statusBar().showMessage(
                f"Remote status probe timed out ({exit_text}); Gitualizer remains available offline.",
                12000,
            )
        elif step.returncode == 130:
            self._set_network_status("unknown")
            self._set_auth_status("required")
            self.statusBar().showMessage(
                f"Remote command canceled (exit {step.returncode}); authentication was not completed.",
                12000,
            )
        elif _looks_like_auth_failure(stderr):
            self._set_network_status("online")
            self._set_auth_status("required")
            self.statusBar().showMessage(
                f"Remote authentication required ({exit_text}).",
                12000,
            )
        else:
            self._set_network_status("offline")
            self._set_auth_status("unavailable")
            detail = stderr.strip().splitlines()
            reason = detail[-1] if detail else "the remote could not be reached"
            self.statusBar().showMessage(
                f"Remote unavailable ({exit_text}): {reason}. Gitualizer remains available offline.",
                12000,
            )

    def _handle_reference_drop(self, source: Reference, target: Reference) -> None:
        if self.state is None:
            return
        plans: list[CommandPlan] = []
        try:
            if source.kind == "remote_tracking" and target.kind == "local_branch":
                plans = [
                    self.planner.integrate_remote_tracking(self.state, source, target, "ff"),
                    self.planner.integrate_remote_tracking(self.state, source, target, "merge"),
                    self.planner.integrate_remote_tracking(self.state, source, target, "rebase"),
                ]
            elif source.kind == "local_branch" and target.kind == "local_branch":
                plans = [
                    self.planner.integrate_local_branch(self.state, source, target, "merge_source_into_target"),
                    self.planner.integrate_local_branch(self.state, source, target, "rebase_source_onto_target"),
                ]
            elif source.kind == "local_branch" and target.kind == "remote_tracking":
                plans = [
                    self.planner.push_branch_to_remote_tracking(self.state, source, target),
                ]
            else:
                QMessageBox.information(
                    self,
                    "No Graph Operation",
                    "This drag does not map to a supported operation yet. Try dragging branches between local and remote-tracking refs.",
                )
                return
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        dialog = OperationChoiceDialog(source, target, plans, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_plan is None:
            return
        plan = dialog.selected_plan
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _handle_changes_drop_to_stage(self, changes: list[FileChange]) -> None:
        if self.state is None:
            return
        try:
            plan = self.planner.stage_paths(self.state, changes)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _handle_changes_drop_to_working(self, changes: list[FileChange]) -> None:
        if self.state is None:
            return
        staged = [change for change in changes if change.area == "staged"]
        if not staged:
            QMessageBox.information(self, "Operation Not Available", "Drag staged files back to the working area to unstage them.")
            return
        try:
            plan = self.planner.unstage_paths(self.state, staged)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _handle_stage_drop_on_branch(self, branch: Reference) -> None:
        if self.state is None:
            return
        staged_count = len(self.state.staged_changes)
        message, accepted = QInputDialog.getText(
            self,
            "Commit Whole Staging Area",
            f"Create one commit on `{branch.name}` from all {staged_count} staged change(s):",
        )
        if not accepted:
            return
        try:
            plan = self.planner.commit_to_branch(self.state, branch.name, message)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _show_commit_context_menu(self, commit: Commit, global_pos) -> None:
        if self.state is None:
            return
        menu = QMenu(self)
        switch_detached = menu.addAction(f"Switch to {commit.short_oid} detached")
        create_branch = menu.addAction("Create Branch Here...")
        create_and_switch = menu.addAction("Create and Switch Branch Here...")
        menu.addSeparator()
        revert_commit = menu.addAction("Revert Commit on Current Branch")
        drop_commit = menu.addAction("Drop Commit from Current Branch...")
        menu.addSeparator()
        copy_oid = menu.addAction("Copy Commit Hash")
        selected = menu.exec(global_pos)
        if selected is None:
            return
        if selected == copy_oid:
            QApplication.clipboard().setText(commit.oid)
            return
        try:
            if selected == switch_detached:
                self._preview_and_confirm(self.planner.switch_to_commit(self.state, commit))
                return
            if selected in {create_branch, create_and_switch}:
                branch_name, accepted = QInputDialog.getText(
                    self,
                    "Branch Name",
                    f"New branch at `{commit.short_oid}`:",
                )
                if not accepted:
                    return
                if selected == create_branch:
                    plan = self.planner.create_branch_at_commit(self.state, commit, branch_name)
                else:
                    plan = self.planner.create_and_switch_branch_at_commit(self.state, commit, branch_name)
                self._preview_and_confirm(plan)
                return
            if selected == revert_commit:
                self._preview_and_confirm(self.planner.revert_commit_on_current_branch(self.state, commit))
                return
            if selected == drop_commit:
                self._preview_and_confirm(self.planner.drop_commit_from_current_branch(self.state, commit))
                return
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))

    def _show_commits_context_menu(self, commits: list[Commit], global_pos) -> None:
        if self.state is None or not commits:
            return
        menu = QMenu(self)
        revert_commits = menu.addAction(f"Revert {len(commits)} Commits on Current Branch")
        selected = menu.exec(global_pos)
        if selected != revert_commits:
            return
        try:
            plan = self.planner.revert_commits_on_current_branch(self.state, commits)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self._preview_and_confirm(plan)

    def _show_reference_context_menu(self, ref: Reference, global_pos) -> None:
        if self.state is None:
            return
        menu = QMenu(self)
        switch_branch = None
        if ref.kind == "local_branch":
            switch_branch = menu.addAction(f"Switch to {ref.name}")
        copy_ref = menu.addAction("Copy Reference Name")
        copy_target = menu.addAction("Copy Target Hash")
        selected = menu.exec(global_pos)
        if selected is None:
            return
        if selected == copy_ref:
            QApplication.clipboard().setText(ref.name)
            return
        if selected == copy_target:
            QApplication.clipboard().setText(ref.target)
            return
        if switch_branch is not None and selected == switch_branch:
            try:
                self._preview_and_confirm(self.planner.switch_branch(self.state, ref.name))
            except ValueError as exc:
                QMessageBox.information(self, "Operation Not Available", str(exc))

    def _handle_changes_drop_to_trash(self, changes: list[FileChange]) -> None:
        if self.state is None:
            return
        try:
            plan = self.planner.discard_changes(self.state, changes)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=True))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _show_file_diff(self, change: FileChange) -> None:
        if self.state is None:
            return
        if change.area == "staged":
            args = ["diff", "--cached", "--", change.path]
        elif change.area == "untracked":
            args = ["diff", "--no-index", "--", "/dev/null", change.path]
        else:
            args = ["diff", "--", change.path]
        result = self.reader.runner.run(args, cwd=self.state.path, check=False)
        text = result.stdout or result.stderr or "No diff output."
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Diff: {change.path}")
        dialog.resize(860, 620)
        layout = QVBoxLayout(dialog)
        viewer = QTextBrowser()
        viewer.setReadOnly(True)
        viewer.setHtml(_render_diff_html(text))
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _handle_commit_drop_on_commit(self, source: Commit, target: Commit) -> None:
        if self.state is None:
            return
        try:
            plan = self.planner.replay_commit_after(self.state, source, target)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _handle_commit_drop_to_trash(self, source: Commit) -> None:
        if self.state is None:
            return
        plans: list[CommandPlan] = []
        try:
            plans.append(self.planner.revert_commit_on_current_branch(self.state, source))
        except ValueError:
            pass
        try:
            plans.append(self.planner.drop_commit_from_current_branch(self.state, source))
        except ValueError:
            pass
        try:
            plans.append(self.planner.forget_unreachable_commits(self.state, [source]))
        except ValueError:
            pass
        if not plans:
            QMessageBox.information(
                self,
                "Operation Not Available",
                "This commit cannot be deleted from the current branch with an available safe plan.",
            )
            return
        self._choose_preview_and_execute(source.short_oid, "trash", plans)

    def _handle_commits_drop_on_reference(self, sources: list[Commit], target: Reference) -> None:
        if self.state is None:
            return
        try:
            plan = self.planner.cherry_pick_commits_to_branch(self.state, sources, target)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self._preview_and_confirm(plan)

    def _handle_commits_drop_on_commit(self, sources: list[Commit], target: Commit) -> None:
        if self.state is None:
            return
        try:
            plan = self.planner.replay_commits_after(self.state, sources, target)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self._preview_and_confirm(plan)

    def _handle_commits_drop_to_trash(self, sources: list[Commit]) -> None:
        if self.state is None:
            return
        plans: list[CommandPlan] = []
        try:
            plans.append(self.planner.revert_commits_on_current_branch(self.state, sources))
        except ValueError:
            pass
        try:
            plans.append(self.planner.drop_commits_from_current_branch(self.state, sources))
        except ValueError:
            pass
        try:
            plans.append(self.planner.forget_unreachable_commits(self.state, sources))
        except ValueError:
            pass
        if not plans:
            QMessageBox.information(
                self,
                "Operation Not Available",
                "The selection is neither a contiguous sequence on the current branch nor entirely unreachable.",
            )
            return
        self._choose_preview_and_execute(f"{len(sources)} selected commits", "trash", plans)

    def _stash_by_ref(self, ref: str) -> Optional[Stash]:
        if self.state is None:
            return None
        return next((stash for stash in self.state.stashes if stash.ref == ref), None)

    def _handle_stash_drop_on_branch(self, stash_ref: str, branch: Reference) -> None:
        if self.state is None:
            return
        stash = self._stash_by_ref(stash_ref)
        if stash is None:
            QMessageBox.information(self, "Stash Changed", "That stash no longer exists. Refresh and try again.")
            return
        try:
            plan = self.planner.apply_stash_to_branch(self.state, stash, branch)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self._preview_and_confirm(plan)

    def _handle_changes_drop_to_stash(self, changes: list[FileChange]) -> None:
        if self.state is None:
            return
        message, accepted = QInputDialog.getText(
            self,
            "Create Stash",
            "Stash name:",
            text="Selected working-tree files",
        )
        if not accepted:
            return
        try:
            plan = self.planner.stash_paths(self.state, changes, message)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self._preview_and_confirm(plan)

    def _handle_stash_drop_to_working(self, stash_ref: str) -> None:
        if self.state is None:
            return
        stash = self._stash_by_ref(stash_ref)
        if stash is None:
            QMessageBox.information(self, "Stash Changed", "That stash no longer exists. Refresh and try again.")
            return
        self._preview_and_confirm(self.planner.apply_stash_to_working_tree(self.state, stash))

    def _handle_stash_drop_to_trash(self, stash_ref: str) -> None:
        if self.state is None:
            return
        stash = self._stash_by_ref(stash_ref)
        if stash is None:
            QMessageBox.information(self, "Stash Changed", "That stash no longer exists. Refresh and try again.")
            return
        self._preview_and_confirm(self.planner.drop_stash(self.state, stash))

    def _handle_reference_drop_to_trash(self, source: Reference) -> None:
        if self.state is None:
            return
        try:
            if source.kind == "local_branch":
                plans = [
                    self.planner.delete_local_branch(self.state, source),
                    self.planner.delete_local_branch(self.state, source, force=True),
                ]
                self._choose_preview_and_execute(source.name, "trash", plans)
                return
            elif source.kind == "remote_tracking":
                plan = self.planner.delete_remote_branch(self.state, source)
            else:
                QMessageBox.information(self, "Operation Not Available", "Only local and remote-tracking branches can be deleted.")
                return
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        if source.kind == "remote_tracking":
            proceed = QMessageBox.warning(
                self,
                "Dangerous Remote Branch Deletion",
                (
                    f"This will ask the remote repository to delete `{source.name}`.\n\n"
                    "That can remove the branch for everyone using that remote. Continue to command preview?"
                ),
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
                QMessageBox.StandardButton.Cancel,
            )
            if proceed != QMessageBox.StandardButton.Ok:
                return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=True))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _handle_commit_drop_on_reference(self, source: Commit, target: Reference) -> None:
        if self.state is None:
            return
        if target.kind != "local_branch":
            QMessageBox.information(self, "Operation Not Available", "Drop commits onto local branches for cherry-pick or revert.")
            return
        plans = [
            self.planner.cherry_pick_commit_to_branch(self.state, source, target),
            self.planner.revert_commit_on_branch(self.state, source, target),
        ]
        self._choose_preview_and_execute(source.short_oid, target.name, plans)

    def _handle_reference_drop_on_commit(self, source: Reference, target: Commit) -> None:
        if self.state is None:
            return
        if source.kind != "local_branch":
            QMessageBox.information(self, "Operation Not Available", "Drop local branches onto commits to reset or move them.")
            return
        plans = [
            self.planner.reset_branch_to_commit(self.state, source, target, "soft"),
            self.planner.reset_branch_to_commit(self.state, source, target, "mixed"),
            self.planner.reset_branch_to_commit(self.state, source, target, "hard"),
        ]
        branch_name, accepted = QInputDialog.getText(
            self,
            "Optional New Branch",
            f"To avoid moving `{source.name}`, enter a new branch name at `{target.short_oid}`. Leave empty to choose reset.",
        )
        if accepted and branch_name.strip():
            try:
                plan = self.planner.create_branch_at_commit(self.state, target, branch_name)
            except ValueError as exc:
                QMessageBox.information(self, "Operation Not Available", str(exc))
                return
            self._preview_and_confirm(plan)
            return
        self._choose_preview_and_execute(source.name, target.short_oid, plans)

    def _choose_preview_and_execute(self, source_label: str, target_label: str, plans: list[CommandPlan]) -> None:
        dialog = OperationChoiceDialogLabels(source_label, target_label, plans, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_plan is None:
            return
        self._preview_and_confirm(dialog.selected_plan)

    def _preview_and_confirm(self, plan: CommandPlan) -> None:
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        confirm = CommandPlanDialog(plan, self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

    def _workspace_mode(self) -> None:
        self.working_panel.show()
        self.graph_scroll.show()
        self.right_panel.show()
        self.command_panel_group.show()
        self.main_splitter.setSizes([220, 700, 360])

    def _graph_focus_mode(self) -> None:
        self.working_panel.hide()
        self.graph_scroll.show()
        self.right_panel.hide()
        self.command_panel_group.hide()

    def _status_focus_mode(self) -> None:
        self.working_panel.show()
        self.graph_scroll.hide()
        self.right_panel.show()
        self.command_panel_group.hide()
        self.main_splitter.setSizes([520, 0, 520])

    def _command_focus_mode(self) -> None:
        self.working_panel.hide()
        self.graph_scroll.show()
        self.right_panel.hide()
        self.command_panel_group.show()

    def _show_stashes_tab(self) -> None:
        self.right_panel.show()
        self.details_tabs.setCurrentIndex(self.stashes_tab_index)

    def _commits_layout(self) -> None:
        self.graph.set_mode("commits")
        self._workspace_mode()

    def _branches_layout(self) -> None:
        self.graph.set_mode("branches")
        self._graph_focus_mode()
        self.command_panel_group.show()

    def _local_remote_layout(self) -> None:
        self.graph.set_mode("local_remote")
        self._workspace_mode()

    def _reset_graph_visualization(self) -> None:
        self.lane_width_spin.setValue(72)
        self.row_height_spin.setValue(52)

    def _change_ui_scale(self, delta: float) -> None:
        self._set_ui_scale(self.ui_scale + delta)

    def _reset_ui_scale(self) -> None:
        self._set_ui_scale(1.0)

    def _set_ui_scale(self, scale: float) -> None:
        scale = max(0.7, min(1.8, round(scale, 2)))
        if scale == self.ui_scale:
            return
        self.ui_scale = scale
        font = QFont(self._base_application_font)
        font.setPointSizeF(self._base_application_font.pointSizeF() * scale)
        QApplication.setFont(font)
        self.graph.set_visualization(zoom=scale)
        self.centralWidget().updateGeometry()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _about(self) -> None:
        QMessageBox.information(
            self,
            "About Gitualizer",
            "Gitualizer visualizes Git state and always shows generated commands before execution.",
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F5:
            self.refresh()
            return
        super().keyPressEvent(event)


class CommandPlanDialog(QDialog):
    def __init__(self, plan: CommandPlan, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Git Commands")
        self.resize(700, 560)
        layout = QVBoxLayout(self)
        text = QTextBrowser()
        text.setReadOnly(True)
        text.setHtml(_render_plan_html(plan, details_open=False))
        layout.addWidget(text)
        details_box = QGroupBox("Details")
        details_box.setCheckable(True)
        details_box.setChecked(False)
        details_layout = QVBoxLayout(details_box)
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(_render_plan_text(plan))
        details_layout.addWidget(details)
        details.setVisible(False)
        details_box.toggled.connect(details.setVisible)
        layout.addWidget(details_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Execute")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class OperationChoiceDialog(QDialog):
    def __init__(
        self,
        source: Reference,
        target: Reference,
        plans: list[CommandPlan],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.plans = plans
        self.selected_plan: Optional[CommandPlan] = None
        self.setWindowTitle("Choose Graph Operation")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"You dragged `{source.name}` onto `{target.name}`. Choose the Git strategy that matches the graph change you expect."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list_widget = QListWidget()
        for index, plan in enumerate(plans):
            item = QListWidgetItem(plan.title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget)

        self.details = QTextBrowser()
        self.details.setMinimumHeight(150)
        layout.addWidget(self.details)
        self.list_widget.currentRowChanged.connect(self._show_plan)
        self._show_plan(0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Preview This Change")
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_plan(self, row: int) -> None:
        if row < 0 or row >= len(self.plans):
            return
        plan = self.plans[row]
        effects = "".join(f"<li>{html.escape(effect)}</li>" for effect in plan.expected_effects)
        warnings = "".join(f"<li>{html.escape(warning)}</li>" for warning in plan.warnings)
        warnings_html = f"<p><b>Warnings</b></p><ul>{warnings}</ul>" if warnings else ""
        self.details.setHtml(
            f"""
            <h3>{html.escape(plan.title)}</h3>
            <p>{html.escape(plan.explanation)}</p>
            <p><b>Expected graph change</b></p>
            <ul>{effects}</ul>
            {warnings_html}
            <p style="color:#6b7280;">Commands are shown after you choose this preview.</p>
            """
        )

    def _accept_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.plans):
            return
        self.selected_plan = self.plans[row]
        self.accept()


class OperationChoiceDialogLabels(QDialog):
    def __init__(
        self,
        source_label: str,
        target_label: str,
        plans: list[CommandPlan],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.plans = plans
        self.selected_plan: Optional[CommandPlan] = None
        self.setWindowTitle("Choose Graph Operation")
        self.resize(640, 430)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"You dragged `{source_label}` onto `{target_label}`. Choose the Git strategy that matches the graph change you expect."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list_widget = QListWidget()
        for index, plan in enumerate(plans):
            item = QListWidgetItem(plan.title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget)
        self.details = QTextBrowser()
        self.details.setMinimumHeight(160)
        layout.addWidget(self.details)
        self.list_widget.currentRowChanged.connect(self._show_plan)
        self._show_plan(0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Preview This Change")
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_plan(self, row: int) -> None:
        if row < 0 or row >= len(self.plans):
            return
        self.details.setHtml(_render_plan_html(self.plans[row], details_open=True))

    def _accept_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.plans):
            return
        self.selected_plan = self.plans[row]
        self.accept()


def _empty_preview_html() -> str:
    return """
    <div style="color:#6b7280;">
      Drag a branch or remote-tracking label onto another branch in the graph.
      Gitualizer will preview possible strategies, then show exact commands before execution.
    </div>
    """


def _render_plan_html(plan: CommandPlan, details_open: bool) -> str:
    command_lines = "<br>".join(html.escape(step.display) for step in plan.steps)
    command_block = (
        "<div style='color:#ffffff; background:#102a43; border-radius:6px; padding:10px; "
        "font-family:JetBrains Mono, DejaVu Sans Mono, monospace;'>"
        f"{command_lines}</div>"
    )
    details = ""
    if details_open:
        details = _details_html(plan)
    else:
        details = (
            "<div style='color:#6b7280; margin-top:10px;'>"
            "Open the confirmation details to inspect expected effects, warnings, and impact.</div>"
        )
    preview = _preview_steps_html(plan)
    graph_preview = _graph_preview_html(plan)
    return f"""
    <h2 style="color:#1f2933;">{html.escape(plan.title)}</h2>
    <p>{html.escape(plan.explanation)}</p>
    {graph_preview}
    {preview}
    {command_block}
    {details}
    """


def _details_html(plan: CommandPlan) -> str:
    effects = "".join(f"<li>{html.escape(effect)}</li>" for effect in plan.expected_effects)
    warnings = "".join(f"<li>{html.escape(warning)}</li>" for warning in plan.warnings)
    if not warnings:
        warnings = "<li>None</li>"
    if not effects:
        effects = "<li>No explicit effects recorded.</li>"
    return f"""
    <div style="color:#1f2933;">
      <p><b>History rewriting:</b> {'YES' if plan.history_rewrite else 'NO'}</p>
      <p><b>Destructive:</b> {'YES' if plan.destructive else 'NO'}</p>
      <p><b>Remote impact:</b> {html.escape(plan.remote_impact)}</p>
      <p><b>Expected effects:</b></p>
      <ul>{effects}</ul>
      <p><b>Warnings:</b></p>
      <ul>{warnings}</ul>
    </div>
    """


def _preview_steps_html(plan: CommandPlan) -> str:
    if not plan.preview_steps:
        return ""
    steps = "".join(f"<li>{html.escape(step)}</li>" for step in plan.preview_steps)
    return f"""
    <div style="background:#f3f8ff; border:1px solid #b9d7ff; border-radius:6px; padding:8px; margin:8px 0;">
      <b>Step-by-step graph preview</b>
      <ol>{steps}</ol>
    </div>
    """


def _graph_preview_html(plan: CommandPlan) -> str:
    if not plan.graph_preview:
        return ""
    text = html.escape("\n".join(plan.graph_preview))
    return f"""
    <div style="background:#fffdf2; border:1px solid #eac54f; border-radius:6px; padding:8px; margin:8px 0;">
      <b>Proposed graph shape</b>
      <pre style="margin:6px 0 0 0; color:#1f2933; font-family:JetBrains Mono, DejaVu Sans Mono, monospace;">{text}</pre>
    </div>
    """


def _render_plan_text(plan: CommandPlan) -> str:
    lines = [
        plan.title,
        "",
        plan.explanation,
        "",
        "Commands:",
        plan.commands_text,
        "",
        f"History rewriting: {'YES' if plan.history_rewrite else 'NO'}",
        f"Destructive: {'YES' if plan.destructive else 'NO'}",
        f"Remote impact: {plan.remote_impact}",
    ]
    if plan.graph_preview:
        lines.extend(["", "Proposed graph shape:", *plan.graph_preview])
    if plan.expected_effects:
        lines.extend(["", "Expected effects:", *[f"- {effect}" for effect in plan.expected_effects]])
    if plan.preview_steps:
        lines.extend(["", "Preview steps:", *[f"{index + 1}. {step}" for index, step in enumerate(plan.preview_steps)]])
    if plan.warnings:
        lines.extend(["", "Warnings:", *[f"- {warning}" for warning in plan.warnings]])
    return "\n".join(lines)


def _render_result_html(result: ExecutionResult) -> str:
    color = "#1a7f37" if result.success else "#d1242f"
    lines = [f"<h3 style='color:{color};'>{'Success' if result.success else 'Failed'}</h3>"]
    for step in result.steps:
        lines.append(
            "<div style='color:#ffffff; background:#102a43; border-radius:6px; padding:10px; "
            "font-family:JetBrains Mono, DejaVu Sans Mono, monospace;'>"
            f"{html.escape(' '.join(step.args))}</div>"
        )
        lines.append(f"<p>exit code: {step.returncode}</p>")
        if step.stdout.strip():
            lines.append(f"<pre>{html.escape(step.stdout.strip())}</pre>")
        if step.stderr.strip():
            lines.append(f"<pre>{html.escape(step.stderr.strip())}</pre>")
    return "\n".join(lines)


def _render_result_text(result: ExecutionResult) -> str:
    lines = ["Success" if result.success else "Failed"]
    for step in result.steps:
        lines.append("")
        lines.append(" ".join(step.args))
        lines.append(f"exit code: {step.returncode}")
        if step.stdout.strip():
            lines.append(step.stdout.strip())
        if step.stderr.strip():
            lines.append(step.stderr.strip())
    return "\n".join(lines)


def _render_diff_html(diff: str) -> str:
    rendered: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            color, background = "#116329", "#dafbe1"
        elif line.startswith("-") and not line.startswith("---"):
            color, background = "#cf222e", "#ffebe9"
        elif line.startswith("@@"):
            color, background = "#0550ae", "#ddf4ff"
        else:
            color, background = "#111111", "transparent"
        rendered.append(
            f'<span style="color:{color}; background-color:{background};">{html.escape(line)}</span>'
        )
    body = "\n".join(rendered) or '<span style="color:#111111;">No diff output.</span>'
    return (
        '<pre style="margin:0; white-space:pre-wrap; color:#111111; '
        'font-family:JetBrains Mono, DejaVu Sans Mono, monospace;">'
        f"{body}</pre>"
    )


def _looks_like_auth_failure(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(
        marker in lowered
        for marker in (
            "authentication failed",
            "could not read password",
            "permission denied",
            "publickey",
            "terminal prompts disabled",
        )
    )


def _is_remote_step(args: list[str]) -> bool:
    return len(args) > 1 and args[0] == "git" and args[1] in REMOTE_AUTH_COMMANDS


def _remote_name_from_step(args: list[str]) -> Optional[str]:
    if len(args) < 3:
        return None
    command = args[1]
    values = [arg for arg in args[2:] if not arg.startswith("-")]
    if not values:
        return None
    if command in {"fetch", "pull", "push", "ls-remote", "clone"}:
        return values[0]
    return None


APP_STYLE = """
QMainWindow, QWidget {
    background: #f6f8fa;
    color: #1f2937;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #d9e0e7;
}
QMenuBar::item {
    padding: 6px 10px;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: #e8f2ff;
}
QMenu {
    background: #ffffff;
    border: 1px solid #d9e0e7;
}
QMenu::item {
    padding: 6px 22px;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #d8dee4;
    border-radius: 8px;
    margin-top: 14px;
    padding: 7px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #4b5563;
    font-weight: 700;
}
QLineEdit, QTextEdit, QTextBrowser, QTableWidget, QListWidget, QLabel#dropZone, QLabel#stageHandle {
    background: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: #d8ecff;
}
QLineEdit[invalid="true"] {
    border: 2px solid #cf222e;
    background: #ffebe9;
}
QTextEdit, QTextBrowser {
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
QPushButton {
    background: #1f6feb;
    color: #ffffff;
    border: 0;
    border-radius: 6px;
    padding: 5px 9px;
    font-weight: 700;
}
QPushButton:hover {
    background: #1a5fd0;
}
QPushButton#refreshButton {
    background: #1a7f37;
}
QPushButton#refreshButton:hover {
    background: #176f31;
}
QPushButton#refreshButton:pressed {
    background: #125c28;
}
QPushButton#fetchButton {
    background: #8250df;
}
QPushButton#fetchButton:hover {
    background: #7042c5;
}
QPushButton#fetchButton:pressed {
    background: #5e35aa;
}
QPushButton:disabled {
    background: #a9b6c5;
}
QPushButton#refreshButton:disabled, QPushButton#fetchButton:disabled {
    background: #a9b6c5;
}
QPushButton#authStatusButton {
    background: #6b7280;
    padding: 3px 8px;
}
QPushButton#authStatusButton[authState="authenticated"] {
    background: #1a7f37;
}
QPushButton#authStatusButton[authState="required"] {
    background: #cf222e;
}
QPushButton#authStatusButton[authState="unavailable"] {
    background: #9a6700;
}
QPushButton#networkStatusButton {
    background: #6b7280;
    padding: 3px 8px;
}
QPushButton#networkStatusButton[networkState="online"] {
    background: #1a7f37;
}
QPushButton#networkStatusButton[networkState="offline"] {
    background: #9a6700;
}
QHeaderView::section {
    background: #eef2f6;
    border: 0;
    border-right: 1px solid #d9e0e7;
    padding: 4px;
    font-weight: 700;
}
QTableWidget {
    gridline-color: #edf1f5;
    alternate-background-color: #f8fafc;
}
QListWidget {
    alternate-background-color: #f8fafc;
}
QListWidget[dropActive="true"], QLabel[dropActive="true"] {
    border: 2px solid #1f6feb;
    background: #e8f2ff;
}
QLabel#dropZone {
    color: #d1242f;
    border-color: #f1aeb5;
    background: #fff5f5;
    font-weight: 800;
}
QLabel#dropZone[dropActive="true"] {
    border: 2px solid #d1242f;
    background: #ffe3e6;
    color: #a40e26;
}
QLabel#stageHandle {
    color: #9a6700;
    background: #fff8c5;
    border-color: #eac54f;
    font-weight: 700;
}
QLabel#stageHandle[dropActive="true"] {
    border: 2px solid #bf8700;
    background: #fff1a7;
}
QLabel#subtleHeading {
    color: #4b5563;
    font-weight: 700;
    border: 0;
    padding: 0;
}
QSplitter::handle {
    background: #d8e0e8;
}
QSplitter::handle:hover {
    background: #b8c6d6;
}
QScrollArea#graphScroll {
    border: 1px solid #d9e0e7;
    border-radius: 8px;
    background: #ffffff;
}
QLabel#summary {
    line-height: 1.35;
}
h2 {
    color: #1f2933;
    margin-bottom: 4px;
}
.command {
    color: #ffffff;
    background: #102a43;
    border-radius: 6px;
    padding: 10px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
.muted {
    color: #6b7280;
}
.details {
    color: #1f2933;
}
.success {
    color: #1a7f37;
}
.failure {
    color: #d1242f;
}
"""
