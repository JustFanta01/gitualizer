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
    dragStarted = Signal(str)
    dragEnded = Signal()

    def __init__(self, area: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.area = area
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)
        self.setProperty("dropActive", False)

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
        self.dragStarted.emit(self.area)
        drag = QDrag(self)
        mime = QMimeData()
        payload = json.dumps([_change_payload(change) for change in changes]).encode("utf-8")
        mime.setData(CHANGE_MIME, QByteArray(payload))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self.dragEnded.emit()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(CHANGE_MIME):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_drop_active(False)
        if not event.mimeData().hasFormat(CHANGE_MIME):
            event.ignore()
            return
        changes = decode_changes(event.mimeData().data(CHANGE_MIME))
        if changes:
            self.changesDropped.emit(changes)
            event.acceptProposedAction()
            return
        event.ignore()

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_drop_hint(self, active: bool) -> None:
        self._set_drop_active(active)


class FileStatusWidget(QWidget):
    changesDroppedToStage = Signal(object)
    changesDroppedToWorking = Signal(object)
    changesDroppedToTrash = Signal(object)
    changeActivated = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.working_list = ChangeListWidget("working")
        self.staged_list = ChangeListWidget("staged")
        self.stage_handle = StageAreaDragHandle()
        self.trash_zone = DropZone("[X]")
        self.trash_zone.setToolTip("Discard selected changes")
        self.staged_list.setAcceptDrops(True)
        self.staged_list.changesDropped.connect(lambda changes: self.changesDroppedToStage.emit(changes))
        self.working_list.changesDropped.connect(lambda changes: self.changesDroppedToWorking.emit(changes))
        self.trash_zone.changesDropped.connect(lambda changes: self.changesDroppedToTrash.emit(changes))
        self.working_list.itemDoubleClicked.connect(self._activate_item)
        self.staged_list.itemDoubleClicked.connect(self._activate_item)
        self.working_list.dragStarted.connect(self._show_drag_targets)
        self.staged_list.dragStarted.connect(self._show_drag_targets)
        self.working_list.dragEnded.connect(self._clear_drag_targets)
        self.staged_list.dragEnded.connect(self._clear_drag_targets)
        self.stage_handle.dragStarted.connect(lambda: self._show_drag_targets("stage_area"))
        self.stage_handle.dragEnded.connect(self._clear_drag_targets)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(QLabel("Working Tree and Index"))
        header.addStretch(1)
        header.addWidget(self.trash_zone)
        lists = QHBoxLayout()
        lists.setContentsMargins(0, 0, 0, 0)
        lists.setSpacing(8)
        lists.addWidget(self._column("Working Tree", self.working_list))
        lists.addWidget(self._column("Staging Area / Index", self.staged_list, self.stage_handle))
        layout.addLayout(header)
        layout.addLayout(lists)

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

    def _column(self, title: str, list_widget: QWidget, drag_handle: Optional[QWidget] = None) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("subtleHeading")
        layout.addWidget(label)
        if drag_handle is not None:
            layout.addWidget(drag_handle)
        layout.addWidget(list_widget)
        return widget

    def _add_change(self, list_widget: QListWidget, change: FileChange) -> None:
        label = f"|||  {change.code}  {change.path}"
        if change.original_path:
            label = f"|||  {change.code}  {change.original_path} -> {change.path}"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, change)
        list_widget.addItem(item)

    def _activate_item(self, item: QListWidgetItem) -> None:
        change = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(change, FileChange):
            self.changeActivated.emit(change)

    def _show_drag_targets(self, area: str) -> None:
        self.trash_zone.set_drop_active(True)
        if area == "working":
            self.staged_list.set_drop_hint(True)
        elif area == "staged":
            self.working_list.set_drop_hint(True)
        elif area == "stage_area":
            self.stage_handle.set_drop_active(True)

    def _clear_drag_targets(self) -> None:
        self.trash_zone.set_drop_active(False)
        self.working_list.set_drop_hint(False)
        self.staged_list.set_drop_hint(False)
        self.stage_handle.set_drop_active(False)


class StageAreaDragHandle(QLabel):
    dragStarted = Signal()
    dragEnded = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("|||  Commit whole staging area", parent)
        self.setObjectName("stageHandle")
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setProperty("dropActive", False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.dragStarted.emit()
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(STAGE_MIME, QByteArray(b"1"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self.dragEnded.emit()

    def set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)


class DropZone(QLabel):
    changesDropped = Signal(object)

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("dropZone")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setFixedSize(34, 28)
        self.setProperty("dropActive", False)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(CHANGE_MIME):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_drop_active(False)
        if not event.mimeData().hasFormat(CHANGE_MIME):
            event.ignore()
            return
        changes = decode_changes(event.mimeData().data(CHANGE_MIME))
        if changes:
            self.changesDropped.emit(changes)
            event.acceptProposedAction()
            return
        event.ignore()

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_drop_active(self, active: bool) -> None:
        self._set_drop_active(active)


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
