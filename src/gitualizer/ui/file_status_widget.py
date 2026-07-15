from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QByteArray, QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from gitualizer.model.repository_state import FileChange


CHANGE_MIME = "application/x-gitualizer-file-changes"
STAGE_MIME = "application/x-gitualizer-stage"


class ChangeListWidget(QListWidget):
    changesDropped = Signal(object)

    def __init__(self, area: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.area = area
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(area == "working")
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)

    def changes(self) -> list[FileChange]:
        items = [self.item(index) for index in range(self.count())]
        return [item.data(Qt.ItemDataRole.UserRole) for item in items if isinstance(item.data(Qt.ItemDataRole.UserRole), FileChange)]

    def selected_changes(self) -> list[FileChange]:
        changes: list[FileChange] = []
        for item in self.selectedItems():
            change = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(change, FileChange):
                changes.append(change)
        return changes

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        changes = self.selected_changes()
        if not changes and self.area == "staged":
            changes = self.changes()
        if not changes:
            return
        drag = QDrag(self)
        mime = QMimeData()
        payload = json.dumps([_change_payload(change) for change in changes]).encode("utf-8")
        mime.setData(CHANGE_MIME, QByteArray(payload))
        if self.area == "staged":
            mime.setData(STAGE_MIME, QByteArray(b"1"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.area == "working":
            event.ignore()
            return
        if event.mimeData().hasFormat(CHANGE_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if self.area != "staged" or not event.mimeData().hasFormat(CHANGE_MIME):
            event.ignore()
            return
        changes = decode_changes(event.mimeData().data(CHANGE_MIME))
        if changes:
            self.changesDropped.emit(changes)
            event.acceptProposedAction()
            return
        event.ignore()


class FileStatusWidget(QWidget):
    changesDroppedToStage = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.working_list = ChangeListWidget("working")
        self.staged_list = ChangeListWidget("staged")
        self.staged_list.setAcceptDrops(True)
        self.staged_list.changesDropped.connect(lambda changes: self.changesDroppedToStage.emit(changes))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._column("Working Tree", self.working_list))
        layout.addWidget(self._column("Staging Area / Index", self.staged_list))

    def set_changes(self, changes: list[FileChange]) -> None:
        self.working_list.clear()
        self.staged_list.clear()
        for change in changes:
            if change.area in {"working_tree", "untracked", "conflict"}:
                self._add_change(self.working_list, change)
            elif change.area == "staged":
                self._add_change(self.staged_list, change)

    def dropEvent(self, event) -> None:  # noqa: N802
        event.ignore()

    def _column(self, title: str, list_widget: ChangeListWidget) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("subtleHeading")
        layout.addWidget(label)
        layout.addWidget(list_widget)
        return widget

    def _add_change(self, list_widget: QListWidget, change: FileChange) -> None:
        label = f"{change.code}  {change.path}"
        if change.original_path:
            label = f"{change.code}  {change.original_path} -> {change.path}"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, change)
        list_widget.addItem(item)


def decode_changes(data: QByteArray) -> list[FileChange]:
    try:
        raw = json.loads(bytes(data).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    changes: list[FileChange] = []
    for item in raw:
        try:
            changes.append(
                FileChange(
                    path=item["path"],
                    area=item["area"],
                    code=item["code"],
                    original_path=item.get("original_path"),
                )
            )
        except (KeyError, TypeError):
            continue
    return changes


def _change_payload(change: FileChange) -> dict[str, Optional[str]]:
    return {
        "path": change.path,
        "area": change.area,
        "code": change.code,
        "original_path": change.original_path,
    }
