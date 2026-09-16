"""Guided, asynchronous first-run setup for nontechnical users."""

import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from manager_backend import Settings, is_administrator
from onboarding import Onboarding


class SetupWindow:
    def __init__(self, parent):
        self.parent = parent
        self.service = Onboarding(parent.manager)
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self.report = None
        self.auto_start_pending = False
        self.window = tk.Toplevel(parent.window)
        self.window.title("Set up Infinite Computer Use MCP")
        self.window.configure(background="#f3f5f9")
        self.window.transient(parent.window)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.grab_set()
        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Set up this computer", font=("Segoe UI", 20, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="Complete these steps once. Your settings stay on this laptop.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 17))
        ttk.Label(frame, text="1. Choose how to connect", font=("Segoe UI", 12, "bold")).pack(
            anchor="w"
        )
        self.remote = tk.BooleanVar(value=parent.settings.remote)
        self.admin = tk.BooleanVar(value=parent.settings.administrator or is_administrator())
        modes = ttk.Frame(frame)
        modes.pack(fill="x", pady=6)
        self.controls = []
        for label, value in [("Remote through ngrok", True), ("This computer only", False)]:
            control = ttk.Radiobutton(
                modes, text=label, variable=self.remote, value=value, command=self.refresh
            )
            control.pack(side="left", padx=(0, 15))
            self.controls.append(control)
        self.admin_box = ttk.Checkbutton(
            frame,
            text="Run MCP as Administrator (Windows will ask for approval)",
            variable=self.admin,
            command=self.persist,
        )
        self.admin_box.pack(anchor="w")
        if is_administrator():
            self.admin_box.configure(state="disabled")
        else:
            self.controls.append(self.admin_box)
        ttk.Label(
            frame,
            text="Remote access has no authentication. Anyone with the link can use this computer.",
            style="Muted.TLabel",
            wraplength=550,
        ).pack(anchor="w", pady=(6, 15))

        ttk.Label(
            frame, text="2. Install the required software", font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")
        self.dependencies = ttk.Label(frame, text="Checking Python, MCP and ngrok…", wraplength=550)
        self.dependencies.pack(anchor="w", pady=6)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.install_button = ttk.Button(buttons, text="Install / repair", command=self.install)
        self.install_button.pack(side="left")
        self.check_button = ttk.Button(buttons, text="Check again", command=self.refresh)
        self.check_button.pack(side="left", padx=8)
        self.controls += [self.install_button, self.check_button]
        ttk.Button(buttons, text="Setup log", command=self.open_log).pack(side="right")

        self.ngrok_frame = ttk.Frame(frame)
        self.ngrok_frame.pack(fill="x", pady=(18, 0))
        ttk.Label(
            self.ngrok_frame, text="3. Connect your ngrok account", font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")
        self.ngrok_detail = ttk.Label(
            self.ngrok_frame, text="", wraplength=550, style="Muted.TLabel"
        )
        self.ngrok_detail.pack(anchor="w", pady=6)
        ttk.Button(
            self.ngrok_frame,
            text="Open ngrok account & authtoken",
            command=lambda: webbrowser.open(
                "https://dashboard.ngrok.com/get-started/your-authtoken"
            ),
        ).pack(anchor="w")
        ttk.Label(
            self.ngrok_frame,
            text="Create an account, verify your email, then copy your authtoken (not an API key).",
            style="Muted.TLabel",
            wraplength=550,
        ).pack(anchor="w", pady=6)
        token_row = ttk.Frame(self.ngrok_frame)
        token_row.pack(fill="x")
        self.token = tk.StringVar()
        self.token_entry = ttk.Entry(token_row, textvariable=self.token, show="*", width=40)
        self.token_entry.pack(side="left", fill="x", expand=True)
        self.token_button = ttk.Button(token_row, text="Save token", command=self.save_token)
        self.token_button.pack(side="left", padx=(8, 0))
        self.controls += [self.token_entry, self.token_button]
        self.token_entry.bind("<Return>", lambda _event: self.save_token())

        self.status = ttk.Label(frame, text="", wraplength=550)
        self.status.pack(anchor="w", pady=(16, 8))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))
        bottom = ttk.Frame(frame)
        self.bottom = bottom
        bottom.pack(fill="x")
        self.shortcut_button = ttk.Button(
            bottom,
            text="Desktop shortcut",
            command=lambda: self.work(self.service.shortcut, "Creating shortcut…"),
        )
        self.shortcut_button.pack(side="left")
        self.controls.append(self.shortcut_button)
        self.done_button = ttk.Button(
            bottom, text="Save & close", style="Primary.TButton", command=self.close
        )
        self.done_button.pack(side="right")
        self.after_id = self.window.after(100, self.drain)
        self.refresh()

    def persist(self):
        settings = Settings(self.remote.get(), self.admin.get())
        self.parent.manager.save_settings(settings)
        self.parent.settings = settings
        self.parent.render_status()
        return settings

    def work(self, operation, message):
        if self.busy:
            return
        self.busy = True
        for control in self.controls:
            control.configure(state="disabled")
        self.done_button.configure(state="disabled")
        self.status.configure(text=message, foreground="#5d6c82")
        self.progress.start(12)
        self.progress.pack(fill="x", pady=(0, 8), before=self.bottom)

        def worker():
            try:
                self.events.put(("done", operation()))
            except Exception as error:
                self.events.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        if self.busy:
            return
        try:
            settings = self.persist()
        except OSError:
            self.status.configure(
                text="This folder is not writable. Move the extracted project into Documents.",
                foreground="#9a620c",
            )
            return
        self.work(lambda: self.service.inspect(settings), "Checking what is already installed…")

    def install(self):
        settings = self.persist()

        def operation():
            self.service.install(settings, lambda text: self.events.put(("progress", text)))
            return self.service.inspect(settings)

        self.work(operation, "Installing required software. This may take a few minutes…")

    def save_token(self):
        token = self.token.get()
        self.token.set("")
        self.auto_start_pending = True
        settings = self.persist()

        def operation():
            self.service.save_token(token)
            return self.service.inspect(settings)

        self.work(operation, "Saving your authtoken locally…")

    def drain(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    self.status.configure(text=value)
                    continue
                self.busy = False
                self.progress.stop()
                self.progress.pack_forget()
                for control in self.controls:
                    control.configure(state="normal")
                self.done_button.configure(state="normal")
                if kind == "error":
                    self.auto_start_pending = False
                    self.status.configure(text=value, foreground="#9a620c")
                elif isinstance(value, dict):
                    self.report = value
                    auto_start_pending = self.auto_start_pending
                    self.auto_start_pending = False
                    self.dependencies.configure(
                        text=f"MCP runtime: {'Ready' if value['runtime'] else 'Install / repair needed'}  ·  ngrok: {'Not needed' if not self.remote.get() else ('Installed' if value['ngrok']['installed'] else 'Missing')}"
                    )
                    self.ngrok_detail.configure(text=value["ngrok"]["detail"])
                    for control in (self.token_entry, self.token_button):
                        control.configure(state="normal" if self.remote.get() else "disabled")
                    self.install_button.configure(
                        state="disabled" if value["running"] else "normal"
                    )
                    if value["ready"]:
                        text = "Ready. Close Setup, click Start MCP, then Copy link into your AI app. Choose Streamable HTTP and No Authentication."
                    else:
                        text = "\n".join(value["issues"])
                        if value["running"] and not value["runtime"]:
                            text = "Stop MCP before installing or repairing its runtime.\n" + text
                    self.status.configure(
                        text=text, foreground="#16815d" if value["ready"] else "#9a620c"
                    )
                    if value["ready"] and auto_start_pending:
                        action = "restart" if value["running"] else "start"
                        self.status.configure(
                            text=(
                                "Authtoken saved. Restarting MCP to create a fresh public link…"
                                if action == "restart"
                                else "Authtoken saved. Starting MCP to create your public link…"
                            ),
                            foreground="#16815d",
                        )
                        self.window.after(250, lambda selected=action: self.finish_and_start(selected))
                else:
                    self.status.configure(text="Desktop shortcut created.", foreground="#16815d")
        except queue.Empty:
            pass
        self.after_id = self.window.after(100, self.drain)

    def finish_and_start(self, action):
        if self.closed or self.busy:
            return
        self.close()
        self.parent.run_action(action)

    def open_log(self):
        if self.service.log_path.exists():
            os.startfile(self.service.log_path)
        else:
            messagebox.showinfo(
                "Setup log",
                "The log will be available after the first installation attempt.",
                parent=self.window,
            )

    def close(self):
        if self.busy:
            self.status.configure(
                text="Please wait for the current setup step to finish. You can retry a failed step.",
                foreground="#9a620c",
            )
            return
        try:
            self.persist()
        except OSError:
            pass
        self.closed = True
        self.window.after_cancel(self.after_id)
        self.token.set("")
        self.window.destroy()
