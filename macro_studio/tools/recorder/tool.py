"""The Recorder page: record your actions in Roblox, save them, play them back later (spec 003).

Press Record, play in a Roblox window, press F8: the recording is saved by name under
`recordings\\` beside the app and stays there. Pick a saved recording and a client and press Play:
every step is replayed with its original pause, each button press is checked against the screen
first, and F6 stops it at once. See specs/003-custom-recorder/spec.md for the rules.

Threads: the input hook has its own thread; recording polls on a scheduler worker; playing runs
on another one. Tk is only touched through `ctx.ui_call`; no window enumeration or capture runs
on the Tk thread.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from macro_studio.core import scaling
from macro_studio.core import ui_theme as ui
from macro_studio.core.inputhook import InputHook
from macro_studio.core.popup_guard import PopupGuard
from macro_studio.core.ui_theme import (
    BODY,
    CANVAS,
    ERROR_TEXT,
    INK,
    MUTE,
    R_LG,
    SP_MD,
    SP_SM,
    SP_XL,
    SP_XS,
    SURFACE,
    Button,
    Card,
    Field,
    Toggle,
)
from macro_studio.tools.macro.validation import FieldError, read_number
from macro_studio.tools.recorder.island import RecorderIsland
from macro_studio.tools.recorder.model import BadName, BadRecording, RecordingStore
from macro_studio.tools.recorder.player import SPEED_RANGE, Player
from macro_studio.tools.recorder.service import MAX_LOOPS, RecorderService
from macro_studio.tools.recorder.system import RealIO, World, window_rect

POLL_JOB, PLAY_JOB, LIST_JOB = "recorder:poll", "recorder:play", "recorder:list"
FOCUS_JOB = "recorder:focus"
POLL_S = 0.05
RING_EVERY_S = 0.12
MAX_RECORD_S = 15 * 60
PROGRESS_EVERY_S = 0.25
CHEVRON = "  ▾"
EMPTY = "No recordings yet. Press Record, play in a Roblox window, then press F8."
COLUMNS = (("name", "Name", 260, tk.W), ("steps", "Steps", 80, tk.E), ("length", "Length", 90, tk.E))


def length_text(seconds):
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class RecorderTool:
    page_titles = ["Recorder"]
    DEFAULTS = {"loops": 1, "speed": 1.0, "step_timeout_s": 10, "guard_profile": "Goliath",
                "client_pid": 0, "verify_clicks": False, "island": True}

    def __init__(self, shell, ctx, service=None, world=None, store=None, confirm=None, ask=None,
                 io=None, rect_of=None):
        self.shell, self.ctx, self.root = shell, ctx, shell.root
        self.settings = ctx.settings.view("recorder", self.DEFAULTS)
        self._confirm = confirm or (lambda title, text: messagebox.askyesno(title, text))
        self._ask = ask or (lambda title, prompt, initial: simpledialog.askstring(
            title, prompt, initialvalue=initial, parent=self.root))
        self.store = store or RecordingStore(scaling.get_base_dir())
        self.world = world or World()
        self._rect_of = rect_of or window_rect
        self.island = None
        self.io = io if io is not None else RealIO(self.world.wm)
        self._halt = threading.Event()        # the pop-up guard or the Stop button
        self.service = service or RecorderService(
            store=self.store, hook=InputHook(), world=self.world, automation=ctx.automation,
            player_factory=self._make_player, log=ctx.log)
        self.rows = []                        # latest clients (from the Clients tool's event)
        self._recordings = []
        self._unsubscribe = None
        self._record_started = 0.0
        self._last_ring = 0.0
        self._last_progress = 0.0
        self._finishing = False
        self.last_text = ""

    # ------------------------------------------------------------ wiring
    def start(self):
        self._unsubscribe = self.ctx.events.subscribe("clients_updated", self._on_clients)

    def on_view_changed(self, title):
        """Shell hook: read the recordings off disk when the page is opened, not at start-up."""
        if title in self.page_titles:
            self.refresh()

    def stop(self):
        if self._unsubscribe:
            self._unsubscribe()
        for name in (POLL_JOB, PLAY_JOB, LIST_JOB, FOCUS_JOB):
            self.ctx.scheduler.remove(name)
        self._halt.set()
        self._destroy_island()
        if self.service.recording:
            self.service.end_recording()
        self.ctx.automation.unregister_stoppable(self)

    def on_emergency_stop(self):
        self._halt.set()

    def _on_clients(self, rows, **_extra):
        self.rows = [r for r in rows if getattr(r, "hwnd", 0)]
        self.ctx.ui_call(self._sync_island)

    # ------------------------------------------------------------ the player
    def _wait(self, seconds):
        """Sleep, but give up the moment stop is pressed. False = stopped."""
        end = time.monotonic() + seconds
        stop = self.ctx.automation.stop_event
        while time.monotonic() < end:
            if stop.is_set() or self._halt.is_set():
                return False
            time.sleep(min(0.02, max(0.0, end - time.monotonic())))
        return not (stop.is_set() or self._halt.is_set())

    def _stopped(self):
        return self.ctx.automation.stop_event.is_set() or self._halt.is_set()

    def _guard_click(self, hwnd, x, y):
        info = self.io.client_info(hwnd)
        if not info or not (self.io.is_foreground(hwnd) or self.io.focus(hwnd)):
            return False
        self.io.move(info[0] + int(x), info[1] + int(y))
        self.io.button("left", True)
        try:
            time.sleep(0.05)
        finally:
            self.io.button("left", False)
        return True

    def _guard_config(self):
        try:
            from macro_studio.profiles import ProfileManager
            return ProfileManager().load_profile(self.settings.get("guard_profile")) or {}
        except (OSError, ValueError, KeyError):
            return {}

    def _make_player(self):
        config = self._guard_config()
        guard = PopupGuard(config, scaling.get_resource_path("templates"),
                           capture=self.world.capture_now, click=self._guard_click,
                           log=self.ctx.log, wait=self._wait, pause=self._halt.set,
                           stop=self._halt.set)
        if not guard.active:
            protection = ("Only the picture check protects the clicks"
                          if self.settings.get("verify_clicks")
                          else "Nothing checks the screen before a click")
            self.ctx.log(f"[RECORDER] No purchase pop-up picture is set for profile "
                         f"'{self.settings.get('guard_profile')}'. {protection}. "
                         f"Capture it in Settings.")
        return Player(self.io, capture=self.world.capture_now, guard=guard if guard.active else None,
                      wait=self._wait, stop=self._stopped, log=self.ctx.log)

    # ------------------------------------------------------------ page
    def build_pages(self, frames):
        (page,) = frames
        ui.table_style(self.root)

        rec = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        rec.pack(fill="x", padx=SP_XL, pady=(SP_XL, SP_SM))
        ui.heading(rec.body, "Record", size=15).pack(anchor="w")
        ui.caption(rec.body, "Records mouse moves, clicks, scrolling and keys inside a Roblox "
                             "window, and nothing else. Press F8 to stop. F6, F7 and Esc are never "
                             "recorded.").pack(anchor="w", pady=(2, SP_SM))
        bar = tk.Frame(rec.body, bg=SURFACE)
        bar.pack(fill="x")
        self.btn_record = Button(bar, "Record", command=self.toggle_record, kind="primary",
                                 height=34, page_bg=SURFACE)
        self.btn_record.pack(side="left")
        self.lbl_state = tk.Label(bar, text="Not recording.", font=ui.F_BODY(), bg=SURFACE,
                                  fg=BODY, anchor="w")
        self.lbl_state.pack(side="left", padx=SP_MD)

        self.save_row = tk.Frame(rec.body, bg=SURFACE)
        tk.Label(self.save_row, text="Name", font=ui.F_BODY(), bg=SURFACE, fg=INK).pack(
            side="left")
        self.e_name = Field(self.save_row, value="", width=22)
        self.e_name.pack(side="left", padx=SP_SM)
        Button(self.save_row, "Save recording", command=self.save_clicked, kind="primary",
               height=32, page_bg=SURFACE).pack(side="left")
        Button(self.save_row, "Discard", command=self.discard_clicked, kind="ghost", height=32,
               page_bg=SURFACE).pack(side="left", padx=(SP_SM, 0))
        self.lbl_save = tk.Label(rec.body, text="", font=ui.F_CAPTION(), bg=SURFACE, fg=BODY,
                                 anchor="w", justify="left")

        lib = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        lib.pack(fill="both", expand=True, padx=SP_XL, pady=SP_SM)
        ui.heading(lib.body, "Saved recordings", size=15).pack(anchor="w")
        holder = tk.Frame(lib.body, bg=SURFACE)
        holder.pack(fill="both", expand=True, pady=(SP_SM, SP_SM))
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in COLUMNS], show="headings",
                                 style=ui.TABLE_STYLE, selectmode="browse", height=5)
        for key, label, width, anchor in COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor=anchor,  # pyright: ignore[reportArgumentType]
                             stretch=key == "name")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        self.tree.bind("<Double-1>", lambda _e: self.play_clicked())
        self.lbl_empty = tk.Label(holder, text=EMPTY, font=ui.F_BODY(), bg=SURFACE, fg=MUTE)

        manage = tk.Frame(lib.body, bg=SURFACE)
        manage.pack(fill="x")
        self.btn_rename = Button(manage, "Rename", command=self.rename_clicked, kind="secondary",
                                 height=30, page_bg=SURFACE, state="disabled")
        self.btn_copy = Button(manage, "Duplicate", command=self.duplicate_clicked,
                               kind="secondary", height=30, page_bg=SURFACE, state="disabled")
        self.btn_delete = Button(manage, "Delete", command=self.delete_clicked, kind="danger",
                                 height=30, page_bg=SURFACE, state="disabled")
        for b in (self.btn_rename, self.btn_copy, self.btn_delete):
            b.pack(side="left", padx=(0, SP_SM))

        play = Card(page, radius=R_LG, padding=(SP_MD, SP_MD), page_bg=CANVAS)
        play.pack(fill="x", padx=SP_XL, pady=(SP_SM, SP_XL))
        ui.heading(play.body, "Play", size=15).pack(anchor="w")
        pick = tk.Frame(play.body, bg=SURFACE)
        pick.pack(fill="x", pady=(SP_SM, SP_SM))
        tk.Label(pick, text="Play on", font=ui.F_BODY(), bg=SURFACE, fg=INK).pack(side="left")
        self.btn_client = Button(pick, "Client" + CHEVRON, command=self._pick_client,
                                 kind="secondary", height=32, page_bg=SURFACE, width=340)
        self.btn_client.pack(side="left", padx=(SP_SM, 0))
        opts = tk.Frame(play.body, bg=SURFACE)
        opts.pack(fill="x", pady=(0, SP_SM))
        self.e_loops = self._option(opts, "Repeat", str(self.settings.get("loops")), first=True)
        self.e_speed = self._option(opts, f"Speed ({SPEED_RANGE[0]}-{SPEED_RANGE[1]}x)",
                                    str(self.settings.get("speed")))
        self.e_timeout = self._option(opts, "Wait for the screen (s)",
                                      str(self.settings.get("step_timeout_s")))
        ui.caption(play.body, f"Repeat: 0 plays until you stop it, up to {MAX_LOOPS} otherwise.").pack(
            fill="x", pady=(0, SP_SM))
        self.var_verify = tk.BooleanVar(value=bool(self.settings.get("verify_clicks")))
        self._switch(play.body, "Check the screen before each click", self.var_verify,
                     self._verify_changed,
                     "Off: every click goes to its exact recorded spot, nothing is scanned. On: it "
                     "first waits (up to the time above) for the spot to look as it did when "
                     "recorded, and stops instead of clicking the wrong thing. Also saves a picture "
                     "of each click while recording.")
        self.var_island = tk.BooleanVar(value=bool(self.settings.get("island")))
        self._switch(play.body, "Show the island above the selected Roblox window", self.var_island,
                     self._island_changed,
                     "A small pill with Record, Play and Stop. It brings that window to the front "
                     "when you use it. Right-click the pill to hide it.")
        go = tk.Frame(play.body, bg=SURFACE)
        go.pack(fill="x")
        self.btn_play = Button(go, "Play", command=self.play_clicked, kind="primary", height=34,
                               page_bg=SURFACE, state="disabled")
        self.btn_play.pack(side="left")
        self.btn_stop = Button(go, "Stop", command=self.stop_clicked, kind="danger", height=34,
                               page_bg=SURFACE, state="disabled")
        self.btn_stop.pack(side="left", padx=(SP_SM, 0))
        self.lbl_play = tk.Label(go, text="Pick a recording and a client.", font=ui.F_BODY(),
                                 bg=SURFACE, fg=BODY, anchor="w", justify="left")
        self.lbl_play.pack(side="left", padx=SP_MD)
        self.lbl_guard = ui.caption(play.body, "")
        self.lbl_guard.pack(fill="x", pady=(SP_SM, 0))
        self._guard_note()
        if self.settings.get("island"):
            self._build_island()

    def _switch(self, parent, text, var, command, hint):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=(SP_XS, 0))
        tk.Label(row, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK, anchor="w").pack(side="left")
        Toggle(row, var, page_bg=SURFACE, command=command).pack(side="right")
        ui.caption(parent, hint).pack(fill="x", pady=(0, SP_XS))

    def _verify_changed(self):
        self.settings.set("verify_clicks", bool(self.var_verify.get()))
        self.ctx.settings.save()

    def _island_changed(self):
        self.settings.set("island", bool(self.var_island.get()))
        self.ctx.settings.save()
        if self.var_island.get():
            self._build_island()
        else:
            self._destroy_island()

    def _hide_island(self):
        """Right-click on the island: same as turning its switch off (saved)."""
        self.var_island.set(False)
        self._island_changed()

    def _option(self, parent, text, value, first=False):
        tk.Label(parent, text=text, font=ui.F_BODY(), bg=SURFACE, fg=INK).pack(
            side="left", padx=(0 if first else SP_MD, SP_SM))
        field = Field(parent, value=value, width=6)
        field.pack(side="left")
        return field

    def _guard_note(self):
        cfg = self._guard_config().get("popup_guard") or {}
        on = bool(cfg.get("enabled") and cfg.get("template"))
        self.lbl_guard.config(
            text=("Purchase pop-up guard is on: no click goes out while a Robux pop-up covers the "
                  "game." if on else
                  "Purchase pop-up guard is off: capture the Robux pop-up in Settings to turn it "
                  "on. Until then only the screen check protects each click."),
            fg=BODY if on else MUTE)

    # ------------------------------------------------------------ recording
    def toggle_record(self):
        if self.service.recording:
            self._finish_recording()
            return
        if self.service.playing:
            self._say_state("Stop playing before recording.", error=True)
            return
        self.save_row.pack_forget()
        self.lbl_save.pack_forget()
        if not self.service.begin_recording(pictures=bool(self.settings.get("verify_clicks"))):
            self._say_state(self.service.error or "Recording could not start.", error=True)
            return
        self._finishing = False
        self._record_started = time.monotonic()
        self.world.ring.clear()
        self.btn_record.set_text("Stop recording")
        self.btn_record.set_kind("danger")
        self.btn_play.set_state("disabled")
        self._say_state("Recording... click into a Roblox window and play. Press F8 to stop.")
        self.ctx.scheduler.add(POLL_JOB, self._poll_job, interval_s=POLL_S, run_now=True,
                               isolated=True)

    def _poll_job(self):
        """Scheduler worker: events to steps, keep a fresh frame for the picture before a click."""
        if not self.service.recording:
            return
        self.service.tick()
        now = time.monotonic()
        if self.settings.get("verify_clicks") and now - self._last_ring >= RING_EVERY_S:
            self._last_ring = now
            hwnd = self.world.window_under_cursor()
            if hwnd:
                self.world.ring.grab(hwnd)
        over = now - self._record_started > MAX_RECORD_S
        count = self.service.step_count
        if self.service.stop_requested or over:
            self.ctx.ui_call(self._finish_recording)
        else:
            self.ctx.ui_call(lambda: (self._say_state(
                f"Recording... {count} steps. Press F8 to stop."),
                self._island_state("recording", f"{count} steps")))

    def _finish_recording(self):
        if self._finishing or not self.service.recording:
            return
        self._finishing = True
        self.ctx.scheduler.remove(POLL_JOB)
        session = self.service.end_recording()
        self.btn_record.set_text("Record")
        self.btn_record.set_kind("primary")
        if session is None or not session.steps:
            self._say_state("Nothing was recorded. Click inside a Roblox window while recording.",
                            error=True)
            return
        steps = len(session.steps)
        seconds = sum(s.dt for s in session.steps) / 1000.0
        note = (f" {session.no_picture} click(s) have no picture and replay on timing only."
                if session.no_picture else "")
        self._say_state(f"Recorded {steps} steps, {length_text(seconds)}.{note}")
        self._island_state("ready", f"{steps} steps recorded")
        self.e_name.set(self._suggest_name())
        self.save_row.pack(fill="x", pady=(SP_SM, 0))
        self.lbl_save.pack(fill="x")
        self.lbl_save.config(text="Save it to play it later.", fg=MUTE)

    def _suggest_name(self):
        taken = {r.name for r in self._recordings}
        n = 1
        while f"Recording {n}" in taken:
            n += 1
        return f"Recording {n}"

    def save_clicked(self):
        name = self.e_name.get().strip()
        try:
            try:
                self.service.save(name)
            except BadRecording as err:
                if "already exists" not in str(err) or not self._confirm(
                        "Replace recording", f"'{name}' already exists. Replace it?"):
                    raise
                self.service.save(name, overwrite=True)
        except (BadName, BadRecording, OSError) as err:
            self.lbl_save.config(text=str(err), fg=ERROR_TEXT)
            return False
        self.save_row.pack_forget()
        self.lbl_save.config(text=f"Saved as '{name}'.", fg=BODY)
        self.refresh(select=name)
        return True

    def discard_clicked(self):
        self.service.discard()
        self.save_row.pack_forget()
        self.lbl_save.pack_forget()
        self._say_state("Discarded.")

    def _say_state(self, text, error=False):
        self.lbl_state.config(text=text, fg=ERROR_TEXT if error else BODY)
        if error:
            self._island_state("error", text[:28])

    # ------------------------------------------------------------ the list
    def refresh(self, select=None):
        """Read the recordings off the Tk thread, then show them."""
        def work():
            rows = self.store.list()
            self.ctx.ui_call(lambda: self.show_recordings(rows, select))
        self.ctx.scheduler.add(LIST_JOB, work, interval_s=3600, run_now=True, once=True)

    def show_recordings(self, rows, select=None):
        self._recordings = list(rows)
        keep = select or self.selected_name()
        self.tree.delete(*self.tree.get_children())
        for r in self._recordings:
            self.tree.insert("", "end", iid=r.name,
                             values=(r.name, r.steps, length_text(r.seconds)))
        if self._recordings:
            self.lbl_empty.place_forget()
        else:
            self.lbl_empty.place(relx=0.5, rely=0.5, anchor="center")
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        self._selection_changed()
        if self.island is not None:
            self.island.set_recordings([r.name for r in self._recordings], self.selected_name())

    def selected_name(self):
        chosen = self.tree.selection()
        return chosen[0] if chosen else None

    def _selection_changed(self):
        if self.island is not None and self.selected_name() in self.island.names:
            self.island.set_recordings(self.island.names, self.selected_name())
        has = self.selected_name() is not None
        state = "normal" if has else "disabled"
        for b in (self.btn_rename, self.btn_copy, self.btn_delete):
            b.set_state(state)
        self._update_play_button()

    def _update_play_button(self):
        ready = (self.selected_name() is not None and not self.service.playing
                 and not self.service.recording)
        self.btn_play.set_state("normal" if ready else "disabled")

    def rename_clicked(self):
        name = self.selected_name()
        new = self._ask("Rename recording", "New name:", name) if name else None
        if new and new.strip() != name:
            self._file_action(lambda: self.store.rename(name, new.strip()), select=new.strip())

    def duplicate_clicked(self):
        name = self.selected_name()
        new = self._ask("Duplicate recording", "Name of the copy:", f"{name} copy") if name else None
        if new:
            self._file_action(lambda: self.store.duplicate(name, new.strip()), select=new.strip())

    def delete_clicked(self):
        name = self.selected_name()
        if name and self._confirm("Delete recording",
                                  f"Delete '{name}' and its pictures? This cannot be undone."):
            self._file_action(lambda: self.store.delete(name))

    def _file_action(self, action, select=None):
        try:
            action()
        except (BadName, BadRecording, OSError) as err:
            self.lbl_play.config(text=str(err), fg=ERROR_TEXT)
            return
        self.refresh(select=select)

    # ------------------------------------------------------------ choosing a client
    def _client_label(self, row):
        return f"{row.account or row.title or 'Roblox'}  (PID {row.pid})"

    def _pick_client(self):
        menu = tk.Menu(self.root, tearoff=0)
        if not self.rows:
            menu.add_command(label="No Roblox client found (open the Clients page)",
                             state="disabled")
        for row in self.rows:
            menu.add_command(label=self._client_label(row),
                             command=lambda r=row: self._set_client(r.pid))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _set_client(self, pid):
        self.settings.set("client_pid", int(pid))
        self.ctx.settings.save()
        row = self._chosen_row()
        self.btn_client.set_text((self._client_label(row) if row else "Client") + CHEVRON)
        self._sync_island()
        if row is not None:
            self._focus_then(row.hwnd, None)

    def _chosen_row(self):
        wanted = self.settings.get("client_pid")
        for row in self.rows:
            if row.pid == wanted:
                return row
        return self.rows[0] if self.rows else None

    # ------------------------------------------------------------ playing
    def play_clicked(self):
        name = self.selected_name()
        if not name or self.service.playing or self.service.recording:
            return False
        row = self._chosen_row()
        if row is None:
            self._play_say("No Roblox client is open. Start one, then try again.", error=True)
            return False
        try:
            loops = read_number(self.e_loops, "Repeat", minimum=0, maximum=MAX_LOOPS)
            speed = read_number(self.e_speed, "Speed", kind=float, minimum=SPEED_RANGE[0],
                                maximum=SPEED_RANGE[1])
            timeout = read_number(self.e_timeout, "Wait for the screen", minimum=1, maximum=120)
        except FieldError as err:
            err.field.set_error(str(err))
            self._play_say(str(err), error=True)
            return False
        for field in (self.e_loops, self.e_speed, self.e_timeout):
            field.set_error(None)
        for key, value in (("loops", loops), ("speed", speed), ("step_timeout_s", timeout)):
            self.settings.set(key, value)
        self.ctx.settings.save()
        self._halt.clear()
        self.btn_play.set_state("disabled")
        self.btn_stop.set_state("normal")
        self._play_say(f"Playing '{name}'...")
        hwnd = row.hwnd
        self.ctx.automation.register_stoppable(self)
        verify = bool(self.settings.get("verify_clicks"))
        self._island_state("playing", "Starting")
        self.ctx.scheduler.add(PLAY_JOB,
                               lambda: self._play_job(name, hwnd, loops, speed, timeout, verify),
                               interval_s=3600, run_now=True, isolated=True, once=True)
        return True

    def _play_job(self, name, hwnd, loops, speed, timeout, verify=False):
        """Scheduler worker: run the recording, then report on the Tk thread."""
        try:
            outcome = self.service.play(name, hwnd, loops=loops, speed=speed,
                                        step_timeout=timeout, progress=self._progress,
                                        verify=verify)
        finally:
            self.ctx.automation.unregister_stoppable(self)
        self.ctx.ui_call(lambda: self.show_outcome(name, outcome))

    def _progress(self, index, total):
        now = time.monotonic()
        if now - self._last_progress < PROGRESS_EVERY_S and index < total:
            return
        self._last_progress = now
        self.ctx.ui_call(lambda: (self._play_say(f"Playing... step {index} of {total}"),
                                  self._island_state("playing", f"{index} / {total}")))

    def show_outcome(self, name, outcome):
        self.btn_stop.set_state("disabled")
        self._update_play_button()
        if outcome.ok:
            times = "once" if outcome.loops_done == 1 else f"{outcome.loops_done} times"
            note = (f" {outcome.unverified} click(s) ran on timing only." if outcome.unverified
                    else "")
            text = f"Played '{name}' {times}.{note}"
        elif outcome.stopped:
            text = f"Stopped after {outcome.loops_done} full run(s)."
        else:
            text = outcome.reason
        self.last_text = text
        self._island_state("ready" if outcome.ok or outcome.stopped else "error",
                           "Done" if outcome.ok else ("Stopped" if outcome.stopped else "Failed"))
        self._play_say(text, error=not outcome.ok and not outcome.stopped)
        self.ctx.log(f"[RECORDER] {text}")

    def stop_clicked(self):
        self._halt.set()

    def _play_say(self, text, error=False):
        self.lbl_play.config(text=text, fg=ERROR_TEXT if error else BODY)


    # ------------------------------------------------------------ the island
    def _build_island(self):
        if self.island is not None:
            return
        self.island = RecorderIsland(
            self.root, lambda hwnd: self._rect_of(hwnd),
            {"on_record": self._island_record, "on_play": self.play_clicked,
             "on_stop": self.stop_clicked, "on_select": self._island_select,
             "on_hide": lambda: self.root.after_idle(self._hide_island)})
        self.island.set_recordings([r.name for r in self._recordings], self.selected_name())
        self._sync_island()

    def _destroy_island(self):
        if self.island is not None:
            self.island.destroy()
            self.island = None

    def _sync_island(self):
        if self.island is not None:
            row = self._chosen_row()
            self.island.set_target(row.hwnd if row is not None else None)

    def _island_state(self, mode, text):
        if self.island is not None:
            self.island.set_state(mode, text)

    def _island_select(self, name):
        if name and self.tree.exists(name):
            self.tree.selection_set(name)
            self._update_play_button()

    def _island_record(self):
        """Record or stop from the island: the chosen window comes to the front first."""
        if self.service.recording:
            self._finish_recording()
            return
        row = self._chosen_row()
        self._focus_then(row.hwnd if row is not None else None, self.toggle_record)

    def _focus_then(self, hwnd, then):
        """Bring a Roblox window to the front on a worker (it can take a second), then `then`."""
        def work():
            if hwnd:
                self.io.focus(hwnd)
            if then is not None:
                self.ctx.ui_call(then)
        self.ctx.scheduler.add(FOCUS_JOB, work, interval_s=3600, run_now=True, isolated=True,
                               once=True)
