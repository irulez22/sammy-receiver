"""Code receiver: submit the decoder PC's codes into this PC's Roblox.

Run on every extra PC (same network) with Roblox in front and the caret in
its Codes field. When the decoder's Sammy Typer (broadcast on) decodes a code,
it arrives here in one signed message: this pastes it (Ctrl+V), presses Enter,
then clicks the Codes field and clears it, ready for the next code. It never
reads the screen. Nothing is typed unless a Roblox window is in front.

Needs code_link.key from the decoder PC (copy the project folder), the same
elevation as Roblox (Start Code Receiver.bat elevates), and Windows Firewall
allowing inbound UDP on code_link.PORT for Python.

  py -3.14 code_receiver.py              listen and submit
  py -3.14 code_receiver.py --dry-run    listen and log only
  py -3.14 code_receiver.py --other-clients   also fill this PC's other Roblox windows
"""
import argparse
import sys
import time
from pathlib import Path

import code_link
import field_typer

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "corpus" / "receiver_logs"

# Field click after Enter, at the settings confirmed live on two clients
# (.blocks/multi_submit.py, 2026-11-21). Roblox is already in front here, so
# click_field makes one click.
CLICK = {"settle": 0.03, "click_gap": 0.05, "move_seconds": 0.02, "drift_seconds": 0.03}


def roblox_in_front(title="Roblox"):
    hwnd = field_typer.user32.GetForegroundWindow()
    return hwnd if hwnd in field_typer.find_windows(title) else None


def submit(code, hwnd):
    """Paste, Enter, then put the caret back in an empty field. Returns phase ms."""
    start = time.perf_counter()
    if not field_typer.type_code(code, hwnd=hwnd):
        return None
    pasted = time.perf_counter()
    field_typer.press_enter()
    entered = time.perf_counter()
    field_typer.click_field(hwnd, **CLICK)
    if field_typer.user32.GetForegroundWindow() == hwnd:
        field_typer.send_key(0x23, 0x4F, extended=True)            # End
        field_typer.send_key(0x08, 0x0E, times=40, interval=0.01)  # Backspace
    return {"paste": round((pasted - start) * 1000), "enter": round((entered - pasted) * 1000),
            "tidy": round((time.perf_counter() - entered) * 1000)}


def main():
    parser = argparse.ArgumentParser(description="Submit codes sent by the decoder PC.")
    parser.add_argument("--dry-run", action="store_true", help="log codes, type nothing")
    parser.add_argument("--port", type=int, default=code_link.PORT)
    parser.add_argument("--window-title", default="Roblox")
    parser.add_argument("--click-first", action="store_true",
                        help="click the Codes box before every paste (safe: it is empty "
                             "then), in case focus was lost; costs ~0.1 s")
    parser.add_argument("--other-clients", action="store_true",
                        help="also submit into every other Roblox window on this PC "
                             "(~175 ms each), then return to the one in front")
    args = parser.parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / time.strftime("%Y%m%d_%H%M%S.log")

    def log(message):
        now = time.time()
        line = (time.strftime("%H:%M:%S", time.localtime(now))
                + f".{int(now % 1 * 1000):03d} " + message)
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    try:
        receiver = code_link.Receiver(port=args.port)
    except (OSError, ValueError) as error:
        log(f"cannot listen: {error}")
        input("Press Enter to close.")
        return 1
    windows = field_typer.find_windows(args.window_title)
    if any(field_typer.window_elevated(h) for h in windows) and not field_typer.self_elevated():
        log("WARNING: Roblox runs as administrator and this does not: pastes will be "
            "dropped. Start it with Start Code Receiver.bat.")
    front = roblox_in_front(args.window_title)
    if front is not None and not args.dry_run:
        # Put the caret in the Codes box now, so nobody has to click it.
        field_typer.click_field(front, **CLICK)
        log("clicked the Codes box of the Roblox window in front")
    log(f"listening on UDP {args.port}{' (dry run)' if args.dry_run else ''}; "
        f"{len(windows)} Roblox window(s). Keep Roblox in front with the Codes field clicked.")
    try:
        while True:
            got = receiver.next(timeout=1.0)
            if got is None:
                continue
            code, source = got
            received = time.perf_counter()
            message_id = getattr(receiver, "last_id", None)

            def report_back(front_ms, others_ms, sent):
                # Timing back to the decoder PC (its log shows the round trip).
                if message_id:
                    busy = round((time.perf_counter() - received) * 1000)
                    code_link.send_ack(source, message_id, code,
                                       {"front": front_ms, "others": others_ms,
                                        "sent": sent, "busy": busy}, key=receiver.key)
            if code is None:
                log(source)
                continue
            if args.dry_run:
                log(f"received {code} from {source} (dry run)")
                continue
            hwnd = roblox_in_front(args.window_title)
            if hwnd is None:
                log(f"received {code} from {source}: NOT typed, Roblox is not in front")
                report_back(None, [], False)
                continue
            if args.click_first:
                # The box is empty between codes, so this click cannot wipe
                # anything; it only restores focus if it was lost.
                field_typer.click_field(hwnd, **field_typer.OTHER_CLIENT_CLICK)
            if args.other_clients:
                # Front window first, then every other client, then back.
                start = time.perf_counter()
                sent = field_typer.type_code(code, hwnd=hwnd)
                if sent:
                    field_typer.press_enter()
                front_ms = round((time.perf_counter() - received) * 1000)
                others = [h for h in field_typer.find_windows(args.window_title) if h != hwnd]
                results = field_typer.submit_to_windows(code, others) if sent else []
                report_back(front_ms, [front_ms + r["ms"] for r in results if r["sent"]], sent)
                field_typer.click_field(hwnd, **CLICK)
                log(f"{'SUBMITTED' if sent else 'paste refused:'} {code} from {source} "
                    f"(front {front_ms} ms; others: "
                    + (", ".join(f"{'sent' if r['sent'] else 'FAILED'} +{front_ms + r['ms']} ms"
                                 for r in results) or "none") + ")")
                continue
            phases = submit(code, hwnd)
            report_back(round((time.perf_counter() - received) * 1000)
                        - (phases["tidy"] if phases else 0), [], phases is not None)
            if phases is None:
                log(f"received {code} from {source}: paste refused")
            else:
                log(f"SUBMITTED {code} from {source} (paste {phases['paste']}, enter "
                    f"{phases['enter']}, tidy {phases['tidy']} ms)")
    except KeyboardInterrupt:
        log("stopped")
    finally:
        receiver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
