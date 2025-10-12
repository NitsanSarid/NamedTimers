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
from PySide6.QtGui import QIcon, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QProgressBar, QToolButton,
    QCheckBox, QStatusBar, QStyle, QMessageBox
)

# ========= GLOBALS / CONFIG =========
APP_NAME = "Named Timers"
VERSION = "1.1.0"

# Single source of truth for duration (change this to adjust all timers globally)
BASE_DURATION_SEC = 1 * 60  # e.g., set to 25*60 for “pomodoro style”

# UI sizing
ROW_HEIGHT = 88
LEFT_STRIP_PX = 10
PROGRESS_HEIGHT = 18
BASE_FONT_PT = 12          # main font size
TITLE_FONT_PT = 14         # timer name
TIME_FONT_PT = 16          # remaining time

# ========= THEME / PALETTE =========
# Centralized color definitions for light and dark modes.
THEMES = {
    'light': {
        "green": "#21A179",
        "orange": "#F39C12",
        "red": "#E74C3C",
        "muted": "#7F8C8D",

        "text": "#2C3E50",              # Dark text for light backgrounds
        "text_on_color_bg": "#2C3E50",  # High-contrast text for colored backgrounds

        "window_bg": "#FDFEFE",
        "finished_bg": "#ECECEC",
        "progress_bg": "#f2f2f2",
        "progress_border": "#e0e0e0",
        "list_item_spacing": "#E0E0E0",
    },
    'dark': {
        "green": "#2ECC71",             # Brighter green for contrast
        "orange": "#F1C40F",            # Brighter orange
        "red": "#E74C3C",
        "muted": "#95A5A6",

        "text": "#ECF0F1",              # Light text for dark backgrounds
        "text_on_color_bg": "#FFFFFF",

        "window_bg": "#2C3E50",
        "finished_bg": "#34495E",
        "progress_bg": "#1C2B3A",
        "progress_border": "#4A6572",
        "list_item_spacing": "#34495E",
    }
}

# This global dictionary will be updated to point to the currently active theme.
THEME = THEMES['light']

def hex_to_rgb(hex_str: str):
    """Converts a hex color string to an (r, g, b) tuple."""
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

def rgba_tint(hex_str: str, alpha: float = 0.22) -> str:
    """Returns a CSS rgba() string with a specified alpha tint."""
    r, g, b = hex_to_rgb(hex_str)
    a = max(0.0, min(1.0, alpha))
    return f"rgba({r}, {g}, {b}, {a:.3f})"

# ========= MODEL =========
@dataclass
class TimerState:
    name: str
    remaining_sec: int = BASE_DURATION_SEC
    running: bool = True
    last_wall_ts: float = time.time()

    def clamp(self):
        if self.remaining_sec < 0:
            self.remaining_sec = 0

    def is_finished(self) -> bool:
        return self.remaining_sec <= 0

    def color_for_remaining(self) -> str:
        """Gets the appropriate status color from the current theme."""
        r = self.remaining_sec
        if r <= 0:
            return THEME["muted"]
        first_cut = (2 * BASE_DURATION_SEC) // 3
        second_cut = BASE_DURATION_SEC // 3
        if r > first_cut:
            return THEME["green"]
        elif r > second_cut:
            return THEME["orange"]
        else:
            return THEME["red"]

    def display_mmss(self) -> str:
        if self.is_finished():
            return "Done"
        m, s = divmod(self.remaining_sec, 60)
        return f"{m:02d}:{s:02d}"

    def progress01(self) -> float:
        done = BASE_DURATION_SEC - max(self.remaining_sec, 0)
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
        candidate = base
        i = 2
        while candidate in self.items:
            candidate = f"{base} {i}"
            i += 1
        return candidate

    def add(self, name: str) -> TimerState:
        name = self.unique_name(name)
        st = TimerState(name=name, remaining_sec=BASE_DURATION_SEC, running=True, last_wall_ts=time.time())
        self.items[st.name] = st
        self.structure_changed.emit()
        return st

    def remove(self, name: str):
        if name in self.items:
            del self.items[name]
            self.structure_changed.emit()

    def clear_finished(self):
        finished = [k for k, v in self.items.items() if v.is_finished()]
        for k in finished:
            del self.items[k]
        if finished:
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
        finished = sum(1 for v in self.items.values() if v.is_finished())
        return active, finished

