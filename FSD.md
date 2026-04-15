# Functional Specification Document

## rclone-tray — Windows tray UI for managed rclone mounts

| Field | Value |
|-------|-------|
| **Document ID** | rclone-tray-FSD-v1 |
| **Platform** | Windows 10 / 11 (x64) |
| **Runtime** | Python 3.11+, `pythonw.exe` (no console) |
| **External deps** | rclone, WinFsp, `pystray`, `pillow`, `psutil` |
| **Install location** | `%LOCALAPPDATA%\Programs\rclone-tray\` (per-user, no admin) |
| **Data location** | `%LOCALAPPDATA%\rclone-tray\` (`config.json`, `rclone_tray.log`) |
| **Lock file** | `%TEMP%\rclone_tray.lock` |

---

## 1. Goals

**G1 — Tray-resident mount manager**

Provide a system-tray icon that lets the user mount, unmount, and
remount any number of rclone remotes via right-click menu, without
touching `.cmd` files or a terminal.

**G2 — Per-mount autostart at Windows login**

The user picks which mounts come up at login; preferences persist
across restarts.

**G3 — Self-healing mounts**

Detect mounts that have lost their drive letter or become unresponsive
and re-establish them automatically.

**G4 — Form-based mount and SFTP-remote management**

Add / edit / delete mounts and underlying SFTP remotes through Tk
dialogs, including SSH-key pickers and password obscuration. Non-SFTP
remote types fall back to `rclone config` in a console.

**G5 — Safe, idempotent operation**

A single instance only. Settings are written atomically. Credentials
live exclusively in `rclone.conf`; the tray never stores secrets in
its own files.

---

## 2. Constraints & Assumptions

- rclone reads its configuration from `%APPDATA%\rclone\rclone.conf`.
- WinFsp is the kernel piece that exposes rclone mounts as drive letters.
- The user has already configured at least one rclone remote, **or**
  will use the in-app SFTP editor to create one.
- All mount operations spawn `rclone.exe mount` as a child process.
  Killing that process unmounts the drive (WinFsp tears the volume
  down when the FUSE handle closes).
- Tk is run on a worker thread per dialog open; only one manage-window
  is allowed at a time (`_manager_open` event guards this).

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       pythonw.exe                            │
│                                                              │
│   ┌──────────────┐    ┌──────────────────┐  ┌─────────────┐  │
│   │ pystray icon │◄──►│ menu / callbacks │  │ Tk manager  │  │
│   │ (main loop)  │    └──────────────────┘  │ window      │  │
│   └──────────────┘             │            └─────────────┘  │
│          │                     ▼                    │        │
│          │            ┌──────────────────┐          │        │
│          │            │   mount table    │◄─────────┘        │
│          │            │ (list[Mount])    │                   │
│          │            └──────────────────┘                   │
│          │                     │                             │
│   ┌──────┴────────┐            │  ┌─────────────────────┐    │
│   │ menu_refresh  │            └─►│ subprocess: rclone  │    │
│   │ loop (2 s)    │               │   mount remote: D:  │    │
│   └───────────────┘               └─────────────────────┘    │
│   ┌───────────────┐                          ▲               │
│   │ watchdog loop │                          │               │
│   │ (30 s)        │──────remount on fail─────┘               │
│   └───────────────┘                                          │
└──────────────────────────────────────────────────────────────┘
                          │                ▲
                          ▼                │
                  config.json        rclone.conf
                  rclone_tray.log    (managed via
                                      `rclone config`)
```

---

## 4. Data Model

### 4.1 Mount (in-memory dataclass)

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | Display + key. Unique. |
| `remote` | str | rclone path, e.g. `iotstack:/data` |
| `drive` | str | Single letter A–Z (no colon) |
| `volname` | str | Windows volume label |
| `extra` | list[str] | Extra args passed to `rclone mount` |
| `proc` | Popen \| None | Tracked child process (runtime only) |

### 4.2 `config.json` (persisted)

