import ctypes
import io
import json
import os
import platform
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import psutil
import pystray
import requests
from PIL import Image, ImageDraw, ImageTk
from pypresence import Presence

def _writable_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resource_path(*parts):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


APP_DIR = _writable_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
ICON_PATH = _resource_path("assets", "icon.png")
LOG_PATH = os.path.join(APP_DIR, "app.log")


class _TeeLogger:
    def __init__(self, path):
        self.terminal = sys.__stdout__
        try:
            self.log = open(path, "a", encoding="utf-8")
        except Exception:
            self.log = None

    def write(self, message):
        if self.terminal:
            try:
                self.terminal.write(message)
            except Exception:
                pass
        if self.log:
            try:
                self.log.write(message)
                self.log.flush()
            except Exception:
                pass

    def flush(self):
        if self.terminal:
            try:
                self.terminal.flush()
            except Exception:
                pass
        if self.log:
            try:
                self.log.flush()
            except Exception:
                pass


def _setup_logging():
    logger = _TeeLogger(LOG_PATH)
    sys.stdout = logger
    sys.stderr = logger


_setup_logging()

DEFAULT_CONFIG = {
    "discord_client_id": "1542594277543125092",
    "poll_interval_seconds": 15,
    "afk_detection": {
        "enabled": True,
        "idle_threshold_seconds": 300,
        "active_text": "Playing",
        "idle_text": "Idle",
    },
    "games": [],
}


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds():
    if platform.system() != "Windows":
        return 0
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return millis / 1000.0
    except Exception as e:
        print("[AFK] Could not read idle time:", e)
    return 0


def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = dict(DEFAULT_CONFIG)
    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, value)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


class VNDB:
    API_URL = "https://api.vndb.org/kana/vn"

    def search(self, title, limit=9):
        if not title:
            return []
        try:
            payload = {
                "filters": ["search", "=", title],
                "fields": "title, image.url",
                "results": limit,
            }
            r = requests.post(self.API_URL, json=payload, timeout=10)
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            print("[VNDB] Search error:", e)
            return []

    @staticmethod
    def url_for(vn_id):
        return f"https://vndb.org/{vn_id}"


class PresenceManager:

    def __init__(self, default_client_id):
        self.default_client_id = default_client_id
        self.rpc = None
        self.active_client_id = None
        self.connected = False
        self.current_game = None
        self.start_ts = None

    def _connect(self, client_id):
        try:
            if self.rpc is not None:
                try:
                    self.rpc.close()
                except Exception:
                    pass
            self.rpc = Presence(client_id)
            self.rpc.connect()
            self.active_client_id = client_id
            self.connected = True
            print(f"[Discord] Connected with app {client_id}.")
        except Exception as e:
            self.connected = False
            print("[Discord] Could not connect (is Discord running?):", e)

    def _ensure_connected(self, client_id):
        if not self.connected or self.active_client_id != client_id:
            self._connect(client_id)

    def update(self, name, icon_url, state_text=None, client_id=None, buttons=None):
        chosen_id = client_id or self.default_client_id
        self._ensure_connected(chosen_id)
        if not self.connected:
            return
        if self.current_game != name:
            self.start_ts = int(time.time())
        self.current_game = name

        using_custom_app = bool(client_id)
        details_text = None if using_custom_app else name

        try:
            kwargs = dict(
                details=details_text,
                state=state_text if state_text else None,
                large_image=icon_url if icon_url else None,
                large_text=name,
                start=self.start_ts,
            )
            if buttons:
                kwargs["buttons"] = buttons
            self.rpc.update(**kwargs)
        except Exception as e:
            print("[Discord] Error updating presence:", e)
            self.connected = False

    def clear(self):
        if self.current_game is None:
            return
        self.current_game = None
        self.start_ts = None
        if self.connected:
            try:
                self.rpc.clear()
            except Exception as e:
                print("[Discord] Error clearing presence:", e)
                self.connected = False


