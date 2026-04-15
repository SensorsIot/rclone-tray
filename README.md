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
| Python 3.11+ | `pythonw.exe` is used so no console window appears — **must be pre-installed** |
| [rclone](https://rclone.org/downloads/) | Installed automatically by `install.bat` if missing |
| [WinFsp](https://winfsp.dev/) | Installed automatically by `install.bat` if missing (UAC prompt) |

Python itself is the only prerequisite you need to install yourself.
Python deps (`pystray`, `pillow`, `psutil`), rclone, and WinFsp are all
handled by `install.bat`.

---

## 🚀 Install

```cmd
git clone https://github.com/SensorsIot/rclone-tray.git
cd rclone-tray
install.bat
```

`install.bat` does five things:

1. Runs `install-prereqs.ps1`, which installs **WinFsp** (latest MSI
   from GitHub, elevated via UAC) and **rclone** (official zip,
   extracted to `%LOCALAPPDATA%\Programs\rclone` and added to user
   PATH) — each only if not already present
2. `pip install --user pystray pillow psutil`
3. Copies the program to `%LOCALAPPDATA%\Programs\rclone-tray\`
4. Creates the data directory `%LOCALAPPDATA%\rclone-tray\`
5. Creates a **Desktop** shortcut (OneDrive-redirected Desktops are
   resolved automatically) and registers a per-user **Scheduled
   Task** `RcloneTray` (trigger: *At log on*) so the tray launches
   within seconds of every login — without the multi-minute throttle
   that affects Startup-folder items

To remove: run `uninstall.bat` (also placed in the install dir).
Your mount config in the data directory is kept — delete it
manually if you want a full wipe.

---

## 🛠️ Usage

1. Launch **Rclone Tray** from the Desktop. On first run (no mounts
   *or* no remotes yet) the **Manage Mounts** window opens
   automatically.
2. Right-click the tray icon (you may need to drag it out of the
   Windows 11 overflow `^` area to keep it visible).
3. **Manage mounts… → Add** to define a mount. The *Remote* field has
   **New…** / **Edit…** buttons that pop up an SFTP-remote form so you
   can configure the underlying rclone remote without leaving the UI.
4. For each mount you want at boot, toggle **Autostart + watchdog**.

### Drop-in config

If you already know your SFTP servers you can skip the dialog and
pre-populate `%LOCALAPPDATA%\rclone-tray\config.json` directly. Each
mount may carry an optional `conn` block with the non-secret fields
(host / port / user / key_file) — the tray will create the
corresponding `rclone.conf` section on next startup:

```json
{
  "autostart": { "IOTstack": true },
  "mounts": [
    {
      "name": "IOTstack", "remote": "iotstack:/", "drive": "I",
      "volname": "IOTstack", "extra": [],
      "conn": {
        "type": "sftp", "host": "192.168.1.10", "port": 22,
        "user": "pi", "key_file": "C:\\Users\\me\\.ssh\\id_ed25519"
      }
    }
  ]
}
```

Passwords and key passphrases are never read from `config.json` —
enter those once via the SFTP dialog; rclone stores them obscured
in `rclone.conf`.

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
| `install.bat` | Installer (installs prereqs, copies to `%LOCALAPPDATA%\Programs\rclone-tray\`, creates shortcuts) |
| `install-prereqs.ps1` | Helper called by the installer to install WinFsp and rclone if missing |
| `uninstall.bat` | Removes program + shortcuts (keeps your data) |
| `%LOCALAPPDATA%\rclone-tray\config.json` | Generated. Mounts, autostart toggles, last window position |
| `%LOCALAPPDATA%\rclone-tray\rclone_tray.log` | Generated. Activity / errors |
| `FSD.md` | Functional spec |

---

## 📝 License

MIT
