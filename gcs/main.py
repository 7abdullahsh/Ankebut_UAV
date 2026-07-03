"""ANKEBUT Yer Istasyonu - Giris noktasi."""

import os

# QtWebEngine SSL sorununu cozer
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--ignore-certificate-errors "
    "--disable-features=BlockInsecurePrivateNetworkRequests"
)

# OpenCV/FFmpeg SDP + RTP/UDP izinleri
# BUNU cv2 import edilmeden once ayarlamak gerekiyor.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "protocol_whitelist;file,udp,rtp,tcp,crypto,data"
    "|fflags;nobuffer"
    "|flags;low_delay"
)

import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow


def load_stylesheet(app, path="resources/style.qss"):
    p = Path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    else:
        print(f"Uyari: {path} bulunamadi.")


def main():
    app = QApplication(sys.argv)
    load_stylesheet(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()