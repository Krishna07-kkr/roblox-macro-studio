"""ScrollPage: a page scrolls only when it is taller than the window (real Tk, own process)."""

import sys
import tkinter as tk
from types import SimpleNamespace

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    from macro_studio.core.scroll_page import ScrollPage

    root = tk.Tk()
    root.geometry("600x400+0+0")
    holder = tk.Frame(root)
    holder.pack(fill="both", expand=True)

    # ---- a page that fits: looks exactly like a plain frame
    short = ScrollPage(holder, bg="#000")
    short.pack(fill="both", expand=True)
    label = tk.Label(short.inner, text="short")
    label.pack(anchor="w")
    expander = tk.Frame(short.inner, bg="#111")
    expander.pack(fill="both", expand=True)
    root.update()
    check("a short page fills the window, so expanding children still expand",
          abs(short.inner.winfo_height() - short.canvas.winfo_height()) <= 1
          and expander.winfo_height() > 200, (short.inner.winfo_height(), short.canvas.winfo_height()))
    check("it is as wide as the window", abs(short.inner.winfo_width() - short.canvas.winfo_width()) <= 1)
    check("no scroll bar when everything fits", not short.bar.winfo_ismapped())
    short.on_wheel(SimpleNamespace(delta=-120, widget=label))
    check("the wheel does nothing then", short.canvas.yview()[0] == 0.0)
    short.pack_forget()

    # ---- a page taller than the window
    tall = ScrollPage(holder, bg="#000")
    tall.pack(fill="both", expand=True)
    for i in range(40):
        tk.Label(tall.inner, text=f"row {i}", height=2).pack(anchor="w")
    root.update()
    check("a tall page keeps its full height", tall.inner.winfo_height() > tall.canvas.winfo_height() * 1.5,
          (tall.inner.winfo_height(), tall.canvas.winfo_height()))
    check("a scroll bar appears", tall.bar.winfo_ismapped())
    check("starts at the top", tall.canvas.yview()[0] == 0.0)
    tall.on_wheel(SimpleNamespace(delta=-120, widget=root))
    root.update()
    check("the wheel scrolls down", tall.canvas.yview()[0] > 0.0, tall.canvas.yview())
    tall.on_wheel(SimpleNamespace(delta=120, widget=root))
    root.update()
    check("and back up", tall.canvas.yview()[0] == 0.0)
    for _ in range(200):
        tall.on_wheel(SimpleNamespace(delta=-120, widget=root))
    root.update()
    check("it stops at the bottom", tall.canvas.yview()[1] > 0.999 and tall.canvas.yview()[1] <= 1.0,
          tall.canvas.yview())

    # ---- content that grows after the page was sized is noticed
    short2 = ScrollPage(holder, bg="#000")
    tall.pack_forget()
    short2.pack(fill="both", expand=True)
    note = tk.Label(short2.inner, text="hello")
    note.pack(anchor="w")
    root.update()
    check("fits at first", not short2.bar.winfo_ismapped())
    for i in range(60):
        tk.Label(short2.inner, text=f"later {i}", height=2).pack(anchor="w")

    def pump(ms):
        root.after(ms, root.quit)
        root.mainloop()
    pump(600)
    check("a page that grows past the window gets its scroll bar by itself", short2.bar.winfo_ismapped()
          and short2.canvas.yview()[1] < 1.0, (short2.bar.winfo_ismapped(), short2.canvas.yview()))
    short2.pack_forget()
    tall.pack(fill="both", expand=True)
    root.update()

    # ---- widgets that scroll themselves keep their own wheel
    box = tk.Text(tall.inner)
    box.pack()
    root.update()
    before = tall.canvas.yview()[0]
    tall.on_wheel(SimpleNamespace(delta=120, widget=box))
    check("a text box or table under the pointer is left to scroll itself",
          tall.canvas.yview()[0] == before)

    # ---- the window grows: the scroll bar goes away
    root.geometry("600x2400+0+0")
    root.update()
    root.update()
    check("a tall enough window needs no scrolling", not tall.bar.winfo_ismapped()
          and tall.canvas.yview()[0] == 0.0, tall.canvas.yview())

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_scroll_page():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
