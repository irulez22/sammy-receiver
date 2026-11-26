"""Put text into the focused Roblox field: capture, paste, verify, submit.

Shared by the live rehearsal (.blocks/typing_test.py) and the watcher app
(sammy_typer.py). Everything here was validated in live runs on 2026-11-21:
clipboard paste lands when this process has the same integrity level as
Roblox (an elevated Roblox silently drops input from a non-elevated process),
and the field read-back confirms landing.
"""
import ctypes
import time

import pyautogui
from PIL import ImageGrab

import code_parts

from ctypes import wintypes

user32 = ctypes.windll.user32

# Key-timing margins on the submit path, in seconds. Measured live on
# 2026-11-21 (~100 ms each for paste and Enter), tightened 2026-11-25, and set
# 2026-11-26 from rehearsal rounds that watched the field for "Redeeming"
# after every Enter: 10 ms paste gaps landed 110/110; 5 ms dropped 7/20
# pastes; a 10 ms pre-Enter/15 ms hold dropped 1/40 Enters; 20/20 landed 40/40.
# A margin that is too tight drops the paste or the Enter (a missed code,
# never a wrong one); sammy_typer --key-margins overrides them for testing.
KEY_MARGINS = {
    "clip": 0.01,        # clipboard set -> Ctrl down
    "ctrl_lead": 0.01,   # Ctrl down -> V
    "v_hold": 0.01,      # V -> Ctrl up
    "pre_enter": 0.02,   # paste done -> Enter down
    "enter_hold": 0.02,  # Enter down -> Enter up
    "enter_after": 0.0,  # after Enter up (nothing waits on it)
}

try:
    # Physical pixels everywhere: window rects, BitBlt and cursor all agree on
    # every monitor and DPI scale. Harmless if the process already set it.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except OSError:  # pragma: no cover - already set or unsupported
    pass


def find_windows(title="Roblox"):
    """Visible top-level windows titled `title`, largest first: [hwnd]."""
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    matches = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value.strip().lower() != title.lower():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > 0:
            matches.append((area, hwnd))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return [hwnd for _area, hwnd in sorted(matches, reverse=True)]


def find_window(title="Roblox"):
    windows = find_windows(title)
    return windows[0] if windows else None


def process_name(pid):
    """Executable file name of a process ("RobloxPlayerBeta.exe"), or ""."""
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.rsplit("\\", 1)[-1]
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def window_info(hwnd):
    """{hwnd, title, pid, exe, w, h} for one top-level window."""
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    _x, _y, width, height = client_rect(hwnd)
    return {"hwnd": hwnd, "title": buffer.value, "pid": pid.value,
            "exe": process_name(pid.value), "w": width, "h": height}


def list_windows(title="Roblox"):
    """Pickable windows: every visible titled top-level window, the ones titled
    `title` first (largest first), then the rest by title."""
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    infos = [info for info in map(window_info, found) if info["w"] > 0 and info["h"] > 0]
    wanted = title.lower()
    return sorted(infos, key=lambda info: (info["title"].strip().lower() != wanted,
                                           -(info["w"] * info["h"]) if
                                           info["title"].strip().lower() == wanted
                                           else 0, info["title"].lower()))


