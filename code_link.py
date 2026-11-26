"""Send a decoded code to Sammy Typer receivers on other PCs (same network).

One signed UDP message per code: the decoder PC broadcasts the whole code the
moment it is decoded, and every receiver (code_receiver.py) pastes it into its
own Roblox Codes field and presses Enter. A paste costs the same ~100ms
whatever the code's length, so one message for the whole code is as fast as
sending segments and leaves no half-typed codes behind (decided 2026-11-21).

Messages are HMAC-SHA256 signed with a shared key (code_link.key, created on
first use; copy it to every PC with the project folder) so nothing else on
the network can make a receiver type. Each message is sent COPIES times; the
receiver drops ids it has seen and messages older than MAX_AGE seconds.

CLI (test without Sammy):
  py -3.14 code_link.py send TESTCODE1          broadcast one code
  py -3.14 code_link.py send TESTCODE1 --to <receiver-ip>
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEY_FILE = ROOT / "code_link.key"
PORT = 47474
COPIES = 3            # UDP can drop a packet; duplicates are ignored by id
MAX_AGE = 120.0       # seconds; generous because PC clocks can differ
CODE_MAX = 40


def load_key(path=KEY_FILE, create=True):
    """The shared secret (bytes). Created on first use when `create`."""
    path = Path(path)
    if path.exists():
        text = path.read_text(encoding="ascii").strip()
        if len(text) >= 32:
            return text.encode("ascii")
        raise ValueError(f"{path} is too short to be a key")
    if not create:
        raise FileNotFoundError(f"no key at {path}: copy code_link.key from the decoder PC")
    key = secrets.token_hex(32)
    path.write_text(key + "\n", encoding="ascii")
    return key.encode("ascii")


def _signature(key, body):
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


def pack(code, key, message_id=None, sent_at=None):
    """The datagram for one code (bytes)."""
    body = json.dumps({"code": code, "id": message_id or secrets.token_hex(8),
                       "t": round(time.time() if sent_at is None else sent_at, 3)},
                      separators=(",", ":"), sort_keys=True)
    return json.dumps({"body": body, "sig": _signature(key, body)},
                      separators=(",", ":")).encode("utf-8")


def unpack(data, key, now=None):
    """(code, id) from a datagram, or raise ValueError saying why it is refused."""
    try:
        outer = json.loads(data.decode("utf-8"))
        body, sig = outer["body"], outer["sig"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as error:
        raise ValueError(f"not a code message: {error}") from None
    if not isinstance(body, str) or not isinstance(sig, str) or \
            not hmac.compare_digest(_signature(key, body), sig):
        raise ValueError("bad signature (different code_link.key?)")
    inner = json.loads(body)
    code, message_id, sent_at = inner.get("code"), inner.get("id"), inner.get("t", 0)
    if not isinstance(code, str) or not code.isalnum() or not 1 <= len(code) <= CODE_MAX:
        raise ValueError(f"not a code: {code!r}")
    age = (time.time() if now is None else now) - float(sent_at)
    if abs(age) > MAX_AGE:
        raise ValueError(f"stale message ({age:.0f}s old)")
    return code, message_id


class Sender:
    """Broadcasts codes. `to` is "255.255.255.255" (everyone) or one address."""

    def __init__(self, key=None, port=PORT, to="255.255.255.255"):
        self.key = key if key is not None else load_key()
        self.port, self.to = port, to
        # Windows sends a 255.255.255.255 broadcast out of ONE adapter (here
        # a VPN adapter, 2026-11-21), so broadcast from every local
        # IPv4 address; receivers drop the duplicates by id.
        sources = local_addresses() if to == "255.255.255.255" else [""]
        self.socks = []
        for source in sources or [""]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                sock.bind((source, 0))
            except OSError:
                sock.close()
                continue
            self.socks.append(sock)

    def send(self, code):
        """Send one code (COPIES datagrams per adapter, same id). Returns the id."""
        message_id = secrets.token_hex(8)
        data = pack(code, self.key, message_id)
        sent = 0
        for _ in range(COPIES):
            for sock in self.socks:
                try:
                    sock.sendto(data, (self.to, self.port))
                    sent += 1
                except OSError:
                    pass
        if not sent:
            raise OSError("no network adapter could send the code")
        return message_id

    def close(self):
        for sock in self.socks:
            sock.close()


def local_addresses():
    """This PC's non-loopback IPv4 addresses."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos if not info[4][0].startswith("127.")})


class Receiver:
    """Listens for codes; `next()` returns (code, sender ip) once per message id."""

    def __init__(self, key=None, port=PORT, bind="0.0.0.0", seen_limit=256):
        self.key = key if key is not None else load_key(create=False)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.seen, self.seen_limit = [], seen_limit

    def next(self, timeout=None):
        """Block until a new valid code arrives. Returns (code, ip), or None on
        timeout. Refused datagrams are returned as (None, reason)."""
        self.sock.settimeout(timeout)
        try:
            data, (ip, _port) = self.sock.recvfrom(2048)
        except socket.timeout:
            return None
        try:
            code, message_id = unpack(data, self.key)
        except ValueError as error:
            return None, f"refused from {ip}: {error}"
        if message_id in self.seen:
            return self.next(timeout)
        self.seen.append(message_id)
        del self.seen[:-self.seen_limit]
        return code, ip

    def close(self):
        self.sock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    send = sub.add_parser("send", help="broadcast one code")
    send.add_argument("code")
    send.add_argument("--to", default="255.255.255.255")
    send.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    sender = Sender(port=args.port, to=args.to)
    try:
        sent_at = time.time()
        message_id = sender.send(args.code)
    finally:
        sender.close()
    stamp = time.strftime("%H:%M:%S", time.localtime(sent_at)) + f".{int(sent_at % 1 * 1000):03d}"
    print(json.dumps({"sent": args.code, "at": stamp, "id": message_id, "to": args.to,
                      "port": args.port,
                      "host": socket.gethostname(), "pid": os.getpid()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
