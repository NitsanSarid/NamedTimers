import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List

# Try to import darkdetect for theme detection.
# If it's not installed, we'll default to a light theme.
# You can install it with: pip install darkdetect
try:
    import darkdetect
except ImportError:
    print("Warning: darkdetect not found. Defaulting to light theme. `pip install darkdetect` for auto-detection.")
    darkdetect = None

from PySide6.QtCore import Qt, QTimer, QSize, Signal, QObject
from PySide6.QtGui import QIcon, QPalette, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QToolButton,
    QCheckBox, QStyle, QMessageBox, QStyleOption
)

# ========= GLOBALS / CONFIG =========
APP_NAME = "Named Timers"
VERSION = "1.1.0"

# Single source of truth for duration (change this to adjust all timers globally)
BASE_DURATION_SEC = 40 * 60  # e.g., set to 25*60 for “pomodoro style”

# UI sizing
ROW_HEIGHT = 80 # Reduced height for a more compact look
BASE_FONT_PT = 12
TITLE_FONT_PT = 14
TIME_FONT_PT = 16

# ========= THEME / PALETTE =========
THEMES = {
    'light': {
        "green": "#27AE60",
        "orange": "#F39C12",
        "red": "#E74C3C",
        "muted": "#BDC3C7",

        "text": "#2C3E50",
        "text_on_color_bg": "#FFFFFF",

        "window_bg": "#ECF0F1",
        "widget_bg": "#FFFFFF",
        "finished_bg": "#F7F9F9",
        "progress_border": "#E0E0E0",
    },
    'dark': {
        "green": "#2ECC71",
        "orange": "#F1C40F",
        "red": "#E74C3C",
        "muted": "#7F8C8D",

        "text": "#ECF0F1",
        "text_on_color_bg": "#1C2833",

        "window_bg": "#2C3E50",
        "widget_bg": "#34495E",
        "finished_bg": "#283747",
        "progress_border": "#4A6572",
    }
}

THEME = THEMES['light']

def hex_to_rgb(hex_str: str):
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

def rgba_string(hex_str: str, alpha: float = 1.0) -> str:
    """Returns a CSS rgba() string with a specified alpha."""
    r, g, b = hex_to_rgb(hex_str)
    a = max(0.0, min(1.0, alpha))
    return f"rgba({r}, {g}, {b}, {int(a * 255)})"


# ========= MODEL =========
@dataclass
class TimerState:
    name: str
    remaining_sec: int
    running: bool
    last_wall_ts: float

    def clamp(self):
        self.remaining_sec = max(0, self.remaining_sec)

    def is_finished(self) -> bool:
        return self.remaining_sec <= 0

    def color_for_remaining(self) -> str:
        r = self.remaining_sec
        if r <= 0: return THEME["muted"]
        first_cut = (2 * BASE_DURATION_SEC) // 3
        second_cut = BASE_DURATION_SEC // 3
        if r > first_cut: return THEME["green"]
        if r > second_cut: return THEME["orange"]
        return THEME["red"]

    def display_mmss(self) -> str:
        if self.is_finished(): return "Done"
        m, s = divmod(self.remaining_sec, 60)
        return f"{m:02d}:{s:02d}"

    def progress01(self) -> float:
        done = BASE_DURATION_SEC - self.remaining_sec
        return max(0.0, min(1.0, done / BASE_DURATION_SEC))

    def tick_wall(self, now_ts: float):
        if not self.running or self.is_finished():
            self.last_wall_ts = now_ts
            return
        elapsed = int(now_ts - self.last_wall_ts)
        if elapsed > 0:
            self.remaining_sec -= elapsed
            self.clamp()
            self.last_wall_ts = now_ts


class TimerManager(QObject):
    updated = Signal()
    structure_changed = Signal()

    def __init__(self):
        super().__init__()
        self.items: Dict[str, TimerState] = {}

    def unique_name(self, base: str) -> str:
        base = base.strip() or "Timer"
        candidate, i = base, 2
        while candidate in self.items:
            candidate = f"{base} {i}"
            i += 1
        return candidate

    def add(self, name: str):
        name = self.unique_name(name)
        st = TimerState(
            name=name,
            remaining_sec=BASE_DURATION_SEC,
            running=True,
            last_wall_ts=time.time()
        )
        self.items[name] = st
        self.structure_changed.emit()

    def remove(self, name: str):
        if name in self.items:
            del self.items[name]
            self.structure_changed.emit()

    def clear_finished(self):
        finished_keys = [k for k, v in self.items.items() if v.is_finished()]
        if not finished_keys: return
        for k in finished_keys:
            del self.items[k]
        self.structure_changed.emit()

    def tick_all(self):
        now = time.time()
        any_change = False
        for st in self.items.values():
            before = st.remaining_sec
            st.tick_wall(now)
            if st.remaining_sec != before:
                any_change = True
        
        if any_change:
            self.updated.emit()

    def all_items(self) -> List[TimerState]:
        return list(self.items.values())

    def counts(self):
        active = sum(1 for v in self.items.values() if not v.is_finished())
        finished = len(self.items) - active
        return active, finished