```json
{
  "autostart":   { "<mount-name>": true, ... },
  "mounts":      [ { name, remote, drive, volname, extra }, ... ],
  "manager_pos": "+x+y"
}
```

### 4.3 `rclone.conf` (managed by rclone)

The tray never edits this file directly. SFTP remotes are written via
`rclone config create|update <name> sftp <key value>... [--obscure]`,
which handles password / passphrase obscuration.

---

## 5. Functional Requirements

### 5.1 Tray and menu

- **FR-TRAY-1** A single tray icon must be created at startup and
  remain until the app exits.
- **FR-TRAY-2** Each configured mount appears as a submenu with:
  *Mounted* (toggle, checked = currently mounted),
  *Autostart + watchdog* (toggle, checked = enabled at boot),
  *Remount now* (one-shot).
- **FR-TRAY-3** Top-level menu also includes *Manage mounts…*,
  *Open log folder*, *Quit (leave mounts up)*, *Quit + unmount all*.
- **FR-TRAY-4** Checkbox state must reflect ground truth within
  ≤ `MENU_REFRESH_INTERVAL` seconds (currently 2 s) even when state
  changes externally (e.g., user runs the legacy `.cmd` script).

### 5.2 Autostart

- **FR-AUTO-1** On launch, every mount with `autostart=true` is
  mounted in parallel daemon threads.
- **FR-AUTO-2** Autostart preference is per-mount and persisted
  immediately on toggle.
- **FR-AUTO-3** A per-user Scheduled Task `RcloneTray` (trigger:
  *At log on*, principal: current user, run level: Limited) launches
  `pythonw.exe rclone_tray.pyw` within seconds of every Windows
  login. Registered by `install.bat` via PowerShell's
  `Register-ScheduledTask` (no admin required). The Startup-folder
  shortcut is intentionally *not* used because Windows throttles
  Startup-folder items by ~2 minutes after logon.

### 5.3 Watchdog

- **FR-WD-1** Every `WATCHDOG_INTERVAL` seconds (30 s) the watchdog
  iterates over autostart-enabled mounts.
- **FR-WD-2** *Drive-missing detection*: `os.path.exists(D:\)` returns
  False → kill any rclone process holding `D:` and remount.
- **FR-WD-3** *Hang detection*: `os.listdir(D:\)` does not return
  within `RESPONSIVE_TIMEOUT` (5 s) → same remediation as FR-WD-2.
- **FR-WD-4** Watchdog must never block the tray UI.

### 5.4 Mount management UI

- **FR-MGR-1** A single Tk window, opened from *Manage mounts…*,
  lists every configured mount (name, remote, drive).
- **FR-MGR-2** *Add* opens a blank form; *Edit* (or double-click)
  opens it pre-filled; *Delete* removes the mount, unmounting first
  if currently mounted.
- **FR-MGR-3** Form fields: Name, Remote (combobox of
  `rclone listremotes`), Drive (dropdown of free letters), Volume
  label, Extra args.
- **FR-MGR-4** Validation: name and drive must be unique among
  configured mounts; drive must be A–Z.
- **FR-MGR-5** If an edit changes drive or remote while the mount is
  active, prompt before unmount/remount.
- **FR-MGR-6** Window position is restored across openings.

### 5.5 SFTP remote editor

- **FR-SFTP-1** *New…* / *Edit…* buttons next to the Remote field
  open an SFTP form: Remote name, Host, Port (default 22), User,
  auth = SSH key (file picker + optional passphrase) or password.
- **FR-SFTP-2** Saving runs `rclone config create|update <name> sftp
  …` with `--obscure` for password/passphrase fields.
- **FR-SFTP-3** *Edit* mode reads existing values via
  `rclone config show <name>`. Password / passphrase fields are
  always blank — leaving them blank preserves the existing value.
- **FR-SFTP-4** A *Delete remote* button removes the remote from
  `rclone.conf` (`rclone config delete`).