def window_rect(hwnd):
    """Outer window rectangle on screen: (left, top, right, bottom)."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def set_client_size(hwnd, width, height, tries=3):
    """Resize a window so its drawing area is exactly width x height.

    The border and title bar are whatever the window has now, so the outer
    size is the wanted client size plus that difference; re-measured a few
    times because DPI rounding can leave it a pixel off. Restores a maximised
    window first (a maximised window ignores the size). Returns the final
    client (w, h). Needs the same privileges as the window (an elevated
    Roblox only accepts this from an elevated process).
    """
    if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                       # SW_RESTORE
    for _ in range(tries):
        _x, _y, cw, ch = client_rect(hwnd)
        if (cw, ch) == (width, height):
            break
        left, top, right, bottom = window_rect(hwnd)
        outer_w = (right - left) + (width - cw)
        outer_h = (bottom - top) + (height - ch)
        # SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
        user32.SetWindowPos(hwnd, None, 0, 0, outer_w, outer_h, 0x0002 | 0x0004 | 0x0010)
    # Keep it on its monitor: growing downwards can push the bottom (and the
    # Codes field) under the taskbar.
    left, top, right, bottom = window_rect(hwnd)
    work = work_area(hwnd)
    if bottom > work[3] or right > work[2]:
        new_left = max(work[0], min(left, work[2] - (right - left)))
        new_top = max(work[1], min(top, work[3] - (bottom - top)))
        user32.SetWindowPos(hwnd, None, new_left, new_top, 0, 0, 0x0001 | 0x0004 | 0x0010)
    return tuple(client_rect(hwnd)[2:])


def work_area(hwnd):
    """Usable area (no taskbar) of the monitor showing `hwnd`: (l, t, r, b)."""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    work = info.rcWork
    return (work.left, work.top, work.right, work.bottom)


def client_rect(hwnd):
    """Where the window's drawing area is on screen right now: (x, y, w, h)."""
    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return (origin.x, origin.y, client.right, client.bottom)


def _token_elevated(process_handle):
    advapi32 = ctypes.windll.advapi32
    token = wintypes.HANDLE()
    # Typed handle: untyped, GetCurrentProcess()'s -1 went in as a 32-bit int,
    # OpenProcessToken failed (invalid handle) and self_elevated() was always
    # False, even elevated (multi_submit refused to run, 2026-11-21).
    if not advapi32.OpenProcessToken(wintypes.HANDLE(process_handle), 0x0008,
                                     ctypes.byref(token)):
        return None
    try:
        elevated = wintypes.DWORD()
        size = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, 20, ctypes.byref(elevated), 4,
                                            ctypes.byref(size)):
            return None
        return bool(elevated.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(token)


def self_elevated():
    return bool(_token_elevated(ctypes.windll.kernel32.GetCurrentProcess()))


def window_elevated(hwnd):
    """True/False for the window's process, None if Windows will not say.

    An elevated Roblox silently drops every key a non-elevated process sends
    (UIPI); that is what made pastes vanish on 2026-11-21.
    """
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return None
    try:
        return _token_elevated(handle)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def grab_region(rect):
    """Screen pixels of one rectangle: BitBlt of the band, not ImageGrab.

    Pillow's Windows ImageGrab always captures the entire virtual desktop and
    crops afterwards: measured 111ms for an 841x476 band, against 5ms for a
    BitBlt of just that rectangle with identical pixels. On the last segment
    that difference *is* the race.
    """
    left, top, right, bottom = [int(value) for value in rect]
    width, height = right - left, bottom - top
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    # 64-bit: without c_void_p the HANDLEs are truncated to 32 bits.
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_uint]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.RestoreDC.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_uint]
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader),
                    ("bmiColors", ctypes.c_uint32 * 3)]

    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        # SRCCOPY
        if not gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top,
                            0x00CC0020):
            raise OSError("BitBlt failed")
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory, bitmap, 0, height, buffer,
                        ctypes.cast(ctypes.byref(info), ctypes.c_void_p), 0)
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
    from PIL import Image
    return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)


