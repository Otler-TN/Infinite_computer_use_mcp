"""Small, dependency-free Windows UI for the existing MCP lifecycle scripts."""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from manager_backend import ElevationCancelled, Manager, Settings

BACKGROUND = "#f3f5f9"
INK = "#17243b"
MUTED = "#5d6c82"
BLUE = "#285de5"
GREEN = "#16815d"
AMBER = "#9a620c"


class ManagerWindow:
    def __init__(self, window: tk.Tk, manager: Manager, *, start_polling: bool = True):
        self.window = window
        self.manager = manager
        self.settings = manager.load_settings()
        self.events: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        self.busy: str | None = None
        self.current: dict = {"status": "loading", "has_processes": False}
        self.settings_window: tk.Toplevel | None = None
        self.setup_prompted = False
        self.poll_id = None
        self._styles()
        self._build()
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Control-q>", lambda _event: self.close())
        if start_polling:
            threading.Thread(target=self._watch, daemon=True, name="mcp-status").start()
        self.poll_id = window.after(100, self._drain)

    def _styles(self):
        style = ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background=BACKGROUND, foreground=INK, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Card.TLabel", background="white")
        style.configure("CardMuted.TLabel", background="white", foreground=MUTED)
        style.configure("Status.TLabel", background="white", font=("Segoe UI", 15, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=(15, 9), borderwidth=0)
        style.map("TButton", background=[("active", "#e1e7f0"), ("!active", "#e9edf5")])
        style.configure("Primary.TButton", font=("Segoe UI", 11, "bold"), padding=(20, 12))
        style.map(
            "Primary.TButton",
            background=[("disabled", "#dce3f0"), ("active", "#194bc8"), ("!active", BLUE)],
            foreground=[("disabled", "#8291aa"), ("!disabled", "white")],
        )
        style.configure("TCheckbutton", background=BACKGROUND, font=("Segoe UI", 10))
        style.configure("TRadiobutton", background=BACKGROUND, font=("Segoe UI", 10))
        style.configure("TEntry", padding=10, font=("Segoe UI", 10))
        style.map(
            "TEntry", fieldbackground=[("readonly", "#f5f7fb")], foreground=[("readonly", INK)]
        )
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor=BACKGROUND)

    def _build(self):
        window = self.window
        window.title("Infinite Computer Use MCP Manager")
        window.configure(background=BACKGROUND)
        window.geometry("600x610")
        window.minsize(570, 600)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        shell = ttk.Frame(window, padding=28)
        shell.grid(sticky="nsew")
        shell.columnconfigure(0, weight=1)

        ttk.Label(shell, text="Infinite Computer Use MCP", style="Title.TLabel").grid(sticky="w")
        ttk.Label(shell, text="Connect your AI app to this computer.", style="Muted.TLabel").grid(
            sticky="w", pady=(3, 23)
        )
        card = ttk.Frame(shell, style="Card.TFrame", padding=22)
        card.grid(sticky="ew")
        card.columnconfigure(1, weight=1)
        self.dot = tk.Canvas(card, width=14, height=22, background="white", highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 6, 12, 16, fill=MUTED, outline="")
        self.dot.grid(row=0, column=0, padx=(0, 9))
        self.status_label = ttk.Label(card, text="Checking status…", style="Status.TLabel")
        self.status_label.grid(row=0, column=1, sticky="w")
        self.detail = ttk.Label(card, text="", style="CardMuted.TLabel")
        self.detail.grid(row=1, column=1, sticky="w", pady=(3, 21))
        ttk.Label(
            card, text="CONNECTION LINK", style="CardMuted.TLabel", font=("Segoe UI", 9, "bold")
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        link_row = ttk.Frame(card, style="Card.TFrame")
        link_row.grid(row=3, column=0, columnspan=2, sticky="ew")
        link_row.columnconfigure(0, weight=1)
        self.link = tk.StringVar(value="Start MCP to get your link")
        self.link_entry = ttk.Entry(link_row, textvariable=self.link, state="readonly")
        self.link_entry.grid(row=0, column=0, sticky="ew", ipady=2)
        self.link_entry.bind("<Control-a>", self._select_link)
        self.copy_button = ttk.Button(
            link_row, text="Copy link", command=self.copy_link, state="disabled"
        )
        self.copy_button.grid(row=0, column=1, padx=(8, 0))
        self.exposure = ttk.Label(
            card, text="", style="CardMuted.TLabel", wraplength=490, font=("Segoe UI", 9)
        )
        self.exposure.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

        actions = ttk.Frame(shell)
        actions.grid(sticky="ew", pady=(20, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(
            actions,
            text="Start MCP",
            style="Primary.TButton",
            command=lambda: self.run_action("start"),
            state="disabled",
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_button = ttk.Button(
            actions, text="Stop MCP", command=lambda: self.run_action("stop"), state="disabled"
        )
        self.stop_button.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.progress = ttk.Progressbar(shell, mode="indeterminate")
        self.progress.grid(sticky="ew", pady=(9, 0))
        self.progress.grid_remove()
        self.message = ttk.Label(shell, text="", style="Muted.TLabel", wraplength=535)
        self.message.grid(sticky="w", pady=(12, 0))
        shell.rowconfigure(5, weight=1)
        bottom = ttk.Frame(shell)
        bottom.grid(row=6, sticky="ew", pady=(20, 10))
        self.check_button = ttk.Button(
            bottom,
            text="Check connection",
            command=lambda: self.run_action("verify"),
            state="disabled",
        )
        self.check_button.pack(side="left")
        self.settings_button = ttk.Button(
            bottom, text="Setup & settings", command=self.show_settings
        )
        self.settings_button.pack(side="left", padx=8)
        ttk.Button(bottom, text="Logs", command=self.open_logs).pack(side="right")
        ttk.Button(shell, text="Connect an AI app", command=self.show_connection_help).grid(
            row=7, sticky="w", pady=(0, 10)
        )
        ttk.Label(
            shell,
            text="Closing this window keeps MCP running.",
            style="Muted.TLabel",
            font=("Segoe UI", 9),
        ).grid(row=8, sticky="w")

    def _select_link(self, _event):
        self.link_entry.selection_range(0, "end")
        return "break"

    def _watch(self):
        while not self.closed.is_set():
            try:
                self.events.put(("snapshot", self.manager.snapshot()))
                self.events.put(("progress", self.manager.operation_status()))
            except Exception as error:
                self.events.put(("snapshot", {"status": "needs_attention", "has_processes": False}))
                self.events.put(("notice", f"Could not read status: {error}"))
            self.closed.wait(2)

    def _drain(self):
        if self.closed.is_set():
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "snapshot":
                    self.current = value
                    self.render_status()
                    if (
                        not self.setup_prompted
                        and value["status"] == "stopped"
                        and not (self.manager.root / ".run/installed-lock.sha256").exists()
                    ):
                        self.setup_prompted = True
                        self.show_settings()
                elif (
                    kind == "progress"
                    and self.busy
                    and value.get("status") == "running"
                    and value.get("message")
                ):
                    self.message.configure(text=value["message"], foreground=MUTED)
                elif kind == "notice" and not self.busy:
                    self.message.configure(text=value, foreground=AMBER)
                elif kind == "result":
                    self.busy = None
                    self.progress.stop()
                    self.progress.grid_remove()
                    self.message.configure(
                        text=value["message"],
                        foreground=GREEN if value["status"] == "complete" else AMBER,
                    )
                    self.render_status()
                    if value["status"] == "setup_required":
                        self.show_settings()
        except queue.Empty:
            pass
        self.poll_id = self.window.after(100, self._drain)

    def render_status(self):
        state = self.current
        status = state["status"]
        running = status == "running"
        label, color = {
            "loading": ("Checking status…", MUTED),
            "running": ("Running", GREEN),
            "stopped": ("Stopped", MUTED),
            "starting": ("Starting…", BLUE),
            "needs_attention": ("Needs attention", AMBER),
        }.get(status, ("Needs attention", AMBER))
        if self.busy:
            label = {"start": "Starting…", "stop": "Stopping…", "verify": "Checking connection…"}[
                self.busy
            ]
            color = BLUE
        self.status_label.configure(text=label)
        self.dot.itemconfigure(self.dot_id, fill=color)
        remote = state.get("remote", self.settings.remote) if running else self.settings.remote
        admin = state.get("elevated", False) if running else self.settings.administrator
        self.detail.configure(
            text=f"{'Remote access' if remote else 'This computer only'}  ·  {'Administrator' if admin else 'Standard access'}"
        )
        self.link.set(state.get("url") or "Start MCP to get your link")
        self.exposure.configure(
            text="Anyone with this link can use your computer."
            if remote
            else "Only apps on this computer can connect."
        )
        self.start_button.configure(
            state="normal" if not self.busy and status == "stopped" else "disabled"
        )
        self.stop_button.configure(
            state="normal" if not self.busy and state.get("has_processes") else "disabled"
        )
        self.copy_button.configure(state="normal" if running and not self.busy else "disabled")
        self.check_button.configure(state="normal" if running and not self.busy else "disabled")
        self.settings_button.configure(state="disabled" if self.busy else "normal")

    def run_action(self, action: str):
        if self.busy or self.closed.is_set():
            return
        self.busy = action
        # Clear a previous operation's progress before the worker creates its new ID.
        self.manager.operation_id = None
        self.message.configure(
            text={
                "start": "Starting MCP. Approve the Windows prompt if it appears.",
                "stop": "Stopping MCP. Approve the Windows prompt if it appears.",
                "verify": "Testing the connection and tools. This may take a minute.",
            }[action],
            foreground=MUTED,
        )
        self.progress.grid()
        self.progress.start(12)
        self.render_status()
        settings = Settings(remote=self.settings.remote, administrator=self.settings.administrator)

        def work():
            try:
                result = self.manager.execute(action, settings)
            except ElevationCancelled as error:
                result = {"status": "cancelled", "message": str(error)}
            except Exception as error:
                result = {"status": "failed", "message": f"Could not {action} MCP: {error}"}
            try:
                self.events.put(("snapshot", self.manager.snapshot()))
            except Exception:
                pass
            self.events.put(("result", result))

        threading.Thread(target=work, daemon=True, name=f"mcp-{action}").start()

    def copy_link(self):
        url = self.current.get("url")
        if not url:
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(url)
        self.message.configure(
            text="Link copied. Choose Streamable HTTP and No Authentication in your AI app.",
            foreground=GREEN,
        )

    def open_logs(self):
        try:
            os.startfile(self.manager.logs_path())
        except OSError as error:
            messagebox.showerror("Could not open logs", str(error), parent=self.window)

    def show_settings(self):
        from setup_window import SetupWindow

        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.setup_prompted = True
        self.setup_dialog = SetupWindow(self)
        self.settings_window = self.setup_dialog.window
        return

    def show_connection_help(self):
        dialog = tk.Toplevel(self.window)
        dialog.title("Connect your AI app")
        dialog.transient(self.window)
        frame = ttk.Frame(dialog, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Connect your AI app", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        guide = (
            "1. Start MCP and wait for Running. Click Copy link.\n\n"
            "2. Open your AI app’s MCP or custom app settings. Add a server, paste the link, and choose Streamable HTTP with No Authentication.\n\n"
            "3. Enable the connection in a conversation. Ask the agent to call ComputerCapabilities, then Screenshot. You should see 24 tools.\n\n"
            "ChatGPT needs Developer mode and permission to add a custom app. Account and workspace policies can affect availability.\n\n"
            "Keep this laptop awake and MCP running. After a restart or update, copy the current link and refresh the tools in your AI app."
        )
        ttk.Label(frame, text=guide, wraplength=480).pack(anchor="w", pady=16)
        ttk.Button(
            frame,
            text="Official ChatGPT connection guide",
            command=lambda: webbrowser.open(
                "https://developers.openai.com/plugins/deploy/connect-chatgpt"
            ),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="For local clients that use stdio, import the generated configuration instead:",
            wraplength=480,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(16, 8))

        def open_config():
            path = self.manager.root / ".run/mcp-stdio.json"
            if path.exists():
                os.startfile(path)
            else:
                messagebox.showinfo(
                    "Install the runtime first",
                    "Open Setup & settings and click Install / repair to generate the local configuration.",
                    parent=dialog,
                )

        ttk.Button(frame, text="Open local agent configuration", command=open_config).pack(
            anchor="w"
        )

    def close(self):
        self.closed.set()
        if self.poll_id:
            self.window.after_cancel(self.poll_id)
        self.window.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    window = tk.Tk()
    ManagerWindow(window, Manager(Path(__file__).resolve().parents[1]))
    window.mainloop()


if __name__ == "__main__":
    main()
