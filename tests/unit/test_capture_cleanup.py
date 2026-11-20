"""capture_client_full must release every DC and bitmap it creates, on every path (A1, H2)."""

import pytest

from macro_studio.core import vision_scanner as vs_mod
from macro_studio.core.vision_scanner import VisionScanner

HWND, SRC_DC, MEM_DC, BITMAP, OLD_BITMAP = 7, 11, 22, 33, 44


class FakeUser32:
    def __init__(self, src_dc=SRC_DC, print_ok=1, size=(120, 90)):
        self.src_dc, self.print_ok, self.size = src_dc, print_ok, size
        self.released = []

    def IsWindow(self, hwnd):
        return 1

    def GetClientRect(self, hwnd, ref):
        ref._obj.right, ref._obj.bottom = self.size
        return 1

    def GetDC(self, hwnd):
        return self.src_dc

    def PrintWindow(self, hwnd, dc, flags):
        return self.print_ok

    def ReleaseDC(self, hwnd, dc):
        self.released.append(dc)
        return 1


class FakeGdi32:
    def __init__(self, mem_dc=MEM_DC, bitmap=BITMAP, dibits_error=None):
        self.mem_dc, self.bitmap, self.dibits_error = mem_dc, bitmap, dibits_error
        self.deleted_dcs, self.deleted_objects, self.selected = [], [], []

    def CreateCompatibleDC(self, dc):
        return self.mem_dc

    def CreateCompatibleBitmap(self, dc, w, h):
        return self.bitmap

    def SelectObject(self, dc, obj):
        self.selected.append((dc, obj))
        return OLD_BITMAP

    def GetDIBits(self, *args):
        if self.dibits_error:
            raise self.dibits_error
        return 1

    def DeleteDC(self, dc):
        self.deleted_dcs.append(dc)
        return 1

    def DeleteObject(self, obj):
        self.deleted_objects.append(obj)
        return 1


@pytest.fixture
def scanner(monkeypatch):
    monkeypatch.setattr(vs_mod, "attach_input_desktop", lambda: True)
    return VisionScanner.__new__(VisionScanner)


def install(monkeypatch, user32, gdi32):
    monkeypatch.setattr(vs_mod, "user32", user32)
    monkeypatch.setattr(vs_mod, "gdi32", gdi32)


def test_a_normal_capture_releases_everything_once(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(), FakeGdi32()
    install(monkeypatch, user32, gdi32)
    image = scanner.capture_client_full(HWND)
    assert image is not None and image.shape == (90, 120, 3)
    assert user32.released == [SRC_DC]
    assert gdi32.deleted_dcs == [MEM_DC] and gdi32.deleted_objects == [BITMAP]


def test_an_error_while_reading_pixels_still_releases_everything(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(), FakeGdi32(dibits_error=MemoryError("no memory"))
    install(monkeypatch, user32, gdi32)
    with pytest.raises(MemoryError):
        scanner.capture_client_full(HWND)
    assert user32.released == [SRC_DC]
    assert gdi32.deleted_dcs == [MEM_DC] and gdi32.deleted_objects == [BITMAP]


def test_the_original_bitmap_is_selected_back_before_deleting_ours(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(), FakeGdi32()
    install(monkeypatch, user32, gdi32)
    scanner.capture_client_full(HWND)
    assert (MEM_DC, OLD_BITMAP) in gdi32.selected


def test_a_failed_print_window_releases_everything(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(print_ok=0), FakeGdi32()
    install(monkeypatch, user32, gdi32)
    assert scanner.capture_client_full(HWND) is None
    assert user32.released == [SRC_DC]
    assert gdi32.deleted_dcs == [MEM_DC] and gdi32.deleted_objects == [BITMAP]


def test_no_source_dc_returns_none_and_creates_nothing(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(src_dc=0), FakeGdi32()
    install(monkeypatch, user32, gdi32)
    assert scanner.capture_client_full(HWND) is None
    assert gdi32.deleted_dcs == [] and gdi32.deleted_objects == [] and user32.released == []


def test_no_memory_dc_returns_none_and_releases_the_source(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(), FakeGdi32(mem_dc=0)
    install(monkeypatch, user32, gdi32)
    assert scanner.capture_client_full(HWND) is None
    assert user32.released == [SRC_DC] and gdi32.deleted_objects == []


def test_no_bitmap_returns_none_and_releases_both_dcs(scanner, monkeypatch):
    user32, gdi32 = FakeUser32(), FakeGdi32(bitmap=0)
    install(monkeypatch, user32, gdi32)
    assert scanner.capture_client_full(HWND) is None
    assert user32.released == [SRC_DC] and gdi32.deleted_dcs == [MEM_DC]
    assert gdi32.deleted_objects == []