def find_ui(hwnd):
    """Locate the modal's field and Submit button from their pixels.

    The field is the wide light-grey rounded rect in the middle of the client
    area, Submit is the green button under it. Guessing their position from the
    1920x1080 render put the click *below* the field, which is why Ctrl+A never
    reached it.
    """
    import numpy as np

    cx, cy, cw, ch = client_rect(hwnd)
    shot = np.array(ImageGrab.grab(bbox=(cx, cy, cx + cw, cy + ch),
                                   all_screens=True).convert("RGB")).astype(int)
    red, green, blue = shot[:, :, 0], shot[:, :, 1], shot[:, :, 2]

    def boxes(mask, min_width_fraction=0.2, max_height_fraction=0.25):
        row_hits = mask.sum(axis=1)
        rows = np.where(row_hits >= cw * min_width_fraction)[0]
        found = []
        if not len(rows):
            return found
        start = previous = rows[0]
        groups = []
        for row in rows[1:]:
            if row - previous > 4:
                groups.append((start, previous))
                start = row
            previous = row
        groups.append((start, previous))
        for top, bottom in groups:
            if not 10 <= bottom - top <= ch * max_height_fraction:
                continue
            columns = np.where(mask[top:bottom + 1].sum(axis=0) >= (bottom - top) * 0.6)[0]
            if not len(columns):
                continue
            found.append((int(columns[0]), int(top), int(columns[-1]), int(bottom)))
        return found

    def in_modal(box, min_width, max_width, min_height, max_height):
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        if not (cw * min_width <= width <= cw * max_width):
            return False
        if not (min_height <= height <= ch * max_height):
            return False
        # The modal is centred: reject taskbars, side panels and the HUD.
        return (abs((left + right) / 2 - cw / 2) < cw * 0.18
                and ch * 0.25 < top < ch * 0.80)

    grey = ((np.abs(red - green) < 14) & (np.abs(green - blue) < 14)
            & (red > 105) & (red < 200))
    # 10% of the client width, not 20%: the field's own white text leaves only the
    # side margins grey on those rows, which split the field into two thin bands
    # a stricter threshold drops entirely.
    field_candidates = [box for box in boxes(grey, min_width_fraction=0.04)
                        if in_modal(box, 0.20, 0.75, 18, 0.10)]
    green_button = (green > red + 25) & (green > blue + 20) & (green > 90)
    submit_candidates = [box for box in boxes(green_button, min_width_fraction=0.08)
                         if in_modal(box, 0.08, 0.45, 24, 0.08)]
    # Anchor on Submit: the field is the grey box sitting directly above it.
    # "Widest grey box" picked the avatar's grey shirt below the modal, so the
    # field read back blank and "Code Here…" (unfocused) was never seen.
    field = submit = None
    for button in sorted(submit_candidates, key=lambda box: box[2] - box[0],
                         reverse=True):
        above = [box for box in field_candidates
                 if 0 <= button[1] - box[3] <= ch * 0.12
                 and box[0] < (button[0] + button[2]) / 2 < box[2]]
        if above:
            field = max(above, key=lambda box: box[3])  # the nearest one
            submit = button
            break
    if field is None and field_candidates:
        field = max(field_candidates, key=lambda box: box[2] - box[0])
        below = [box for box in submit_candidates if box[1] > field[3]]
        submit = max(below, key=lambda box: box[2] - box[0]) if below else None

    def centre(box):
        return (cx + (box[0] + box[2]) // 2, cy + (box[1] + box[3]) // 2) if box else None

    return centre(field), centre(submit), field, submit


def field_text(point, box=None, offset=(0, 0)):
    """OCR the field: inside its detected box when we have one.

    Reading a strip around the click point also caught the modal's "codes from
    Sammy here" subtitle, which looked like content. The detected box is exact.
    """
    import winocr
    if box is not None:
        left, top, right, bottom = box
        ox, oy = offset
        grab = (ox + left, oy + top, ox + right, oy + bottom)
    else:
        x, y = point
        grab = (max(0, x - 320), max(0, y - 70), x + 320, y + 20)
    shot = ImageGrab.grab(bbox=grab, all_screens=True).convert("RGB")
    best = ""
    for scale in (2, 3):
        text = "".join(winocr.recognize_lines(shot, scale=scale))
        cleaned = "".join(character for character in text if character.isalnum())
        if len(cleaned) > len(best):
            best = cleaned
    return best


def activate(hwnd, settle=0.3):
    """Bring the game window forward so key events land in it.

    Returns True only when the foreground window actually changed. Windows
    refuses SetForegroundWindow from a background process unless the calling
    thread owns the foreground, which is why a synthetic Alt press is sent
    first: without it, every paste went to whatever window did have focus.
    """
    import ctypes
    user32 = ctypes.windll.user32
    if user32.GetForegroundWindow() == hwnd:
        return False
    user32.keybd_event(0x12, 0x38, 0, 0)          # Alt down
    user32.keybd_event(0x12, 0x38, 0x0002, 0)     # Alt up
    user32.ShowWindow(hwnd, 9)                    # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    for _ in range(200):  # up to 1 s, checked every 5 ms (was 50 ms steps)
        if user32.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.005)
    time.sleep(settle)
    return True


