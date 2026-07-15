from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from gitualizer.model.repository_state import Commit, Reference, RepositoryState
from gitualizer.operations.command_plan import CommandPlan


@dataclass(frozen=True)
class CommitNode:
    oid: str
    x: float
    y: float


class CommitGraphWidget(QWidget):
    referenceDropped = Signal(object, object)
    referenceDroppedOnCommit = Signal(object, object)
    commitDroppedOnReference = Signal(object, object)
    stageDroppedOnBranch = Signal(object)
    commitDroppedOnCommit = Signal(object, object)
    commitDroppedToTrash = Signal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state: Optional[RepositoryState] = None
        self._preview_plan: Optional[CommandPlan] = None
        self._nodes: dict[str, CommitNode] = {}
        self._ref_hitboxes: list[tuple[QRectF, Reference]] = []
        self._commit_hitboxes: list[tuple[QRectF, Commit]] = []
        self._drag_ref: Optional[Reference] = None
        self._drag_commit: Optional[Commit] = None
        self._drag_pos: Optional[QPoint] = None
        self._hover_ref: Optional[Reference] = None
        self._hover_commit: Optional[Commit] = None
        self._external_stage_drag = False
        self._mode = "commits"
        self._trash_rect = QRectF()
        self._row_spacing = 52
        self._lane_spacing = 72
        self.setMinimumSize(420, 340)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def set_state(self, state: Optional[RepositoryState]) -> None:
        self._state = state
        self._nodes = self._layout_nodes(state)
        row_count = max(len(self._nodes), 8)
        lane_count = max((int(node.x) for node in self._nodes.values()), default=1)
        self.setMinimumHeight(84 + row_count * self._row_spacing)
        self.setMinimumWidth(max(500, lane_count + 420))
        self.update()

    def set_preview_plan(self, plan: Optional[CommandPlan]) -> None:
        self._preview_plan = plan
        self.update()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def _layout_nodes(self, state: Optional[RepositoryState]) -> dict[str, CommitNode]:
        if state is None:
            return {}
        commits = list(state.commits.values())
        lane_by_oid: dict[str, int] = {}
        branch_targets = {ref.target for ref in state.references if ref.kind in {"local_branch", "remote_tracking"}}
        next_lane = 0
        for commit in commits:
            if commit.oid in branch_targets and commit.oid not in lane_by_oid:
                lane_by_oid[commit.oid] = next_lane
                next_lane += 1
            if commit.oid not in lane_by_oid:
                inherited = next((lane_by_oid[parent] for parent in commit.parents if parent in lane_by_oid), None)
                if inherited is None:
                    inherited = next_lane
                    next_lane += 1
                lane_by_oid[commit.oid] = inherited
            for parent in commit.parents:
                lane_by_oid.setdefault(parent, lane_by_oid[commit.oid])
        nodes: dict[str, CommitNode] = {}
        for index, commit in enumerate(commits):
            lane = lane_by_oid.get(commit.oid, 0)
            nodes[commit.oid] = CommitNode(
                oid=commit.oid,
                x=78 + lane * self._lane_spacing,
                y=116 + index * self._row_spacing,
            )
        return nodes

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        self._ref_hitboxes = []
        self._commit_hitboxes = []
        self._draw_grid(painter)
        if self._state is None:
            self._draw_empty(painter, "Open a Git repository to inspect it.")
            return
        if self._mode == "branches":
            self._draw_branch_overview(painter, include_remote_columns=False)
            self._draw_drag(painter)
            return
        if self._mode == "local_remote":
            self._draw_branch_overview(painter, include_remote_columns=True)
            self._draw_drag(painter)
            return
        self._draw_git_flow(painter)
        if not self._state.commits:
            self._draw_empty(painter, "Repository has no commits yet.")
            return
        self._draw_preview(painter)
        self._draw_edges(painter)
        self._draw_commits(painter)
        self._draw_trash(painter)
        self._draw_drag(painter)

    def _draw_branch_overview(self, painter: QPainter, include_remote_columns: bool) -> None:
        assert self._state is not None
        painter.setFont(QFont("Sans Serif", 10, QFont.Weight.Bold))
        painter.setPen(QColor("#1f2933"))
        if include_remote_columns:
            columns = [("Local branches", self._state.local_branches, 48.0), ("Remote-tracking", self._state.remote_tracking_branches, 360.0)]
        else:
            refs = self._state.local_branches + self._state.remote_tracking_branches + self._state.tags
            columns = [("Branches and refs", refs, 48.0)]
        for title, refs, x in columns:
            painter.drawText(QRectF(x, 28, 260, 24), title)
            y = 60.0
            for ref in refs:
                self._draw_branch_card(painter, ref, x, y, include_remote_columns)
                y += 38
        if self._state.commits_truncated:
            painter.setPen(QColor("#6b7280"))
            painter.setFont(QFont("Sans Serif", 8))
            painter.drawText(QRectF(48, self.height() - 34, 620, 24), f"Commit history is lazy-loaded: showing latest {self._state.commit_limit} commits.")

    def _draw_branch_card(self, painter: QPainter, ref: Reference, x: float, y: float, include_remote_columns: bool) -> None:
        color = {
            "local_branch": QColor("#1a7f37"),
            "remote_tracking": QColor("#8250df"),
            "tag": QColor("#bf8700"),
        }.get(ref.kind, QColor("#57606a"))
        width = 260.0
        rect = QRectF(x, y, width, 30)
        self._ref_hitboxes.append((rect, ref))
        is_target = self._hover_ref is not None and self._hover_ref.full_name == ref.full_name
        is_possible = self._is_possible_ref_drop(ref)
        painter.setPen(QPen(color, 2 if is_target or is_possible else 1))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 72 if is_target else (46 if is_possible else 20)))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(color)
        painter.setFont(QFont("Sans Serif", 9, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(8, 3, -54, -3), Qt.AlignmentFlag.AlignVCenter, f"||| {ref.name}")
        painter.setFont(QFont("Sans Serif", 8))
        painter.setPen(QColor("#52616f"))
        meta = ref.target[:12]
        if ref.upstream:
            meta = f"{meta}  -> {ref.upstream}"
        painter.drawText(rect.adjusted(8, 15, -10, 0), meta)
        if ref.behind and ref.behind > 0:
            painter.setBrush(QColor("#2da44e"))
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawEllipse(QPointF(x + width - 22, y + 15), 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Sans Serif", 7, QFont.Weight.Bold))
            painter.drawText(QRectF(x + width - 30, y + 7, 16, 16), Qt.AlignmentFlag.AlignCenter, str(ref.behind))
        if include_remote_columns and ref.ahead and ref.ahead > 0:
            painter.setBrush(QColor("#d1242f"))
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawEllipse(QPointF(x + width - 44, y + 15), 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(x + width - 52, y + 7, 16, 16), Qt.AlignmentFlag.AlignCenter, str(ref.ahead))

    def _draw_empty(self, painter: QPainter, text: str) -> None:
        painter.setPen(QColor("#666a73"))
        painter.setFont(QFont("Sans Serif", 12))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_git_flow(self, painter: QPainter) -> None:
        assert self._state is not None
        staged = len(self._state.staged_changes)
        working = len(self._state.working_tree_changes) + len(self._state.untracked_changes)
        head = self._state.head.short_oid or "unborn"
        flow = [
            ("Working Tree", f"{working} changed", QColor("#0f766e")),
            ("Staging Area / Index", f"{staged} staged", QColor("#b45309")),
            ("HEAD", head, QColor("#1f6feb")),
        ]
        x = 24.0
        y = 22.0
        width = 136.0
        height = 50.0
        for index, (title, subtitle, color) in enumerate(flow):
            rect = QRectF(x, y, width, height)
            active = self._preview_targets_flow(title)
            painter.setPen(QPen(color if active else QColor("#d0d7de"), 2))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 22 if active else 10))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QColor("#1f2933"))
            painter.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
            painter.drawText(rect.adjusted(8, 7, -8, -27), Qt.AlignmentFlag.AlignLeft, title)
            painter.setFont(QFont("Sans Serif", 8))
            painter.setPen(QColor("#52616f"))
            painter.drawText(rect.adjusted(8, 26, -8, -7), Qt.AlignmentFlag.AlignLeft, subtitle)
            if index < len(flow) - 1:
                painter.setPen(QPen(QColor("#9aa7b4"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                start = QPointF(x + width + 6, y + height / 2)
                end = QPointF(x + width + 38, y + height / 2)
                painter.drawLine(start, end)
                painter.drawLine(end, QPointF(end.x() - 8, end.y() - 6))
                painter.drawLine(end, QPointF(end.x() - 8, end.y() + 6))
            x += width + 40

    def _preview_targets_flow(self, title: str) -> bool:
        if self._preview_plan is None:
            return False
        command_text = self._preview_plan.commands_text
        if title.startswith("Working"):
            return "git add" in command_text or "git restore --staged" in command_text
        if title.startswith("Staging"):
            return "git add" in command_text or "git restore --staged" in command_text or "git commit" in command_text
        if title == "HEAD":
            return "git commit" in command_text or "git switch" in command_text or "git merge --ff-only" in command_text
        return False

    def _draw_grid(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor("#edf2f7"), 1))
        x = 78
        while x < self.width():
            painter.drawLine(QPointF(x, 90), QPointF(x, self.height()))
            x += self._lane_spacing

    def _draw_preview(self, painter: QPainter) -> None:
        if self._preview_plan is None or self._state is None:
            return
        command_text = self._preview_plan.commands_text
        if "git commit" not in command_text or not self._state.head.oid:
            return
        head_node = self._nodes.get(self._state.head.oid)
        if head_node is None:
            return
        ghost = QPointF(head_node.x, head_node.y - 58)
        painter.setPen(QPen(QColor("#1f6feb"), 2, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(ghost, QPointF(head_node.x, head_node.y - 12))
        painter.setBrush(QColor(31, 111, 235, 32))
        painter.setPen(QPen(QColor("#1f6feb"), 2, Qt.PenStyle.DashLine))
        painter.drawEllipse(ghost, 11, 11)
        painter.setPen(QColor("#1f6feb"))
        painter.setFont(QFont("Sans Serif", 9, QFont.Weight.Bold))
        painter.drawText(QRectF(ghost.x() + 20, ghost.y() - 12, 320, 24), "preview: new commit from index")

    def _draw_edges(self, painter: QPainter) -> None:
        assert self._state is not None
        for commit in self._state.commits.values():
            node = self._nodes.get(commit.oid)
            if node is None:
                continue
            for parent_oid in commit.parents:
                parent = self._nodes.get(parent_oid)
                if parent is None:
                    continue
                same_lane = abs(node.x - parent.x) < 1
                color = QColor("#8b98a8") if same_lane else QColor("#5b8def")
                painter.setPen(QPen(color, 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                path = QPainterPath(QPointF(node.x, node.y))
                midpoint = (node.y + parent.y) / 2
                path.cubicTo(QPointF(node.x, midpoint), QPointF(parent.x, midpoint), QPointF(parent.x, parent.y))
                painter.drawPath(path)

    def _draw_commits(self, painter: QPainter) -> None:
        assert self._state is not None
        refs_by_target: dict[str, list[Reference]] = {}
        for ref in self._state.references:
            refs_by_target.setdefault(ref.target, []).append(ref)
        if self._state.head.oid:
            refs_by_target.setdefault(self._state.head.oid, []).append(
                Reference(name="HEAD", full_name="HEAD", target=self._state.head.oid, kind="head")
            )

        for commit in self._state.commits.values():
            node = self._nodes.get(commit.oid)
            if node is None:
                continue
            self._commit_hitboxes.append((QRectF(node.x - 13, node.y - 13, 26, 26), commit))
            is_head = self._state.head.oid == commit.oid
            is_commit_target = self._is_possible_commit_drop(commit)
            fill = QColor("#1f6feb") if is_head else QColor("#26313d")
            if is_commit_target:
                painter.setPen(QPen(QColor("#2da44e"), 5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(node.x, node.y), 15, 15)
            if is_head:
                painter.setPen(QPen(QColor("#9ed0ff"), 6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(node.x, node.y), 13, 13)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(fill)
            painter.drawEllipse(QPointF(node.x, node.y), 9, 9)

            painter.setPen(QColor("#20242a"))
            painter.setFont(QFont("Sans Serif", 8))
            painter.drawText(QRectF(node.x - 32, node.y - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, "|||")
            label = f"{commit.short_oid}  {commit.subject}"
            painter.drawText(QRectF(node.x + 18, node.y - 12, max(240, self.width() - node.x - 38), 20), label)

            refs = refs_by_target.get(commit.oid, [])
            self._draw_refs(painter, refs, node.x + 18, node.y + 10)

    def _draw_refs(self, painter: QPainter, refs: list[Reference], x: float, y: float) -> None:
        offset = 0.0
        for ref in sorted(refs, key=lambda item: item.name):
            text = f"||| {ref.name}" if ref.kind != "head" else ref.name
            color = {
                "head": QColor("#d1242f"),
                "local_branch": QColor("#1a7f37"),
                "remote_tracking": QColor("#8250df"),
                "tag": QColor("#bf8700"),
            }.get(ref.kind, QColor("#57606a"))
            painter.setFont(QFont("Sans Serif", 8))
            width = painter.fontMetrics().horizontalAdvance(text) + 14
            rect = QRectF(x + offset, y, width, 20)
            if ref.kind != "head":
                self._ref_hitboxes.append((rect, ref))
            is_target = self._hover_ref is not None and self._hover_ref.full_name == ref.full_name
            is_possible = self._is_possible_ref_drop(ref)
            painter.setPen(QPen(color, 2 if is_target or is_possible else 1))
            alpha = 78 if is_target else (48 if is_possible else 28)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), alpha))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            offset += width + 6

    def _draw_drag(self, painter: QPainter) -> None:
        if self._drag_pos is None:
            return
        if self._drag_ref is not None:
            text = self._drag_ref.name
        elif self._drag_commit is not None:
            text = self._drag_commit.short_oid
        else:
            return
        painter.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
        width = painter.fontMetrics().horizontalAdvance(text) + 18
        rect = QRectF(self._drag_pos.x() + 12, self._drag_pos.y() + 12, width, 24)
        painter.setPen(QPen(QColor("#1f6feb"), 1))
        painter.setBrush(QColor(31, 111, 235, 36))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor("#1f6feb"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_trash(self, painter: QPainter) -> None:
        if self._drag_commit is None:
            self._trash_rect = QRectF()
            return
        size = 44.0
        margin = 18.0
        self._trash_rect = QRectF(self.width() - size - margin, margin, size, size)
        active = self._trash_rect.contains(self._drag_pos) if self._drag_pos is not None else False
        painter.setPen(QPen(QColor("#d1242f"), 2))
        painter.setBrush(QColor(209, 36, 47, 90 if active else 36))
        painter.drawRoundedRect(self._trash_rect, 8, 8)
        painter.setPen(QColor("#d1242f"))
        painter.setFont(QFont("Sans Serif", 12, QFont.Weight.Bold))
        painter.drawText(self._trash_rect, Qt.AlignmentFlag.AlignCenter, "X")

    def _is_possible_ref_drop(self, ref: Reference) -> bool:
        if self._external_stage_drag:
            return ref.kind == "local_branch"
        if self._drag_ref is None:
            if self._drag_commit is not None:
                return ref.kind == "local_branch"
            return False
        if self._drag_ref.kind == "remote_tracking":
            return ref.kind == "local_branch"
        if self._drag_ref.kind == "local_branch":
            return ref.kind == "local_branch" and ref.full_name != self._drag_ref.full_name
        return False

    def _is_possible_commit_drop(self, commit: Commit) -> bool:
        if self._drag_ref is not None and self._drag_ref.kind == "local_branch":
            return True
        if self._drag_commit is not None:
            return commit.oid != self._drag_commit.oid
        return False

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        ref = self._reference_at(event.position().toPoint())
        if ref is not None:
            self._drag_ref = ref
            self._drag_pos = event.position().toPoint()
            self.update()
            return
        commit = self._commit_at(event.position().toPoint())
        if commit is not None:
            self._drag_commit = commit
            self._drag_pos = event.position().toPoint()
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_ref is not None:
            self._drag_pos = event.position().toPoint()
            self._hover_ref = self._reference_at(event.position().toPoint())
            self.update()
            return
        if self._drag_commit is not None:
            self._drag_pos = event.position().toPoint()
            self._hover_commit = self._commit_at(event.position().toPoint())
            self.update()
            return
        if self._reference_at(event.position().toPoint()) is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_ref is None and self._drag_commit is None:
            super().mouseReleaseEvent(event)
            return
        source_ref = self._drag_ref
        source_commit = self._drag_commit
        self._drag_ref = None
        self._drag_commit = None
        self._drag_pos = None
        self._hover_ref = None
        self._hover_commit = None
        self.update()
        if source_ref is not None:
            target = self._reference_at(event.position().toPoint())
            if target is not None and target.name != source_ref.name:
                self.referenceDropped.emit(source_ref, target)
                return
            target_commit = self._commit_at(event.position().toPoint())
            if target_commit is not None:
                self.referenceDroppedOnCommit.emit(source_ref, target_commit)
                return
        if source_commit is not None:
            if self._trash_rect.isValid() and self._trash_rect.contains(event.position().toPoint()):
                self.commitDroppedToTrash.emit(source_commit)
                return
            target_ref = self._reference_at(event.position().toPoint())
            if target_ref is not None:
                self.commitDroppedOnReference.emit(source_commit, target_ref)
                return
            target_commit = self._commit_at(event.position().toPoint())
            if target_commit is not None and target_commit.oid != source_commit.oid:
                self.commitDroppedOnCommit.emit(source_commit, target_commit)
                return
        super().mouseReleaseEvent(event)

    def _commit_at(self, pos: QPoint) -> Optional[Commit]:
        for rect, commit in reversed(self._commit_hitboxes):
            if rect.contains(pos):
                return commit
        return None

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_ref = None
        self._hover_commit = None
        super().leaveEvent(event)

    def _set_external_stage_drag(self, active: bool, pos: Optional[QPoint] = None) -> None:
        self._external_stage_drag = active
        self._hover_ref = self._reference_at(pos) if active and pos is not None else None
        self.update()

    def _reference_at(self, pos: QPoint) -> Optional[Reference]:
        for rect, ref in reversed(self._ref_hitboxes):
            if rect.contains(pos):
                return ref
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-gitualizer-stage"):
            self._set_external_stage_drag(True, event.position().toPoint())
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-gitualizer-stage"):
            self._set_external_stage_drag(True, event.position().toPoint())
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_external_stage_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat("application/x-gitualizer-stage"):
            self._set_external_stage_drag(False)
            event.ignore()
            return
        target = self._reference_at(event.position().toPoint())
        self._set_external_stage_drag(False)
        if target is None or target.kind != "local_branch":
            event.ignore()
            return
        self.stageDroppedOnBranch.emit(target)
        event.acceptProposedAction()