# ========= UI WIDGET (NEW DESIGN) =========
class TimerWidget(QWidget):
    request_remove = Signal(str)
    state_changed = Signal()

    def __init__(self, model: TimerState, parent=None):
        super().__init__(parent)
        self.model = model
        self._build_ui()
        self.update_view()

    def paintEvent(self, event):
        """
        Ensure the widget is drawn using the stylesheet, which is necessary for
        properties like 'border' and 'border-radius' on a plain QWidget.
        """
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)

    def _build_ui(self):
        self.name_lbl = QLabel(self.model.name)
        self.time_lbl = QLabel()
        self.time_lbl.setMinimumWidth(100)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        font_name = self.font()
        font_name.setPointSize(TITLE_FONT_PT)
        font_name.setWeight(QFont.Weight.Bold)
        self.name_lbl.setFont(font_name)
        
        font_time = self.font()
        font_time.setFamily("Consolas, 'Cascadia Mono', 'Courier New', monospace")
        font_time.setPointSize(TIME_FONT_PT)
        self.time_lbl.setFont(font_time)

        self.pause_btn = QToolButton()
        self.pause_btn.setToolTip("Pause / Resume")
        self.pause_btn.setIconSize(QSize(24, 24))
        self.pause_btn.clicked.connect(self._toggle_pause)

        self.remove_btn = QToolButton()
        self.remove_btn.setToolTip("Remove timer")
        self.remove_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.remove_btn.setIconSize(QSize(20, 20))
        self.remove_btn.clicked.connect(lambda: self.request_remove.emit(self.model.name))
        
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(20, 10, 15, 10)
        content_layout.setSpacing(15)
        content_layout.addWidget(self.name_lbl, 1)
        content_layout.addWidget(self.time_lbl)
        content_layout.addWidget(self.pause_btn)
        content_layout.addWidget(self.remove_btn)


        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0,0,0,0)
        root_layout.setSpacing(0)
        root_layout.addLayout(content_layout)
        
        self.setMinimumHeight(ROW_HEIGHT)

    def _toggle_pause(self):
        if self.model.is_finished(): return
        self.model.running = not self.model.running
        self.state_changed.emit()
        self.update_view()

    def update_view(self):
        """Updates colors and styles for the widget and its progress bar."""
        self.name_lbl.setText(self.model.name)
        self.time_lbl.setText(self.model.display_mmss())

        icon = QStyle.SP_MediaPause if self.model.running and not self.model.is_finished() else QStyle.SP_MediaPlay
        self.pause_btn.setIcon(self.style().standardIcon(icon))
        self.pause_btn.setEnabled(not self.model.is_finished())
        
        status_color_hex = self.model.color_for_remaining()
        bg_color_hex = THEME['widget_bg']
        text_color_hex = THEME['text']
        
        text_color_rgba_str = rgba_string(text_color_hex)
        
        if not self.model.running and not self.model.is_finished(): # Paused
            text_color_rgba_str = rgba_string(text_color_hex, alpha=0.6)
        elif self.model.is_finished():
            bg_color_hex = THEME['finished_bg']
            text_color_rgba_str = rgba_string(text_color_hex, alpha=0.5)

        self.setStyleSheet(f"""
            TimerWidget {{
                background-color: {bg_color_hex};
                border-radius: 8px;
                border: 5px solid {status_color_hex};
            }}
            QLabel {{
                color: {text_color_rgba_str};
                background-color: transparent;
                border: none;
            }}
            QToolButton {{
                background-color: transparent; 
                border: none;
                border-radius: 4px; 
                padding: 5px;
            }}
            QToolButton:hover {{
                background-color: {rgba_string(THEME['text'], alpha=0.1)};
            }}
        """)

