import os
import sys
import time
import re
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
from PySide6.QtWidgets import (QRadioButton, QTimeEdit, QSpinBox, QComboBox, QGroupBox,
    QApplication, QWidget, QMainWindow, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QToolButton,
    QCheckBox, QStyle, QMessageBox, QStyleOption,QGridLayout
)

# ========= GLOBALS / CONFIG =========
APP_NAME = "Named Timers"
VERSION = "1.2.0"

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
    initial_duration_sec: int
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
        m, s = divmod(abs(self.remaining_sec), 60)
        return f"{m:02d}:{s:02d}"

    def progress01(self) -> float:
        if self.initial_duration_sec <= 0: return 1.0 if self.is_finished() else 0.0
        done = self.initial_duration_sec - self.remaining_sec
        return max(0.0, min(1.0, done / self.initial_duration_sec))

    def tick_wall(self, now_ts: float):
        if not self.running:
            self.last_wall_ts = now_ts # Prevent time jump when resuming
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

    def add(self, name: str, duration_sec: int):
        name = self.unique_name(name)
        st = TimerState(
            name=name,
            initial_duration_sec=duration_sec,
            remaining_sec=duration_sec,
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
        just_finished = False
        for st in self.items.values():
            before = st.remaining_sec
            was_finished = st.is_finished()
            st.tick_wall(now)
            if st.remaining_sec != before:
                any_change = True
            if not was_finished and st.is_finished():
                just_finished = True
        
        if any_change:
            self.updated.emit()
        if just_finished:
            self.structure_changed.emit()

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

        self._build_timer_options_ui()

        self.add_btn = QPushButton("Add Timer")
        self.add_btn.setDefault(True)

        self.clear_finished_btn = QPushButton("Clear Finished")

        self.group_active_top_chk = QCheckBox("Active on top")
        self.group_active_top_chk.setChecked(True)

        top_row = QHBoxLayout()
        top_row.addWidget(self.name_edit, 1.5)
        top_row.addStretch()
        top_row.addWidget(self.add_btn)
        top_row.addWidget(self.clear_finished_btn)
        top_row.addWidget(self.group_active_top_chk)

        add_section_layout = QVBoxLayout()
        add_section_layout.addLayout(top_row)
        add_section_layout.addWidget(self.options_group_box)

        self.list_widget = QListWidget()
        self.list_widget.setSpacing(20)
        self.list_widget.setSelectionMode(QListWidget.NoSelection)
        self.list_widget.setStyleSheet("QListWidget::item { border-bottom: 1px solid " + THEME['progress_border'] + "; }")

        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addLayout(add_section_layout)
        layout.addWidget(self.list_widget, 1)

        self.resize(700, 600)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.name_edit.returnPressed.connect(self.add_btn.click)
        self.clear_finished_btn.clicked.connect(self._on_clear_finished)
        self.group_active_top_chk.toggled.connect(self._rebuild_list)
        self.manager.updated.connect(self._on_manager_updated)
        self.manager.structure_changed.connect(self._rebuild_list)
        
        self.default_timer_radio.toggled.connect(self._update_timer_options_visibility)
        self.duration_timer_radio.toggled.connect(self._update_timer_options_visibility)
        self.start_time_timer_radio.toggled.connect(self._update_timer_options_visibility)

    def _build_timer_options_ui(self):
        self.options_group_box = QGroupBox("Timer Type")
        grid_layout = QGridLayout(self.options_group_box)
        grid_layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Radio Buttons (Top Row) ---
        self.default_timer_radio = QRadioButton(f"Default ({BASE_DURATION_SEC//60} minutes)")
        self.duration_timer_radio = QRadioButton("Custom Duration")
        self.start_time_timer_radio = QRadioButton("From Start Time")
        self.default_timer_radio.setChecked(True)
        
        grid_layout.addWidget(self.default_timer_radio, 0, 0, Qt.AlignTop)
        grid_layout.addWidget(self.duration_timer_radio, 0, 1, Qt.AlignTop)

        # --- 'From Start Time' radio button with its label ---
        start_time_header_layout = QHBoxLayout()
        start_time_header_layout.setContentsMargins(0,0,0,0)
        start_time_header_layout.addWidget(self.start_time_timer_radio)
        start_time_label = QLabel(f"(ends {BASE_DURATION_SEC//60} min later)")
        start_time_label.setStyleSheet("color: " + THEME['muted'] + "; font-size: 9pt;")
        start_time_header_layout.addWidget(start_time_label)
        start_time_header_layout.addStretch()
        grid_layout.addLayout(start_time_header_layout, 0, 2, Qt.AlignTop)

        # --- Custom Duration controls ---
        self.duration_inputs_widget = QWidget() # Container for visibility toggling
        duration_layout = QHBoxLayout(self.duration_inputs_widget)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        self.duration_spinbox = QSpinBox()
        self.duration_spinbox.setRange(1, 9999)
        self.duration_spinbox.setValue(10)
        self.duration_unit_combo = QComboBox()
        self.duration_unit_combo.addItems(["minutes", "seconds"])
        duration_layout.addWidget(self.duration_spinbox)
        duration_layout.addWidget(self.duration_unit_combo)
        grid_layout.addWidget(self.duration_inputs_widget, 1, 1)

        # --- Start Time controls ---
        self.start_time_inputs_widget = QWidget() # Container for visibility toggling
        start_time_layout = QHBoxLayout(self.start_time_inputs_widget)
        start_time_layout.setContentsMargins(0, 0, 0, 0)
        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setDisplayFormat("HH:mm")
        start_time_layout.addWidget(self.start_time_edit)
        grid_layout.addWidget(self.start_time_inputs_widget, 1, 2)

        grid_layout.setColumnStretch(3, 1) # Add stretch to the end

        self._update_timer_options_visibility()

    def _update_timer_options_visibility(self):
        self.duration_inputs_widget.setVisible(self.duration_timer_radio.isChecked())
        self.start_time_inputs_widget.setVisible(self.start_time_timer_radio.isChecked())

    def _on_add_clicked(self):
        name = self.name_edit.text().strip()
        if not name:
            name = "Untitled Timer"

        duration = BASE_DURATION_SEC

        if self.duration_timer_radio.isChecked():
            value = self.duration_spinbox.value()
            unit = self.duration_unit_combo.currentText()
            duration = value * 60 if unit == "minutes" else value

        elif self.start_time_timer_radio.isChecked():
            start_time = self.start_time_edit.time()
            start_h, start_m = start_time.hour(), start_time.minute()
            
            now = time.localtime()
            end_time_t = time.mktime(now[:3] + (start_h, start_m + BASE_DURATION_SEC // 60, 0) + now[6:])
            
            if end_time_t < time.time():
                end_time_t += 24 * 60 * 60 # Assume next day if time is in the past
            
            duration = int(end_time_t - time.time())

        # Cap the duration at the global maximum
        if duration > BASE_DURATION_SEC:
            max_minutes = BASE_DURATION_SEC // 60
            QMessageBox.warning(self, "Duration Too Long", f"The maximum allowed timer duration is {max_minutes} minutes.\nPlease choose a shorter duration.")
            return

        if duration <= 0:
            QMessageBox.warning(self, "Invalid Time", "The calculated timer duration is in the past. Please choose a future time.")
            return
        
        self.manager.add(name, duration)
        self.name_edit.clear()
        self.name_edit.setFocus()

    def _on_clear_finished(self):
        self.manager.clear_finished()

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
        
        # Update stylesheet in case theme changed
        self.list_widget.setStyleSheet("QListWidget::item { border-bottom: 1px solid " + THEME['progress_border'] + "; }")


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

    def _on_tick(self):
        self.manager.tick_all()

    def _on_manager_updated(self):
        # This is more efficient than rebuilding the whole list on every tick
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            w = self.list_widget.itemWidget(item)
            if isinstance(w, TimerWidget):
                w.update_view()


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
