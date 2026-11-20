"""The list of tools the studio shows. Adding a tool = one module + one entry here.

Each entry's factory imports its tool lazily, so importing the registry stays cheap and
free of Tk. The shell calls `factory(shell, ctx)` and the returned object follows the
tool contract (specs/001-modular-multitool/contracts/tool-contract.md):

    page_titles            list of page names this tool adds to the navigation
    build_pages(frames)    fill one frame per page title (required)
    build_sidebar(parent)  optional widgets above the navigation (e.g. profile switcher)
    build_topbar(parent)   optional widgets in the top bar beside the page title
    start() / stop()       optional: register / unregister jobs, hotkeys, handlers
    on_emergency_stop()    optional: stop any automation immediately
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ToolSpec:
    id: str
    title: str
    order: int
    factory: Callable


def ordered(specs):
    """Tools sorted for the sidebar; duplicate ids are a programming error."""
    seen = set()
    for spec in specs:
        if spec.id in seen:
            raise ValueError(f"Two tools registered with the id '{spec.id}'.")
        seen.add(spec.id)
    return sorted(specs, key=lambda s: (s.order, s.id))


def _macro(shell, ctx):
    from macro_studio.tools.macro.tool import MacroTool
    return MacroTool(shell, ctx)


def _clients(shell, ctx):
    from macro_studio.tools.clients.tool import ClientsTool
    return ClientsTool(shell, ctx)


def _memory(shell, ctx):
    from macro_studio.tools.memory.tool import MemoryTool
    return MemoryTool(shell, ctx)


def _layout(shell, ctx):
    from macro_studio.tools.layout.tool import LayoutTool
    return LayoutTool(shell, ctx)


def _afk(shell, ctx):
    from macro_studio.tools.afk.tool import AfkTool
    return AfkTool(shell, ctx)


def _notify(shell, ctx):
    from macro_studio.tools.notify.tool import NotifyTool
    return NotifyTool(shell, ctx)


def _system(shell, ctx):
    from macro_studio.tools.system.tool import SystemTool
    return SystemTool(shell, ctx)


def _recorder(shell, ctx):
    from macro_studio.tools.recorder.tool import RecorderTool
    return RecorderTool(shell, ctx)


def _logs(shell, ctx):
    from macro_studio.tools.logs.tool import LogsTool
    return LogsTool(shell, ctx)


TOOLS = [
    ToolSpec(id="macro", title="Macro", order=0, factory=_macro),
    ToolSpec(id="recorder", title="Recorder", order=1, factory=_recorder),
    ToolSpec(id="clients", title="Clients", order=2, factory=_clients),
    ToolSpec(id="memory", title="Memory", order=3, factory=_memory),
    ToolSpec(id="layout", title="Layout", order=4, factory=_layout),
    ToolSpec(id="afk", title="Anti-AFK", order=5, factory=_afk),
    ToolSpec(id="notify", title="Notifications", order=6, factory=_notify),
    ToolSpec(id="system", title="System", order=7, factory=_system),
    ToolSpec(id="logs", title="Logs", order=8, factory=_logs),
]
