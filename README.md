# VN Discord Presence

_by nullprc_

Detects running processes on your PC and displays them on your Discord Rich Presence with the real visual novel title, its cover art, and a "View on VNDB" button — all sourced from VNDB (free, VN-specific, and with correct titles and covers for virtually any VN out there).

## Requirements

- Windows with Python 3.10+ (check "Add Python to PATH" during installation)
- Desktop Discord open (does not work with browser Discord)

No accounts or API keys required — VNDB search is public and free.

## Installation

1. Open a terminal in this folder → `pip install -r requirements.txt`
2. Run **once** with a visible console to check for errors: `python main.py`

## Normal Usage (Recommended, without console)

For everyday use, run with `pythonw` instead of `python`:

```bash
pythonw main.py
