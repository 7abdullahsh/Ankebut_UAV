"""ANKEBUT GCS - Mission Planner gomme modulu."""

import win32gui
import win32con
from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QApplication
)


class MissionPlannerHost(QWidget):
    """Mission Planner'i icine gomer. MP zaten calisiyor olmali."""

    def __init__(self, title_match: str = "Mission Planner", parent=None):
        super().__init__(parent)
        self._title_match = title_match
        self._mp_widget = None
        self._mp_window = None
        self._mp_hwnd = 0

        # Gom oncesi orijinal pencere durumu (geri yuklemek icin)
        self._original_style = None
        self._original_parent = 0

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # --- Placeholder (gomulu degilken gorunur) ---
        self._placeholder_widget = QWidget()
        self._placeholder_widget.setStyleSheet("background-color: #000000;")

        placeholder_layout = QVBoxLayout(self._placeholder_widget)
        placeholder_layout.setAlignment(Qt.AlignCenter)
        placeholder_layout.setSpacing(20)

        self._embed_button = QPushButton("OPEN MISSION PLANNER")
        self._embed_button.setCursor(Qt.PointingHandCursor)
        self._embed_button.setFixedWidth(320)
        self._embed_button.setStyleSheet(
            "QPushButton {"
            "    background-color: white;"
            "    color: black;"
            "    font-size: 15px;"
            "    font-weight: bold;"
            "    letter-spacing: 1px;"
            "    padding: 16px;"
            "    border: none;"
            "    border-radius: 8px;"
            "}"
            "QPushButton:hover {"
            "    background-color: #f0f0f0;"
            "}"
        )
        self._embed_button.clicked.connect(self.embed)

        placeholder_layout.addWidget(self._embed_button, alignment=Qt.AlignCenter)

        # --- Kontrol cubugu (gomuluyken ust kisimda gorunur) ---
        self._control_bar = QWidget()
        self._control_bar.setFixedHeight(44)
        self._control_bar.setStyleSheet("background-color: #000000;")

        control_layout = QHBoxLayout(self._control_bar)
        control_layout.setContentsMargins(10, 6, 10, 6)
        control_layout.addStretch()

        self._stop_button = QPushButton("STOP MISSION PLANNER")
        self._stop_button.setCursor(Qt.PointingHandCursor)
        self._stop_button.setStyleSheet(
            "QPushButton {"
            "    background-color: #c0392b;"
            "    color: white;"
            "    font-size: 13px;"
            "    font-weight: bold;"
            "    letter-spacing: 1px;"
            "    padding: 8px 18px;"
            "    border: none;"
            "    border-radius: 6px;"
            "}"
            "QPushButton:hover {"
            "    background-color: #e74c3c;"
            "}"
        )
        self._stop_button.clicked.connect(self.release)
        control_layout.addWidget(self._stop_button)

        self._control_bar.hide()

        self._layout.addWidget(self._control_bar)
        self._layout.addWidget(self._placeholder_widget)

        # UI kapanirken MP'yi eski haline dondur
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.release)

    def embed(self):
        """MP penceresini bul ve gom."""
        if self.is_embedded():
            return

        self._mp_hwnd = self._find_mp_window()

        if self._mp_hwnd == 0:
            QMessageBox.warning(
                self,
                "Mission Planner Bulunamadi",
                "Acik bir Mission Planner penceresi bulunamadi.\n\n"
                "Once Mission Planner'i ayri pencerede calistirin,\n"
                "sonra tekrar deneyin."
            )
            return

        # Orijinal durumu kaydet (geri yuklemek icin)
        self._original_style = win32gui.GetWindowLong(
            self._mp_hwnd, win32con.GWL_STYLE
        )
        self._original_parent = win32gui.GetParent(self._mp_hwnd)

        style = self._original_style
        style &= ~win32con.WS_CAPTION
        style &= ~win32con.WS_THICKFRAME
        win32gui.SetWindowLong(self._mp_hwnd, win32con.GWL_STYLE, style)

        self._mp_window = QWindow.fromWinId(self._mp_hwnd)
        self._mp_widget = QWidget.createWindowContainer(self._mp_window, self)

        self._placeholder_widget.hide()
        self._control_bar.show()
        self._layout.addWidget(self._mp_widget)

        win32gui.SetWindowPos(
            self._mp_hwnd,
            0,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOZORDER
            | win32con.SWP_FRAMECHANGED,
        )

    def release(self):
        """MP penceresini serbest birak ve eski haline (cerceve + baslik) dondur."""
        if self._mp_hwnd == 0:
            return

        hwnd = self._mp_hwnd

        # 1) MP'yi tekrar masaustune (orijinal parent'a) tasiyarak Qt'den ayir
        try:
            win32gui.SetParent(hwnd, self._original_parent or 0)
        except Exception:
            pass

        # 2) Orijinal pencere stilini (baslik cubugu + cerceve) geri yukle
        if self._original_style is not None:
            try:
                win32gui.SetWindowLong(
                    hwnd, win32con.GWL_STYLE, self._original_style
                )
            except Exception:
                pass

        # 3) Cerceve degisikligini uygula ve pencereyi tekrar goster
        try:
            win32gui.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_NOZORDER
                | win32con.SWP_FRAMECHANGED,
            )
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
        except Exception:
            pass

        # 4) Qt tarafindaki container'i temizle
        if self._mp_widget is not None:
            self._layout.removeWidget(self._mp_widget)
            self._mp_widget.setParent(None)
            self._mp_widget.deleteLater()
            self._mp_widget = None
        self._mp_window = None

        # 5) Arayuzu tekrar placeholder durumuna getir
        self._control_bar.hide()
        self._placeholder_widget.show()

        self._mp_hwnd = 0
        self._original_style = None
        self._original_parent = 0

    def is_embedded(self) -> bool:
        return self._mp_widget is not None

    def closeEvent(self, event):
        """Host kapanirken MP'yi eski haline dondur."""
        self.release()
        super().closeEvent(event)

    def _find_mp_window(self) -> int:
        """Acik MP penceresinin hwnd'sini bulur. Yoksa 0 doner."""
        found = [0]

        def callback(hwnd, _):
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)

            if self._title_match in title and win32gui.IsWindowVisible(hwnd):
                if cls not in (
                    "ConsoleWindowClass",
                    "CASCADIA_HOSTING_WINDOW_CLASS",
                ):
                    found[0] = hwnd

        win32gui.EnumWindows(callback, None)
        return found[0]
