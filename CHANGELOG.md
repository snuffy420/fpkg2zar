# Changelog

Each addition bumps the version by one step: 0.0.1 … 0.0.9, then 0.1.0 (never 0.0.10).

## Still to do

- [ ] Add more games to the README Benchmarks table as they're tested.
- [ ] Remove the temporary AstroHybrid fix once AstroHybrid fixes its issues #272 and #283.

## 0.1.9

- **Release files.** `tools\make_release.py` builds `releases\v<version>\` with the portable exe, a `fpkg2zar-v<version>-source.7z` of the repo files, and a `.sha256` checksum beside each. The README download table links them.
- **GitHub upload folder.** `tools\make_upload.py` copies only the files that belong in the repo into `github_upload\fpkg2zar\` (source, assets, docs, LICENSE, CHANGELOG, start/build scripts and `kraken.dll`; nothing from test folders, no packages or archives).
- README rewritten in the BoxStream layout: centred icon and title, the **no proprietary code, keys, ROMs or game data** notice up front, download table, features with screenshots, **Benchmarks**, Support (BTC) and Contact sections.
- Screenshots in `assets/screenshots/` are made with `tools\make_screenshots.py`, which fills the window with **made-up games, title IDs, file names and paths**. Nothing real is shown.
- `docs/contact.html`: contact page in the app's colours, using the same Formspree form as BoxStream (`myeekrpy`), subject "fPKG2Zar contact", spam trap, and a note not to send packages, keys or game files. The About and Help tabs link to it.
- Benchmarks: Astro Bot at level 15 (default) measured at **41.73 GiB** (118% of the `.pkg`, 28.0% of the unpacked game) in about 28 minutes, and it loads in AstroHybrid with the DLL fix.

## 0.1.8

- **New icon**: the blue-tint game disc with data rings and a Z with a curved arrow in the hub. `tools\make_icon.py` builds `icon.svg`, `icon.png` and a multi-size `icon.ico` (16 to 256 px). Used for the window, taskbar, title bar, About tab and the portable exe.

## 0.1.7

- The **fPKG2Zar** title at the top of the window is now bold.

## 0.1.6

- **About** button in the title bar. It opens a themed window with three tabs:
  - **About**: version, "by snuffy420 · zerØ software", PS5-fPKG-only notice, no proprietary code/keys/ROMs/game data notice, credits, licence and repo link.
  - **Help**: how to convert, how the streaming works, compression levels, the options (including the temporary AstroHybrid fix), supported packages and common problems.
  - **Donate**: Bitcoin QR code (`bitcoin:` link, generated into `fpkg2zar/assets/btc_qr.png`), the address with a **Copy** button, and **Open in wallet**. The address's bech32 checksum was checked before the QR was made.

## 0.1.5

- Ticking the AstroHybrid fix now shows a plain notice with an OK button, then opens the folder picker straight away (no Yes/No question). Cancelling the picker unticks the box.

## 0.1.4

- **Temporary:** "Fix AstroHybrid .zar loading" option. It applies the community workaround from AstroHybrid's issue #272: it downloads the four Microsoft DLL packages from nuget.org (checked against NuGet's published SHA-512), backs up AstroHybrid's originals and swaps them in. It re-checks at every start, and unticking it restores the originals. Lives in `fpkg2zar/astrohybrid_fix.py` plus GUI lines marked `TEMPORARY`. **Remove once AstroHybrid fixes the bug** (its issues #272 and #283).

## 0.1.3

- Every pop-up now matches the app: message dialogs are drawn in the same theme with a blended, draggable title bar; the file and folder pickers use Qt's own picker so they follow the theme; right-click menus, spin-box arrows, dropdowns, lists and tooltips are themed too (arrows drawn from small SVGs in `fpkg2zar/assets`, bundled into the exe).
- README: new **Benchmarks** table (`.pkg`, unpacked, files, `.zar` and level, `.zar` vs `.pkg`, time), starting with Astro Bot.

## 0.1.2

- The full Log tab lists every file as it's written into the archive, with its size (and notes for files stored once as shared data, or added from the package metadata). Lines are sent in batches so the window stays responsive on games with 100,000+ files. The bottom log panel keeps showing only the summary messages.

## 0.1.1

- PS4 and PS3 packages now get a clear "only PS5 fake packages" message (PS3 packages used to show a generic error), and retail PS5 packages say why they're refused.
- README now says plainly that fPKG2Zar is **PS5 only**: retail PS5, PS4 and PS3 packages aren't supported, and why.

## 0.1.0

- "Free at output" in the Progress panel refreshes every 2 seconds during a conversion, instead of showing the value from before it started.

## 0.0.9

- The current file name in the Progress panel always reserves two lines, so a long name wrapping no longer shifts the numbers below it.

## 0.0.8

- The archive is flushed to disk about every 1 GiB during a conversion, so its size in Explorer keeps up instead of staying at 0 KB until the end.

## 0.0.7

- Renamed to **fPKG2Zar** (Python module `fpkg2zar`), with "fPKG only" made clear in the README.
- PS5-style dark theme: navy gradient, console-blue accents, pill buttons.
- App-drawn title bar that blends with the background (minimize, maximize, close, drag to move, edge resize, Windows snap).
- Opens at about 1600×900 and never larger than the screen; minimum size keeps every control readable.
- Log tab fills the window; the Packages tab shows a live log at the bottom.
- Estimated `.zar` size per package and in total, updated as the compression slider moves (sampled, usually within about 5%).
- Default compression level 15, with levels up to 22 (level 6 was the old default).
- Removed the Working folder field from the GUI (`--work-dir` is still on the command line).
- Free space at the output moved into the Progress panel.
- Removed the slogan; the version is shown next to the title.

## 0.0.5

- First working version: streams a PS5 fake package straight into a `.zar` with no extraction, with a PyQt6 GUI, CLI, verification, cancel, and `start.bat`.
