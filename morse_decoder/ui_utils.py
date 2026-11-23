"""Qt desktop interface and the bridge to the camera session."""

from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Event, Lock
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from config import NOTIFICATION_DURATION

if TYPE_CHECKING:
    from numpy import ndarray


STYLES = """
QWidget#window { background: #edf3f0; }
QWidget { color: #17302c; font-size: 14px; }
QLabel { background: transparent; }
QLabel[role="brand"] { font-size: 20px; font-weight: 700; }
QLabel[role="mark"] { background: #174d43; color: #fff; border-radius: 9px;
    font-size: 19px; font-weight: 700; }
QLabel[role="headline"] { font-size: 41px; font-weight: 700; }
QLabel[role="subtitle"] { font-size: 16px; color: #52655f; }
QLabel[role="heading"] { font-size: 17px; font-weight: 700; }
QLabel[role="muted"] { color: #52655f; }
QLabel[role="key"] { background: #edf3f0; border: 1px solid #d4e0da; border-radius: 5px;
    color: #36574e; font-size: 13px; font-weight: 700; }
QLabel[role="icon"] { background: #dcefe6; border-radius: 13px; }
QLabel[role="icon"][mode="light"] { background: #f5ebcf; }
QPushButton { background: #f8fbf9; border: 1px solid #cbdad3; border-radius: 8px;
    padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #e5f0ea; border-color: #83a698; }
QPushButton:focus { border: 2px solid #287763; background: #edf7f1; }
QPushButton:disabled { background: #eef3f0; color: #667972; border-color: #dce6e1; }
QPushButton[role="mode"] { background: #fcfdfb; border-radius: 16px; padding: 0; }
QPushButton[role="mode"]:hover, QPushButton[role="mode"]:focus { background: #f1f8f3; }
QPushButton[role="primary"] { background: #174d43; border-color: #174d43; color: white; }
QPushButton[role="primary"]:hover { background: #256455; }
QPushButton[role="primary"]:focus { border: 2px solid #64b39a; background: #174d43; }
QPushButton[role="primary"]:disabled { background: #e1eae4; border-color: #dce6e1; color: #667972; }
QFrame[role="panel"] { background: #fcfdfb; border: 1px solid #d3dfd9; border-radius: 14px; }
QLabel[role="phase"] { font-size: 23px; font-weight: 700; }
QLabel[role="countdown"] { color: #1a6252; font-size: 20px; font-weight: 700; }
QLabel[role="sequence"] { color: #18634f; font-size: 30px; font-weight: 700; }
QLabel[role="sequence"][invalid="true"] { color: #9f3438; }
QLabel[role="notice"] { color: #24574a; padding: 4px 0; }
QLabel[role="notice"][error="true"] { color: #9f3438; }
QPlainTextEdit { background: #f0f5f2; border: 1px solid #e0e9e4; border-radius: 8px;
    padding: 11pkeep the modelx; font-size: 20px; selection-background-color: #badfce;
    selection-color: #17302c; }
QPlainTextEdit:focus { border: 1px solid #287763; }
QProgressBar { background: #e1ebe5; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #31836e; border-radius: 3px; }
"""


@dataclass(frozen=True)
class SessionFrame:
    frame: ndarray
    morse_sequence: str
    decoded_text: str
    invalid_sequence: bool
    symbol: str | None
    time_left: float
    duration: float
    status: str = ""
    eye_readings: tuple[float, float] | None = None


class SessionChannel:
    def __init__(self):
        self.stop_requested = Event()
        self.keys = SimpleQueue()
        self._lock = Lock()
        self._latest_frame = None
        self._notice = ("", 0.0, False)

    def next_key(self):
        try:
            return self.keys.get_nowait()
        except Empty:
            return -1

    def present(self, frame: SessionFrame):
        # A slow display can skip stale previews without skipping any detections.
        with self._lock:
            self._latest_frame = frame

    def show_notification(self, message: str, current_time: float, is_error: bool = False):
        with self._lock:
            self._notice = (message, current_time + NOTIFICATION_DURATION, is_error)

    def take_update(self):
        with self._lock:
            frame = self._latest_frame
            self._latest_frame = None
            message, expires_at, is_error = self._notice
        if time.time() >= expires_at:
            message, is_error = "", False
        return frame, message, is_error


