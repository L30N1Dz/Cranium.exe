from PySide6.QtWidgets import QApplication
import sys

def apply_dark_theme(app: QApplication) -> None:
    """Apply a dark theme with custom colors to the application using stylesheets.

    Styles all widgets (QGroupBox, QPushButton, QComboBox, QSlider, QSpinBox,
    QLabel, QTextEdit, QCheckBox) with consistent colors and rounded buttons.
    """
    try:
        # Define color palette
        primary_bg = "rgb(43, 43, 43)"  # Main window background
        input_bg = "rgb(30, 30, 30)"    # Input widget background
        gold = "rgb(161, 163, 25)"      # Custom gold color
        pink = "pink"                    # QGroupBox title color
        green = "rgb(0, 255, 0)"        # Text color (from QPalette.WindowText)
        black = "rgb(0, 0, 0)"          # Text color for inputs/buttons
        bright_red = "rgb(255, 64, 64)" # Bright text (e.g., errors)
        link_blue = "rgb(90, 170, 220)" # Links
        highlight = "rgb(60, 120, 180)" # Selection/highlight
        text_highlight = "rgb(245, 245, 245)"  # Highlighted text

        # Apply stylesheet
        app.setStyleSheet(f"""
            /* General window and widget background */
            QWidget {{
                background-color: {primary_bg};
                color: {green};
            }}

            /* QGroupBox title */
            QGroupBox {{
                color: {pink};
            }}

            /* QPushButton with rounded corners */
            QPushButton {{
                background-color: {gold};
                color: {black};
                border-radius: 10px;
                border: 1px solid {gold};
                padding: 5px;
            }}
            QPushButton:hover {{
                background-color: rgb(181, 183, 45); /* Lighter gold on hover */
            }}
            QPushButton:pressed {{
                background-color: rgb(141, 143, 15); /* Darker gold when pressed */
            }}

            /* QComboBox */
            QComboBox {{
                background-color: {gold};
                color: {black};
                border: 1px solid {gold};
            }}
            QComboBox QAbstractItemView {{
                background-color: {gold};
                color: {black};
                selection-background-color: {highlight};
                selection-color: {text_highlight};
            }}

            /* QSlider */
            QSlider::groove:horizontal, QSlider::groove:vertical {{
                background-color: {gold};
            }}
            QSlider::handle:horizontal, QSlider::handle:vertical {{
                background-color: {highlight};
                border: 1px solid {highlight};
                width: 10px;
                margin: -2px 0;
                border-radius: 5px;
            }}
            QSlider::handle:horizontal:hover, QSlider::handle:vertical:hover {{
                background-color: rgb(80, 140, 200); /* Lighter highlight on hover */
            }}

            /* QSpinBox */
            QSpinBox {{
                background-color: {gold};
                color: {black};
                border: 1px solid {gold};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {gold};
                border: none;
            }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
                background-color: rgb(181, 183, 45); /* Lighter gold on hover */
            }}

            /* QLabel */
            QLabel {{
                color: {green};
            }}

            /* QTextEdit */
            QTextEdit {{
                background-color: {input_bg};
                color: {green};
                selection-background-color: {highlight};
                selection-color: {text_highlight};
            }}

            /* QCheckBox */
            QCheckBox {{
                color: {green};
            }}
            QCheckBox::indicator {{
                background-color: {input_bg};
                border: 1px solid {gold};
            }}
            QCheckBox::indicator:checked {{
                background-color: {gold};
            }}

            /* Tooltips */
            QToolTip {{
                background-color: {input_bg};
                color: {green};
                border: 1px solid {gold};
            }}
        """)

        # Debug information
        print(f"Applied dark theme. Qt style: {app.style().objectName()}")
        print(f"Application stylesheet: {app.styleSheet()}")
    except Exception as e:
        print(f"Error applying dark theme: {e}")
        sys.stderr.write(f"Theme application failed: {e}\n")