"""Rclone mount manager — system tray UI with autostart, watchdog, and management."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import string
import subprocess
import tempfile
import threading
import time
import traceback
import winreg
from dataclasses import dataclass, field
from pathlib import Path

import psutil
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

LOCK_FILE = Path(tempfile.gettempdir()) / "rclone_tray.lock"

def _find_rclone() -> str:
    found = shutil.which("rclone") or shutil.which("rclone.exe")
    if found:
        return found
    pkg = Path(os.environ.get("LOCALAPPDATA", "")) / \
        "Microsoft" / "WinGet" / "Packages"
    if pkg.is_dir():
        for d in pkg.glob("Rclone.Rclone_*/rclone-*-windows-amd64/rclone.exe"):
            return str(d)
    return "rclone.exe"


RCLONE_EXE = _find_rclone()
SCRIPT_DIR = Path(__file__).parent

# Per-Windows-11 convention: program files in %LOCALAPPDATA%\Programs\<app>,
# user data in %LOCALAPPDATA%\<app>.
APPDATA_LOCAL = Path(os.environ.get("LOCALAPPDATA",
                                    str(Path.home() / "AppData" / "Local")))
DATA_DIR = APPDATA_LOCAL / "rclone-tray"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "rclone_tray.log"

WATCHDOG_INTERVAL = 30
MENU_REFRESH_INTERVAL = 2
RESPONSIVE_TIMEOUT = 5
COMMON_ARGS = ["--vfs-cache-mode", "writes", "--network-mode", "--links"]
CREATE_NO_WINDOW = 0x08000000

DEFAULT_MOUNTS: list[dict] = []


@dataclass
class Mount:
    name: str
    remote: str
    drive: str
    volname: str
    extra: list[str] = field(default_factory=list)
    proc: subprocess.Popen | None = None

    @property
    def drive_path(self) -> str:
        return f"{self.drive}:\\"

    def to_dict(self) -> dict:
        return {"name": self.name, "remote": self.remote, "drive": self.drive,
                "volname": self.volname, "extra": list(self.extra)}

    @classmethod
    def from_dict(cls, d: dict) -> "Mount":
        return cls(name=d["name"], remote=d["remote"], drive=d["drive"].upper(),
                   volname=d.get("volname", d["name"]),
                   extra=list(d.get("extra", [])))


# ---------- config + mount list ----------

def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {"autostart": {}, "mounts": []}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


config = load_config()
config.setdefault("autostart", {})
if "mounts" not in config or not config["mounts"]:
    config["mounts"] = DEFAULT_MOUNTS
    save_config(config)

mounts: list[Mount] = [Mount.from_dict(d) for d in config["mounts"]]
mounts_lock = threading.Lock()
state_lock = threading.Lock()


def persist_mounts() -> None:
    config["mounts"] = [m.to_dict() for m in mounts]
    save_config(config)


def find_mount(name: str) -> Mount | None:
    for m in mounts:
        if m.name == name:
            return m
    return None


# ---------- state checks ----------

def is_autostart(m: Mount) -> bool:
    return bool(config["autostart"].get(m.name, False))


def set_autostart(m: Mount, value: bool) -> None:
    config["autostart"][m.name] = value
    save_config(config)


def is_drive_present(m: Mount) -> bool:
    return os.path.exists(m.drive_path)


def is_responsive(m: Mount) -> bool:
    if not is_drive_present(m):
        return False
    result = {"ok": False}

    def probe() -> None:
        try:
            os.listdir(m.drive_path)
            result["ok"] = True
        except OSError:
            result["ok"] = False

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(RESPONSIVE_TIMEOUT)
    return result["ok"] if not t.is_alive() else False


def is_mounted(m: Mount) -> bool:
    if m.proc is not None and m.proc.poll() is None and is_drive_present(m):
        return True
    return is_drive_present(m)


# ---------- mount/unmount ----------

def kill_rclone_for(m: Mount) -> None:
    drive = m.drive
    if m.proc is not None:
        try:
            m.proc.terminate()
            try:
                m.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                m.proc.kill()
        except OSError:
            pass
        m.proc = None

    drive_arg = f"{drive}:"
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() != "rclone.exe":
                continue
            cmd = p.info["cmdline"] or []
            if drive_arg in cmd:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for _ in range(20):
        if not is_drive_present(m):
            break
        time.sleep(0.5)

    _net_use_delete(m, drive)
    _purge_mountpoints2(m)
    _notify_shell_drive_removed(m, drive)


def _net_use_delete(m: Mount, drive: str) -> None:
    """Force-remove the WinFsp.Np network-drive registration."""
    try:
        subprocess.run(
            ["net", "use", f"{drive}:", "/delete", "/yes"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _purge_mountpoints2(m: Mount) -> None:
    """Remove HKCU\\...\\MountPoints2\\##server#<volname>. Without this,
    Explorer remembers past drive letters used for the volume and shows
    a 'red X' ghost when the letter changes."""
    if not m.volname:
        return
    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2"
    name = f"##server#{m.volname}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base, 0,
                            winreg.KEY_ALL_ACCESS) as parent:
            try:
                _registry_delete_tree(parent, name)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def _registry_delete_tree(parent_key, name: str) -> None:
    with winreg.OpenKey(parent_key, name, 0, winreg.KEY_ALL_ACCESS) as sub:
        children = []
        i = 0
        while True:
            try:
                children.append(winreg.EnumKey(sub, i))
                i += 1
            except OSError:
                break
        for c in children:
            _registry_delete_tree(sub, c)
    winreg.DeleteKey(parent_key, name)


def _notify_shell_drive_removed(m: Mount, drive: str) -> None:
    """Tell Explorer the drive letter is gone, to clear any cached
    'red X' entry from This PC."""
    try:
        SHCNE_DRIVEREMOVED = 0x00000080
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_PATHW = 0x0005
        path = ctypes.c_wchar_p(f"{drive}:\\")
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_DRIVEREMOVED, SHCNF_PATHW, path, None)
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_ASSOCCHANGED, 0, None, None)
    except Exception:
        pass


def _rclone_log_path(m: Mount) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in m.name)
    return DATA_DIR / f"rclone-{safe}.log"


def _verify_mount(m: Mount) -> None:
    """Run a few seconds after Popen to detect an rclone process that
    died on startup or never produced a drive letter."""
    time.sleep(4)
    proc = m.proc
    if proc is None:
        return
    rc = proc.poll()
    if rc is not None:
        log(f"{m.name}: rclone exited rc={rc} — see {_rclone_log_path(m).name}")
        return
    if not is_drive_present(m):
        log(f"{m.name}: rclone alive but {m.drive}: not present after 4s "
            f"— see {_rclone_log_path(m).name}")


def mount(m: Mount) -> None:
    with state_lock:
        if is_mounted(m):
            log(f"{m.name}: already mounted")
            return
        cmd = [RCLONE_EXE, "mount", m.remote, f"{m.drive}:",
               *COMMON_ARGS, "--volname", m.volname, *m.extra]
        log(f"{m.name}: mounting → {' '.join(cmd)}")
        try:
            errlog = open(_rclone_log_path(m), "ab")
            errlog.write(
                f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"mount {m.remote} -> {m.drive}: ===\n".encode())
            errlog.flush()
            m.proc = subprocess.Popen(
                cmd, creationflags=CREATE_NO_WINDOW,
                stdout=errlog, stderr=errlog)
        except OSError as e:
            log(f"{m.name}: mount failed: {e}")
            return
    threading.Thread(target=_verify_mount, args=(m,), daemon=True).start()


def unmount(m: Mount) -> None:
    with state_lock:
        log(f"{m.name}: unmounting")
        kill_rclone_for(m)


def remount(m: Mount) -> None:
    log(f"{m.name}: remount")
    unmount(m)
    time.sleep(1)
    mount(m)


def toggle_mount(m: Mount) -> None:
    if is_mounted(m):
        unmount(m)
    else:
        mount(m)


def toggle_autostart(m: Mount) -> None:
    set_autostart(m, not is_autostart(m))


# ---------- background loops ----------

def watchdog_loop(stop: threading.Event) -> None:
    while not stop.wait(WATCHDOG_INTERVAL):
        for m in list(mounts):
            if not is_autostart(m):
                continue
            if not is_drive_present(m):
                log(f"{m.name}: drive missing — remounting")
                remount(m)
                continue
            if not is_responsive(m):
                log(f"{m.name}: unresponsive — remounting")
                remount(m)


def menu_refresh_loop(stop: threading.Event, icon: Icon) -> None:
    while not stop.wait(MENU_REFRESH_INTERVAL):
        try:
            icon.update_menu()
        except Exception:
            pass


# ---------- rclone helpers ----------

def get_rclone_remotes() -> list[str]:
    try:
        r = subprocess.run([RCLONE_EXE, "listremotes"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


def get_rclone_remotes_with_types() -> dict[str, str]:
    """Return {bare_name: type} for each configured remote (no trailing ':')."""
    try:
        r = subprocess.run([RCLONE_EXE, "listremotes", "--long"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        out: dict[str, str] = {}
        for line in r.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                name = parts[0].rstrip(":")
                out[name] = parts[1]
        return out
    except (OSError, subprocess.TimeoutExpired):
        return {}


def rclone_config_show(name: str) -> dict:
    try:
        r = subprocess.run([RCLONE_EXE, "config", "show", name],
                           capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        cfg: dict = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("[") or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
        return cfg
    except (OSError, subprocess.TimeoutExpired):
        return {}


def rclone_config_save_sftp(name: str, host: str, port: str, user: str,
                            key_file: str = "", key_pass: str = "",
                            password: str = "",
                            update: bool = False) -> tuple[bool, str]:
    cmd = "update" if update else "create"
    args = [RCLONE_EXE, "config", cmd, name]
    if not update:
        args.append("sftp")
    args += ["host", host, "user", user, "port", str(port),
             "shell_type", "unix",
             "md5sum_command", "md5sum",
             "sha1sum_command", "sha1sum"]
    if key_file:
        args += ["key_file", key_file]
    if password:
        args += ["pass", password]
    if key_pass:
        args += ["key_file_pass", key_pass]
    if password or key_pass:
        args += ["--obscure"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()
        return True, ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def rclone_config_delete_remote(name: str) -> tuple[bool, str]:
    try:
        r = subprocess.run([RCLONE_EXE, "config", "delete", name],
                           capture_output=True, text=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()
        return True, ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def launch_rclone_config_console() -> None:
    """Open `rclone config` in a real console window for non-SFTP wizards."""
    try:
        subprocess.Popen(["cmd.exe", "/c", "start", "rclone config",
                          RCLONE_EXE, "config"], shell=False)
    except OSError as e:
        log(f"failed to launch rclone config: {e}")


def used_drive_letters() -> set[str]:
    used = set()
    for part in psutil.disk_partitions(all=True):
        d = part.device[:1].upper()
        if d in string.ascii_uppercase:
            used.add(d)
    for m in mounts:
        used.add(m.drive.upper())
    return used


# ---------- menu ----------

def make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((6, 18, 58, 54), fill=(60, 120, 200, 255))
    d.rectangle((6, 14, 26, 22), fill=(60, 120, 200, 255))
    d.ellipse((24, 26, 40, 42), fill=(255, 255, 255, 255))
    return img


def build_menu(icon: Icon) -> Menu:
    def make_toggle_mount(mo):
        def cb(_i, _it):
            toggle_mount(mo); icon.update_menu()
        return cb

    def make_toggle_autostart(mo):
        def cb(_i, _it):
            toggle_autostart(mo); icon.update_menu()
        return cb

    def make_remount(mo):
        def cb(_i, _it):
            threading.Thread(target=remount, args=(mo,), daemon=True).start()
        return cb

    def make_is_mounted(mo):
        def cb(_it): return is_mounted(mo)
        return cb

    def make_is_autostart(mo):
        def cb(_it): return is_autostart(mo)
        return cb

    items = []
    for m in mounts:
        items.append(MenuItem(
            f"{m.name}  ({m.drive}:)",
            Menu(
                MenuItem("Mounted", make_toggle_mount(m),
                         checked=make_is_mounted(m)),
                MenuItem("Autostart + watchdog", make_toggle_autostart(m),
                         checked=make_is_autostart(m)),
                MenuItem("Remount now", make_remount(m)),
            ),
        ))
    items.append(Menu.SEPARATOR)
    items.append(MenuItem("Manage mounts...",
                          lambda _i, _it: open_manager(icon)))
    items.append(MenuItem("Open data folder",
                          lambda _i, _it: os.startfile(DATA_DIR)))
    items.append(MenuItem("Quit (leave mounts up)",
                          lambda _i, _it: icon.stop()))
    items.append(MenuItem("Quit + unmount all",
                          lambda _i, _it: quit_unmount(icon)))
    return Menu(*items)


def quit_unmount(icon: Icon) -> None:
    for m in list(mounts):
        unmount(m)
    icon.stop()


def rebuild_menu(icon: Icon) -> None:
    icon.menu = build_menu(icon)
    icon.update_menu()


# ---------- management UI (Tkinter) ----------

_manager_open = threading.Event()


def _center_geometry(width: int, height: int, parent=None) -> str:
    """Build a Tk geometry string '+x+y' clamped to the visible screen."""
    if parent is not None:
        try:
            parent.update_idletasks()
            sw = parent.winfo_screenwidth()
            sh = parent.winfo_screenheight()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width() or width
            ph = parent.winfo_height() or height
            x = px + max(0, (pw - width) // 2)
            y = py + max(0, (ph - height) // 2)
        except Exception:
            sw, sh, x, y = 1920, 1080, 100, 100
    else:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
        except Exception:
            sw, sh = 1920, 1080
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
    x = max(0, min(x, sw - width))
    y = max(0, min(y, sh - height))
    return f"+{x}+{y}"


def open_manager(icon: Icon) -> None:
    if _manager_open.is_set():
        return
    _manager_open.set()
    threading.Thread(target=_manager_thread, args=(icon,), daemon=True).start()


def _manager_thread(icon: Icon) -> None:
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        log("tkinter not available")
        _manager_open.clear()
        return
    try:
        _manager_main(icon, tk, ttk, messagebox)
    except Exception:
        log("manage window crashed:\n" + traceback.format_exc())
    finally:
        _manager_open.clear()


def _manager_main(icon, tk, ttk, messagebox) -> None:

    root = tk.Tk()
    root.title("Rclone Mounts — Manage")
    root.withdraw()
    width, height = 520, 340
    pos = config.get("manager_pos", "")
    if not pos:
        pos = _center_geometry(width, height)
    root.geometry(f"{width}x{height}{pos}")

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)

    cols = ("name", "remote", "drive")
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=10)
    for c, w in zip(cols, (130, 280, 60)):
        tree.heading(c, text=c.capitalize())
        tree.column(c, width=w, anchor="w")
    tree.pack(side="left", fill="both", expand=True)

    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    sb.pack(side="left", fill="y")
    tree.configure(yscrollcommand=sb.set)

    def refresh_tree():
        tree.delete(*tree.get_children())
        with mounts_lock:
            for m in mounts:
                tree.insert("", "end", iid=m.name,
                            values=(m.name, m.remote, f"{m.drive}:"))

    def selected_mount() -> Mount | None:
        sel = tree.selection()
        if not sel:
            return None
        with mounts_lock:
            return find_mount(sel[0])

    def on_add():
        edit_mount_dialog(root, None, on_saved)

    def on_edit():
        m = selected_mount()
        if not m:
            messagebox.showinfo("Edit", "Select a mount first.", parent=root)
            return
        edit_mount_dialog(root, m, on_saved)

    def on_delete():
        m = selected_mount()
        if not m:
            messagebox.showinfo("Delete", "Select a mount first.", parent=root)
            return
        if not messagebox.askyesno("Delete",
                f"Remove '{m.name}'? This will unmount it if currently mounted.",
                parent=root):
            return
        if is_mounted(m):
            unmount(m)
        with mounts_lock:
            mounts.remove(m)
            config["autostart"].pop(m.name, None)
            persist_mounts()
        rebuild_menu(icon)
        refresh_tree()

    def on_saved():
        rebuild_menu(icon)
        refresh_tree()

    def remember_pos_and_close():
        try:
            geo = root.geometry()  # "WxH+X+Y" — already excludes title bar
            if "+" in geo:
                config["manager_pos"] = "+" + geo.split("+", 1)[1]
                save_config(config)
        except Exception:
            pass
        root.destroy()

    btns = ttk.Frame(root, padding=(10, 0, 10, 10))
    btns.pack(fill="x")
    ttk.Button(btns, text="Add",    command=on_add).pack(side="left")
    ttk.Button(btns, text="Edit",   command=on_edit).pack(side="left", padx=4)
    ttk.Button(btns, text="Delete", command=on_delete).pack(side="left")
    ttk.Button(btns, text="Close",  command=remember_pos_and_close).pack(side="right")

    tree.bind("<Double-Button-1>", lambda _e: on_edit())

    refresh_tree()
    root.protocol("WM_DELETE_WINDOW", remember_pos_and_close)
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.focus_force()
    root.mainloop()


def edit_mount_dialog(parent, existing: Mount | None, on_saved) -> None:
    import tkinter as tk
    from tkinter import ttk, messagebox

    dlg = tk.Toplevel(parent)
    dlg.title("Edit mount" if existing else "Add mount")
    dlg.transient(parent)
    dlg.grab_set()
    dlg.resizable(False, False)
    parent.update_idletasks()
    dlg.geometry(f"+{parent.winfo_rootx() + 30}+{parent.winfo_rooty() + 30}")

    pad = {"padx": 8, "pady": 4}
    frm = ttk.Frame(dlg, padding=10)
    frm.pack(fill="both", expand=True)

    name_var    = tk.StringVar(value=existing.name if existing else "")
    remote_var  = tk.StringVar(value=existing.remote if existing else "")
    drive_var   = tk.StringVar(value=(existing.drive if existing else "").upper())
    volname_var = tk.StringVar(value=existing.volname if existing else "")
    extra_var   = tk.StringVar(value=" ".join(existing.extra) if existing else "")

    ttk.Label(frm, text="Display name:").grid(row=0, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=name_var, width=36).grid(row=0, column=1, **pad)

    ttk.Label(frm, text="Remote (e.g. iotstack:/path):").grid(row=1, column=0, sticky="e", **pad)
    remote_row = ttk.Frame(frm)
    remote_row.grid(row=1, column=1, sticky="w", **pad)
    remote_box = ttk.Combobox(remote_row, textvariable=remote_var,
                              values=get_rclone_remotes(), width=26)
    remote_box.pack(side="left")

    def refresh_remotes(select: str | None = None):
        remote_box["values"] = get_rclone_remotes()
        if select:
            remote_var.set(select)

    def parse_remote_name(text: str) -> str:
        return text.split(":", 1)[0].strip()

    def on_new_remote():
        edit_remote_dialog(dlg, None,
                           lambda new_name: refresh_remotes(f"{new_name}:/"))

    def on_edit_remote():
        types = get_rclone_remotes_with_types()
        name = parse_remote_name(remote_var.get())
        if not name or name not in types:
            messagebox.showinfo("Edit remote",
                                "Pick an existing remote in the dropdown first.",
                                parent=dlg)
            return
        if types[name] != "sftp":
            if messagebox.askyesno(
                    "Non-SFTP remote",
                    f"'{name}' is type '{types[name]}'. The form-based editor only "
                    f"supports SFTP. Open `rclone config` in a console instead?",
                    parent=dlg):
                launch_rclone_config_console()
            return
        edit_remote_dialog(dlg, name,
                           lambda new_name: refresh_remotes(f"{new_name}:/"))

    ttk.Button(remote_row, text="New...", command=on_new_remote,
               width=7).pack(side="left", padx=(4, 0))
    ttk.Button(remote_row, text="Edit...", command=on_edit_remote,
               width=7).pack(side="left", padx=(2, 0))

    ttk.Label(frm, text="Drive letter:").grid(row=2, column=0, sticky="e", **pad)
    used = used_drive_letters()
    if existing:
        used.discard(existing.drive.upper())
    free = [c for c in string.ascii_uppercase if c not in used]
    if existing and existing.drive.upper() not in free:
        free = [existing.drive.upper()] + free
    drive_box = ttk.Combobox(frm, textvariable=drive_var, values=free, width=5, state="readonly")
    drive_box.grid(row=2, column=1, sticky="w", **pad)

    ttk.Label(frm, text="Volume label:").grid(row=3, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=volname_var, width=36).grid(row=3, column=1, **pad)

    ttk.Label(frm, text="Extra rclone args:").grid(row=4, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=extra_var, width=36).grid(row=4, column=1, **pad)
    ttk.Label(frm, text="(space-separated, optional)",
              foreground="#777").grid(row=5, column=1, sticky="w", padx=8)

    def on_ok():
        name = name_var.get().strip()
        remote = remote_var.get().strip()
        drive = drive_var.get().strip().upper()
        volname = volname_var.get().strip() or name
        extra = extra_var.get().split()

        if not name or not remote or not drive:
            messagebox.showerror("Missing field",
                                 "Name, remote, and drive letter are required.",
                                 parent=dlg)
            return
        if len(drive) != 1 or drive not in string.ascii_uppercase:
            messagebox.showerror("Invalid drive",
                                 "Drive must be a single letter A-Z.", parent=dlg)
            return

        with mounts_lock:
            for other in mounts:
                if existing and other is existing:
                    continue
                if other.name == name:
                    messagebox.showerror("Duplicate name",
                                         f"A mount named '{name}' already exists.",
                                         parent=dlg)
                    return
                if other.drive.upper() == drive:
                    messagebox.showerror("Drive in use",
                                         f"Drive {drive}: is already used by '{other.name}'.",
                                         parent=dlg)
                    return

            remount_after = False
            if existing:
                changed = (existing.drive != drive or existing.remote != remote
                           or existing.volname != volname or existing.extra != extra)
                if is_mounted(existing) and changed:
                    if not messagebox.askyesno(
                            "Remount required",
                            f"'{existing.name}' is mounted. Unmount and remount with new settings?",
                            parent=dlg):
                        return
                    unmount(existing)
                    remount_after = True
                old_name = existing.name
                existing.name = name
                existing.remote = remote
                existing.drive = drive
                existing.volname = volname
                existing.extra = extra
                if old_name != name and old_name in config["autostart"]:
                    config["autostart"][name] = config["autostart"].pop(old_name)
                target = existing
            else:
                target = Mount(name=name, remote=remote, drive=drive,
                               volname=volname, extra=extra)
                mounts.append(target)
            persist_mounts()

        if remount_after:
            threading.Thread(target=mount, args=(target,), daemon=True).start()
        on_saved()
        dlg.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=6, column=0, columnspan=2, pady=(10, 0), sticky="e")
    ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=4)
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")

    dlg.wait_window()


def edit_remote_dialog(parent, existing_name: str | None, on_saved) -> None:
    """Create or update an SFTP remote in rclone.conf."""
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    cfg = rclone_config_show(existing_name) if existing_name else {}

    dlg = tk.Toplevel(parent)
    dlg.title("Edit SFTP remote" if existing_name else "New SFTP remote")
    dlg.transient(parent)
    dlg.grab_set()
    dlg.resizable(False, False)
    parent.update_idletasks()
    dlg.geometry(f"+{parent.winfo_rootx() + 30}+{parent.winfo_rooty() + 30}")

    pad = {"padx": 8, "pady": 4}
    frm = ttk.Frame(dlg, padding=10)
    frm.pack(fill="both", expand=True)

    name_var = tk.StringVar(value=existing_name or "")
    host_var = tk.StringVar(value=cfg.get("host", ""))
    port_var = tk.StringVar(value=cfg.get("port", "22"))
    user_var = tk.StringVar(value=cfg.get("user", ""))
    has_password = bool(cfg.get("pass"))
    auth_var = tk.StringVar(value="password" if has_password else "key")
    key_var = tk.StringVar(value=cfg.get("key_file", ""))
    keypass_var = tk.StringVar(value="")
    pw_var = tk.StringVar(value="")

    ttk.Label(frm, text="Remote name:").grid(row=0, column=0, sticky="e", **pad)
    name_entry = ttk.Entry(frm, textvariable=name_var, width=32)
    name_entry.grid(row=0, column=1, columnspan=2, sticky="w", **pad)
    if existing_name:
        name_entry.configure(state="disabled")

    ttk.Label(frm, text="Host:").grid(row=1, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=host_var, width=32).grid(
        row=1, column=1, columnspan=2, sticky="w", **pad)

    ttk.Label(frm, text="Port:").grid(row=2, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=port_var, width=8).grid(
        row=2, column=1, sticky="w", **pad)

    ttk.Label(frm, text="User:").grid(row=3, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=user_var, width=32).grid(
        row=3, column=1, columnspan=2, sticky="w", **pad)

    ttk.Label(frm, text="Auth:").grid(row=4, column=0, sticky="e", **pad)
    auth_row = ttk.Frame(frm)
    auth_row.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
    ttk.Radiobutton(auth_row, text="SSH key", variable=auth_var,
                    value="key", command=lambda: refresh_auth()).pack(side="left")
    ttk.Radiobutton(auth_row, text="Password", variable=auth_var,
                    value="password", command=lambda: refresh_auth()).pack(side="left", padx=(8, 0))

    key_label = ttk.Label(frm, text="Key file:")
    key_entry = ttk.Entry(frm, textvariable=key_var, width=32)
    key_browse = ttk.Button(frm, text="Browse...",
                            command=lambda: browse_key())
    keypass_label = ttk.Label(frm, text="Key passphrase:")
    keypass_entry = ttk.Entry(frm, textvariable=keypass_var, width=32, show="*")
    pw_label = ttk.Label(frm, text="Password:")
    pw_entry = ttk.Entry(frm, textvariable=pw_var, width=32, show="*")
    pw_hint = ttk.Label(frm,
                        text="(leave blank to keep existing)" if has_password else "",
                        foreground="#777")
    keypass_hint = ttk.Label(frm,
                             text="(leave blank for none / unchanged)",
                             foreground="#777")

    def browse_key():
        initial = str(Path(key_var.get()).parent) if key_var.get() else \
                  str(Path.home() / ".ssh")
        path = filedialog.askopenfilename(
            parent=dlg, initialdir=initial,
            title="Select SSH private key")
        if path:
            key_var.set(path.replace("/", "\\"))

    def clear_auth_rows():
        for w in (key_label, key_entry, key_browse,
                  keypass_label, keypass_entry, keypass_hint,
                  pw_label, pw_entry, pw_hint):
            w.grid_forget()

    def refresh_auth():
        clear_auth_rows()
        if auth_var.get() == "key":
            key_label.grid(row=5, column=0, sticky="e", **pad)
            key_entry.grid(row=5, column=1, sticky="w", **pad)
            key_browse.grid(row=5, column=2, sticky="w", padx=(0, 8), pady=4)
            keypass_label.grid(row=6, column=0, sticky="e", **pad)
            keypass_entry.grid(row=6, column=1, sticky="w", **pad)
            keypass_hint.grid(row=7, column=1, sticky="w", padx=8)
        else:
            pw_label.grid(row=5, column=0, sticky="e", **pad)
            pw_entry.grid(row=5, column=1, sticky="w", **pad)
            pw_hint.grid(row=6, column=1, sticky="w", padx=8)

    refresh_auth()

    def on_ok():
        name = name_var.get().strip()
        host = host_var.get().strip()
        port = port_var.get().strip() or "22"
        user = user_var.get().strip()

        if not name or not host or not user:
            messagebox.showerror("Missing field",
                                 "Name, host, and user are required.", parent=dlg)
            return
        if not port.isdigit():
            messagebox.showerror("Invalid port",
                                 "Port must be numeric.", parent=dlg)
            return

        kwargs = dict(name=name, host=host, port=port, user=user,
                      update=bool(existing_name))
        if auth_var.get() == "key":
            kwargs["key_file"] = key_var.get().strip()
            if keypass_var.get():
                kwargs["key_pass"] = keypass_var.get()
            if not kwargs["key_file"]:
                messagebox.showerror("Missing key",
                                     "Pick an SSH private key file.", parent=dlg)
                return
            if not Path(kwargs["key_file"]).exists():
                if not messagebox.askyesno(
                        "Key not found",
                        f"'{kwargs['key_file']}' does not exist. Save anyway?",
                        parent=dlg):
                    return
        else:
            if pw_var.get():
                kwargs["password"] = pw_var.get()
            elif not has_password:
                messagebox.showerror("Missing password",
                                     "Enter a password.", parent=dlg)
                return

        ok, err = rclone_config_save_sftp(**kwargs)
        if not ok:
            messagebox.showerror("rclone error", err or "unknown error", parent=dlg)
            return
        log(f"saved SFTP remote '{name}'")
        on_saved(name)
        dlg.destroy()

    def on_delete():
        if not existing_name:
            return
        if not messagebox.askyesno(
                "Delete remote",
                f"Delete remote '{existing_name}' from rclone.conf?\n"
                f"Mounts using it will fail until reconfigured.", parent=dlg):
            return
        ok, err = rclone_config_delete_remote(existing_name)
        if not ok:
            messagebox.showerror("rclone error", err or "unknown error", parent=dlg)
            return
        log(f"deleted remote '{existing_name}'")
        on_saved("")
        dlg.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=20, column=0, columnspan=3, pady=(12, 0), sticky="ew")
    ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=4)
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
    if existing_name:
        ttk.Button(btns, text="Delete remote",
                   command=on_delete).pack(side="left")

    dlg.wait_window()


# ---------- entry point ----------

def already_running() -> bool:
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            if psutil.pid_exists(pid):
                p = psutil.Process(pid)
                if "python" in (p.name() or "").lower():
                    return True
        except (OSError, ValueError, psutil.Error):
            pass
    try:
        LOCK_FILE.write_text(str(os.getpid()))
    except OSError:
        pass
    return False


def main() -> None:
    log(f"=== rclone_tray starting (pid {os.getpid()}) ===")
    if already_running():
        log("another instance is running — exiting")
        return

    try:
        for m in list(mounts):
            if is_autostart(m) and not is_mounted(m):
                threading.Thread(target=mount, args=(m,), daemon=True).start()

        stop = threading.Event()
        threading.Thread(target=watchdog_loop, args=(stop,), daemon=True).start()

        icon = Icon("rclone_tray", make_icon_image(), "Rclone Mounts")
        icon.menu = build_menu(icon)
        threading.Thread(target=menu_refresh_loop, args=(stop, icon),
                         daemon=True).start()
        try:
            icon.run()
        finally:
            stop.set()
            log("=== rclone_tray exiting ===")
    except Exception:
        log("FATAL: " + traceback.format_exc())
        raise
    finally:
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