class SessionWorker(QThread):
    failed = Signal(str)

    def __init__(self, mode, channel, run_session, parent):
        super().__init__(parent)
        self.mode = mode
        self.channel = channel
        self.run_session = run_session

    def run(self):
        try:
            self.run_session(self.mode, self.channel)
        except Exception as error:
            self.failed.emit(str(error) or type(error).__name__)


def label(text="", role=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setProperty("role", role)
    return widget


def set_flag(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def panel():
    widget = QFrame()
    widget.setProperty("role", "panel")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(17, 15, 17, 15)
    layout.setSpacing(10)
    return widget, layout


def brand():
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    mark = label("·−", "mark")
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark.setFixedSize(35, 35)
    layout.addWidget(mark)
    layout.addWidget(label("LuminEye", "brand"))
    return widget


class ModeIcon(QLabel):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.setProperty("role", "icon")
        self.setProperty("mode", mode)
        self.setFixedSize(48, 48)

    def paintEvent(self, event):
        super().paintEvent(event)
        color = "#1c6957" if self.mode == "eye" else "#805d25"
        pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(pen)
            if self.mode == "eye":
                eye = QPainterPath()
                eye.moveTo(11, 24)
                eye.quadTo(24, 8, 37, 24)
                eye.quadTo(24, 40, 11, 24)
                painter.drawPath(eye)
                painter.drawEllipse(QRectF(20, 20, 8, 8))
            else:
                painter.drawEllipse(QRectF(18, 18, 12, 12))
                for x1, y1, x2, y2 in (
                    (24, 11, 24, 14), (24, 34, 24, 37),
                    (11, 24, 14, 24), (34, 24, 37, 24),
                    (15, 15, 17, 17), (31, 31, 33, 33),
                    (15, 33, 17, 31), (31, 17, 33, 15),
                ):
                    painter.drawLine(x1, y1, x2, y2)


def mode_button(mode, key, title, description):
    button = QPushButton()
    button.setObjectName(f"{mode}Mode")
    button.setAccessibleName(title)
    button.setAccessibleDescription(description)
    button.setProperty("role", "mode")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(168)
    layout = QVBoxLayout(button)
    layout.setContentsMargins(22, 18, 22, 18)
    layout.setSpacing(7)

    content = QWidget()
    content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    details = QVBoxLayout(content)
    details.setContentsMargins(0, 0, 0, 0)
    details.setSpacing(7)
    top = QHBoxLayout()
    top.addWidget(ModeIcon(mode))
    top.addStretch()
    key_label = label(key, "key")
    key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    key_label.setFixedSize(26, 24)
    top.addWidget(key_label, alignment=Qt.AlignmentFlag.AlignTop)
    details.addLayout(top)
    details.addSpacing(4)
    details.addWidget(label(title, "heading"))
    body = label(description, "muted")
    body.setWordWrap(True)
    details.addWidget(body, 1, Qt.AlignmentFlag.AlignTop)
    layout.addWidget(content)
    return button


class VideoPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.setAccessibleName("Camera preview")
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, frame):
        height, width = frame.shape[:2]
        # Own the pixels before the worker publishes its next frame.
        self.image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 8, 8)
        painter.setClipPath(clip)
        painter.fillRect(bounds, QColor("#152925"))
        if not self.image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                            size.width(), size.height())
            painter.drawImage(target, self.image)