# ========= UI WIDGET =========
class TimerWidget(QWidget):
    request_remove = Signal(str)
    state_changed = Signal()

    def __init__(self, model: TimerState, parent=None):
        super().__init__(parent)
        self.model = model
        self._build_ui()
        self.update_view()

    def _build_ui(self):
        self.name_lbl = QLabel(self.model.name)
        self.time_lbl = QLabel()
        self.time_lbl.setMinimumWidth(80)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Set base font styles here before adding the labels to the layout.
        # This ensures the initial sizeHint is calculated with the correct font metrics,
        # preventing the label from being truncated when the layout is first built.
        self.name_lbl.setStyleSheet(f"font-weight: 700; font-size: {TITLE_FONT_PT}pt;")
        self.time_lbl.setStyleSheet(f"font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace; font-size: {TIME_FONT_PT}pt;")

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(PROGRESS_HEIGHT)

        self.pause_btn = QToolButton()
        self.pause_btn.setToolTip("Pause / Resume")
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.pause_btn.setIconSize(QSize(28, 28))

        self.remove_btn = QToolButton()
        self.remove_btn.setToolTip("Remove timer")
        self.remove_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.remove_btn.clicked.connect(lambda: self.request_remove.emit(self.model.name))
        self.remove_btn.setIconSize(QSize(24, 24))

        text_col = QVBoxLayout()
        top_row = QHBoxLayout()
        top_row.addWidget(self.name_lbl)
        top_row.addStretch()
        top_row.addWidget(self.time_lbl)
        text_col.addLayout(top_row)
        text_col.addWidget(self.progress)

        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.addWidget(self.pause_btn)
        right_col.addWidget(self.remove_btn)
        right_col.addStretch()

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(12)
        root.addLayout(text_col, 1)
        root.addLayout(right_col, 0)

        self.setAutoFillBackground(True)
        self.setMinimumHeight(ROW_HEIGHT)

    def _toggle_pause(self):
        if self.model.is_finished():
            return
        self.model.running = not self.model.running
        self.state_changed.emit()
        self.update_view()

    def update_view(self):
        """Updates the widget's appearance based on its state and the global THEME."""
        self.name_lbl.setText(self.model.name)
        self.time_lbl.setText(self.model.display_mmss())
        self.progress.setValue(int(self.model.progress01() * 1000))

        if self.model.running and not self.model.is_finished():
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

        color = self.model.color_for_remaining()

        # Update progress bar colors from theme
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {THEME['progress_bg']};
                border: 1px solid {THEME['progress_border']};
                border-radius: 9px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 9px;
            }}
        """)

        # Determine colors for background and text based on timer state
        if self.model.is_finished():
            self.pause_btn.setEnabled(False)
            widget_bg = THEME['finished_bg']
            widget_border = THEME['muted']
            label_color = THEME['muted']
        else:
            self.pause_btn.setEnabled(True)
            widget_bg = rgba_tint(color, 0.22)
            widget_border = color
            label_color = THEME['text_on_color_bg']

        # Apply styles to the main widget background and border
        self.setStyleSheet(f"background:{widget_bg}; border-left: {LEFT_STRIP_PX}px solid {widget_border};")

        # Apply styles to labels, ensuring correct text color
        self.name_lbl.setStyleSheet(f"""
            QLabel {{
                font-weight: 700; font-size: {TITLE_FONT_PT}pt;
                background: transparent; color: {label_color};
            }}""")
        self.time_lbl.setStyleSheet(f"""
            QLabel {{
                font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace; font-size: {TIME_FONT_PT}pt;
                background: transparent; color: {label_color};
            }}""")

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
        self._apply_global_stylesheet() # Apply initial theme styles

        # Global tick for timers (1s)
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._on_tick)
        self.ticker.start()

        # Timer to poll for system theme changes
        self.theme_timer = QTimer(self)
        self.theme_timer.setInterval(1000) # Check every second
        self.theme_timer.timeout.connect(self._check_theme)
        self.theme_timer.start()

    def get_system_theme(self) -> str:
        """Checks for the system theme, defaulting to 'light'."""
        if darkdetect is None:
            return 'light'
        return 'dark' if darkdetect.isDark() else 'light'
    
    def _update_global_theme(self):
        """Updates the global THEME variable."""
        global THEME
        THEME = THEMES[self.current_theme_name]

    def _check_theme(self):
        """Periodically checks if the system theme has changed."""
        new_theme_name = self.get_system_theme()
        if new_theme_name != self.current_theme_name:
            self.current_theme_name = new_theme_name
            self._update_global_theme()
            self._on_theme_changed()

    def _on_theme_changed(self):
        """Applies new theme colors and rebuilds the list to reflect changes."""
        self._apply_global_stylesheet()
        self._rebuild_list()

    def _apply_global_stylesheet(self):
        """Sets a rich stylesheet for the whole application based on the current theme."""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(THEME["window_bg"]))
        palette.setColor(QPalette.WindowText, QColor(THEME["text"]))
        palette.setColor(QPalette.Base, QColor(THEME["window_bg"]))
        palette.setColor(QPalette.AlternateBase, QColor(THEME["finished_bg"]))
        palette.setColor(QPalette.Text, QColor(THEME["text"]))
        palette.setColor(QPalette.Button, QColor(THEME["finished_bg"]))
        palette.setColor(QPalette.ButtonText, QColor(THEME["text"]))
        palette.setColor(QPalette.Highlight, QColor(THEME["green"]))
        palette.setColor(QPalette.HighlightedText, QColor(THEME["text_on_color_bg"]))
        self.setPalette(palette)
        
        self.setStyleSheet(f"""
            QWidget {{
                font-size: {BASE_FONT_PT}pt;
            }}
            QMainWindow, QStatusBar {{
                background-color: {THEME['finished_bg']};
            }}
            QLineEdit, QListWidget {{
                padding: 6px 8px;
                border: 1px solid {THEME['progress_border']};
                border-radius: 4px;
                background-color: {THEME['window_bg']};
            }}
            QPushButton {{
                padding: 8px 12px; border-radius: 4px;
                border: 1px solid {THEME['progress_border']};
                background-color: {THEME['finished_bg']};
            }}
            QPushButton:hover {{ background-color: {rgba_tint(THEME['green'], 0.2)}; }}
            QPushButton:pressed {{ background-color: {rgba_tint(THEME['green'], 0.4)}; }}
            QPushButton:default {{ border: 2px solid {THEME['green']}; }}
            QToolButton {{ padding: 6px; border: none; border-radius: 4px; }}
            QToolButton:hover {{ background-color: {rgba_tint(THEME['muted'], 0.2)}; }}
            QListWidget::item {{ border-bottom: 1px solid {THEME['list_item_spacing']}; }}
            QListWidget::item:last-child {{ border-bottom: none; }}
            QMessageBox {{ background-color: {THEME['window_bg']}; }}
        """)


    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Timer name…")

        self.add_btn = QPushButton("Add")
        self.add_btn.setDefault(True)

        self.clear_finished_btn = QPushButton("Clear finished")

        self.group_active_top_chk = QCheckBox("Active on top")
        self.group_active_top_chk.setChecked(True)

        top_row = QHBoxLayout()
        top_row.addWidget(self.name_edit, 1)
        top_row.addWidget(self.add_btn)
        top_row.addWidget(self.clear_finished_btn)
        top_row.addSpacing(18)
        top_row.addWidget(self.group_active_top_chk)

        self.list_widget = QListWidget()
        self.list_widget.setSpacing(0) # Use border for spacing instead

        layout = QVBoxLayout(central)
        layout.addLayout(top_row)
        layout.addWidget(self.list_widget, 1)

        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_active_lbl = QLabel("")
        self.status_finished_lbl = QLabel("")
        sb.addPermanentWidget(self.status_active_lbl)
        sb.addPermanentWidget(self.status_finished_lbl)
        self._update_status_counts()

        self.resize(650, 550)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.name_edit.returnPressed.connect(self._on_add_clicked)
        self.clear_finished_btn.clicked.connect(self._on_clear_finished)
        self.group_active_top_chk.toggled.connect(self._rebuild_list)
        self.manager.updated.connect(self._on_manager_updated)
        self.manager.structure_changed.connect(self._rebuild_list)

    def _on_add_clicked(self):
        base = self.name_edit.text().strip() or "Timer"
        self.manager.add(base)
        self.name_edit.clear()
        self.name_edit.setFocus()

    def _on_clear_finished(self):
        self.manager.clear_finished()

    def _on_tick(self):
        self.manager.tick_all()

    def _on_manager_updated(self):
        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if isinstance(w, TimerWidget):
                w.update_view()
        self._update_status_counts()
        if self.group_active_top_chk.isChecked():
            # A resort may be needed if a timer's remaining seconds changed its order
            self._rebuild_list()

    def _add_list_item(self, st: TimerState):
        item = QListWidgetItem()
        widget = TimerWidget(st)
        widget.request_remove.connect(self._on_remove_requested)
        widget.state_changed.connect(self._on_manager_updated)
        item.setSizeHint(QSize(560, ROW_HEIGHT))
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)

    def _rebuild_list(self):
        sel_names = {w.model.name for idx in self.list_widget.selectedIndexes()
                     if (w := self.list_widget.itemWidget(self.list_widget.item(idx.row())))}

        self.list_widget.clear()

        items = self.manager.all_items()
        sort_key = lambda t: (t.is_finished(), t.remaining_sec, t.name.lower())
        if not self.group_active_top_chk.isChecked():
            sort_key = lambda t: (t.name.lower(),)
        items.sort(key=sort_key)

        for st in items:
            self._add_list_item(st)

        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if w and w.model.name in sel_names:
                self.list_widget.item(i).setSelected(True)

        self._update_status_counts()

    def _on_remove_requested(self, name: str):
        st = self.manager.items.get(name)
        if st and not st.is_finished():
            remaining = st.display_mmss()
            resp = QMessageBox.question(
                self, "Remove running timer?",
                f"Timer \"{name}\" is still running ({remaining}).\n\nDo you want to delete it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        self.manager.remove(name)

    def _update_status_counts(self):
        active, finished = self.manager.counts()
        self.status_active_lbl.setText(f"Active: {active}")
        self.status_finished_lbl.setText(f"Finished: {finished}")

# ========= ENTRY POINT =========
def main():
    def resource_path(relative_path):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
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

