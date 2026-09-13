# VN Discord Presence

so i got tired of using random generic rich presence tools for visual novels that either pull broken metadata or require steam/steamgriddb setup, so i made this.


## what it actually does

<img width="475" height="647" alt="image" src="https://github.com/user-attachments/assets/ade284c2-71b5-4e57-a291-054e95f8ecd7" /> <img width="474" height="648" alt="image" src="https://github.com/user-attachments/assets/63f97d85-522d-4c21-8c9b-90df5bd306a6" />

 
- **background process scanner:** keeps tabs on your tasks so it instantly catches when you boot up a VN.
- **vndb integration:** when you add a game, it queries vndb and gives you a nice thumbnail grid of search results with the real covers so you never click the wrong one.
- **tray only:** runs with `pythonw` so there's no annoying console sitting on your taskbar. the only way to close it is hitting "quit" in the tray, meaning you won't accidentally kill it. if something breaks, it dumps logs into `app.log` quietly.

---

## playing / idle status

it handles afk detection out of the box.
- shows **Playing** while you're active.
- drops to **Idle** if you walk away or leave it sitting for a few minutes.

you can easily change the timers or custom text in `config.json` if you want:
```
json
"afk_detection": {
    "enabled": true,
    "idle_threshold_seconds": 300,
    "active_text": "Playing",
    "idle_text": "Idle"
}
```
## why does the title appear as " " and how to fix it

<img width="219" height="359" alt="Screenshot 2026-09-13 021302" src="https://github.com/user-attachments/assets/6ad69aea-2c05-428c-b714-03347d4eb943" />     <img width="221" height="361" alt="Screenshot 2026-09-13 021214" src="https://github.com/user-attachments/assets/7c9b00d3-d3d0-4889-a8bc-6ad3eef56343" />



ok heads up: discord forces the big bold title to be whatever the registered application is named in their developer portal. every single app does this (spotify, vscode, etc), 
it's just how rich presence works. if you don't set one up, it defaults to a shared generic app that literally just says " ".

_if you want it to actually show the proper title of the VN (plus a custom icon next to your name during voice calls), do this:_

- head over to discord.com/developers/applications.
- hit New Application.
- name it exactly what you want shown on your profile (like Hanachirasu).
- upload an image under App Icon (that's the icon that shows up in voice chats).
- grab the Application ID from the general info tab.
- paste it into the app when adding your game and save.

_yeah, you gotta make a quick app per VN if you want custom titles/icons for each one, but it takes literally two minutes per game and you only do it once._

## requirements

- Windows with Python 3.10+ (check "Add Python to PATH" during installation)
- Desktop Discord open (does not work with browser Discord)

No accounts or API keys required — VNDB search is public and free.

## how to run with python

1. Open a terminal in this folder → `pip install -r requirements.txt`
2. Run **once** with a visible console to check for errors: `python main.py`

## normal usage (recommended, without console)

For everyday use, run with `pythonw` instead of `python`:

```bash
pythonw main.py