# ---------------------------------------------------------------------------
# Process polling thread
# ---------------------------------------------------------------------------
class Poller(threading.Thread):
    def __init__(self, get_config, presence_manager):
        super().__init__(daemon=True)
        self.get_config = get_config
        self.presence_manager = presence_manager
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def get_running_process_names(self):
        running = set()
        for p in psutil.process_iter(["name"]):
            try:
                n = p.info["name"]
                if n:
                    running.add(n.lower())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return running

    def run(self):
        while not self._stop.is_set():
            cfg = self.get_config()
            games = cfg.get("games", [])
            interval = cfg.get("poll_interval_seconds", 15)
            running = self.get_running_process_names()

            match = None
            for g in games:
                if g.get("process", "").lower() in running:
                    match = g
                    break

            if match:
                afk_cfg = cfg.get("afk_detection", {})
                state_text = afk_cfg.get("active_text", "Playing") or None
                if afk_cfg.get("enabled", True):
                    idle_seconds = get_idle_seconds()
                    threshold = afk_cfg.get("idle_threshold_seconds", 300)
                    if idle_seconds >= threshold:
                        state_text = afk_cfg.get("idle_text", "Idle")

                buttons = None
                vndb_url = match.get("vndb_url")
                if vndb_url:
                    buttons = [{"label": "View on VNDB", "url": vndb_url}]

                self.presence_manager.update(
                    match.get("name", match.get("process")),
                    match.get("icon_url", ""),
                    state_text=state_text,
                    client_id=match.get("discord_client_id") or None,
                    buttons=buttons,
                )
            else:
                self.presence_manager.clear()

            self._stop.wait(interval)