- **FR-SFTP-5** If the remote in the dropdown is non-SFTP, *Edit…*
  offers to launch `rclone config` in a real console window for the
  OAuth/wizard flow instead of attempting a form edit.

### 5.6 Single-instance lock

- **FR-LOCK-1** On startup, write the current PID to
  `%TEMP%\rclone_tray.lock`. If the file already names a live python
  process, exit silently.
- **FR-LOCK-2** Remove the lock file on clean exit.

### 5.7 Logging

- **FR-LOG-1** Every mount, unmount, remount, watchdog action, save,
  delete, and unhandled exception is appended to
  `%LOCALAPPDATA%\rclone-tray\rclone_tray.log` with an ISO-style
  timestamp.

### 5.8 Install / data layout (Windows 11 conventions)

- **FR-INST-1** Program files (the `.pyw`, README, LICENSE, FSD,
  uninstall.bat) are copied by `install.bat` to
  `%LOCALAPPDATA%\Programs\rclone-tray\`.
- **FR-INST-2** Runtime data (`config.json`, `rclone_tray.log`) lives
  in a separate directory `%LOCALAPPDATA%\rclone-tray\`, created on
  first launch.
- **FR-INST-3** First launch creates an empty `config.json` in the
  data directory if none exists; users coming from an earlier layout
  copy their old `config.json` in manually. The application performs
  no automatic migration.
- **FR-INST-4** Uninstall removes the Scheduled Task, the Desktop and
  Startup-folder shortcuts (the latter for legacy installs), and the
  program directory. The data directory is intentionally left in
  place; the user removes it manually for a full wipe.

---

## 6. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | Memory footprint of the tray process ≤ 50 MB at idle. |
| NFR-2 | Watchdog and menu-refresh threads must be daemon threads. |
| NFR-3 | All file writes (`config.json`) are full-file rewrites; if a write fails, the next save retries — no partial-state migrations. |
| NFR-4 | The tray must survive Windows resume from sleep. (Watchdog re-establishes mounts that the kernel dropped.) |
| NFR-5 | No secrets in `config.json` or logs. |

---

## 7. Out of Scope (for v1)

- Cloud-remote (Google Drive, S3, OneDrive, …) form editor — the OAuth
  flows are handled by `rclone config` in a console.
- Bandwidth limiting / per-mount rclone flag presets beyond the
  free-text *Extra args* field.
- Linux / macOS support.
- Encrypted `config.json`. (`rclone.conf` master-password
  encryption is supported by rclone itself if the user opts in;
  the tray does not manage that secret.)
- Localisation — English UI strings only.

---

## 8. Security Considerations

- **S1** `config.json` contains mount metadata (remote names, drive
  letters) but no credentials. Safe to back up.
- **S2** All credentials live in `%APPDATA%\rclone\rclone.conf`. The
  tray invokes rclone's `--obscure` flag whenever it writes a password
  or key passphrase. The user is responsible for the security of
  `rclone.conf` and any SSH key files.
- **S3** Mount processes run as the current user; no elevation is
  required or requested. WinFsp itself runs as a system service,
  installed once.
- **S4** The single-instance lock is not a security boundary — it
  prevents accidental duplicates, not malicious co-execution.

---

## 9. Tunables

| Constant | Default | Where |
|----------|---------|-------|
| `WATCHDOG_INTERVAL` | 30 s | top of `rclone_tray.pyw` |
| `MENU_REFRESH_INTERVAL` | 2 s | top of `rclone_tray.pyw` |
| `RESPONSIVE_TIMEOUT` | 5 s | top of `rclone_tray.pyw` |
| `COMMON_ARGS` | `--vfs-cache-mode writes --network-mode --links` | top of `rclone_tray.pyw` |

---

## 10. Future Work

- Toast notifications on remount events.
- Per-mount custom watchdog interval / responsiveness threshold.
- Optional `rclone serve` integration (HTTP / WebDAV exposure of
  selected mounts).
- A small status panel in the manage window showing recent log lines
  and last watchdog action per mount.
