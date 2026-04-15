# rclone-tray

[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![rclone](https://img.shields.io/badge/rclone-required-00ADD8?logo=rclone&logoColor=white)](https://rclone.org/)
[![WinFsp](https://img.shields.io/badge/WinFsp-required-444?logo=windows&logoColor=white)](https://winfsp.dev/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#-license)

A small Windows system-tray UI for [rclone](https://rclone.org/) mounts.
Pick which remotes mount at login, get them auto-reconnected when they
drop or hang, and add / edit / delete mounts and SFTP remotes from a
graphical form — no `rclone config` wizardry required for the common case.

---

## ✨ Features

- 🖱️ **Tray icon** with a per-mount submenu (Mounted / Autostart + watchdog / Remount now).
- 🚀 **Autostart at Windows login** — toggle per mount, persisted to `config.json`.
- 🐕 **Watchdog** every 30 s: if an autostart mount has lost its drive letter
  *or* `os.listdir` doesn't return within 5 s (mount hung), the rclone
  process is killed and the mount is re-established.
- 🗂️ **Manage mounts window** with Add / Edit / Delete and double-click-to-edit.
- 🔐 **SFTP remote editor** built into the Add/Edit form — host, port, user,
  SSH key (file picker, optional passphrase) or password. Writes
  `rclone.conf` via `rclone config create / update --obscure`.
  Non-SFTP remote types (Google Drive, S3, …) hand off to
  `rclone config` in a console for the OAuth wizard.
- 🛡️ **Single-instance lock** so double-clicking the shortcut doesn't pile up
  zombie processes.

---

## 📦 Requirements

| Component | Notes |
|-----------|-------|
| Windows 10 / 11 | x64 |
| Python 3.11+ | `pythonw.exe` is used so no console window appears |
| [rclone](https://rclone.org/downloads/) | On `PATH`, or installed via `winget install Rclone.Rclone` |
| [WinFsp](https://winfsp.dev/) | rclone needs this for `mount` |

Python deps (`pystray`, `pillow`, `psutil`) are installed by `install.bat`.

---

## 🚀 Install

```cmd
git clone https://github.com/SensorsIot/rclone-tray.git
cd rclone-tray
install.bat
```

`install.bat` does three things:

1. `pip install --user pystray pillow psutil`
2. Creates a **Desktop** shortcut → `Rclone Tray.lnk`
3. Creates a **Startup-folder** shortcut so the tray launches at every login

---

## 🛠️ Usage

1. Launch **Rclone Tray** from the Desktop.
2. Right-click the tray icon (you may need to drag it out of the
   Windows 11 overflow `^` area to keep it visible).
3. **Manage mounts… → Add** to define a mount. The *Remote* field has
   **New…** / **Edit…** buttons that pop up an SFTP-remote form so you
   can configure the underlying rclone remote without leaving the UI.
4. For each mount you want at boot, toggle **Autostart + watchdog**.

### Tray menu

```
🖥️  Actual    (A:)        ▸  Mounted ✓
                              Autostart + watchdog ✓
                              Remount now
🖥️  IOTstack  (I:)        ▸  …
─────────────────────────
    Manage mounts...
    Open log folder
    Quit (leave mounts up)
    Quit + unmount all
```

---

## 📁 Files

| File | Purpose |
|------|---------|
| `rclone_tray.pyw` | The application |
| `install.bat` | One-shot setup (deps + shortcuts) |
| `config.json` | Generated. Mounts, autostart toggles, last window position |
| `rclone_tray.log` | Generated. Activity / errors |
| `FSD.md` | Functional spec |

---

## 📝 License

MIT
