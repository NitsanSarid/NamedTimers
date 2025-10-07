# Named Timers

A lightweight, easy-to-use desktop app built with **Python + PySide6** for managing multiple 40-minute countdown timers — each with a custom name and clear visual indicators.

---

## 🕒 Features

- **Simple creation** — type a name and press Enter to start a new 40-minute timer.
- **Multiple timers** — run as many as you like in parallel.
- **Color phases:**
  - 🟩 Green — first third (40→26:40)
  - 🟧 Orange — second third (26:39→13:20)
  - 🟥 Red — last third (13:19→0:00)
- **Finished timers** turn gray (“Done”).
- **Pause / Resume** each timer (icon toggles play/pause).
- **Delete confirmation** if the timer isn’t done.
- **Clear Finished** button for convenience.
- **Large, clear UI** for visibility at a glance.

---

## ⚙️ Requirements

- Python 3.10+  
- The following packages (see `requirements.txt`):
  ```bash
  PySide6>=6.7
  pyinstaller>=6.0
  ```

Install them with:
```bash
pip install -r requirements.txt
```

---

## ▶️ Run from Source

```bash
python main.py
```

---

## 🏗️ Build to EXE (Windows)

To make a single executable for non-technical users:

```bash
pyinstaller --onefile --windowed --name "NamedTimers" --icon app.ico --add-data "app.ico;." main.py
```

After the build completes, you’ll find your app under:
```
dist/NamedTimers.exe
```

You can then share that `.exe` file directly.

For better UX (optional), create an installer using **Inno Setup**.

---

## 📦 Folder Structure

```
NamedTimers/
├── main.py
├── requirements.txt
└── README.md
```



