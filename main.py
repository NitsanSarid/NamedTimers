import sys
import time
from dataclasses import dataclass
from typing import Dict, List

from PySide6.QtCore import Qt, QTimer, QSize, Signal, QObject
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QListWidget, QListWidgetItem, QLabel, QProgressBar, QToolButton,
    QCheckBox, QStatusBar, QStyle, QMessageBox
)

# ========= GLOBALS / CONFIG =========
APP_NAME = "Named Timers"
VERSION = "1.0.0"

# Single source of truth for duration (change this to adjust all timers globally)
BASE_DURATION_SEC = 40 * 60  # e.g., set to 25*60 for “pomodoro style”

# Palette
COLOR_GREEN = "#21A179"
COLOR_ORANGE = "#F39C12"
COLOR_RED = "#E74C3C"
COLOR_FINISHED_BG = "#ECECEC"
COLOR_TEXT = "#D1D0D0"
COLOR_MUTED = "#7F8C8D"

# UI sizing
ROW_HEIGHT = 88
LEFT_STRIP_PX = 10
PROGRESS_HEIGHT = 18
BASE_FONT_PT = 12          # main font size
TITLE_FONT_PT = 14         # timer name
TIME_FONT_PT = 16          # remaining time

def hex_to_rgb(hex_str: str):
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))

def rgba_tint(hex_str: str, alpha: float = 0.22) -> str:
    """Return an rgba() with a gentle alpha tint for backgrounds."""
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
        r = self.remaining_sec
        if r <= 0:
            return COLOR_MUTED
        first_cut = (2 * BASE_DURATION_SEC) // 3
        second_cut = BASE_DURATION_SEC // 3
        if r > first_cut:          # first third remaining
            return COLOR_GREEN
        elif r > second_cut:       # second third remaining
            return COLOR_ORANGE
        else:                      # last third
            return COLOR_RED

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
    state_changed = Signal()  # for resort if state toggles

    def __init__(self, model: TimerState, parent=None):
        super().__init__(parent)
        self.model = model
        self._build_ui()
        self.update_view()

    def _build_ui(self):
        # Labels over *app background* (matches window background)
        self.name_lbl = QLabel(self.model.name)
        self.name_lbl.setStyleSheet(f"""
            QLabel {{
                font-weight: 700;
                font-size: {TITLE_FONT_PT}pt;
                background: palette(window);
                padding: 2px 6px;
                border-radius: 4px;
            }}
        """)

        self.time_lbl = QLabel()
        self.time_lbl.setMinimumWidth(80)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_lbl.setStyleSheet(f"""
            QLabel {{
                font-family: Consolas, 'Cascadia Mono', 'Courier New', monospace;
                font-size: {TIME_FONT_PT}pt;
                background: palette(window);
                padding: 2px 6px;
                border-radius: 4px;
            }}
        """)

        # Progress bar (secondary to the big color background/strip)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(PROGRESS_HEIGHT)
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: #f2f2f2;
                border: 1px solid #e0e0e0;
                border-radius: 9px;
            }
            QProgressBar::chunk {
                border-radius: 9px;
            }
        """)

        self.pause_btn = QToolButton()
        self.pause_btn.setToolTip("Pause / Resume")
        # icon set in update_view() based on running state
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
        # Base style; actual color/tint set in update_view()
        self.setStyleSheet(f"color:{COLOR_TEXT}; border-left: {LEFT_STRIP_PX}px solid transparent;")
        self.setMinimumHeight(ROW_HEIGHT)

    def _toggle_pause(self):
        if self.model.is_finished():
            return
        self.model.running = not self.model.running
        self.state_changed.emit()
        self.update_view()  # ensures icon flips immediately

    def update_view(self):
        self.name_lbl.setText(self.model.name)
        self.time_lbl.setText(self.model.display_mmss())

        pct = int(self.model.progress01() * 1000)
        self.progress.setValue(pct)

        # Pause/Play icon reflects state (Requirement #1)
        if self.model.running and not self.model.is_finished():
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        else:
            self.pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

        color = self.model.color_for_remaining()
        # Progress chunk color
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: #f2f2f2;
                border: 1px solid #e0e0e0;
                border-radius: 9px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 9px;
            }}
        """)

        # Big visible signal: left color strip + tinted background
        if self.model.is_finished():
            self.pause_btn.setEnabled(False)
            self.setStyleSheet(
                f"background:{COLOR_FINISHED_BG}; color:{COLOR_TEXT}; border-left: {LEFT_STRIP_PX}px solid {COLOR_MUTED};"
            )
        else:
            self.pause_btn.setEnabled(True)
            tint = rgba_tint(color, 0.22)
            self.setStyleSheet(
                f"background:{tint}; color:{COLOR_TEXT}; border-left: {LEFT_STRIP_PX}px solid {color};"
            )
        # keep minimum height
        self.setMinimumHeight(ROW_HEIGHT)

