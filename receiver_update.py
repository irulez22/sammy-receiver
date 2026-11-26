"""Update the code receiver from its public GitHub repo, then exit.

Run by "Start Code Receiver.bat" before every start. Downloads files.txt (the
list of receiver files) and each listed file from the repo's main branch, and
replaces a local file only when its content changed. Never touches
code_link.key or anything not listed. A network error leaves the current
files in place: the receiver still starts.

  py receiver_update.py            update from the default repo
  py receiver_update.py --check    report what would change, write nothing
"""
import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_RAW = "https://raw.githubusercontent.com/irulez22/sammy-receiver/main/"
NEVER = {"code_link.key"}
CRLF, LF = bytes([13, 10]), bytes([10])


def fetch(url, timeout=6):
    request = urllib.request.Request(url, headers={"User-Agent": "receiver-update",
                                                   "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=REPO_RAW, help="raw base URL ending in /")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        listing = fetch(args.repo + "files.txt").decode("utf-8")
    except Exception as error:  # noqa: BLE001 - offline must not stop the receiver
        print(f"update: could not reach the repo ({error}); starting with the current files")
        return 0
    names = [line.strip() for line in listing.splitlines()
             if line.strip() and not line.startswith("#")]
    changed = []
    for name in names:
        if name in NEVER or "/" in name or "\\" in name or name.startswith("."):
            continue  # only plain files in this folder, never the key
        try:
            data = fetch(args.repo + urllib.parse.quote(name))
        except Exception as error:  # noqa: BLE001 - never block the receiver
            print(f"update: {name} not downloaded ({error}); keeping the current one")
            continue
        data = data.replace(CRLF, LF)
        if name.lower().endswith(".bat"):
            data = data.replace(LF, CRLF)  # cmd wants CRLF
        target = ROOT / name
        if target.exists() and target.read_bytes().replace(CRLF, LF) == data.replace(CRLF, LF):
            continue
        changed.append(name)
        if not args.check:
            staged = target.with_name(target.name + ".new")
            staged.write_bytes(data)
            staged.replace(target)
    if changed:
        print(("update: would change " if args.check else "update: updated ") + ", ".join(changed))
    else:
        print("update: already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
