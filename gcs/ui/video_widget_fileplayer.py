"""ANKEBUT GCS - Video paneli (QMediaPlayer tabanli, Faz 1).

Faz 3'te bu sinifin ici GStreamer ile degistirilecek;
dis arayuz (set_source, sinyaller) ayni kalacak.
"""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget


class StatusLED(QLabel):
    """Yuvarlak LED gibi gorunen kucuk gosterge."""
    COLORS = {
        "ok":   "#27AE60",
        "warn": "#F39C12",
        "err":  "#E74C3C",
        "off":  "#555555",
    }

    def __init__(self, status="off"):
        super().__init__()
        self.setFixedSize(14, 14)
        self.set_status(status)

    def set_status(self, status):
        color = self.COLORS.get(status, self.COLORS["off"])
        self.setStyleSheet(f"""
            background-color: {color};
            border-radius: 7px;
            border: 1px solid #222;
        """)


class VideoPanel(QWidget):
    """Drone kamera goruntusunu gosteren panel."""

    connection_changed = Signal(str)  # "ok" / "warn" / "err"

    def __init__(self, video_source=None):
        super().__init__()
        self._build_ui()
        self._build_player()
        if video_source:
            self.set_source(video_source)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Ust baslik cubugu
        header = QFrame()
        header.setStyleSheet("background-color: #1F3346; border: 1px solid #3A5066;")
        header.setFixedHeight(32)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 10, 4)

        title = QLabel("DRONE KAMERA - CANLI")
        title.setStyleSheet("color: #8FA8C2; font-weight: bold; font-size: 13px;")

        self.led = StatusLED("off")
        self.status_label = QLabel("Hazir")
        self.status_label.setStyleSheet("color: #8FA8C2; font-size: 12px;")

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.led)
        header_layout.addWidget(self.status_label)

        # Video alani
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: black;")

        layout.addWidget(header)
        layout.addWidget(self.video_widget, stretch=1)

    def _build_player(self):
        self.audio_output = QAudioOutput()
        self.audio_output.setMuted(True)

        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.player.errorOccurred.connect(self._on_error)
        self.player.mediaStatusChanged.connect(self._on_status_change)

    def set_source(self, source):
        path = Path(source)
        if not path.exists():
            self._set_status("warn", f"Video bulunamadi: {source}")
            return
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.player.play()
        self._set_status("ok", "Oynatiliyor")

    def _on_error(self, error, error_string):
        self._set_status("err", f"Hata: {error_string}")

    def _on_status_change(self, status):
        if status == QMediaPlayer.LoadingMedia:
            self._set_status("warn", "Yukleniyor...")
        elif status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self._set_status("ok", "Oynatiliyor")
        elif status == QMediaPlayer.EndOfMedia:
            # Loop: video bitince basa sar
            self.player.setPosition(0)
            self.player.play()

    def _set_status(self, level, text):
        self.led.set_status(level)
        self.status_label.setText(text)
        self.connection_changed.emit(level)