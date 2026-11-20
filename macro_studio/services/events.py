"""Small publish/subscribe bus so tools react to each other without importing each other.

Events: client_started, client_closed, client_hung, client_recovered, all_closed,
ram_over, afk_none, runner_started, runner_stopped (contracts/services-contract.md).
A failing handler is reported and the remaining handlers still run.
"""

import threading


class EventBus:
    def __init__(self, on_error=None):
        self._on_error = on_error or (lambda event, exc: None)
        self._handlers = {}
        self._lock = threading.Lock()

    def subscribe(self, event, handler):
        """Register `handler(**payload)`; returns a function that unsubscribes it."""
        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

        def unsubscribe():
            with self._lock:
                handlers = self._handlers.get(event, [])
                if handler in handlers:
                    handlers.remove(handler)
        return unsubscribe

    def publish(self, event, **payload):
        with self._lock:
            handlers = list(self._handlers.get(event, []))
        for handler in handlers:
            try:
                handler(**payload)
            except Exception as exc:
                self._on_error(event, exc)
