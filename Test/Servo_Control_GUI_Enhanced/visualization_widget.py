"""
visualization_widget.py
========================

Contains a simple widget for visualising where an animatronic eye is currently
pointing.  Each instance of :class:`EyeVisualizer` paints a square view with
crosshairs and a coloured dot whose position corresponds to the horizontal
and vertical servo angles (0–180°).  The widget is intended to provide
immediate feedback when adjusting limits or using a joystick to control the
eyes.

Usage::

    vis = EyeVisualizer()
    vis.set_angles(x_angle=90, y_angle=90)

The widget will repaint itself whenever the angles change.  Values outside
the 0–180° range are clamped.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget, QSizePolicy


class EyeVisualizer(QWidget):
    """Widget that draws a crosshair and a dot representing eye orientation."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Default angles (0–180)
        self._x_angle = 90
        self._y_angle = 90
        # Use expanding policy to grow if allowed
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> 'QSize':  # type: ignore[name-match]
        from PySide6.QtCore import QSize
        return QSize(150, 150)

    def set_angles(self, *, x_angle: int, y_angle: int) -> None:
        """Update the internal angles and schedule a repaint."""
        x_angle = max(0, min(180, int(x_angle)))
        y_angle = max(0, min(180, int(y_angle)))
        if (x_angle, y_angle) != (self._x_angle, self._y_angle):
            self._x_angle, self._y_angle = x_angle, y_angle
            self.update()

    def _angle_to_offset(self, angle: int, length: float) -> float:
        """Map a 0–180° angle into a coordinate offset between −0.5 and 0.5."""
        # Convert angle 0..180 to range -0.5..+0.5
        return (angle / 180.0) - 0.5

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        size = min(rect.width(), rect.height())
        # Define a square within the widget for drawing crosshair and dot
        s = size * 0.9  # leave some margin
        x0 = rect.center().x() - s / 2
        y0 = rect.center().y() - s / 2
        square = QRectF(x0, y0, s, s)

        # Draw background (transparent or with slight tone)
        bg = QColor(40, 40, 40) if self.palette().color(self.backgroundRole()).value() < 128 else QColor(230, 230, 230)
        painter.fillRect(square, bg)

        # Draw crosshairs
        pen = QPen(QColor(90, 90, 90), 1)
        painter.setPen(pen)
        # Vertical line
        painter.drawLine(square.center().x(), square.top(), square.center().x(), square.bottom())
        # Horizontal line
        painter.drawLine(square.left(), square.center().y(), square.right(), square.center().y())

        # Draw the dot representing the eye direction
        dot_pen = QPen(Qt.NoPen)
        dot_brush = QBrush(QColor(200, 80, 80))
        painter.setPen(dot_pen)
        painter.setBrush(dot_brush)
        # Compute offset in both axes
        dx = self._angle_to_offset(self._x_angle, s) * s
        dy = -self._angle_to_offset(self._y_angle, s) * s  # invert Y
        dot_radius = s * 0.05
        cx = square.center().x() + dx
        cy = square.center().y() + dy
        painter.drawEllipse(QRectF(cx - dot_radius, cy - dot_radius, dot_radius * 2, dot_radius * 2))