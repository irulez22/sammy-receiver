# Sammy receiver

Submits Roblox codes that Sammy Typer decodes on another PC on the same
network. Each code arrives as one signed UDP message; the receiver pastes it
into the Roblox window in front and presses Enter (optionally into every other
Roblox window on the PC too).

## Setup (once per PC)
1. Install Python 3 and the packages: `py -m pip install pyautogui pydirectinput pillow`
2. Download this folder (Code -> Download ZIP) and unzip it.
3. Copy `code_link.key` from the decoder PC into the folder. It is the shared
   secret: without it the receiver accepts nothing. It is never in this repo.
4. Allow Python through Windows Firewall for inbound UDP when Windows asks.

## Use
- Double-click `Start Code Receiver.bat`. It updates itself from this repo,
  then starts as administrator.
- Keep Roblox in front with the Codes window open.
- Every Roblox window on this PC gets the code (front one first, then the
  others, ~95 ms each). `Start Code Receiver.bat --front-only` limits it to
  the window in front.
- `--dry-run` only logs what it receives.

Logs go to `corpus/receiver_logs/` next to the files.
