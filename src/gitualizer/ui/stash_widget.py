from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from gitualizer.model.repository_state import Stash
from gitualizer.ui.drag_mime import CHANGE_MIME, STASH_MIME
from gitualizer.ui.file_status_widget import decode_changes


class StashListWidget(QListWidget):
    changesDropped = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setProperty("dropActive", False)

    def startDrag(self, supported_actions) -> None:  # noqa: N802
        item = self.currentItem()
        if item is None:
            return
        stash = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(stash, Stash):
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(STASH_MIME, QByteArray(stash.ref.encode("utf-8")))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(CHANGE_MIME):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_drop_active(False)
        if not event.mimeData().hasFormat(CHANGE_MIME):
            event.ignore()
            return
        changes = decode_changes(event.mimeData().data(CHANGE_MIME))
        working_changes = [change for change in changes if change.area in {"working_tree", "untracked", "conflict"}]
        if working_changes:
            self.changesDropped.emit(working_changes)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)


class StashWidget(QWidget):
    changesDropped = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.list_widget = StashListWidget()
        self.list_widget.setDragEnabled(True)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.changesDropped.connect(self.changesDropped.emit)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list_widget)

    def set_stashes(self, stashes: list[Stash]) -> None:
        self.list_widget.clear()
        for stash in stashes:
            item = QListWidgetItem(f"|||  {stash.ref}  {stash.subject}")
            item.setToolTip(stash.oid)
            item.setData(Qt.ItemDataRole.UserRole, stash)
            self.list_widget.addItem(item)
