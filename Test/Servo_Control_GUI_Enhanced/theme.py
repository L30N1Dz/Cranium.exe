"""
Modern dystopian / neon theme for PySide6/PyQt widgets.
Drop-in replacement for your previous theme.py.

Usage:
    from theme import apply_dark_theme
    apply_dark_theme(app)  # where `app` is your QApplication

Only the header palette below needs tweaking to shift the vibe.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication


def apply_dark_theme(app: QApplication) -> None:
    """Apply a sleek sci‑fi/dystopian dark theme via Qt stylesheets.

    This keeps layout/metrics intact while fixing contrast on buttons and
    introducing subtle neon accents, focus rings, and calm selections.
    """
    # ===================== THEME HEADER (edit me) ==========================
    base_bg        = "#0B0F14"
    input_bg       = "#121823"
    panel_bg       = "#0E141E"

    green_accent   = "#34F5C5"
    light_green    = "#6CFFD9"
    dark_green     = "#17C29E"

    bright_purple  = "#8B5CF6"

    highlight_bg   = "#1F2A44"
    highlight_fg   = "#E7F9FF"

    text_primary   = "#D6E6EC"
    text_muted     = "#9BB1BA"

    dark_gray      = "#E6F3F7"

    radius_sm      = 6
    radius_md      = 8
    outline        = green_accent

    font_stack     = "'Orbitron', 'Segoe UI', 'Roboto', Arial, sans-serif"

    # ======================= STYLESHEET START ==============================
    qss = f"""
    QWidget {{
        background-color: {base_bg};
        color: {text_primary};
        font-family: {font_stack};
        selection-background-color: {highlight_bg};
        selection-color: {highlight_fg};
    }}

    QGroupBox {{
        border: 1px solid {outline};
        border-radius: {radius_md}px;
        /* symmetric vertical spacing around group boxes */
        margin: 12px 0;
        background-color: {panel_bg};
        /* reduce internal padding so the contents sit closer to the border */
        padding: 6px 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        /* adjust the title position slightly upward */
        top: -4px;
        padding: 0 8px;
        color: {bright_purple};
        background-color: {base_bg};
    }}

    QLabel {{ color: {text_primary}; }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
        background-color: rgba(18, 24, 35, 0.85);
        border: 1px solid {outline};
        border-radius: {radius_sm}px;
        padding: 4px 6px;
        color: {text_primary};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {bright_purple};
        /* Qt style sheets do not support box-shadow; rely solely on border colour */
    }}

    QComboBox QAbstractItemView {{
        background: {input_bg};
        color: {text_primary};
        border: 1px solid {outline};
        selection-background-color: {highlight_bg};
        selection-color: {highlight_fg};
    }}

    QPushButton {{
        color: {dark_gray};
        border: 1px solid {outline};
        border-radius: {radius_sm}px;
        background-color: rgba(52, 245, 197, 0.06);
        padding: 4px 10px;
    }}
    QPushButton:hover {{
        border-color: {light_green};
        background-color: rgba(108, 255, 217, 0.10);
    }}
    QPushButton:pressed {{
        border-color: {dark_green};
        background-color: rgba(23, 194, 158, 0.14);
    }}
    QPushButton:focus {{
        border: 1px solid {bright_purple};
        /* box-shadow is not supported in Qt style sheets */
    }}
    QPushButton:disabled {{
        color: rgba(231, 249, 255, 0.35);
        border-color: rgba(52, 245, 197, 0.25);
        background-color: rgba(255,255,255,0.02);
    }}

    QCheckBox, QRadioButton {{
        color: {text_primary};
        spacing: 6px;
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 15px; height: 15px;
        border: 1px solid {outline};
        background: {base_bg};
        border-radius: 3px;
    }}
    QCheckBox::indicator:checked {{
        background: {green_accent};
        border-color: {green_accent};
    }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QRadioButton::indicator:checked {{
        background: {green_accent};
        border-color: {green_accent};
    }}

    QSlider::groove:horizontal {{
        height: 6px;
        background: {panel_bg};
        border: 1px solid {outline};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        width: 16px;
        margin: -6px 0;
        border: 1px solid {bright_purple};
        background: {green_accent};
        border-radius: 8px;
    }}

    QProgressBar {{
        background: {panel_bg};
        border: 1px solid {outline};
        border-radius: {radius_sm}px;
        text-align: center;
        color: {text_muted};
    }}
    QProgressBar::chunk {{ background: {green_accent}; }}

    QHeaderView::section {{
        background: {panel_bg};
        color: {text_primary};
        border: 1px solid {outline};
        padding: 4px 6px;
    }}
    QTableView {{
        gridline-color: {outline};
        selection-background-color: {highlight_bg};
        selection-color: {highlight_fg};
        background: {base_bg};
    }}

    QScrollBar:vertical {{
        width: 12px;
        background: {base_bg};
        margin: 2px; border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {outline};
        min-height: 24px; border-radius: 6px;
    }}
    QScrollBar:horizontal {{ height: 12px; background: {base_bg}; margin: 2px; border: none; }}
    QScrollBar::handle:horizontal {{ background: {outline}; min-width: 24px; border-radius: 6px; }}

    QFrame[frameShape="4"], QFrame[frameShape="5"] {{
        background: {outline};
    }}
    """

    app.setStyleSheet(qss)


__all__ = ["apply_dark_theme"]
