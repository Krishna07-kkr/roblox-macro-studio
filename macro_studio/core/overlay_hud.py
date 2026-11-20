"""
Floating Mini HUD & Emergency Stop Overlay
A lightweight, non-blocking, always-on-top button bar.
Allows the user to click STOP or PAUSE at any moment, regardless of keyboard focus.

Threading: the window's Tk root is created, used and destroyed on the HUD's own thread. Other
threads (the stop hotkey, the runner) only call close(), which sets a flag that the HUD thread
notices within 100 ms; they never touch Tk.
"""

import threading
import tkinter as tk


class FloatingOverlayHUD:
    CLOSE_POLL_MS = 100

    def __init__(self, on_stop_callback, on_pause_callback):
        self.on_stop = on_stop_callback
        self.on_pause = on_pause_callback
        self.root = None
        self.thread = None
        self.pause_btn = None
        self.is_paused = False
        self.ready = threading.Event()          # set once the window exists (or failed to)
        self.error = None                       # why the window could not be built, if it could not
        self._close_requested = threading.Event()

    def start(self):
        self.thread = threading.Thread(target=self._run_gui, daemon=True)
        self.thread.start()

    def _run_gui(self):
        try:
            self.root = tk.Tk()
            self.root.title("Macro Control")
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 0.90)
            self.root.overrideredirect(True) # Borderless floating pill

            # Position at top-center of primary screen
            screen_w = self.root.winfo_screenwidth()
            bar_w = 260
            bar_h = 36
            pos_x = (screen_w // 2) - (bar_w // 2)
            pos_y = 10
            self.root.geometry(f"{bar_w}x{bar_h}+{pos_x}+{pos_y}")

            # Dark theme styling
            frame = tk.Frame(self.root, bg="#1a1a1a", bd=1, relief="solid")
            frame.pack(fill="both", expand=True)

            # Drag handling so user can reposition if it covers UI
            def start_move(event):
                self.root.x = event.x
                self.root.y = event.y

            def do_move(event):
                deltax = event.x - self.root.x
                deltay = event.y - self.root.y
                new_x = self.root.winfo_x() + deltax
                new_y = self.root.winfo_y() + deltay
                self.root.geometry(f"+{new_x}+{new_y}")

            frame.bind("<ButtonPress-1>", start_move)
            frame.bind("<B1-Motion>", do_move)

            # Stop Button
            stop_btn = tk.Button(
                frame,
                text="🛑 STOP (F6)",
                bg="#d32f2f",
                fg="white",
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                cursor="hand2",
                command=self._handle_stop,
                padx=8
            )
            stop_btn.pack(side="left", padx=6, pady=4)

            # Pause Button
            self.pause_btn = tk.Button(
                frame,
                text="⏸️ PAUSE (F7)",
                bg="#333333",
                fg="#eeeeee",
                font=("Segoe UI", 9, "bold"),
                relief="flat",
                cursor="hand2",
                command=self._handle_pause,
                padx=8
            )
            self.pause_btn.pack(side="left", padx=4, pady=4)

            self.root.after(self.CLOSE_POLL_MS, self._poll_close)
            self.ready.set()
            self.root.mainloop()
        except tk.TclError as err:
            self.error = err
        finally:
            self.ready.set()
            # Tk must be torn down on the thread that made it. Dropping the last references
            # here (not later, from whichever thread frees this object) avoids Tcl aborting
            # with "async handler deleted by the wrong thread".
            self.pause_btn = None
            self.root = None

    def _poll_close(self):
        """Runs on the HUD thread: close the window when another thread asked for it."""
        root = self.root
        if root is None:
            return
        if self._close_requested.is_set():
            root.destroy()
            return
        root.after(self.CLOSE_POLL_MS, self._poll_close)

    def _handle_stop(self):
        if self.on_stop:
            self.on_stop()
        self.close()

    def _handle_pause(self):
        self.is_paused = not self.is_paused
        if self.pause_btn:
            self.pause_btn.config(
                text="▶️ RESUME" if self.is_paused else "⏸️ PAUSE (F7)",
                bg="#f57c00" if self.is_paused else "#333333"
            )
        if self.on_pause:
            self.on_pause()

    def close(self):
        """Safe from any thread: asks the HUD thread to close its window."""
        self._close_requested.set()
