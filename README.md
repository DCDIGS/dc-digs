# DC Digs

A daily inbox of Bandcamp releases picked for your taste, running on your own computer.

You give it a few **reference tracks**: Bandcamp tracks or albums you love. Every day it looks at what the people who bought them also bought, and brings you the releases several of them share, in the same genre. You listen in the browser (each preview starts mid-track), star what you like, and clear the rest.

- **Two inboxes**, each with its own reference tracks, so you can dig for two styles side by side.
- Optional: new releases from the artists and labels **you follow on Bandcamp**, and from labels you **watch** without following.
- "**You may also like**" from releases you star.
- A fast player: one click plays and clears the row above, a double click only plays; auto-next after 60 s; waveform; keyboard shortcuts.
- Every pick says **why** it's there ("owned by 7 buyers of …").
- Runs **only on your computer** (http://localhost:8765). No account, no cloud, nothing to pay.

DC Digs is not made by or affiliated with Bandcamp. It reads public Bandcamp pages the way a browser does, with a pause between requests; see [Good to know](#good-to-know).

## Install

You need **Python 3.10 or newer**: [python.org/downloads](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** in the Python installer.

Download this project (green **Code** button → **Download ZIP**, then unzip it), or `git clone` it.

### Mac

1. Put the `dc-digs` folder in your **home folder** (not in Documents, Desktop or Downloads: macOS doesn't let background apps read those).
2. Double-click **`Install on Mac.command`**.
   If macOS says it can't be opened, right-click it → **Open** → **Open**, or run `bash "Install on Mac.command"` in Terminal.
3. Your browser opens DC Digs and the setup wizard. Done.

DC Digs now runs in the background and starts again at every login.

### Windows

1. Put the `dc-digs` folder somewhere permanent, e.g. `C:\Users\<you>\dc-digs`.
2. Double-click **`Install on Windows.bat`**.
   If Windows SmartScreen warns about it, click **More info** → **Run anyway**.
3. Your browser opens DC Digs and the setup wizard. Done.

DC Digs now runs in the background (no window) and starts again at every sign-in. A **DC Digs** entry in the Start menu opens it.

> The Windows installer hasn't been tested as thoroughly as the Mac one yet. If something goes wrong, please open an issue with what the window showed.

### Other systems / by hand

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m dig.web
```

Then open http://localhost:8765. (On Windows: `.venv\Scripts\python.exe` instead of `.venv/bin/python`.) The app collects new picks once a day while it's running.

## First start: the setup wizard

1. **Reference tracks.** Paste links to Bandcamp tracks or albums you love, one per line, e.g. `https://artist.bandcamp.com/track/some-track`. Five to ten is a good start.
2. **A second inbox** *(optional)*: another style, with its own reference tracks.
3. **Your Bandcamp name** *(optional)*: the part after `bandcamp.com/` on your fan page, if you want new releases from the artists and labels you follow.
4. **When to dig**: once a day, e.g. 06:00.

**Start digging** runs the first collection right away. It takes a few minutes (about 1–2 minutes per reference track) and your inbox fills up as it goes.

## Using it

| | |
|---|---|
| **Click** a row | plays its first track and removes the row above (working down the list, that's the one you just heard) |
| **Double-click** a row | plays it without removing anything |
| `Space` | play / pause |
| `←` `→` | 30 s back / forward |
| `1`–`9` | jump to 10–90 % of the track |
| `J` `K` | next / previous release |
| `N` | next track |
| `F` | favourite |
| `X` | remove |

- Previews start at the point set under **Player** in the sidebar (default 50 %).
- **Auto-next:** after 60 s of playback you didn't touch, the player moves on to the next track, then the next release. It never removes anything. Switch it off under **Player**.
- **Removed** shows the last 24 hours, so you can undo a slip. A removed release never comes back, not even under another link or as a single track from it.
- **Favourites** stay even when you remove them from the inbox, and their "you may also like" is collected on the next run.
- The **copy** button next to a track copies "Artist Title" for searching elsewhere.
- **Settings →** (sidebar) changes everything from the wizard and more: watched labels, filters, how many picks per day, ranking weights. **Save & collect now** runs a collection straight away.

## How picks are found

For each reference track, DC Digs reads up to 60 of its buyers (from the public "supported by" list) and their most recent purchases. Releases owned by at least two of them are checked, most-shared first. They must share a real genre tag with the reference (broad ones like "electronic" don't count) unless many buyers own them. At most 5 new picks per reference per day and 2 per artist, so one artist or label can't flood your inbox. All of this is adjustable in Settings.

Only counts are kept. Other people's names and collections aren't stored; the raw pages are cached for up to a week in `cache/` and then deleted.

## Good to know

- **Bandcamp's pages and internal endpoints aren't an official API.** They can change at any time, and DC Digs may then stop finding picks until it's updated. Problems show up under **Collector** in the sidebar.
- It's meant for **personal use**: it reads pages slowly (at least a second between requests), caches them, and doesn't download audio. Previews stream from Bandcamp as they would on Bandcamp's own player; no audio is saved. Please respect Bandcamp's terms of use and support the artists you discover by buying their music.
- Your data stays in this folder: `data/` (inbox, favourites), `criteria.yaml` (settings), `logs/`, `cache/`.

## Uninstall

Double-click **`Uninstall on Mac.command`** or **`Uninstall on Windows.bat`**. That stops DC Digs and removes it from your login items; then delete the folder.

## For developers

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

- `dig/`: the app (Flask) and the collectors (`dig/collectors/`); `python -m dig.collect` runs a collection by hand.
- `static/`: plain HTML, CSS and JavaScript, no build step.
- `tests/fixtures/`: made-up sample pages with the structure of Bandcamp's pages, for the parser tests. Every artist, label, fan and link in them is invented; `tests/fixtures/make_fixtures.py` writes them.
- `.env` (optional, see `.env.example`): `DIG_PORT` to use another port.

## License

MIT, see [LICENSE](LICENSE).