class LivePage(QWidget):
    def __init__(self, mode, send_key, quit_app):
        super().__init__()
        self.blink_hint = (
            "Blink both eyes to select this symbol." if mode == "eye"
            else "Turn the light off and back on to select this symbol."
        )
        self.error_message = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(17)
        header.addWidget(brand())
        header.addWidget(label("Eye blink" if mode == "eye" else "Light blink", "muted"))
        header.addStretch()
        quit_button = QPushButton("Quit    Q")
        quit_button.clicked.connect(quit_app)
        header.addWidget(quit_button)
        root.addLayout(header)

        workspace = QHBoxLayout()
        workspace.setSpacing(15)
        root.addLayout(workspace, 1)

        camera_panel, camera = panel()
        workspace.addWidget(camera_panel, 3)
        camera_header = QHBoxLayout()
        camera_header.addWidget(label("Camera preview", "heading"))
        camera_header.addStretch()
        self.camera_status = label("Starting camera…", "muted")
        camera_header.addWidget(self.camera_status)
        camera.addLayout(camera_header)
        self.preview = VideoPreview()
        camera.addWidget(self.preview, 1)

        phase = QHBoxLayout()
        self.phase = label("Waiting for camera", "phase")
        self.countdown = label("", "countdown")
        phase.addWidget(self.phase, 1)
        phase.addWidget(self.countdown)
        camera.addLayout(phase)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Time left in the current phase")
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        camera.addWidget(self.progress)
        self.phase_help = label("", "muted")
        self.phase_help.setWordWrap(True)
        camera.addWidget(self.phase_help)

        output_panel, output = panel()
        output_panel.setMinimumWidth(295)
        workspace.addWidget(output_panel, 2)
        output.addWidget(label("Current sequence", "heading"))
        self.sequence = label("—", "sequence")
        self.sequence.setAccessibleName("Current Morse sequence")
        output.addWidget(self.sequence)
        output.addSpacing(4)
        output.addWidget(label("Decoded text", "heading"))
        self.decoded = QPlainTextEdit()
        self.decoded.setObjectName("decodedText")
        self.decoded.setAccessibleName("Decoded text")
        self.decoded.setReadOnly(True)
        self.decoded.setMinimumHeight(95)
        self.decoded.setPlaceholderText("Submitted letters appear here.")
        output.addWidget(self.decoded, 1)

        actions = QGridLayout()
        actions.setSpacing(7)
        self.buttons = {}
        for text, key, row, column, span in (
            ("Submit letter    Enter", 13, 0, 0, 2),
            ("Reset    R", ord("r"), 1, 0, 1),
            ("Delete    D", ord("d"), 1, 1, 1),
            ("Speak    T", ord("t"), 2, 0, 1),
            ("Beeps    B", ord("b"), 2, 1, 1),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, key=key: send_key(key))
            button.setEnabled(False)
            self.buttons[key] = button
            actions.addWidget(button, row, column, 1, span)
        self.buttons[13].setProperty("role", "primary")
        output.addLayout(actions)
        self.notice = label("", "notice")
        self.notice.setWordWrap(True)
        self.notice.setMinimumHeight(40)
        output.addWidget(self.notice)

    def render(self, view: SessionFrame):
        self.preview.set_frame(view.frame)
        readings = view.eye_readings
        self.camera_status.setText(
            f"EAR  L {readings[0]:.2f}  /  R {readings[1]:.2f}" if readings else "Camera live"
        )
        for button in self.buttons.values():
            button.setEnabled(True)

        if view.duration == 0:
            phase, hint = "Calibrating", view.status
        elif view.symbol is None:
            phase, hint = "Rest", "Wait for the next symbol."
        else:
            phase = {".": "Dot", "-": "Dash", " ": "Space"}[view.symbol]
            hint = view.status or self.blink_hint
        self.phase.setText(phase)
        self.phase_help.setText(hint)
        self.countdown.setText(f"{view.time_left:.1f} s" if view.duration else "")
        self.progress.setValue(round(1000 * view.time_left / view.duration) if view.duration else 0)

        self.sequence.setText(" ".join(view.morse_sequence.translate(str.maketrans(".-", "·−"))) or "—")
        set_flag(self.sequence, "invalid", view.invalid_sequence)
        if self.decoded.toPlainText() != view.decoded_text:
            scroll = self.decoded.verticalScrollBar()
            previous_position = scroll.value()
            following = previous_position == scroll.maximum()
            self.decoded.setPlainText(view.decoded_text)
            scroll.setValue(scroll.maximum() if following else previous_position)

    def show_notice(self, message, is_error):
        message = self.error_message or message
        if self.notice.text() != message:
            self.notice.setText(message)
        set_flag(self.notice, "error", bool(self.error_message) or is_error)

    def session_stopped(self, message):
        self.camera_status.setText("Camera stopped")
        self.phase.setText("Session ended")
        self.phase_help.clear()
        self.countdown.clear()
        self.progress.setValue(0)
        for button in self.buttons.values():
            button.setEnabled(False)
        self.error_message = message
        self.show_notice("", False)