PLACEHOLDER = "CodeHere"


def key_event(vk, scan, up=False, extended=False):
    """One scan-code key event; the game filters pure virtual-key events."""
    import ctypes
    flags = (0x0002 if up else 0) | (0x0001 if extended else 0)
    ctypes.windll.user32.keybd_event(vk, scan, flags, 0)


def send_key(vk, scan, extended=False, times=1, interval=0.03):
    """A scan-code key press; the game filters pure virtual-key events."""
    for _ in range(times):
        key_event(vk, scan, extended=extended)
        time.sleep(0.01)
        key_event(vk, scan, up=True, extended=extended)
        time.sleep(interval)


def clear_field_verified(hwnd, point, box, offset, tries=5, count=60):
    """Empty the field, retrying with a longer settle, and say whether it worked.

    The first pass after a focus change often does nothing (the game drops keys
    sent while it is still taking focus), and 40 backspaces at 15ms can be
    throttled. Each attempt re-activates, waits longer, and reads the field back;
    callers must not type when this returns False.
    """
    state, raw = "text", ""
    for attempt in range(tries):
        already = ctypes.windll.user32.GetForegroundWindow() == hwnd
        activate(hwnd)
        # The settle is for keys dropped while the game takes focus; when it
        # already has focus the first pass lands.
        time.sleep((0.05 if already and attempt == 0 else 0.3) + 0.15 * attempt)
        # `Code Here…` shows whenever the field is empty, focused or not (user
        # confirmed 2026-11-21), so it says nothing about focus: it is "empty".
        # Focus cannot be read from pixels; the readback after pasting is the check.
        send_key(0x23, 0x4F, extended=True)
        time.sleep(0.05)
        send_key(0x08, 0x0E, times=count if attempt == 0 else 60, interval=0.03)
        time.sleep(0.3)
        state, raw = field_state(point, box, offset)
        if state != "text":
            # Confirm with a second read: a single noisy read of the field is
            # what let a previous code survive and get prefixed onto the next.
            time.sleep(0.2)
            again, raw_again = field_state(point, box, offset)
            if again != "text":
                return True, again, raw_again
    return False, state, raw


# Where the Codes field's centre sits in the client area when the detector
# cannot see it (grey walls beside the modal): measured at 1207x1246,
# 2026-11-21 (field centre at 602,650).
FIELD_AT = (602 / 1207, 650 / 1246)


def _relative_move(dx, dy):
    """A raw relative mouse move: the game sees it as the mouse moving (an
    absolute jump is a teleport, which Roblox ignores for focus)."""
    try:
        import pydirectinput
        pydirectinput.moveRel(dx, dy, duration=0)
    except ImportError:
        user32.mouse_event(0x0001, dx, dy, 0, 0)          # MOUSEEVENTF_MOVE


