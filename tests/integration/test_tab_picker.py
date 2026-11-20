"""Tabs picker: scrolling, multi-select and the right-click menu (real Tk, own process).

Handlers are called directly. Nothing sends keys to "whatever has focus" and the real pop-up
menu is never posted, because a modal menu can hang the run (see CLAUDE.md).
"""

import sys
import tkinter as tk
from types import SimpleNamespace

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


class Scheduler:
    def add(self, *args, **kwargs):
        pass

    def remove(self, *args, **kwargs):
        pass


class Actions:
    def __init__(self):
        self.calls = []

    def run(self, name, tabs):
        self.calls.append((name, [t.pid for t in tabs]))


def make_tabs(n, image):
    return [SimpleNamespace(hwnd=1000 + i, pid=100 + i, title=f"Roblox {i}", image=image)
            for i in range(n)]


def labels(menu):
    last = menu.index("end")
    if last is None:
        return []
    return [menu.entrycget(i, "label") for i in range(last + 1)
            if menu.type(i) not in ("separator",)]


def entry(menu, text):
    """Index of the first menu entry whose label starts with `text`."""
    last = menu.index("end")
    for i in range(last + 1):
        if menu.type(i) != "separator" and menu.entrycget(i, "label").startswith(text):
            return i
    return None


def run():
    from PIL import Image

    from macro_studio.core.tab_picker import TabPicker
    from macro_studio.core.tab_selection import TabSelection

    root = tk.Tk()
    root.geometry("1000x800+0+0")
    anchor = tk.Button(root, text="Tabs")
    anchor.pack()
    root.update()
    image = Image.new("RGB", (192, 144), (30, 90, 160))
    asked = []

    def make(n, selection=None, actions=None, answer=True):
        selection = selection or TabSelection()
        tabs = make_tabs(n, image)
        picker = TabPicker(root, anchor, selection, snapshot=lambda: tabs, scheduler=Scheduler(),
                           ui_call=lambda fn: fn(), minimized_count=lambda: 0,
                           actions=actions, confirm=lambda title, text: asked.append(text) or answer)
        picker.show_tabs(tabs, 0)
        root.update()
        return picker, selection, tabs

    # ---------------- scrolling
    picker, selection, tabs = make(12)
    check("many tabs fit on screen: the list gets a limited height",
          picker.view_height() < picker.content_height(), (picker.view_height(), picker.content_height()))
    check("the popover stays inside the screen",
          picker.winfo_rooty() + picker.winfo_height() <= root.winfo_screenheight(),
          (picker.winfo_rooty(), picker.winfo_height(), root.winfo_screenheight()))
    check("starts at the top", picker.canvas.yview()[0] == 0.0)
    picker.on_wheel(SimpleNamespace(delta=-120))
    root.update()
    check("mouse wheel scrolls down", picker.canvas.yview()[0] > 0.0, picker.canvas.yview())
    picker.on_wheel(SimpleNamespace(delta=120))
    root.update()
    check("and back up", picker.canvas.yview()[0] == 0.0, picker.canvas.yview())
    for _ in range(100):
        picker.on_wheel(SimpleNamespace(delta=-120))
    root.update()
    check("cannot scroll past the last row", picker.canvas.yview()[1] <= 1.0 + 1e-9
          and picker.canvas.yview()[1] > 0.99, picker.canvas.yview())
    picker.close()
    root.update()

    picker, selection, tabs = make(3)
    check("few tabs: no scrolling needed", picker.view_height() >= picker.content_height() - 1,
          (picker.view_height(), picker.content_height()))
    picker.on_wheel(SimpleNamespace(delta=-120))
    root.update()
    check("wheel does nothing when everything is visible", picker.canvas.yview()[0] == 0.0)
    picker.close()
    root.update()

    # ---------------- selecting several
    picker, selection, tabs = make(6)
    picker.click_tab(101)
    picker.click_tab(103)
    check("clicking an unticked-from-all tab unticks only it, then the next",
          selection.pids == frozenset({100, 102, 104, 105}), selection.pids)
    picker.click_tab(101)
    check("clicking again ticks it back", 101 in selection.pids, selection.pids)

    selection.select_only([100], [t.pid for t in tabs])
    picker.anchor_pid = 100
    picker.click_tab(103, shift=True)
    check("shift-click ticks the whole range", selection.pids == frozenset({100, 101, 102, 103}),
          selection.pids)
    picker.click_tab(105, shift=True)
    check("shift-click again extends from the same start",
          selection.pids == frozenset({100, 101, 102, 103, 104, 105}) or selection.all_tabs,
          selection.pids)
    picker.select_none()
    check("select none unticks everything", selection.pids == frozenset(), selection.pids)
    picker.select_all()
    check("select all", selection.all_tabs)
    picker.on_select_all_key()
    check("Ctrl+A also selects all", selection.all_tabs)
    picker.close()
    root.update()

    # ---------------- right-click menu
    actions = Actions()
    picker, selection, tabs = make(5, actions=actions)

    check("with everything ticked, right-click acts on the clicked tab only",
          [t.pid for t in picker.targets_for(102)] == [102])
    menu = picker.build_menu(102)
    check("menu header names the tab", "Roblox 2" in labels(menu)[0] and "102" in labels(menu)[0],
          labels(menu))
    check("menu offers the actions",
          all(entry(menu, t) is not None for t in
              ("Bring to front", "Trim memory", "Close client", "Copy PID", "Only this tab",
               "Select all", "Select none", "Invert selection")), labels(menu))
    menu.invoke(entry(menu, "Bring to front"))
    menu.invoke(entry(menu, "Trim memory"))
    check("actions go to the clicked tab", actions.calls == [("focus", [102]), ("trim", [102])],
          actions.calls)

    menu.invoke(entry(menu, "Close client"))
    check("closing asks first", len(asked) == 1 and "Roblox 2" in asked[0], asked)
    check("and closes after a yes", actions.calls[-1] == ("close", [102]), actions.calls)

    asked.clear()
    actions.calls.clear()
    picker.close()
    root.update()
    picker, selection, tabs = make(5, actions=actions, answer=False)
    menu = picker.build_menu(103)
    menu.invoke(entry(menu, "Close client"))
    check("a no closes nothing", actions.calls == [] and len(asked) == 1, (actions.calls, asked))
    picker.close()
    root.update()

    actions = Actions()
    picker, selection, tabs = make(5, actions=actions)
    selection.select_only([100, 101, 102], [t.pid for t in tabs])
    check("right-click on a ticked tab of a chosen group acts on the whole group",
          sorted(t.pid for t in picker.targets_for(101)) == [100, 101, 102])
    check("right-click on an unticked tab acts on that tab only",
          [t.pid for t in picker.targets_for(104)] == [104])
    menu = picker.build_menu(101)
    check("group menu says how many", "3 tabs" in labels(menu)[0], labels(menu))
    check("Bring to front is off for a group", menu.entrycget(entry(menu, "Bring to front"),
                                                             "state") == "disabled",
          labels(menu))
    check("Close names the count", entry(menu, "Close 3 clients") is not None, labels(menu))
    menu.invoke(entry(menu, "Trim memory"))
    check("trim goes to all three", actions.calls == [("trim", [100, 101, 102])], actions.calls)
    menu.invoke(entry(menu, "Only these 3 tabs"))
    check("only these keeps them ticked", selection.pids == frozenset({100, 101, 102}))

    menu = picker.build_menu(104)
    menu.invoke(entry(menu, "Only this tab"))
    check("only this tab ticks just it", selection.pids == frozenset({104}), selection.pids)
    menu.invoke(entry(menu, "Invert selection"))
    check("invert", selection.pids == frozenset({100, 101, 102, 103}), selection.pids)

    root.clipboard_clear()
    menu = picker.build_menu(103)           # the chosen group of four is ticked
    menu.invoke(entry(menu, "Copy PIDs"))
    check("Copy PIDs puts the group's numbers on the clipboard",
          root.clipboard_get() == "100 101 102 103", root.clipboard_get())
    selection.set_all()
    menu = picker.build_menu(103)
    menu.invoke(entry(menu, "Copy PID"))
    check("Copy PID puts the number on the clipboard", root.clipboard_get() == "103",
          root.clipboard_get())
    picker.close()
    root.update()

    # ---------------- no actions wired: selection items still work, action items are off
    picker, selection, tabs = make(2)
    menu = picker.build_menu(100)
    check("without actions the window actions are disabled",
          menu.entrycget(entry(menu, "Trim memory"), "state") == "disabled"
          and menu.entrycget(entry(menu, "Close client"), "state") == "disabled", labels(menu))
    check("selection items stay enabled",
          menu.entrycget(entry(menu, "Select all"), "state") == "normal")

    # ---------------- the popover survives the menu
    check("menu does not close the popover", not picker.closed)
    picker.close()
    root.update()
    check("closing releases the grab", root.grab_current() is None, root.grab_current())

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_tab_picker_scroll_select_and_menu():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
