from __future__ import annotations

import html
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gitualizer.git.repository import RepositoryReader
from gitualizer.git.runner import GitError
from gitualizer.model.repository_state import FileChange, Reference, RepositoryState
from gitualizer.operations.command_plan import CommandPlan, ExecutionResult
from gitualizer.operations.executor import CommandExecutor
from gitualizer.operations.planner import OperationPlanner, state_fingerprint
from gitualizer.ui.graph_widget import CommitGraphWidget


class MainWindow(QMainWindow):
    def __init__(self, initial_path: Optional[Path] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.reader = RepositoryReader()
        self.planner = OperationPlanner()
        self.executor = CommandExecutor()
        self.state: Optional[RepositoryState] = None
        self.auto_refresh_enabled = True
        self.refresh_in_progress = False
        self.setWindowTitle("Gitualizer")
        self.resize(1120, 720)
        self.setStyleSheet(APP_STYLE)

        self.path_edit = QLineEdit(str(initial_path or Path.cwd()))
        self.path_edit.setPlaceholderText("Path inside a Git repository")
        self.open_button = QPushButton("Browse")
        self.refresh_button = QPushButton("Refresh")
        self.minimize_button = QPushButton("-")
        self.fullscreen_button = QPushButton("[]")
        self.close_button = QPushButton("X")
        for button, tooltip in [
            (self.minimize_button, "Minimize"),
            (self.fullscreen_button, "Toggle full screen"),
            (self.close_button, "Close"),
        ]:
            button.setObjectName("windowControl")
            button.setToolTip(tooltip)
            button.setFixedSize(30, 28)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("Repository"))
        top.addWidget(self.path_edit, 1)
        top.addWidget(self.open_button)
        top.addWidget(self.refresh_button)
        top.addSpacing(8)
        top.addWidget(self.minimize_button)
        top.addWidget(self.fullscreen_button)
        top.addWidget(self.close_button)

        self.graph = CommitGraphWidget()
        graph_scroll = QScrollArea()
        graph_scroll.setWidgetResizable(True)
        graph_scroll.setWidget(self.graph)
        graph_scroll.setObjectName("graphScroll")

        self.status_table = self._table(["Area", "Code", "Path"])
        self.refs_table = self._table(["Kind", "Name", "Target", "Upstream", "Ahead/Behind"])
        self.remotes_table = self._table(["Remote", "Fetch URL", "Push URL"])
        self.summary = QLabel("No repository loaded.")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("summary")

        self.command_panel = QTextBrowser()
        self.command_panel.setOpenExternalLinks(False)
        self.command_panel.setMinimumHeight(115)
        self.command_panel.setHtml(_empty_preview_html())

        self.branch_combo = QComboBox()
        self.branch_name = QLineEdit()
        self.branch_name.setPlaceholderText("new-branch-name")
        self.remote_combo = QComboBox()
        self.commit_message = QLineEdit()
        self.commit_message.setPlaceholderText("Commit message")

        self.switch_button = QPushButton("Switch")
        self.create_branch_button = QPushButton("Create")
        self.stage_selected_button = QPushButton("Stage Selected")
        self.stage_all_button = QPushButton("Stage All")
        self.unstage_selected_button = QPushButton("Unstage Selected")
        self.unstage_all_button = QPushButton("Unstage All")
        self.commit_button = QPushButton("Commit")
        self.fetch_button = QPushButton("Fetch")
        self.ff_button = QPushButton("Fast-forward")
        self.push_button = QPushButton("Push")

        self.working_panel = self._panel("Working Tree and Index", self.status_table)
        self.operations_panel = self._operations_panel()

        self.repo_panel = self._panel("Repository State", self.summary)
        self.refs_panel = self._panel("References", self.refs_table)
        self.remotes_panel = self._panel("Remotes", self.remotes_table)
        self.right_panel = QWidget()
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(self.repo_panel)
        right_layout.addWidget(self.refs_panel, 2)
        right_layout.addWidget(self.remotes_panel, 1)

        self.graph_scroll = graph_scroll
        self.main_splitter = QSplitter()
        self.main_splitter.addWidget(self.working_panel)
        self.main_splitter.addWidget(self.graph_scroll)
        self.main_splitter.addWidget(self.right_panel)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setSizes([280, 600, 300])

        self.command_panel_group = self._panel("Operation / Preview / Commands", self.command_panel)
        self.bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.bottom_splitter.addWidget(self.operations_panel)
        self.bottom_splitter.addWidget(self.command_panel_group)
        self.bottom_splitter.setChildrenCollapsible(False)
        self.bottom_splitter.setSizes([360, 720])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addLayout(top)
        root_layout.addWidget(self.main_splitter, 1)
        root_layout.addWidget(self.bottom_splitter)
        self.setCentralWidget(root)
        self._build_menus()

        self.open_button.clicked.connect(self._browse)
        self.refresh_button.clicked.connect(self.refresh)
        self.path_edit.returnPressed.connect(self.refresh)
        self.minimize_button.clicked.connect(self.showMinimized)
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        self.close_button.clicked.connect(self.close)
        self.switch_button.clicked.connect(self._switch_branch)
        self.create_branch_button.clicked.connect(self._create_branch)
        self.stage_selected_button.clicked.connect(self._stage_selected)
        self.stage_all_button.clicked.connect(self._stage_all)
        self.unstage_selected_button.clicked.connect(self._unstage_selected)
        self.unstage_all_button.clicked.connect(self._unstage_all)
        self.commit_button.clicked.connect(self._commit)
        self.fetch_button.clicked.connect(self._fetch)
        self.ff_button.clicked.connect(self._fast_forward)
        self.push_button.clicked.connect(self._push)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2500)
        self.refresh_timer.timeout.connect(self._auto_refresh)
        self.refresh_timer.start()

        if initial_path is not None:
            self.refresh()
        else:
            self._set_enabled(False)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open Repository...", self)
        open_action.triggered.connect(self._browse)
        file_menu.addAction(open_action)
        file_menu.addAction("Refresh", self.refresh)
        file_menu.addSeparator()
        file_menu.addAction("Quit", QApplication.instance().quit)

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.addAction("Stage Selected", self._stage_selected)
        edit_menu.addAction("Unstage Selected", self._unstage_selected)
        edit_menu.addAction("Commit Staged", self._commit)

        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction("Workspace", self._workspace_mode)
        view_menu.addAction("Graph Focus", self._graph_focus_mode)
        view_menu.addAction("Status Focus", self._status_focus_mode)
        view_menu.addAction("Command Focus", self._command_focus_mode)
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

        operations_menu = self.menuBar().addMenu("Operations")
        operations_menu.addAction("Fetch", self._fetch)
        operations_menu.addAction("Fast-forward Current Branch", self._fast_forward)
        operations_menu.addAction("Push Current Branch", self._push)

        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("About Gitualizer", self._about)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(24)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _panel(self, title: str, child: QWidget) -> QWidget:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(child)
        return group

    def _operations_panel(self) -> QWidget:
        group = QGroupBox("V1 Safe Operations")
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(6)
        layout.addWidget(QLabel("Branch"), 0, 0)
        layout.addWidget(self.branch_combo, 0, 1)
        layout.addWidget(self.switch_button, 0, 2)
        layout.addWidget(QLabel("New branch"), 1, 0)
        layout.addWidget(self.branch_name, 1, 1)
        layout.addWidget(self.create_branch_button, 1, 2)
        layout.addWidget(self.stage_selected_button, 2, 0)
        layout.addWidget(self.stage_all_button, 2, 1)
        layout.addWidget(self.unstage_selected_button, 2, 2)
        layout.addWidget(self.unstage_all_button, 3, 0)
        layout.addWidget(QLabel("Message"), 4, 0)
        layout.addWidget(self.commit_message, 4, 1)
        layout.addWidget(self.commit_button, 4, 2)
        layout.addWidget(QLabel("Remote"), 5, 0)
        layout.addWidget(self.remote_combo, 5, 1)
        layout.addWidget(self.fetch_button, 5, 2)
        layout.addWidget(self.ff_button, 6, 1)
        layout.addWidget(self.push_button, 6, 2)
        layout.setColumnStretch(1, 1)
        return group

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

    def refresh(self, show_errors: bool = True) -> None:
        if self.refresh_in_progress:
            return
        self.refresh_in_progress = True
        try:
            self.state = self.reader.read(Path(self.path_edit.text()))
        except GitError as exc:
            self.state = None
            self.graph.set_state(None)
            self.graph.set_preview_plan(None)
            self.summary.setText("No repository loaded.")
            self._set_changes([])
            self._set_refs([])
            self._set_remotes(None)
            self._set_enabled(False)
            if show_errors:
                QMessageBox.warning(self, "Unable to Open Repository", str(exc))
            self.refresh_in_progress = False
            return
        self.graph.set_state(self.state)
        self._set_summary(self.state)
        self._set_changes(self.state.changes)
        self._set_refs(self.state.references)
        self._set_remotes(self.state)
        self._refresh_controls(self.state)
        self._set_enabled(True)
        self.refresh_in_progress = False

    def _set_enabled(self, enabled: bool) -> None:
        for widget in [
            self.switch_button,
            self.create_branch_button,
            self.stage_selected_button,
            self.stage_all_button,
            self.unstage_selected_button,
            self.unstage_all_button,
            self.commit_button,
            self.fetch_button,
            self.ff_button,
            self.push_button,
        ]:
            widget.setEnabled(enabled)

    def _refresh_controls(self, state: RepositoryState) -> None:
        self.branch_combo.clear()
        for ref in state.local_branches:
            self.branch_combo.addItem(ref.name)
        if state.head.branch:
            index = self.branch_combo.findText(state.head.branch)
            if index >= 0:
                self.branch_combo.setCurrentIndex(index)
        self.remote_combo.clear()
        for remote in state.remotes:
            self.remote_combo.addItem(remote.name)

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
            f"Commits loaded: {len(state.commits)}\n"
            f"Operation in progress: {operation}"
        )

    def _set_changes(self, changes: list[FileChange]) -> None:
        self.status_table.setRowCount(len(changes))
        for row, change in enumerate(changes):
            self.status_table.setItem(row, 0, QTableWidgetItem(change.area))
            self.status_table.setItem(row, 1, QTableWidgetItem(change.code))
            path = change.path if change.original_path is None else f"{change.original_path} -> {change.path}"
            self.status_table.setItem(row, 2, QTableWidgetItem(path))
            for column in range(3):
                item = self.status_table.item(row, column)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, change)
        self.status_table.resizeColumnsToContents()

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

    def _set_remotes(self, state: Optional[RepositoryState]) -> None:
        remotes = state.remotes if state else []
        self.remotes_table.setRowCount(len(remotes))
        for row, remote in enumerate(remotes):
            self.remotes_table.setItem(row, 0, QTableWidgetItem(remote.name))
            self.remotes_table.setItem(row, 1, QTableWidgetItem(remote.fetch_url or ""))
            self.remotes_table.setItem(row, 2, QTableWidgetItem(remote.push_url or ""))
        self.remotes_table.resizeColumnsToContents()

    def _selected_changes(self, area: Optional[str] = None) -> list[FileChange]:
        changes: list[FileChange] = []
        seen: set[str] = set()
        for item in self.status_table.selectedItems():
            change = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(change, FileChange):
                continue
            key = f"{change.area}:{change.path}"
            if key in seen:
                continue
            if area is not None and change.area != area:
                continue
            changes.append(change)
            seen.add(key)
        return changes

    def _with_plan(self, builder: Callable[[RepositoryState], CommandPlan]) -> None:
        if self.state is None:
            return
        try:
            plan = builder(self.state)
        except ValueError as exc:
            QMessageBox.information(self, "Operation Not Available", str(exc))
            return
        self.graph.set_preview_plan(plan)
        self.command_panel.setHtml(_render_plan_html(plan, details_open=False))
        dialog = CommandPlanDialog(plan, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._execute_plan(plan)

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
        self.command_panel.setHtml(_render_plan_html(plan, details_open=True) + _render_result_html(result))
        self.graph.set_preview_plan(None)
        self.refresh()
        if not result.success:
            QMessageBox.warning(self, "Git Command Failed", _render_result_text(result))

    def _switch_branch(self) -> None:
        self._with_plan(lambda state: self.planner.switch_branch(state, self.branch_combo.currentText()))

    def _create_branch(self) -> None:
        self._with_plan(lambda state: self.planner.create_branch(state, self.branch_name.text()))

    def _stage_selected(self) -> None:
        self._with_plan(lambda state: self.planner.stage_paths(state, self._selected_changes()))

    def _stage_all(self) -> None:
        self._with_plan(self.planner.stage_all)

    def _unstage_selected(self) -> None:
        self._with_plan(lambda state: self.planner.unstage_paths(state, self._selected_changes("staged")))

    def _unstage_all(self) -> None:
        self._with_plan(self.planner.unstage_all)

    def _commit(self) -> None:
        self._with_plan(lambda state: self.planner.commit(state, self.commit_message.text()))

    def _fetch(self) -> None:
        self._with_plan(lambda state: self.planner.fetch(state, self.remote_combo.currentText()))

    def _fast_forward(self) -> None:
        self._with_plan(self.planner.fast_forward_current_branch)

    def _push(self) -> None:
        self._with_plan(self.planner.push_current_branch)

    def _workspace_mode(self) -> None:
        self.working_panel.show()
        self.graph_scroll.show()
        self.right_panel.show()
        self.bottom_splitter.show()
        self.operations_panel.show()
        self.command_panel_group.show()
        self.main_splitter.setSizes([280, 600, 300])
        self.bottom_splitter.setSizes([360, 720])

    def _graph_focus_mode(self) -> None:
        self.working_panel.hide()
        self.graph_scroll.show()
        self.right_panel.hide()
        self.bottom_splitter.hide()

    def _status_focus_mode(self) -> None:
        self.working_panel.show()
        self.graph_scroll.hide()
        self.right_panel.show()
        self.bottom_splitter.hide()
        self.main_splitter.setSizes([520, 0, 520])

    def _command_focus_mode(self) -> None:
        self.working_panel.hide()
        self.graph_scroll.show()
        self.right_panel.hide()
        self.bottom_splitter.show()
        self.operations_panel.show()
        self.command_panel_group.show()
        self.bottom_splitter.setSizes([330, 760])

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


def _empty_preview_html() -> str:
    return """
    <div style="color:#6b7280;">Select an operation. Gitualizer will show the exact Git commands before execution.</div>
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
    return f"""
    <h2 style="color:#1f2933;">{html.escape(plan.title)}</h2>
    <p>{html.escape(plan.explanation)}</p>
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
    if plan.expected_effects:
        lines.extend(["", "Expected effects:", *[f"- {effect}" for effect in plan.expected_effects]])
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


APP_STYLE = """
QMainWindow, QWidget {
    background: #f4f6f8;
    color: #1f2933;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
    font-size: 9pt;
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
    border: 1px solid #d9e0e7;
    border-radius: 8px;
    margin-top: 14px;
    padding: 7px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #52616f;
    font-weight: 700;
}
QLineEdit, QComboBox, QTextEdit, QTextBrowser, QTableWidget {
    background: #ffffff;
    border: 1px solid #cfd8e3;
    border-radius: 6px;
    padding: 4px;
    selection-background-color: #d8ecff;
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
QPushButton:disabled {
    background: #a9b6c5;
}
QPushButton#windowControl {
    background: #eef2f6;
    color: #344054;
    border: 1px solid #cfd8e3;
    padding: 0;
}
QPushButton#windowControl:hover {
    background: #dce9f8;
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
