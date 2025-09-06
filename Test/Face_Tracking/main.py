"""Graphical user interface for real-time face tracking and vision LLM chat.

This module defines the main PySide6 application. It integrates webcam
capture, face detection, a multimodal LLM via Ollama, and optional
serial communication to drive servos based on face tracking. Users
can chat with the model, attach the live frame to their prompts, and
adjust settings such as model name, polling intervals, and serial
port configuration from within the interface.
"""

from __future__ import annotations

import sys
import os
from typing import Optional, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QTextEdit, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QCheckBox, QSpinBox,
    QComboBox, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QPixmap, QImage, QTextCursor

from webcam import VideoWorker
from llm_client import OllamaClient
from uart import SerialManager
from utils import map_range_clamped
from tts import TextToSpeech
from stt import STTWorker, list_input_devices


class MainWindow(QMainWindow):
    """Primary window containing the video feed, chat, and controls."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Face Tracking & Vision LLM")
        self.resize(1280, 800)

        # State
        self.last_frame_qimage: Optional[QImage] = None
        self.last_face_center_norm: Tuple[float, float] = (0.5, 0.5)
        self.only_on_user_prompt: bool = True
        self.auto_prompt: str = "Describe what you see in this frame."  # default auto prompt

        # Video display
        self.video_label = QLabel("Waiting for camera...")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(640, 480)
        self.video_label.setStyleSheet(
            "background: #0b0f14; color: #9aa1a9; border: 1px solid #263241;"
        )

        # Coordinates display
        self.coords_label = QLabel("X: -, Y: - (deg)")
        self.coords_label.setStyleSheet("color: #cdd6f4; padding: 4px;")

        # Chat widgets
        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask the model... (the frame will be attached)")
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.on_send_prompt)
        self.chat_input.returnPressed.connect(self.on_send_prompt)

        chat_layout = QVBoxLayout()
        chat_layout.addWidget(self.chat_log)
        input_row = QHBoxLayout()
        input_row.addWidget(self.chat_input, 1)
        input_row.addWidget(self.send_btn)
        chat_layout.addLayout(input_row)
        chat_group = QGroupBox("Chat with Vision LLM")
        chat_group.setLayout(chat_layout)

        # Controls / settings
        # Model selection
        self.model_edit = QLineEdit("llava")
        self.model_edit.setToolTip("Name of the vision model hosted in Ollama, e.g. 'llava', 'llava:13b', 'qwen2.5vl:7b'")
        # Detector selection
        self.detector_combo = QComboBox()
        self.detector_combo.addItems(["FaceDetection", "FaceMesh"])
        self.detector_combo.setToolTip("Face detection algorithm: FaceDetection (bounding box) or FaceMesh (landmarks)")
        self.detector_combo.currentIndexChanged.connect(self.on_detector_changed)
        # Only prompt on user
        self.only_on_prompt_cb = QCheckBox("Only prompt on user message")
        self.only_on_prompt_cb.setChecked(True)
        self.only_on_prompt_cb.stateChanged.connect(self.on_only_on_prompt_changed)
        # Auto analysis enable
        self.auto_enable_cb = QCheckBox("Automatic frame analysis")
        self.auto_enable_cb.setChecked(False)
        self.auto_enable_cb.stateChanged.connect(self.on_auto_toggle)
        # Interval spin
        self.auto_interval_ms = QSpinBox()
        self.auto_interval_ms.setRange(100, 10000)
        self.auto_interval_ms.setValue(1500)
        self.auto_interval_ms.setSuffix(" ms")
        self.auto_interval_ms.valueChanged.connect(self.on_auto_interval_change)
        # UART controls
        self.uart_enable_cb = QCheckBox("Enable UART output")
        self.uart_enable_cb.setChecked(False)
        # Port selection
        self.port_box = QComboBox()
        self.refresh_ports()
        # Baud selection
        self.baud_box = QComboBox()
        self.baud_box.addItems(["115200", "57600", "38400", "19200", "9600"])
        # Connect/disconnect button
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.toggled.connect(self.on_connect_serial)
        # Inversion toggles
        self.invert_x_cb = QCheckBox("Invert X")
        self.invert_y_cb = QCheckBox("Invert Y")

        # ----- Text-to-speech (TTS) and speech-to-text (STT) controls -----
        # TTS
        self.tts_enable_cb = QCheckBox("Enable TTS (speak responses)")
        self.tts_enable_cb.setChecked(False)
        self.voice_box = QComboBox()
        # Initialize TTS engine and populate voices
        self.tts = TextToSpeech()
        try:
            voices = self.tts.get_voices()
            for vid, vname in voices:
                self.voice_box.addItem(vname, vid)
            # Set default to first voice
            if voices:
                self.voice_box.setCurrentIndex(0)
                self.tts.set_voice(voices[0][0])
        except Exception:
            # If voice listing fails, disable TTS controls
            self.tts_enable_cb.setEnabled(False)
            self.voice_box.setEnabled(False)
        # Connect voice selection change
        self.voice_box.currentIndexChanged.connect(self.on_voice_changed)
        # STT
        self.stt_enable_cb = QCheckBox("Enable STT (hotword)")
        self.stt_enable_cb.setChecked(False)
        self.hotword_edit = QLineEdit("cranium")
        self.hotword_edit.setToolTip("Keyword to trigger speech-to-text transcription")
        self.mic_box = QComboBox()
        # Populate microphones
        self.refresh_mics()
        # Connect STT toggle
        self.stt_enable_cb.stateChanged.connect(self.on_stt_toggle)
        self.stt_worker: Optional[STTWorker] = None
        # Storage for accumulating assistant response for TTS
        self.current_response_text: str = ""
        # Flag to indicate whether the current assistant reply has started streaming
        self._stream_started: bool = False

        # Layout for settings
        ctrl_layout = QGridLayout()
        row = 0
        # Model and detector selection
        ctrl_layout.addWidget(QLabel("Model:"), row, 0)
        ctrl_layout.addWidget(self.model_edit, row, 1, 1, 2)
        row += 1
        ctrl_layout.addWidget(QLabel("Detector:"), row, 0)
        ctrl_layout.addWidget(self.detector_combo, row, 1, 1, 2)
        row += 1
        # Prompting options
        ctrl_layout.addWidget(self.only_on_prompt_cb, row, 0, 1, 3)
        row += 1
        ctrl_layout.addWidget(self.auto_enable_cb, row, 0)
        ctrl_layout.addWidget(QLabel("Interval:"), row, 1)
        ctrl_layout.addWidget(self.auto_interval_ms, row, 2)
        row += 1
        # Serial settings
        ctrl_layout.addWidget(QLabel("Port:"), row, 0)
        ctrl_layout.addWidget(self.port_box, row, 1)
        ctrl_layout.addWidget(self.connect_btn, row, 2)
        row += 1
        ctrl_layout.addWidget(QLabel("Baud:"), row, 0)
        ctrl_layout.addWidget(self.baud_box, row, 1)
        ctrl_layout.addWidget(self.uart_enable_cb, row, 2)
        row += 1
        ctrl_layout.addWidget(self.invert_x_cb, row, 0)
        ctrl_layout.addWidget(self.invert_y_cb, row, 1)
        row += 1
        ctrl_group = QGroupBox("Settings")
        ctrl_group.setLayout(ctrl_layout)

        # Assemble layout
        left_col = QVBoxLayout()
        left_col.addWidget(self.video_label, 1)
        left_col.addWidget(self.coords_label)

        right_col = QVBoxLayout()
        right_col.addWidget(chat_group, 3)
        right_col.addWidget(ctrl_group, 1)
        # Audio & Speech group
        speech_layout = QGridLayout()
        row_s = 0
        # TTS controls
        speech_layout.addWidget(self.tts_enable_cb, row_s, 0, 1, 2)
        row_s += 1
        speech_layout.addWidget(QLabel("Voice:"), row_s, 0)
        speech_layout.addWidget(self.voice_box, row_s, 1)
        row_s += 1
        # STT controls
        speech_layout.addWidget(self.stt_enable_cb, row_s, 0, 1, 2)
        row_s += 1
        speech_layout.addWidget(QLabel("Microphone:"), row_s, 0)
        speech_layout.addWidget(self.mic_box, row_s, 1)
        row_s += 1
        speech_layout.addWidget(QLabel("Hotword:"), row_s, 0)
        speech_layout.addWidget(self.hotword_edit, row_s, 1)
        row_s += 1
        speech_group = QGroupBox("Audio & Speech")
        speech_group.setLayout(speech_layout)
        right_col.addWidget(speech_group, 1)

        main_layout = QHBoxLayout()
        main_layout.addLayout(left_col, 3)
        main_layout.addLayout(right_col, 2)

        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)

        # Apply simple dark theme to unify look
        self.setStyleSheet(
            """
            QMainWindow {
                background: #0b0f14;
            }
            QGroupBox {
                color: #9dc1ff;
                font-weight: 600;
                border: 1px solid #263241;
                border-radius: 8px;
                margin-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                top: -7px;
                background: #0b0f14;
                padding: 0 6px;
            }
            QLabel {
                color: #cdd6f4;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox {
                background: #0f141b;
                color: #d1e0ff;
                border: 1px solid #273244;
                border-radius: 6px;
                padding: 6px;
            }
            QPushButton {
                background: #1a2330;
                color: #d7e3ff;
                border: 1px solid #33465e;
                border-radius: 8px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                border-color: #5aa2ff;
            }
            QPushButton:checked {
                background: #213147;
                color: #ffffff;
            }
            QCheckBox {
                color: #c7d3e7;
            }
            """
        )

        # Initialize subsystems
        # Video worker (face detection)
        self.video = VideoWorker(camera_index=0)
        self.video.frame_ready.connect(self.on_frame_ready)
        self.video.face_center_available.connect(self.on_face_center)
        self.video.start()

        # LLM client; supply callback functions
        self.ollama = OllamaClient(
            model=self.model_edit.text().strip(),
            on_stream_delta=self.on_llm_stream,
            on_response_done=self.on_llm_done,
            on_error=self.on_llm_error,
        )

        # Auto analysis timer
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(self.auto_interval_ms.value())
        self.auto_timer.timeout.connect(self.on_auto_tick)

        # Serial manager
        self.serial = SerialManager()

    # ---------- Slots and callbacks ----------
    @Slot()
    def on_only_on_prompt_changed(self) -> None:
        self.only_on_user_prompt = self.only_on_prompt_cb.isChecked()

    @Slot()
    def on_auto_toggle(self) -> None:
        if self.auto_enable_cb.isChecked():
            self.auto_timer.start()
        else:
            self.auto_timer.stop()

    @Slot(int)
    def on_auto_interval_change(self, val_ms: int) -> None:
        self.auto_timer.setInterval(val_ms)

    @Slot()
    def on_auto_tick(self) -> None:
        # Auto analysis: send frame with generic prompt
        if self.only_on_user_prompt:
            return
        if self.last_frame_qimage is None:
            return
        if self.ollama.is_busy:
            return
        # Compose generic auto message
        self.append_chat("system", f"[auto] Analyzing frame...")
        # Reset the assistant response accumulator and streaming flag for new response
        self.current_response_text = ""
        self._stream_started = False
        self.ollama.set_model(self.model_edit.text().strip())
        self.ollama.send_frame_with_prompt(self.last_frame_qimage, self.auto_prompt)

    @Slot()
    def on_send_prompt(self) -> None:
        text = self.chat_input.text().strip()
        if not text:
            return
        if self.last_frame_qimage is None:
            QMessageBox.warning(self, "No frame", "No webcam frame yet. Please wait a moment.")
            return
        if self.ollama.is_busy:
            QMessageBox.information(self, "Model busy", "The model is still processing the previous request. Please wait.")
            return
        self.append_chat("user", text)
        # Update model if changed
        self.ollama.set_model(self.model_edit.text().strip())
        # Reset assistant response accumulator and streaming flag
        self.current_response_text = ""
        self._stream_started = False
        # Send message with frame
        self.ollama.send_user_message_with_frame(text, self.last_frame_qimage)
        self.chat_input.clear()

    def append_chat(self, role: str, text: str) -> None:
        """Append a message to the chat log with formatting based on role."""
        if role == "user":
            self.chat_log.append(f"<b><span style='color:#98c1ff'>You:</span></b> {text}")
        elif role == "assistant":
            self.chat_log.append(f"<b><span style='color:#a6e3a1'>Assistant:</span></b> {text}")
        elif role == "system":
            self.chat_log.append(f"<i><span style='color:#7f8ea3'>{text}</span></i>")
        else:
            self.chat_log.append(text)
        # Scroll to bottom
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

    @Slot(QImage, dict)
    def on_frame_ready(self, qimg: QImage, meta: dict) -> None:
        self.last_frame_qimage = qimg
        # Show the frame scaled to the label size
        pix = QPixmap.fromImage(qimg)
        self.video_label.setPixmap(pix.scaled(
            self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        ))

    @Slot(tuple, tuple)
    def on_face_center(self, center_norm_xy: Tuple[float, float], bbox_rel_xywh: Tuple[float, float, float, float]) -> None:
        # Save normalized coordinates for potential use
        cx_norm, cy_norm = center_norm_xy
        self.last_face_center_norm = center_norm_xy
        # Map normalized coordinates to servo degrees with optional inversion
        inv_x = self.invert_x_cb.isChecked()
        inv_y = self.invert_y_cb.isChecked()
        x_deg = int(map_range_clamped(cx_norm, 0.0, 1.0, 180 if inv_x else 0, 0 if inv_x else 180))
        y_deg = int(map_range_clamped(cy_norm, 0.0, 1.0, 180 if inv_y else 0, 0 if inv_y else 180))
        self.coords_label.setText(f"X: {x_deg}, Y: {y_deg} (deg)")
        # Send to UART if enabled
        if self.uart_enable_cb.isChecked() and self.serial.is_open:
            self.serial.send_set_angles(x_deg, y_deg)

    @Slot(bool)
    def on_connect_serial(self, checked: bool) -> None:
        if checked:
            port = self.port_box.currentText().strip()
            try:
                baud = int(self.baud_box.currentText())
            except ValueError:
                baud = 115200
            try:
                self.serial.open(port, baud)
                self.connect_btn.setText("Disconnect")
                self.uart_enable_cb.setChecked(True)
            except Exception as exc:
                self.connect_btn.setChecked(False)
                QMessageBox.critical(self, "Serial Error", str(exc))
        else:
            self.serial.close()
            self.connect_btn.setText("Connect")
            self.uart_enable_cb.setChecked(False)

    def refresh_ports(self) -> None:
        """Refresh the list of available serial ports."""
        try:
            from serial.tools import list_ports
            ports = [p.device for p in list_ports.comports()]
        except Exception:
            ports = []
        # Provide some sensible defaults if no ports discovered
        if not ports:
            ports = ["COM3", "COM4", "/dev/ttyUSB0", "/dev/ttyACM0"]
        self.port_box.clear()
        self.port_box.addItems(ports)

    def refresh_mics(self) -> None:
        """Populate microphone device list for STT."""
        try:
            devices = list_input_devices()
        except Exception:
            devices = []
        if not devices:
            # Provide a generic default if enumeration fails
            devices = [(-1, "Default")]  # -1 lets sounddevice choose default
        self.mic_box.clear()
        for idx, name in devices:
            self.mic_box.addItem(name, idx)

    @Slot()
    def on_detector_changed(self) -> None:
        """Change face detector when user selects a different option."""
        text = self.detector_combo.currentText()
        if text.lower() == "facemesh":
            dtype = "face_mesh"
        else:
            dtype = "face_detection"
        try:
            self.video.set_detector_type(dtype)
        except Exception as exc:
            self.append_chat("system", f"[error] Failed to set detector: {exc}")

    @Slot()
    def on_voice_changed(self) -> None:
        """Handle change of selected TTS voice."""
        voice_id = self.voice_box.currentData()
        if voice_id:
            try:
                self.tts.set_voice(voice_id)
            except Exception:
                pass

    @Slot()
    def on_stt_toggle(self) -> None:
        """Enable or disable the speech-to-text worker based on checkbox."""
        if self.stt_enable_cb.isChecked():
            self.start_stt()
        else:
            self.stop_stt()

    def start_stt(self) -> None:
        """Create and start the STT worker."""
        if self.stt_worker is not None:
            return
        # Determine device index
        device_idx = self.mic_box.currentData()
        try:
            device_index = int(device_idx) if device_idx is not None else None
        except Exception:
            device_index = None
        hotword = self.hotword_edit.text().strip().lower() or "cranium"
        try:
            worker = STTWorker(hotword=hotword, device_index=device_index)
        except Exception as exc:
            self.append_chat("system", f"[error] Failed to start STT: {exc}")
            return
        worker.recognized.connect(self.on_stt_result)
        self.stt_worker = worker
        worker.start()
        self.append_chat("system", f"[stt] Listening for '{hotword}'...")

    def stop_stt(self) -> None:
        """Stop and clean up the STT worker."""
        if self.stt_worker:
            try:
                self.stt_worker.stop()
                self.stt_worker.wait()
            except Exception:
                pass
            self.stt_worker = None
            self.append_chat("system", "[stt] Stopped listening")

    @Slot(str)
    def on_stt_result(self, text: str) -> None:
        """Handle recognized speech and forward to LLM if appropriate."""
        query = text.strip()
        if not query:
            return
        # Append to chat as user message
        self.append_chat("user", query)
        if self.last_frame_qimage is None:
            self.append_chat("system", "[stt] No video frame available to send to model.")
            return
        if self.ollama.is_busy:
            self.append_chat("system", "[stt] Model is busy; ignoring transcription.")
            return
        # Ensure model selection is up-to-date
        self.ollama.set_model(self.model_edit.text().strip())
        # Reset assistant response accumulator and streaming flag
        self.current_response_text = ""
        self._stream_started = False
        self.ollama.send_user_message_with_frame(query, self.last_frame_qimage)

    # LLM callbacks
    def on_llm_stream(self, delta_text: str) -> None:
        """Handle streaming deltas from the LLM. Append to chat log with separation."""
        if not delta_text:
            return
        # On the first delta of a new assistant response, insert a header on its own line
        if not self._stream_started:
            # Create a new paragraph with Assistant label
            self.chat_log.append("<b><span style='color:#a6e3a1'>Assistant:</span></b>")
            self._stream_started = True
        # Append the new delta to the current assistant message
        self.chat_log.moveCursor(QTextCursor.End)
        self.chat_log.insertPlainText(delta_text)
        self.chat_log.moveCursor(QTextCursor.End)
        # Accumulate assistant response for TTS
        self.current_response_text += delta_text

    def on_llm_done(self) -> None:
        """Mark the end of the LLM response."""
        # Insert a newline for separation before future messages
        self.chat_log.append("")
        # Speak out the assistant response if TTS is enabled
        if self.tts_enable_cb.isChecked() and self.current_response_text.strip():
            # Set selected voice
            try:
                voice_id = self.voice_box.currentData()
                if voice_id:
                    self.tts.set_voice(voice_id)
            except Exception:
                pass
            # Speak asynchronously
            try:
                self.tts.speak(self.current_response_text.strip())
            except Exception:
                pass
        # Reset stream state for the next response
        self._stream_started = False
        self.current_response_text = ""

    def on_llm_error(self, msg: str) -> None:
        self.append_chat("system", f"[error] {msg}")

    def closeEvent(self, event):
        # Clean up resources when closing the window
        try:
            self.video.stop()
        except Exception:
            pass
        try:
            self.serial.close()
        except Exception:
            pass
        try:
            self.ollama.close()
        except Exception:
            pass
        # Stop STT if running
        try:
            self.stop_stt()
        except Exception:
            pass
        return super().closeEvent(event)


def main():
    """Entry point for running the application."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()