# ---------------------------------------------------------------------------
# Add game dialog — process picker (with search filter) + VNDB search
# with visible cover thumbnails so it's easy to tell VNs apart
# ---------------------------------------------------------------------------
class AddGameDialog(tk.Toplevel):
    THUMB_SIZE = (70, 96)
    GRID_COLUMNS = 3

    def __init__(self, master, config, on_save):
        super().__init__(master)
        self.title("Add game")
        self.geometry("480x620")
        self.minsize(420, 320)
        self.config_data = config
        self.on_save = on_save
        self.vndb = VNDB()
        self.selected_icon_url = ""
        self.selected_vndb_url = ""
        self.vndb_results = []
        self._thumbnails = []  # keep references so Tk doesn't garbage-collect them
        self._vn_buttons = []

        # --- Scrollable container ---------------------------------------
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        body = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(window_id, width=event.width)

        body.bind("<Configure>", _on_body_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            delta = -1 if event.num == 5 or event.delta < 0 else 1
            canvas.yview_scroll(-delta, "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)
        self._canvas = canvas

        self.protocol(
            "WM_DELETE_WINDOW",
            lambda: (self._unbind_wheel(canvas), self.destroy()),
        )

        # --- Process picker with search filter ---------------------------
        ttk.Label(body, text="Filter running processes:").pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.process_filter_var = tk.StringVar()
        self.process_filter_var.trace_add("write", lambda *a: self.refresh_processes())
        ttk.Entry(body, textvariable=self.process_filter_var).pack(fill="x", padx=10)

        self.process_list = tk.Listbox(body, height=6)
        self.process_list.pack(fill="x", padx=10, pady=(4, 0))
        self.process_list.bind("<<ListboxSelect>>", self.on_process_select)

        ttk.Button(body, text="Refresh list", command=self.refresh_processes).pack(
            padx=10, pady=4, anchor="w"
        )

        ttk.Label(body, text="Process name (e.g. game.exe):").pack(
            anchor="w", padx=10, pady=(10, 0)
        )
        self.process_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.process_var).pack(fill="x", padx=10)

        # --- VNDB search with visible thumbnails --------------------------
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=10, pady=(14, 10))
        ttk.Label(body, text="Search the VN on VNDB:").pack(anchor="w", padx=10)

        search_row = ttk.Frame(body)
        search_row.pack(fill="x", padx=10, pady=(4, 4))
        self.vndb_query_var = tk.StringVar()
        ttk.Entry(search_row, textvariable=self.vndb_query_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(search_row, text="Search", command=self.search_vndb).pack(
            side="left", padx=(6, 0)
        )

        self.vndb_grid = ttk.Frame(body)
        self.vndb_grid.pack(fill="x", padx=10, pady=(4, 0))

        ttk.Label(body, text="Selected:").pack(anchor="w", padx=10, pady=(10, 0))
        selected_row = ttk.Frame(body)
        selected_row.pack(fill="x", padx=10)
        self.selected_thumb_label = ttk.Label(selected_row, text="(none)")
        self.selected_thumb_label.pack(side="left")
        self.selected_info_label = ttk.Label(
            selected_row, text="No VN selected yet.", wraplength=340, justify="left"
        )
        self.selected_info_label.pack(side="left", padx=(10, 0))

        # --- Manual fallback ----------------------------------------------
        ttk.Label(
            body,
            text="Display name (auto-filled from VNDB, editable):",
        ).pack(anchor="w", padx=10, pady=(14, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.name_var).pack(fill="x", padx=10)

        ttk.Label(
            body, text="Or paste a cover image URL manually (optional):"
        ).pack(anchor="w", padx=10, pady=(10, 0))
        self.manual_url_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.manual_url_var).pack(fill="x", padx=10)
        ttk.Button(body, text="Use this URL as icon", command=self.use_manual_url).pack(
            padx=10, pady=4, anchor="w"
        )

        # --- Custom Discord Application ID ---
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=10, pady=10)
        title_row = ttk.Frame(body)
        title_row.pack(fill="x", padx=10)
        ttk.Label(
            title_row,
            text="Discord Application ID (REQUIRED if you don't want\n"
            "the big title to say \"a game\" — see 'How?'):",
        ).pack(side="left")
        ttk.Button(title_row, text="How?", width=6, command=self.show_client_id_help).pack(
            side="right"
        )
        self.client_id_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.client_id_var).pack(fill="x", padx=10)

        ttk.Button(body, text="Save game", command=self.save).pack(padx=10, pady=16)
        ttk.Label(
            body, text="VN Discord Presence — made by nullprc", foreground="#888888"
        ).pack(pady=(0, 10))

        self.refresh_processes()

    def _unbind_wheel(self, canvas):
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    # --- Process list -------------------------------------------------
    def refresh_processes(self):
        filter_text = self.process_filter_var.get().strip().lower()
        self.process_list.delete(0, tk.END)
        names = set()
        for p in psutil.process_iter(["name"]):
            try:
                n = p.info["name"]
                if n:
                    names.add(n)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for n in sorted(names, key=str.lower):
            if not filter_text or filter_text in n.lower():
                self.process_list.insert(tk.END, n)

    def on_process_select(self, event):
        sel = self.process_list.curselection()
        if not sel:
            return
        proc_name = self.process_list.get(sel[0])
        self.process_var.set(proc_name)
        base = os.path.splitext(proc_name)[0]
        guess = base.replace("_", " ").replace("-", " ").title()
        self.vndb_query_var.set(guess)
        if not self.name_var.get():
            self.name_var.set(guess)

    # --- VNDB search ----------------------------------------------------
    def search_vndb(self):
        for w in self.vndb_grid.winfo_children():
            w.destroy()
        self._thumbnails.clear()
        self._vn_buttons.clear()

        term = self.vndb_query_var.get().strip() or self.name_var.get().strip()
        if not term:
            messagebox.showwarning("Missing search text", "Type a VN title to search first.")
            return

        results = self.vndb.search(term)
        self.vndb_results = results
        if not results:
            messagebox.showinfo("No results", "No visual novels found on VNDB with that name.")
            return

        for i, r in enumerate(results):
            frame = ttk.Frame(self.vndb_grid)
            frame.grid(row=i // self.GRID_COLUMNS, column=i % self.GRID_COLUMNS, padx=4, pady=4)

            photo = None
            img_url = (r.get("image") or {}).get("url")
            if img_url:
                try:
                    resp = requests.get(img_url, timeout=10)
                    img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                    img.thumbnail(self.THUMB_SIZE)
                    photo = ImageTk.PhotoImage(img)
                    self._thumbnails.append(photo)
                except Exception as e:
                    print("[VNDB] Thumbnail load error:", e)

            btn = tk.Button(
                frame,
                image=photo if photo else None,
                text="(no image)" if not photo else "",
                width=self.THUMB_SIZE[0] if not photo else None,
                height=6 if not photo else None,
                relief="raised",
                command=lambda rr=r, ii=i: self.select_vn(rr, ii),
            )
            btn.pack()
            self._vn_buttons.append(btn)

            ttk.Label(
                frame, text=r.get("title", ""), wraplength=90, justify="center",
                font=("", 8),
            ).pack()

    def select_vn(self, result, index):
        for i, btn in enumerate(self._vn_buttons):
            btn.config(relief="sunken" if i == index else "raised")

        title = result.get("title", "")
        self.name_var.set(title)
        self.selected_vndb_url = VNDB.url_for(result.get("id"))

        img_url = (result.get("image") or {}).get("url", "")
        self.selected_icon_url = img_url

        if index < len(self._thumbnails):
            self.selected_thumb_label.config(image=self._thumbnails[index], text="")
            self.selected_thumb_label.image = self._thumbnails[index]
        else:
            self.selected_thumb_label.config(image="", text="(no image)")

        self.selected_info_label.config(
            text=f"{title}\n{self.selected_vndb_url}"
        )

    def use_manual_url(self):
        url = self.manual_url_var.get().strip()
        if url:
            self.selected_icon_url = url
            self.selected_info_label.config(
                text=(self.selected_info_label.cget("text") + f"\nCustom icon: {url}")
            )

    def show_client_id_help(self):
        win = tk.Toplevel(self)
        win.title("How to get your own Application ID")
        win.geometry("440x300")
        text = (
            "The big bold title is NOT something this app can set per\n"
            "update - Discord fixes it to whatever your Application is\n"
            "named in the Developer Portal. This is true for every app\n"
            "with Rich Presence (Spotify, VS Code, etc), not just this\n"
            "tool. Leaving this field empty means it will say \"᲼᲼\"\n"
            "To make it show THIS VN's real name instead:\n\n"
            "1. Go to discord.com/developers/applications\n"
            "2. Click 'New Application'\n"
            "3. Name it EXACTLY the title you want shown\n"
            "   (e.g. 'Hanachirasu')\n"
            "4. Under 'App Icon', upload a picture for it too - this\n"
            "   is what shows next to your name during voice calls\n"
            "5. Copy the 'Application ID' from General Information\n"
            "6. Paste it in the field above, then Save\n\n"
            "You need one app per VN you want to show its own name\n"
            "(and its own call icon) this way — it's a one-time,\n"
            "2-minute setup per VN, not something to redo each time."
        )
        ttk.Label(win, text=text, justify="left").pack(padx=14, pady=14, anchor="w")
        ttk.Button(
            win,
            text="Open discord.com/developers/applications",
            command=lambda: webbrowser.open(
                "https://discord.com/developers/applications"
            ),
        ).pack(padx=14, pady=(0, 14))

    def save(self):
        process = self.process_var.get().strip()
        name = self.name_var.get().strip()
        if not process or not name:
            messagebox.showwarning("Missing data", "Fill in the process and the name.")
            return
        entry = {
            "process": process,
            "name": name,
            "icon_url": self.selected_icon_url,
            "vndb_url": self.selected_vndb_url,
            "discord_client_id": self.client_id_var.get().strip(),
        }
        self.on_save(entry)
        messagebox.showinfo("Done", f"'{name}' was added to the configuration.")
        self._unbind_wheel(self._canvas)
        self.destroy()


class GamesListDialog(tk.Toplevel):
    """View and delete already configured games."""

    def __init__(self, master, config, on_change):
        super().__init__(master)
        self.title("Configured games")
        self.geometry("380x300")
        self.config_data = config
        self.on_change = on_change

        self.listbox = tk.Listbox(self)
        self.listbox.pack(fill="both", expand=True, padx=10, pady=10)
        self.refresh()

        ttk.Button(self, text="Delete selected", command=self.delete_selected).pack(
            padx=10, pady=(0, 10)
        )

    def refresh(self):
        self.listbox.delete(0, tk.END)
        for g in self.config_data.get("games", []):
            self.listbox.insert(tk.END, f"{g['name']}  ({g['process']})")

    def delete_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        games = self.config_data.get("games", [])
        removed = games.pop(idx)
        save_config(self.config_data)
        self.on_change()
        self.refresh()
        messagebox.showinfo("Deleted", f"'{removed['name']}' was removed.")


class App:
    def __init__(self):
        self.config = load_config()
        self.presence_manager = PresenceManager(self.config.get("discord_client_id"))
        self.poller = Poller(lambda: self.config, self.presence_manager)

        self.root = tk.Tk()
        self.root.withdraw()

        self.tray_icon = pystray.Icon(
            "vn_discord_presence",
            self.load_icon_image(),
            "VN Discord Presence",
            menu=self.make_menu(),
        )

    def load_icon_image(self):
        if os.path.exists(ICON_PATH):
            try:
                return Image.open(ICON_PATH).convert("RGBA")
            except Exception as e:
                print("[Tray] Could not load custom icon, using default:", e)
        img = Image.new("RGB", (64, 64), color=(88, 101, 242))
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=(255, 255, 255))
        d.ellipse((20, 20, 44, 44), fill=(88, 101, 242))
        return img

    def make_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Add game...", self.on_add_game),
            pystray.MenuItem("View / delete games...", self.on_view_games),
            pystray.MenuItem("Reload configuration", self.on_reload),
            pystray.MenuItem("Open config folder", self.on_open_folder),
            pystray.MenuItem("About", self.on_about),
            pystray.MenuItem("Quit", self.on_quit),
        )

    def on_add_game(self, icon, item):
        self.root.after(0, self.open_add_game_dialog)

    def on_view_games(self, icon, item):
        self.root.after(0, self.open_games_list_dialog)

    def on_reload(self, icon, item):
        self.config = load_config()

    def on_open_folder(self, icon, item):
        try:
            os.startfile(APP_DIR)  # Windows
        except AttributeError:
            import subprocess

            subprocess.Popen(["xdg-open", APP_DIR])

    def on_about(self, icon, item):
        self.root.after(0, self.show_about_dialog)

    def show_about_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("About")
        win.geometry("300x140")
        win.resizable(False, False)
        ttk.Label(win, text="VN Discord Presence", font=("", 12, "bold")).pack(
            pady=(16, 4)
        )
        ttk.Label(win, text="by nullprc").pack()
        link = ttk.Label(
            win, text="github.com/nullprc", foreground="#5865F2", cursor="hand2"
        )
        link.pack(pady=(4, 0))
        link.bind(
            "<Button-1>", lambda e: webbrowser.open("https://github.com/nullprc")
        )

    def on_quit(self, icon, item):
        self.poller.stop()
        self.presence_manager.clear()
        self.tray_icon.stop()
        self.root.after(0, self.root.quit)

    def open_add_game_dialog(self):
        AddGameDialog(self.root, self.config, self.on_game_saved)

    def open_games_list_dialog(self):
        GamesListDialog(self.root, self.config, self.on_reload)

    def on_game_saved(self, entry):
        self.config.setdefault("games", []).append(entry)
        save_config(self.config)

    def run(self):
        self.poller.start()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        self.root.mainloop()


if __name__ == "__main__":
    app = App()
    app.run()
