# 🎵 OC ReMix Jukebox

**Stream video game remixes and original soundtracks from OC ReMix – with a powerful queue, shuffle modes, and media key support.**

## Features

- **Browse all OCRemix games** – Scrape and cache the full game catalog (thousands of titles).
- **Add games to your queue** – Load original tracks, remixes, and even external album links (SoundCloud, Bandcamp, YouTube).
- **Playback control** – Play, pause, next, previous, volume slider, seek bar, and repeat modes (Off / One / All / Sequential).
- **Shuffle modes** – Shuffle the current queue or enable **Global Shuffle** to randomly pick tracks from **all games**.
- **Save & load queues** – Export your playlist to a JSON file and reload it later. Reload last queue with `Ctrl+Shift+L`.
- **Auto‑remove invalid tracks** – When a track fails to play, it is automatically deleted from the queue (optional).
- **Auto‑export current track** – Write track info (game, title, artist, URL) to a text file each time a new track starts.
- **SoundCloud cookie support** – Load Netscape‑format cookies to avoid 403/429 errors. Cookies can be stored encrypted (machine‑bound) or as plain text.
- **System tray** – Minimise to tray with media controls and tooltip showing the current track.
- **Media keys** – Control playback using keyboard media keys (Play/Pause, Next, Previous).
- **Dark mode detection** – Automatically switches to a dark theme on Windows when the system is in dark mode.

## Installation

Install [VLC media player](https://www.videolan.org/vlc/) and ensure `vlc` is in your PATH.

## First time usage

- Click **File → Refresh Games** to scrape the full game catalog (takes about a minute). The cache is saved locally.
- Search for a game, click its name to add it to the queue.
- Tracks are loaded incrementally – you'll see progress in the right panel.
- Click the ▶ button next to any track to start playback.

## Managing SoundCloud cookies

Many OC ReMix albums link to SoundCloud playlists. To avoid rate limiting (429 errors), you should provide a cookies file:

1. Install a browser extension that exports cookies in **Netscape format** (e.g., *Get cookies.txt LOCALLY* for Chrome/Edge, *cookies.txt* for Firefox).
2. Log into SoundCloud in your browser.
3. Export cookies and save as `soundcloud_cookies.txt`.
4. In the application, go to **File → Manage SoundCloud Cookies**, select the file, and choose **Auto‑Encrypt** (recommended) or **Plain Text**.

The encrypted file is tied to your machine – no password needed, and it will be loaded automatically on next startup.

## Queue management

- **Save queue** – File → Save Queue (JSON format).
- **Load queue** – File → Load Queue.
- **Reload last queue** – File → Reload Last Playlist or press `Ctrl+Shift+L`.
- **Clear queue** – File → Clear Queue.
- **Remove single track** – Click the red ✕ button next to a track.
- **Remove entire game** – Click the ✕ button in the game header.

## Keyboard shortcuts

| Action                 | Shortcut        |
|------------------------|-----------------|
| Reload last queue      | `Ctrl+Shift+L`  |
| Play / Pause           | Media Play/Pause key |
| Next track             | Media Next key        |
| Previous track         | Media Previous key    |

## Acknowledgements

- [OC ReMix](https://ocremix.org) for the amazing music.
- `yt-dlp` for extracting streams.
- VLC for audio playback.
- PyQt5 for the GUI.
- DeepSeek for doing the heavy lifting and frustrating me.