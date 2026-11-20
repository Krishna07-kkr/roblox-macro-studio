"""ui_call must never raise into a worker thread, even after the window is gone (A3, H9)."""

import tkinter as tk
from types import SimpleNamespace

import pytest

from macro_studio.app import MacroStudioApp


class Root:
    def __init__(self, error=None):
        self.error, self.scheduled = error, []

    def after(self, ms, fn):
        if self.error:
            raise self.error
        self.scheduled.append((ms, fn))


def app_with(root):
    app = MacroStudioApp.__new__(MacroStudioApp)
    app.root = root
    return app


def test_ui_call_schedules_on_the_tk_thread():
    root = Root()
    marker = object()
    app_with(root).ui_call(marker)
    assert root.scheduled == [(0, marker)]


@pytest.mark.parametrize("error", [tk.TclError("application has been destroyed"),
                                   RuntimeError("main thread is not in main loop")])
def test_ui_call_after_the_window_is_gone_is_silent(error):
    app_with(Root(error)).ui_call(lambda: None)        # must not raise


def test_other_errors_still_surface():
    with pytest.raises(ValueError):
        app_with(Root(ValueError("a real bug"))).ui_call(lambda: None)


def test_the_macro_tool_sends_worker_thread_updates_through_ui_call():
    import inspect

    from macro_studio.tools.macro.tool import MacroTool
    for method in (MacroTool._append_log, MacroTool._update_stats):
        source = inspect.getsource(method)
        assert "root.after(" not in source, method.__name__
        assert "ui_call" in source, method.__name__
    assert SimpleNamespace  # keep the import used
