"""ANKEBUT GCS - Harita paneli (Leaflet + OSM)."""
from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings


class MapPanel(QWidget):
    """Drone GPS konumunu Leaflet harita uzerinde gosterir.

    Telemetri saglayicisindan dict alir, JS fonksiyonu ile haritayi gunceller.
    """

    def __init__(self, html_path="resources/map.html"):
        super().__init__()
        self._build_ui()
        self._load_map(html_path)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Ust baslik
        header = QFrame()
        header.setStyleSheet("background-color: #1F3346; border: 1px solid #3A5066;")
        header.setFixedHeight(28)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 10, 4)

        title = QLabel("HARITA - GPS TAKIBI")
        title.setStyleSheet("color: #8FA8C2; font-weight: bold; font-size: 12px;")

        self.coord_label = QLabel("---")
        self.coord_label.setStyleSheet("color: #8FA8C2; font-size: 11px;")

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.coord_label)

        # Harita
        self.web_view = QWebEngineView()
        # QtWebEngine'in lokal HTML'den remote tile'lara erismesine izin ver
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)

        layout.addWidget(header)
        layout.addWidget(self.web_view, stretch=1)

    def _load_map(self, html_path):
        path = Path(html_path).resolve()
        if path.exists():
            self.web_view.setUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.web_view.setHtml(
                f"<body style='background:#1A2530;color:white;padding:20px;'>"
                f"<h2>Harita yuklenemedi</h2><p>{html_path} bulunamadi.</p></body>"
            )

    def update_position(self, data):
        """Telemetri dict'inden lat/lon/heading alip JS fonksiyonunu cagirir."""
        lat = data.get("lat")
        lon = data.get("lon")
        heading = data.get("heading_deg", 0)

        if lat is None or lon is None:
            return

        js = f"updateDrone({lat}, {lon}, {heading});"
        self.web_view.page().runJavaScript(js)

        self.coord_label.setText(f"{lat:.6f}, {lon:.6f}")