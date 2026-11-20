"""Recording state, saving and playing with loops, without any Tk or Win32 (spec 003).

The hook, the world (windows and screenshots), the store and the player come in from outside, so
every rule is unit-tested. Playing holds the shared AutomationLock under the name "recorder": it
never runs together with Goliath, Mutation or Anti-AFK, and the emergency stop ends it.
"""

import threading
import time
from dataclasses import dataclass

from macro_studio.tools.recorder.model import BadRecording
from macro_studio.tools.recorder.player import SPEED_RANGE, Result
from macro_studio.tools.recorder.session import Session

OWNER = "recorder"
MAX_LOOPS = 999


@dataclass
class PlayOutcome:
    ok: bool
    reason: str = ""
    loops_done: int = 0
    stopped: bool = False
    unverified: int = 0


class RecorderService:
    def __init__(self, store, hook, world, automation, player_factory, log, clock=time.monotonic):
        """world: window_at, client_info, foreground, capture(hwnd, t) for the Session."""
        self._store, self._hook, self._world = store, hook, world
        self._automation, self._player_factory = automation, player_factory
        self._log, self._clock = log, clock
        self.session = None
        self.recording = False
        self.error = ""
        self._finished = None            # the last finished Session, until discarded
        self._saved_as = None            # the name it was saved under, if it was
        self._play_lock = threading.Lock()
        self.playing = False

    # ------------------------------------------------------------ recording
    @property
    def step_count(self):
        return len(self.session.steps) if self.session else 0

    @property
    def stop_requested(self):
        return bool(self.session and (self.session.stop_requested or self.session.full))

    @property
    def has_unsaved(self):
        return (self._finished is not None and bool(self._finished.steps)
                and self._saved_as is None)

    def begin_recording(self, pictures=True):
        self.error = ""
        if self.recording:
            return False
        if self.playing or self._automation.owner is not None:
            self.error = "Stop the running macro before recording."
            return False
        self.session = Session(self._world.window_at, self._world.client_info,
                               self._world.foreground, self._world.capture, pictures=pictures)
        if not self._hook.start():
            self.error = getattr(self._hook, "error", "") or "The input hook could not start."
            self.session = None
            return False
        self._finished = self._saved_as = None
        self.recording = True
        return True

    def tick(self):
        """Move queued events into the session. Call every ~50 ms while recording."""
        if self.recording and self.session is not None:
            self._hook.drain(self.session.feed)

    def end_recording(self):
        """Stop the hook and finish the session (anything held is released). Returns the session."""
        if not self.recording or self.session is None:
            return self._finished
        self.recording = False
        self._hook.stop()
        self.tick_remaining = self._hook.drain(self.session.feed, limit=10_000)
        self.session.finish(self._clock())
        self._finished = self.session
        return self._finished

    def discard(self):
        self._finished = self._saved_as = None

    def save(self, name, overwrite=False):
        """Save the last finished recording. Raises BadName or BadRecording."""
        session = self._finished
        if session is None or not session.steps:
            raise BadRecording("There is nothing recorded to save.")
        if not overwrite and self._store.exists(name):
            raise BadRecording(f"'{name}' already exists. Pick another name or overwrite it.")
        if self._saved_as is not None and name != self._saved_as:
            self._store.duplicate(self._saved_as, name)    # keeps the step pictures
        else:
            self._store.save(session.to_recording(name))
        self._saved_as = name

    # ------------------------------------------------------------ playing
    def play(self, name, hwnd, loops=1, speed=1.0, step_timeout=10.0, progress=None, verify=True):
        """Run a saved recording on `hwnd`, `loops` times (0 = until stopped). Blocks."""
        if self.recording:
            return PlayOutcome(False, "Finish recording first.")
        if not 0 <= loops <= MAX_LOOPS:
            return PlayOutcome(False, f"Loops must be between 0 and {MAX_LOOPS}.")
        if not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
            return PlayOutcome(False, f"Speed must be between {SPEED_RANGE[0]}x and "
                                      f"{SPEED_RANGE[1]}x.")
        try:
            rec = self._store.load(name)
        except (BadRecording, ValueError) as err:
            reason = str(err)
            return PlayOutcome(False, reason if "no recording" in reason.lower()
                               else f"No recording could be loaded: {reason}")
        if not self._play_lock.acquire(blocking=False):
            return PlayOutcome(False, "A recording is already playing.")
        try:
            if not self._automation.acquire(OWNER):
                return PlayOutcome(False, "Another macro is running; stop it first.")
            self.playing = True
            try:
                return self._loops(rec, hwnd, loops, speed, step_timeout, progress, verify)
            finally:
                self.playing = False
                self._automation.release(OWNER)
        finally:
            self._play_lock.release()

    def _loops(self, rec, hwnd, loops, speed, step_timeout, progress, verify=True):
        cache = {}

        def load_picture(file):
            if file not in cache:
                cache[file] = self._store.picture(rec.name, file)
            return cache[file]

        player = self._player_factory()
        done = unverified = 0
        while True:
            try:
                result = player.play(rec, hwnd, load_picture, speed=speed,
                                     step_timeout=step_timeout, progress=progress,
                                     verify=verify)
            except Exception as err:                  # noqa: BLE001 - one bad pass must not leak the lock
                self._log(f"[RECORDER] The replay crashed: {err}")
                return PlayOutcome(False, f"The replay crashed: {err}", done, False, unverified)
            unverified += getattr(result, "unverified", 0)
            if not result.ok:
                return PlayOutcome(False, result.reason, done, result.stopped, unverified)
            done += 1
            if (loops and done >= loops) or self._automation.stop_event.is_set():
                return PlayOutcome(True, "Done.", done, False, unverified)


__all__ = ["OWNER", "PlayOutcome", "RecorderService", "Result"]