def click_field(hwnd, point=None, drift=2, drift_seconds=0.1, settle=0.3, click_gap=0.25,
                move_seconds=0.1, clicks=None, after_click=0.05):
    """Put the caret in the Codes field with one deliberate click.

    The user allows exactly this click, right after a code is submitted (the
    field is empty then). A teleport-click does not focus the field; the
    method that works is SlimeCardMega's (screen_text_clicker.perform_mouse_click):
    pydirectinput moveTo, then a short drift of real relative moves, then a
    pydirectinput click. A window brought forward first needs two clicks (the
    first only activates it). The cursor goes back where it was.
    """
    import ctypes
    import random
    import pydirectinput
    if point is None:
        x, y, w, h = client_rect(hwnd)
        point = (x + round(w * FIELD_AT[0]), y + round(h * FIELD_AT[1]))
    wanted = clicks
    clicks = 1
    if user32.GetForegroundWindow() != hwnd:
        activate(hwnd, settle)
        time.sleep(settle)
        clicks = 2
    if wanted:
        clicks = wanted
    cursor = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cursor))
    pause, pydirectinput.PAUSE = pydirectinput.PAUSE, 0
    try:
        pydirectinput.moveTo(point[0], point[1], duration=move_seconds)
        # Drift a pixel or two with relative events, spread over drift_seconds.
        dx, dy = random.choice((-1, 1)) * drift, random.choice((-1, 1)) * drift
        steps = max(abs(dx), abs(dy), 1)
        for step in range(steps):
            _relative_move(dx // steps, dy // steps)
            time.sleep(drift_seconds / steps)
        for index in range(clicks):
            pydirectinput.click()
            if index < clicks - 1:
                time.sleep(click_gap)
        if after_click:
            time.sleep(after_click)
    finally:
        pydirectinput.PAUSE = pause
        user32.SetCursorPos(cursor.x, cursor.y)
    return point


def press_enter():
    """Enter as a scan-code key event.

    A virtual-key-only event is what our typing used, and the game ignored it
    (the field never submitted) while a physical Enter clears it. Legacy
    keybd_event with the real scan code (0x1C) is accepted where the synthetic
    VK path is not. A 30 ms hold is still well above a real keypress; the
    50 ms margins measured live (2026-11-21) were tightened on 2026-11-25
    (~100 ms -> ~60 ms) -- recheck if a submit is ever dropped.
    """
    import ctypes
    user32 = ctypes.windll.user32
    user32.keybd_event(0x0D, 0x1C, 0, 0)
    time.sleep(KEY_MARGINS["enter_hold"])
    user32.keybd_event(0x0D, 0x1C, 0x0002, 0)
    if KEY_MARGINS["enter_after"]:
        time.sleep(KEY_MARGINS["enter_after"])


# Switching to another client and focusing its Codes field, measured
# 2026-11-26 (.blocks/multi_submit.py, 8/8 rounds landed): no settle, 10 ms
# between the two clicks, no slow move or drift -> that client's Enter 175 ms
# after the first client's.
# 2026-11-26 later: one click after switching and a 10 ms wait (was a double
# click + 50 ms) with 5 ms focus polling: 175 -> 95 ms, 3/3 rounds landed
# (pictures of the second client after each Enter).
OTHER_CLIENT_CLICK = {"settle": 0.0, "click_gap": 0.01, "move_seconds": 0.0,
                      "drift_seconds": 0.0, "clicks": 1, "after_click": 0.01}


def submit_to_windows(code, hwnds, clock=time.perf_counter):
    """Paste `code` and press Enter in each window of `hwnds`, one after the
    other: bring it forward, click its Codes field (empty: the code was never
    pasted there), paste, Enter. Windows only send keys to the window in front,
    so this is sequential. Returns [{hwnd, sent, ms}] (ms = since the call)."""
    start = clock()
    results = []
    for hwnd in hwnds:
        try:
            click_field(hwnd, **OTHER_CLIENT_CLICK)
            sent = type_code(code, hwnd=hwnd)
            if sent:
                press_enter()
        except Exception:  # noqa: BLE001 - one client failing must not stop the rest
            sent = False
        results.append({"hwnd": hwnd, "sent": sent, "ms": round((clock() - start) * 1000)})
    return results


def clipboard_set(text):
    """Put text on the Windows clipboard (CF_UNICODETEXT)."""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    # 64-bit: without these the handle is truncated to 32 bits and GlobalLock
    # returns NULL ("access violation writing 0x0").
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.OpenClipboard(None)
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
    finally:
        user32.CloseClipboard()


def type_code(text, mode="paste", hwnd=None):
    """Enter a code into the focused field: paste by default.

    A single batched SendInput burst is what a game TextBox drops characters
    from; so either paste it (one Ctrl+V, no per-character race) or pace the
    characters out. `hwnd` is a safety rail: if the game is not the foreground
    window, nothing is sent -- a paste into whatever window does have focus is
    much worse than a missed code. Returns True only when keys were sent, so a
    refused paste is never scored as typed.
    """
    if not text:
        return False
    if hwnd is not None:
        import ctypes
        if ctypes.windll.user32.GetForegroundWindow() != hwnd:
            return False
    if mode == "paste":
        clipboard_set(text)
        time.sleep(KEY_MARGINS["clip"])
        # Explicit down/up with a gap: with pyautogui's PAUSE at 0 the modifier
        # and the V arrive in one input batch and the game drops the paste.
        # The gaps are margins measured live (2026-11-21) and tightened on
        # 2026-11-25 (~100 ms -> ~65 ms); recheck if a paste is ever dropped.
        pause, pyautogui.PAUSE = pyautogui.PAUSE, 0
        try:
            pyautogui.keyDown("ctrl")
            time.sleep(KEY_MARGINS["ctrl_lead"])
            pyautogui.press("v")
            time.sleep(KEY_MARGINS["v_hold"])
            pyautogui.keyUp("ctrl")
        finally:
            pyautogui.PAUSE = pause
    elif mode == "paced":
        from ocr_waves import type_character
        for character in text:
            type_character(character)
            time.sleep(0.02)
    else:
        from ocr_waves import type_text
        type_text(text)
    return True


def field_state(point, box=None, offset=(0, 0)):
    """What the field shows: ('placeholder'|'empty'|text, raw)."""
    raw = field_text(point, box, offset)
    if raw and raw.lower().startswith(PLACEHOLDER.lower()):
        return "placeholder", raw
    return ("empty" if not raw else "text"), raw


def field_matches(typed, seen):
    """True when the field read-back shows what was pasted.

    The paste itself is exact (clipboard), so this checks *landing*, and the
    field OCR is lossy: it folds I/l/1 and O/0, and reads the text cursor as an
    extra `I` at either end (live 2026-11-21: `IBLINGBLINGBLINGI`, `B2GMUHX81`).
    Any other extra glyph still fails -- one character a clear left behind looks
    exactly like that and would submit a wrong code.
    """
    if not typed or not seen:
        return False
    want = code_parts.fold_confusables(typed)
    got = code_parts.fold_confusables(seen)
    if got.startswith("I") and got[1:].startswith(want) and len(got) > len(want):
        got = got[1:]
    if got.endswith("I") and len(got) == len(want) + 1:
        got = got[:-1]
    return got == want


def sync_field(read, typed, hwnd, point, box, offset, mode="paste"):
    """Bring the field from `typed` to `read`; return (typed now, refusals).

    Pastes only the new suffix when the reading grew; a reading that changed
    underneath is cleared (verified) and pasted whole. `typed` advances only
    when keys were actually sent, so the score reflects the field, not intent.
    """
    if not read or read == typed:
        return typed, 0
    if activate(hwnd):
        # Only a focus change needs a settle; when the game already holds
        # focus, keys land immediately.
        time.sleep(0.15)
    if typed and read.startswith(typed):
        return (read, 0) if type_code(read[len(typed):], mode, hwnd) else (typed, 1)
    if typed:
        cleared, _state, _raw = clear_field_verified(hwnd, point, box, offset)
        if not cleared:
            return typed, 1
    return (read, 0) if type_code(read, mode, hwnd) else ("", 1)