# ========= MAIN WINDOW =========
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — v{VERSION} — {BASE_DURATION_SEC//60}min")
        self.manager = TimerManager()
        self._build_ui()
        self._connect_signals()

        # Global tick (1s)
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._on_tick)
        self.ticker.start()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        # Global app stylesheet for bigger, readable UI (Requirement #3)
        self.setStyleSheet(f"""
            QWidget {{
                font-size: {BASE_FONT_PT}pt;
            }}
            QLineEdit {{
                padding: 6px 8px;
            }}
            QPushButton {{
                padding: 8px 12px;
            }}
            QToolButton {{
                padding: 6px;
            }}
            QListWidget {{
                padding: 6px;
            }}
            QStatusBar QLabel {{
                font-size: {BASE_FONT_PT}pt;
            }}
        """)

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
        self.list_widget.setSpacing(8)

        layout = QVBoxLayout(central)
        layout.addLayout(top_row)
        layout.addWidget(self.list_widget, 1)

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_active_lbl = QLabel("")
        self.status_finished_lbl = QLabel("")
        sb.addPermanentWidget(self.status_active_lbl)
        sb.addPermanentWidget(self.status_finished_lbl)
        self._update_status_counts()

        # Bigger default window
        self.resize(650, 550)

    def _connect_signals(self):
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.name_edit.returnPressed.connect(self._on_add_clicked)
        self.clear_finished_btn.clicked.connect(self._on_clear_finished)
        self.group_active_top_chk.toggled.connect(lambda _: self._rebuild_list())

        self.manager.updated.connect(self._on_manager_updated)
        self.manager.structure_changed.connect(self._rebuild_list)

    # ----- actions -----
    def _on_add_clicked(self):
        base = self.name_edit.text().strip() or "Timer"
        st = self.manager.add(base)
        self.name_edit.clear()
        self.name_edit.setFocus()
        self._add_list_item(st)
        self._update_status_counts()

    def _on_clear_finished(self):
        self.manager.clear_finished()
        self._update_status_counts()

    def _on_tick(self):
        self.manager.tick_all()

    def _on_manager_updated(self):
        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if isinstance(w, TimerWidget):
                w.update_view()
        self._update_status_counts()
        if self.group_active_top_chk.isChecked():
            self._rebuild_list()

    # ----- list handling -----
    def _add_list_item(self, st: TimerState):
        item = QListWidgetItem()
        widget = TimerWidget(st)
        widget.request_remove.connect(self._on_remove_requested)
        widget.state_changed.connect(lambda: self._on_manager_updated())
        item.setSizeHint(QSize(560, ROW_HEIGHT))
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)
        widget.update_view()
        if self.group_active_top_chk.isChecked():
            self._rebuild_list()

    def _rebuild_list(self):
        sel_names = set()
        for idx in self.list_widget.selectedIndexes():
            w = self.list_widget.itemWidget(self.list_widget.item(idx.row()))
            if isinstance(w, TimerWidget):
                sel_names.add(w.model.name)

        self.list_widget.clear()

        items = self.manager.all_items()
        if self.group_active_top_chk.isChecked():
            items.sort(key=lambda t: (t.is_finished(), t.remaining_sec, t.name.lower()))
        else:
            items.sort(key=lambda t: (t.name.lower(),))

        for st in items:
            self._add_list_item_no_rebuild(st)

        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if isinstance(w, TimerWidget) and w.model.name in sel_names:
                self.list_widget.item(i).setSelected(True)

        self._update_status_counts()

    def _add_list_item_no_rebuild(self, st: TimerState):
        item = QListWidgetItem()
        widget = TimerWidget(st)
        widget.request_remove.connect(self._on_remove_requested)
        widget.state_changed.connect(lambda: self._on_manager_updated())
        item.setSizeHint(QSize(560, ROW_HEIGHT))
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)
        widget.update_view()

    def _on_remove_requested(self, name: str):
        """Ask for confirmation when deleting a non-finished timer (Requirement #2)."""
        st = self.manager.items.get(name)
        if st and not st.is_finished():
            # Show remaining time in message
            remaining = st.display_mmss()
            resp = QMessageBox.question(
                self,
                "Remove running timer?",
                f"Timer \"{name}\" is still running ({remaining}).\n\nDo you want to delete it?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        self.manager.remove(name)
        self._rebuild_list()

    # ----- status -----
    def _update_status_counts(self):
        active, finished = self.manager.counts()
        self.status_active_lbl.setText(f"Active: {active}")
        self.status_finished_lbl.setText(f"Finished: {finished}")

# ========= ENTRY POINT =========
def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
