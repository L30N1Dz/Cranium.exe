import sys

import serial
from serial.tools import list_ports

from PyQt5 import QtWidgets, QtCore, QtGui

BAUD_RATE = 9600


def available_ports():
    """Return a list of available serial ports."""
    return [p.device for p in list_ports.comports()]


class JoystickView(QtWidgets.QWidget):
    """Widget that displays joystick position as a plus-shaped cursor."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.setFixedSize(220, 220)
        self.position = 512, 512  # default center (0-1023 range)

    def set_position(self, x: int, y: int):
        self.position = x, y
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt method name
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        # background
        painter.fillRect(self.rect(), QtGui.QColor("#111111"))

        # border
        painter.setPen(QtGui.QPen(QtGui.QColor("#444"), 2))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        # center cross
        painter.setPen(QtGui.QPen(QtGui.QColor("#333"), 1))
        painter.drawLine(self.width() // 2, 0, self.width() // 2, self.height())
        painter.drawLine(0, self.height() // 2, self.width(), self.height() // 2)

        yogldd-codex/create-joystick_test_app-python-script
        # cursor position (cast to int for QPainter)
        x = int(self.position[0] / 1023 * self.width())
        y = int(self.position[1] / 1023 * self.height())

        # cursor position
        x = self.position[0] / 1023 * self.width()
        y = self.position[1] / 1023 * self.height()
        main
        painter.setPen(QtGui.QPen(QtGui.QColor("#0f0"), 2))
        size = 10
        painter.drawLine(x - size, y, x + size, y)
        painter.drawLine(x, y - size, x, y + size)

        # title
        painter.setPen(QtGui.QPen(QtGui.QColor("#0f0")))
        painter.drawText(5, 15, self.name)


class ButtonIndicator(QtWidgets.QFrame):
    """Indicator that lights up when a joystick button is pressed."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.setFixedSize(80, 40)
        self._pressed = False
        self._update_style()

        label = QtWidgets.QLabel(name, self)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #0f0; font-size: 12px;")

    def set_pressed(self, pressed: bool):
        self._pressed = pressed
        self._update_style()

    def _update_style(self):
        color = "#ff0000" if self._pressed else "#330000"
        self.setStyleSheet(
            f"background-color: {color}; border: 2px solid #555; border-radius: 5px;"
        )


class SerialReader(QtCore.QThread):
    """Thread to read serial data."""

    data_received = QtCore.pyqtSignal(str)
    joystick_update = QtCore.pyqtSignal(int, int, bool, int, int, bool)

    def __init__(self, port: str):
        super().__init__()
        self.port = port
        self._running = True

    def run(self):  # noqa: D401,N802
        ser = serial.Serial(self.port, BAUD_RATE, timeout=1)
        ser.write(b"START\n")
        while self._running:
            try:
                line = ser.readline().decode("utf-8").strip()
            except serial.SerialException:
                break
            if not line:
                continue
            self.data_received.emit(line)
            parts = line.split(",")
            if len(parts) == 6:
                try:
                    j1x, j1y, j1b, j2x, j2y, j2b = parts
                    self.joystick_update.emit(
                        int(j1x),
                        int(j1y),
                        j1b.strip() == "1",
                        int(j2x),
                        int(j2y),
                        j2b.strip() == "1",
                    )
                except ValueError:
                    continue
        ser.close()

    def stop(self):
        self._running = False


class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Joystick Test App")
        self.setStyleSheet(
            """
            QWidget { background-color: #1e1e1e; color: #0f0; font-family: Consolas; }
            QPushButton { background-color: #333; border: 1px solid #555; padding: 4px; }
            QPushButton:pressed { background-color: #555; }
            QTextEdit { background-color: #000; color: #0f0; }
            QComboBox { background-color: #333; color: #0f0; }
            """
        )

        # COM port selection
        self.port_box = QtWidgets.QComboBox()
        self.refresh_ports()
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_ports)

        port_layout = QtWidgets.QHBoxLayout()
        port_layout.addWidget(QtWidgets.QLabel("Port:"))
        port_layout.addWidget(self.port_box)
        port_layout.addWidget(refresh_btn)

        # Joystick views
        self.j1_view = JoystickView("Joystick 1")
        self.j2_view = JoystickView("Joystick 2")
        self.j1_btn = ButtonIndicator("Button 1")
        self.j2_btn = ButtonIndicator("Button 2")

        j_layout = QtWidgets.QHBoxLayout()
        for view, btn in [(self.j1_view, self.j1_btn), (self.j2_view, self.j2_btn)]:
            vbox = QtWidgets.QVBoxLayout()
            vbox.addWidget(view)
            vbox.addWidget(btn, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
            j_layout.addLayout(vbox)

        # console output
        self.console = QtWidgets.QTextEdit(readOnly=True)

        # start/stop buttons
        self.start_btn = QtWidgets.QPushButton("Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(port_layout)
        layout.addLayout(j_layout)
        layout.addWidget(self.console)
        layout.addLayout(btn_layout)

        self.serial_thread = None

    def refresh_ports(self):
        self.port_box.clear()
        self.port_box.addItems(available_ports())

    def start(self):
        port = self.port_box.currentText()
        if not port:
            QtWidgets.QMessageBox.warning(self, "No Port", "Please select a COM port.")
            return
        self.serial_thread = SerialReader(port)
        self.serial_thread.data_received.connect(self.log)
        self.serial_thread.joystick_update.connect(self.update_joysticks)
        self.serial_thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def stop(self):
        if self.serial_thread:
            self.serial_thread.stop()
            self.serial_thread.wait(1000)
            self.serial_thread = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def log(self, text: str):
        self.console.append(text)

    def update_joysticks(self, j1x, j1y, j1b, j2x, j2y, j2b):
        self.j1_view.set_position(j1x, j1y)
        self.j2_view.set_position(j2x, j2y)
        self.j1_btn.set_pressed(j1b)
        self.j2_btn.set_pressed(j2b)


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