class LuminEyeWindow(QWidget):
    def __init__(self, run_session):
        super().__init__()
        self.run_session = run_session
        self.channel = None
        self.worker = None
        self.live = None
        self.closing = False
        self.session_error = ""

        self.setWindowTitle("LuminEye")
        self.setObjectName("window")
        self.setMinimumSize(760, 510)
        self.resize(1080, 690)
        self.setStyleSheet(STYLES)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        root.addWidget(self.pages)
        self.pages.addWidget(self.create_menu())

        self.mode_shortcuts = [
            self.shortcut("1", lambda: self.start_session("eye")),
            self.shortcut("2", lambda: self.start_session("light")),
        ]
        self.shortcut("Q", self.close)
        for key, command in (("Return", 13), ("Enter", 13), ("R", ord("r")),
                             ("D", ord("d")), ("T", ord("t")), ("B", ord("b"))):
            self.shortcut(key, lambda command=command: self.send_key(command))

        self.refresh = QTimer(self)
        self.refresh.setInterval(33)
        self.refresh.timeout.connect(self.render_latest)

    def shortcut(self, key, callback):
        shortcut = QShortcut(QKeySequence(key), self)
        if len(key) == 1 and key.isalpha():
            shortcut.setKeys([QKeySequence(key), QKeySequence(f"Shift+{key}")])
        shortcut.activated.connect(callback)
        return shortcut

    def create_menu(self):
        screen = QWidget()
        outer = QVBoxLayout(screen)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.addStretch()
        content = QWidget()
        content.setMaximumWidth(700)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(brand(), alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(10)
        layout.addWidget(label("Choose your input", "headline"))
        layout.addWidget(label("Use eye blinks or a blinking light to enter Morse code.", "subtitle"))
        layout.addSpacing(9)
        modes = QHBoxLayout()
        modes.setSpacing(14)
        for mode, key, title, description in (
            ("eye", "1", "Eye blink", "Select symbols by blinking both eyes."),
            ("light", "2", "Light blink", "Select symbols by briefly switching off or covering a light."),
        ):
            button = mode_button(mode, key, title, description)
            button.clicked.connect(lambda checked=False, mode=mode: self.start_session(mode))
            modes.addWidget(button, 1)
        layout.addLayout(modes)
        layout.addSpacing(4)
        quit_button = QPushButton("Quit    Q")
        quit_button.clicked.connect(self.close)
        layout.addWidget(quit_button, alignment=Qt.AlignmentFlag.AlignRight)
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(content, 10)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch()
        return screen

    def start_session(self, mode):
        if self.worker is not None:
            return
        for shortcut in self.mode_shortcuts:
            shortcut.setEnabled(False)
        self.live = LivePage(mode, self.send_key, self.close)
        self.pages.addWidget(self.live)
        self.pages.setCurrentWidget(self.live)
        self.channel = SessionChannel()
        self.worker = SessionWorker(mode, self.channel, self.run_session, self)
        self.worker.failed.connect(self.record_error)
        self.worker.finished.connect(self.session_finished)
        self.refresh.start()
        self.worker.start()

    def send_key(self, key):
        if self.live and self.live.buttons[key].isEnabled() and not self.closing:
            self.channel.keys.put(key)

    def render_latest(self):
        view, message, is_error = self.channel.take_update()
        if view is not None:
            self.live.render(view)
        self.live.show_notice(message, is_error)

    def record_error(self, message):
        self.session_error = message

    def session_finished(self):
        self.refresh.stop()
        if self.closing:
            self.close()
            return
        self.render_latest()
        self.live.session_stopped(self.session_error or "The camera feed ended.")

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.closing = True
            self.refresh.stop()
            self.live.camera_status.setText("Closing camera…")
            self.channel.stop_requested.set()
            event.ignore()
            return
        event.accept()


def run_ui(run_session):
    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    application.setApplicationName("LuminEye")
    window = LuminEyeWindow(run_session)
    window.show()

    # Let Python process terminal interrupts while Qt owns the event loop.
    signal_timer = QTimer(application)
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(250)
    previous_handler = signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, window.close))
    try:
        return application.exec()
    finally:
        signal.signal(signal.SIGINT, previous_handler)
