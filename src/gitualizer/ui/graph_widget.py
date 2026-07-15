from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
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
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._state: Optional[RepositoryState] = None
        self._preview_plan: Optional[CommandPlan] = None
        self._nodes: dict[str, CommitNode] = {}
        self._row_spacing = 78
        self._lane_spacing = 104
        self.setMinimumSize(520, 420)

    def set_state(self, state: Optional[RepositoryState]) -> None:
        self._state = state
        self._nodes = self._layout_nodes(state)
        row_count = max(len(self._nodes), 8)
        lane_count = max((int(node.x) for node in self._nodes.values()), default=1)
        self.setMinimumHeight(110 + row_count * self._row_spacing)
        self.setMinimumWidth(max(560, lane_count + 480))
        self.update()

    def set_preview_plan(self, plan: Optional[CommandPlan]) -> None:
        self._preview_plan = plan
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
                y=176 + index * self._row_spacing,
            )
        return nodes

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        self._draw_grid(painter)
        if self._state is None:
            self._draw_empty(painter, "Open a Git repository to inspect it.")
            return
        self._draw_git_flow(painter)
        if not self._state.commits:
            self._draw_empty(painter, "Repository has no commits yet.")
            return
        self._draw_preview(painter)
        self._draw_edges(painter)
        self._draw_commits(painter)

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
        x = 34.0
        y = 28.0
        width = 190.0
        height = 74.0
        for index, (title, subtitle, color) in enumerate(flow):
            rect = QRectF(x, y, width, height)
            active = self._preview_targets_flow(title)
            painter.setPen(QPen(color if active else QColor("#d0d7de"), 2))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 22 if active else 10))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QColor("#1f2933"))
            painter.setFont(QFont("Sans Serif", 10, QFont.Weight.Bold))
            painter.drawText(rect.adjusted(14, 12, -14, -36), Qt.AlignmentFlag.AlignLeft, title)
            painter.setFont(QFont("Sans Serif", 9))
            painter.setPen(QColor("#52616f"))
            painter.drawText(rect.adjusted(14, 38, -14, -10), Qt.AlignmentFlag.AlignLeft, subtitle)
            if index < len(flow) - 1:
                painter.setPen(QPen(QColor("#9aa7b4"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                start = QPointF(x + width + 8, y + height / 2)
                end = QPointF(x + width + 56, y + height / 2)
                painter.drawLine(start, end)
                painter.drawLine(end, QPointF(end.x() - 8, end.y() - 6))
                painter.drawLine(end, QPointF(end.x() - 8, end.y() + 6))
            x += width + 64

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
            painter.drawLine(QPointF(x, 148), QPointF(x, self.height()))
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
            is_head = self._state.head.oid == commit.oid
            fill = QColor("#1f6feb") if is_head else QColor("#26313d")
            if is_head:
                painter.setPen(QPen(QColor("#9ed0ff"), 6))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(node.x, node.y), 13, 13)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.setBrush(fill)
            painter.drawEllipse(QPointF(node.x, node.y), 9, 9)

            painter.setPen(QColor("#20242a"))
            painter.setFont(QFont("Sans Serif", 9))
            label = f"{commit.short_oid}  {commit.subject}"
            painter.drawText(QRectF(node.x + 22, node.y - 14, max(260, self.width() - node.x - 44), 22), label)

            refs = refs_by_target.get(commit.oid, [])
            self._draw_refs(painter, refs, node.x + 22, node.y + 12)

    def _draw_refs(self, painter: QPainter, refs: list[Reference], x: float, y: float) -> None:
        offset = 0.0
        for ref in sorted(refs, key=lambda item: item.name):
            text = ref.name
            color = {
                "head": QColor("#d1242f"),
                "local_branch": QColor("#1a7f37"),
                "remote_tracking": QColor("#8250df"),
                "tag": QColor("#bf8700"),
            }.get(ref.kind, QColor("#57606a"))
            painter.setFont(QFont("Sans Serif", 8))
            width = painter.fontMetrics().horizontalAdvance(text) + 14
            rect = QRectF(x + offset, y, width, 22)
            painter.setPen(QPen(color, 1))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 28))
            painter.drawRoundedRect(rect, 6, 6)
            painter.setPen(color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
            offset += width + 6
