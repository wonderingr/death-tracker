# Death Tracker

Transparent death counter for Windows. Always on top, only the text shows.

Made for games where you die a lot (horror, roguelikes, whatever).  
By **electro** · MIT

## Hotkeys

| Key | What it does |
|-----|----------------|
| **D** | +1 death |
| **R** | reset |
| **H** | settings |
| **U** | unlock so you can drag it (locks again after) |
| **End** | quit |
| **Double-click** | settings |

In settings you can change colors, the +1 popup text (default `I SUCK`), and **freeze hotkeys** if you need to type in chat.

## Run it

**Easiest:** download **DeathTracker.zip** from [Releases](https://github.com/wonderingr/death-tracker/releases), unzip it, run `DeathTracker.exe`.

Keep everything in that folder. Your save and logs live next to the exe:
- `deaths.json` — count / colors / position
- `hotkey_status.txt` — whether hotkeys registered
- `death_tracker_error.txt` — only if something crashes

Windows Defender / SmartScreen may warn once (the app isn’t code-signed). That’s normal for small open-source tools. More info → Run anyway if you trust the download.

Or from source (Windows + Python 3.10+):

```bat
git clone https://github.com/wonderingr/death-tracker.git
cd death-tracker
py -3 -m pip install -r requirements.txt
```

Then double-click `Start Death Tracker.bat`, or:

```bat
pyw death_tracker.py
```

## License

MIT. Use it, fork it, stream with it, goon with it IDK MAN.  
Keep the copyright if you redistribute.

```
Copyright (c) 2026 electro
```
