"""One worker thread for every periodic job (Constitution II).

Tools register named jobs (client scan every 3 s, RAM check, anti-AFK, summaries)
instead of starting their own threads. Jobs run off the Tk thread, so a job never
overlaps itself. A job that raises is reported and kept.

Most jobs run one after another on the scheduler thread. A job that talks to another
process (screen capture of a game client that may be frozen) is registered with
`isolated=True`: it runs on its own worker thread, so if it hangs it holds up nobody else,
and it is not started again until the stuck run ends. Any job that takes longer than
`slow_after_s` is reported through `on_slow`, at most once a minute per job.

One long-lived worker per isolated job NAME, never one thread per run: every new thread that
calls into Tkinter leaves a Windows handle behind for good (measured: 300 short threads
calling root.after = +301 handles; found by scripts/soak.py), and the live feed runs 2.5
times a second. A worker is reused when its job is removed and added again (the Tabs picker
does that on every open) and ends only when the scheduler stops.
"""

import random
import threading
import time


class _Job:
    __slots__ = (
        "fn",
        "interval_s",
        "isolated",
        "jitter_s",
        "last_slow_report",
        "name",
        "next_run",
        "once",
        "running",
    )

    def __init__(self, name, fn, interval_s, jitter_s, next_run, isolated=False, once=False):
        self.name, self.fn = name, fn
        self.interval_s, self.jitter_s = interval_s, jitter_s
        self.next_run = next_run
        self.isolated = isolated
        self.once = once                 # run a single time, then drop the job
        self.running = False
        self.last_slow_report = None


class _Worker:
    """The long-lived thread behind one isolated job name."""

    __slots__ = ("job", "quit", "thread", "wake")

    def __init__(self):
        self.wake = threading.Event()
        self.job = None
        self.quit = False
        self.thread: threading.Thread | None = None


class Scheduler:
    SLOW_REPORT_EVERY_S = 60.0

    def __init__(self, clock=time.monotonic, rng=random.uniform, on_error=None,
                 idle_wait_s=0.05, on_slow=None, slow_after_s=1.0):
        self._clock = clock
        self._rng = rng
        self._on_error = on_error or (lambda name, exc: None)
        self._on_slow = on_slow or (lambda name, seconds: None)
        self._slow_after_s = slow_after_s
        self._idle_wait_s = idle_wait_s
        self._jobs = {}
        self._workers = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------ jobs
    def _delay(self, job):
        jitter = self._rng(-job.jitter_s, job.jitter_s) if job.jitter_s else 0.0
        return max(0.0, job.interval_s + jitter)

    def add(self, name, fn, interval_s, jitter_s=0.0, run_now=False, isolated=False, once=False):
        """Register (or replace) a job by name.

        `isolated` runs it on its own worker thread; `once` runs it a single time (use with
        run_now) and then removes it, for "do this now" buttons that need no thread of their own.
        """
        job = _Job(name, fn, float(interval_s), float(jitter_s), 0.0, isolated=isolated, once=once)
        job.next_run = self._clock() if run_now else self._clock() + self._delay(job)
        with self._lock:
            self._jobs[name] = job
        self._wake.set()

    def remove(self, name):
        with self._lock:
            self._jobs.pop(name, None)

    def next_run(self, name):
        with self._lock:
            job = self._jobs.get(name)
            return job.next_run if job else None

    def running_jobs(self):
        """How many isolated runs are in progress right now."""
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.running)

    def run_pending(self):
        """Start every job that is due. The worker loop calls this; tests call it directly.

        Ordinary jobs run to completion here. Isolated jobs are handed to their own thread
        and skipped while their previous run is still going.
        """
        now = self._clock()
        with self._lock:
            due = [job for job in self._jobs.values() if job.next_run <= now and not job.running]
        for job in due:
            if job.isolated:
                with self._lock:
                    job.running = True
                    worker = self._workers.get(job.name)
                    if worker is None:
                        worker = self._workers[job.name] = _Worker()
                        thread = threading.Thread(
                            target=self._work, args=(worker,), daemon=True,
                            name=f"studio-job-{job.name}")
                        worker.thread = thread
                        thread.start()
                    worker.job = job
                worker.wake.set()
            else:
                self._run_job(job)

    def _work(self, worker):
        """Worker thread: run whichever job was handed over, until told to quit."""
        while True:
            worker.wake.wait()
            worker.wake.clear()
            if worker.quit:
                return
            job = worker.job
            if job is not None:
                self._run_job(job)

    def _run_job(self, job):
        started = self._clock()
        try:
            job.fn()
        except Exception as exc:  # noqa: BLE001 - a job must never take the scheduler down
            self._on_error(job.name, exc)
        finally:
            finished = self._clock()
            self._report_if_slow(job, finished - started, finished)
            with self._lock:
                job.running = False
                if self._jobs.get(job.name) is job:  # not replaced or removed meanwhile
                    if job.once:
                        del self._jobs[job.name]
                    else:
                        job.next_run = finished + self._delay(job)
            self._wake.set()

    def _report_if_slow(self, job, elapsed, now):
        last = job.last_slow_report
        if elapsed <= self._slow_after_s or (
                last is not None and now - last < self.SLOW_REPORT_EVERY_S):
            return
        job.last_slow_report = now
        try:
            self._on_slow(job.name, elapsed)
        except Exception as exc:  # noqa: BLE001 - reporting must not break the job loop
            self._on_error(job.name, exc)

    # ------------------------------------------------------------ thread
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _seconds_until_next(self):
        with self._lock:
            waiting = [j.next_run for j in self._jobs.values() if not j.running]
            if not waiting:
                return self._idle_wait_s
            return max(0.0, min(waiting) - self._clock())

    def _loop(self):
        while not self._stop.is_set():
            self.run_pending()
            self._wake.wait(timeout=min(self._seconds_until_next(), 0.5))
            self._wake.clear()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="studio-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout=1.0):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        # Only now, with the scheduler thread gone, nothing can start a new worker.
        with self._lock:
            workers, self._workers = list(self._workers.values()), {}
        for worker in workers:                  # idle ones end now; a stuck one ends when it returns
            worker.quit = True
            worker.wake.set()
