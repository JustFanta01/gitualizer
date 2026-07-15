from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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
        self.setWindowTitle("Gitualizer")
        self.resize(1320, 840)
        self.setStyleSheet(APP_STYLE)

        self.path_edit = QLineEdit(str(initial_path or Path.cwd()))
        self.path_edit.setPlaceholderText("Path inside a Git repository")
        self.open_button = QPushButton("Browse")
        self.refresh_button = QPushButton("Refresh")

        top = QHBoxLayout()
        top.addWidget(QLabel("Repository"))
        top.addWidget(self.path_edit, 1)
        top.addWidget(self.open_button)
        top.addWidget(self.refresh_button)

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

        self.command_panel = QTextEdit()
        self.command_panel.setReadOnly(True)
        self.command_panel.setMinimumHeight(130)
        self.command_panel.setPlainText("Select a V1 operation. Commands will appear here before execution.")

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

        self.row_spacing = QSlider(Qt.Orientation.Horizontal)
        self.row_spacing.setRange(48, 110)
        self.row_spacing.setValue(72)
        self.lane_spacing = QSlider(Qt.Orientation.Horizontal)
        self.lane_spacing.setRange(68, 150)
        self.lane_spacing.setValue(96)

        left = self._panel("Working Tree and Index", self.status_table)
        operations = self._operations_panel()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._panel("Repository State", self.summary))
        right_layout.addWidget(self._panel("References", self.refs_table), 2)
        right_layout.addWidget(self._panel("Remotes", self.remotes_table), 1)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addWidget(self._graph_controls())
        center_layout.addWidget(graph_scroll, 1)

        main_splitter = QSplitter()
        main_splitter.addWidget(left)
        main_splitter.addWidget(center)
        main_splitter.addWidget(right)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setSizes([330, 680, 360])

        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        bottom_splitter.addWidget(operations)
        bottom_splitter.addWidget(self._panel("Operation / Preview / Commands", self.command_panel))
        bottom_splitter.setChildrenCollapsible(False)
        bottom_splitter.setSizes([430, 850])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(12)
        root_layout.addLayout(top)
        root_layout.addWidget(main_splitter, 1)
        root_layout.addWidget(bottom_splitter)
        self.setCentralWidget(root)

        self.open_button.clicked.connect(self._browse)
        self.refresh_button.clicked.connect(self.refresh)
        self.path_edit.returnPressed.connect(self.refresh)
        self.row_spacing.valueChanged.connect(self.graph.set_row_spacing)
        self.lane_spacing.valueChanged.connect(self.graph.set_lane_spacing)
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

        if initial_path is not None:
            self.refresh()
        else:
            self._set_enabled(False)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _panel(self, title: str, child: QWidget) -> QWidget:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.addWidget(child)
        return group

    def _graph_controls(self) -> QWidget:
        group = QGroupBox("Graph")
        layout = QHBoxLayout(group)
        layout.addWidget(QLabel("Row height"))
        layout.addWidget(self.row_spacing)
        layout.addWidget(QLabel("Lane width"))
        layout.addWidget(self.lane_spacing)
        return group

    def _operations_panel(self) -> QWidget:
        group = QGroupBox("V1 Safe Operations")
        layout = QGridLayout(group)
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

    def refresh(self) -> None:
        try:
            self.state = self.reader.read(Path(self.path_edit.text()))
        except GitError as exc:
            self.state = None
            self.graph.set_state(None)
            self.summary.setText("No repository loaded.")
            self._set_changes([])
            self._set_refs([])
            self._set_remotes(None)
            self._set_enabled(False)
            QMessageBox.warning(self, "Unable to Open Repository", str(exc))
            return
        self.graph.set_state(self.state)
        self._set_summary(self.state)
        self._set_changes(self.state.changes)
        self._set_refs(self.state.references)
        self._set_remotes(self.state)
        self._refresh_controls(self.state)
        self._set_enabled(True)

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
        self.command_panel.setPlainText(_render_plan(plan))
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
        self.command_panel.setPlainText(_render_plan(plan) + "\n\n" + _render_result(result))
        self.refresh()
        if not result.success:
            QMessageBox.warning(self, "Git Command Failed", _render_result(result))

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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F5:
            self.refresh()
            return
        super().keyPressEvent(event)


class CommandPlanDialog(QDialog):
    def __init__(self, plan: CommandPlan, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Git Commands")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(_render_plan(plan))
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Execute")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _render_plan(plan: CommandPlan) -> str:
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


def _render_result(result: ExecutionResult) -> str:
    lines = ["Execution result:", "Success" if result.success else "Failed"]
    for step in result.steps:
        lines.append("")
        lines.append(" ".join(step.args))
        lines.append(f"exit code: {step.returncode}")
        if step.stdout.strip():
            lines.append("stdout:")
            lines.append(step.stdout.strip())
        if step.stderr.strip():
            lines.append("stderr:")
            lines.append(step.stderr.strip())
    return "\n".join(lines)


APP_STYLE = """
QMainWindow, QWidget {
    background: #f4f6f8;
    color: #1f2933;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #d9e0e7;
    border-radius: 8px;
    margin-top: 18px;
    padding: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #52616f;
    font-weight: 700;
}
QLineEdit, QComboBox, QTextEdit, QTableWidget {
    background: #ffffff;
    border: 1px solid #cfd8e3;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #d8ecff;
}
QTextEdit {
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
QPushButton {
    background: #1f6feb;
    color: #ffffff;
    border: 0;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: 700;
}
QPushButton:hover {
    background: #1a5fd0;
}
QPushButton:disabled {
    background: #a9b6c5;
}
QHeaderView::section {
    background: #eef2f6;
    border: 0;
    border-right: 1px solid #d9e0e7;
    padding: 7px;
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
"""
