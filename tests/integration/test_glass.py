"""Liquid Glass: the glass surface images and the translucent window (real Tk, own process)."""

import gc
import sys
import tkinter as tk

failures = []


def check(name, cond, detail: object = ""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        failures.append(name)


def run():
    from PIL import ImageTk

    from macro_studio.core import glass, ui_theme as ui

    check("old and unknown Windows builds are not offered glass",
          not glass.glass_supported(19045) and not glass.glass_supported(22000)
          and glass.glass_supported(22621) and glass.glass_supported(26200))

    root = tk.Tk()
    root.geometry("700x500+40+40")
    ui.init_fonts()
    root.update()

    image = ui.glass_image(300, 120, 14, ui.SURFACE, page_bg=ui.CANVAS)
    check("a glass panel is an image of the asked size", (image.width(), image.height()) == (300, 120))
    check("the same request is served from the cache", ui.glass_image(300, 120, 14, ui.SURFACE, page_bg=ui.CANVAS) is image)

    # Look at the pixels: rim light is brighter at the top left than the bottom right,
    # the corner outside the rounded shape is the page colour, the middle is the panel colour.
    from PIL import Image
    pil = ImageTk.getimage(image).convert("RGB") if hasattr(ImageTk, "getimage") else None
    if pil is None:
        pil = Image.new("RGB", (300, 120))
    def lum(p):
        return sum(p) / 3
    top_left_rim, bottom_right_rim = lum(pil.getpixel((40, 0))), lum(pil.getpixel((259, 119)))
    check("the rim light is brighter at the top than at the bottom", top_left_rim > bottom_right_rim + 8,
          (top_left_rim, bottom_right_rim))
    check("the corner outside the rounded edge is the page colour", pil.getpixel((0, 0)) == (0, 0, 0),
          pil.getpixel((0, 0)))
    middle = pil.getpixel((150, 60))
    check("the middle is the panel colour with a soft sheen", 0x1c <= middle[0] <= 0x30, middle)

    card = ui.Card(root, radius=ui.R_LG, page_bg=ui.CANVAS)
    card.pack(fill="x", padx=20, pady=20)
    tk.Label(card.body, text="Card", bg=ui.SURFACE, fg=ui.INK).pack()
    root.update()
    check("cards are glass by default", card._glass is True and card._img is not None)
    flat = ui.Card(root, border=ui.HAIRLINE, page_bg=ui.CANVAS)
    flat.pack(fill="x", padx=20)
    root.update()
    check("a card given an explicit border stays flat", flat._glass is True and flat._border == ui.HAIRLINE)

    button = ui.Button(root, "OK", kind="primary")
    button.pack()
    root.update()
    check("filled buttons are glass too", button._img is not None)

    # The translucent window
    got_on = glass.apply_window_look(root, True)
    root.update()
    check("the glass backdrop turns on where it is supported", got_on == glass.glass_supported(), got_on)
    got_off = glass.apply_window_look(root, False)
    root.update()
    check("and turns off again", got_off is False)

    # The image cache stays bounded however many sizes a window is dragged through
    for width in range(100, 700):
        ui.glass_image(width, 60, 12, ui.SURFACE)
    gc.collect()
    check("the image cache is capped", len(ui._img_cache) <= 450, len(ui._img_cache))

    root.destroy()
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


def test_glass():
    from conftest import run_in_fresh_python
    code, output = run_in_fresh_python(__file__)
    assert code == 0, output


if __name__ == "__main__":
    sys.exit(run())