# ========= MAIN WINDOW =========
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — v{VERSION} — {BASE_DURATION_SEC//60}min")
        self.manager = TimerManager()
        self.current_theme_name = self.get_system_theme()
        
        self._update_global_theme()
        self._build_ui()
        self._connect_signals()
        self._apply_global_stylesheet()

        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._on_tick)
        self.ticker.start()

        self.theme_timer = QTimer(self)
        self.theme_timer.setInterval(1000)
        self.theme_timer.timeout.connect(self._check_theme)
        self.theme_timer.start()

    def get_system_theme(self) -> str:
        if darkdetect is None: return 'light'
        return 'dark' if darkdetect.isDark() else 'light'
    
    def _update_global_theme(self):
        global THEME
        THEME = THEMES[self.current_theme_name]

    def _check_theme(self):
        new_theme_name = self.get_system_theme()
        if new_theme_name != self.current_theme_name:
            self.current_theme_name = new_theme_name
            self._update_global_theme()
            self._on_theme_changed()

    def _on_theme_changed(self):
        self._apply_global_stylesheet()
        self._rebuild_list()

    def _apply_global_stylesheet(self):
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(THEME["window_bg"]))
        palette.setColor(QPalette.WindowText, QColor(THEME["text"]))
        self.setPalette(palette)
        
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {THEME['window_bg']}; }}
            QWidget {{ font-size: {BASE_FONT_PT}pt; color: {THEME['text']}; }}
            QLineEdit, QListWidget {{
                padding: 6px 8px; border: 1px solid {THEME['progress_border']};
                border-radius: 8px; background-color: {THEME['widget_bg']};
            }}
            QListWidget {{ border: none; background-color: {THEME['window_bg']}; }}
            QPushButton {{
                padding: 8px 12px; border-radius: 8px;
                border: 1px solid {THEME['progress_border']};
                background-color: {THEME['widget_bg']};
            }}
            QPushButton:hover {{ background-color: {rgba_string(THEME['green'], alpha=0.2)}; }}
            QPushButton:default {{ border: 2px solid {THEME['green']}; }}
            QMessageBox {{ background-color: {THEME['window_bg']}; }}
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("New timer name…")

        self.add_btn = QPushButton("Add Timer")
        self.add_btn.setDefault(True)

        self.clear_finished_btn = QPushButton("Clear Finished")

        self.group_active_top_chk = QCheckBox("Active on top")
        self.group_active_top_chk.setChecked(True)

        top_row = QHBoxLayout()
        top_row.addWidget(self.name_edit, 1)
        top_row.addWidget(self.add_btn)
        
        controls_row = QHBoxLayout()
        controls_row.addWidget(self.group_active_top_chk)
        controls_row.addStretch()
        controls_row.addWidget(self.clear_finished_btn)

        self.list_widget = QListWidget()
        self.list_widget.setSpacing(20)
        self.list_widget.setSelectionMode(QListWidget.NoSelection)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addLayout(top_row)
        layout.addLayout(controls_row)
        layout.addWidget(self.list_widget, 1)

        self.resize(700, 600)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.name_edit.returnPressed.connect(self._on_add_clicked)
        self.clear_finished_btn.clicked.connect(self._on_clear_finished)
        self.group_active_top_chk.toggled.connect(self._rebuild_list)
        self.manager.updated.connect(self._on_manager_updated)
        self.manager.structure_changed.connect(self._rebuild_list)

    def _on_add_clicked(self):
        self.manager.add(self.name_edit.text())
        self.name_edit.clear()
        self.name_edit.setFocus()

    def _on_clear_finished(self):
        self.manager.clear_finished()

    def _on_tick(self):
        self.manager.tick_all()

    def _on_manager_updated(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self.list_widget.itemWidget(item)
            if isinstance(w, TimerWidget):
                w.update_view()
        if self.group_active_top_chk.isChecked():
            self._rebuild_list()

    def _add_list_item(self, st: TimerState):
        item = QListWidgetItem(self.list_widget)
        widget = TimerWidget(st)
        widget.request_remove.connect(self._on_remove_requested)
        widget.state_changed.connect(self._on_manager_updated)
        item.setSizeHint(widget.sizeHint())
        self.list_widget.setItemWidget(item, widget)

    def _rebuild_list(self):
        self.list_widget.clear()
        items = self.manager.all_items()
        if self.group_active_top_chk.isChecked():
            items.sort(key=lambda t: (t.is_finished(), t.remaining_sec, t.name.lower()))
        else:
            items.sort(key=lambda t: (t.name.lower(),))

        for st in items:
            self._add_list_item(st)

    def _on_remove_requested(self, name: str):
        st = self.manager.items.get(name)
        if st and not st.is_finished():
            resp = QMessageBox.question(
                self, "Remove Running Timer?",
                f"Timer \"{name}\" is still running ({st.display_mmss()}).\n\nAre you sure you want to delete it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if resp != QMessageBox.Yes: return
        self.manager.remove(name)


# ========= ENTRY POINT =========
def main():
    def resource_path(relative_path):
        try: base_path = sys._MEIPASS
        except Exception: base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

    app = QApplication(sys.argv)
    
    try:
        iconPath = resource_path("app.ico")
        if os.path.exists(iconPath):
            app.setWindowIcon(QIcon(iconPath))
    except Exception as e:
        print(f"Could not load application icon: {e}")

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

