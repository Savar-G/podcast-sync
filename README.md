# Podcast Sync

**Watch a podcast on YouTube at your desk, then keep listening in Apple Podcasts on your iPhone, from the same second. And back again.**

Many podcasts publish the same episode twice: as video on YouTube and as audio in Apple Podcasts. The two apps do not share your position. Podcast Sync connects them, without accounts, servers, or a YouTube subscription.

| On YouTube, after you listened on your iPhone | On YouTube, when you pause |
|---|---|
| ![Resumed at 40:30 from Apple Podcasts](docs/toast-resumed.png) | ![Ready on iPhone at 41:40](docs/toast-ready.png) |

## How you use it

- **iPhone → YouTube:** open the episode on YouTube in Chrome. It jumps to where you stopped in Apple Podcasts. An **Undo** button is there if you do not want the jump. If the video is a little early or late, press **−15 s** or **+15 s**. The helper remembers this for the show, in both directions. If the check takes more than a moment, you see **Checking Apple Podcasts…**.
- **iPhone was here:** a small purple mark on the YouTube progress bar shows where you stopped on the iPhone. Point at it to see the time and when you played it. Click it (or press Enter on it) to jump there. It hides when the video is within 15 seconds of it.
- **Progress on thumbnails:** on the home page, search, channel pages, and the list next to a video, a thin purple bar shows how far you are in Apple Podcasts. It shows only on episodes you started and the helper matched before. It sits above YouTube's red bar.
- **YouTube → iPhone:** pause the video. The toast says **Sent to iPhone at 13:40**: open Apple Podcasts on your iPhone and press play. If the toast says **Ready on iPhone at 13:40**, tap the **Resume Podcast** shortcut instead, and Apple Podcasts opens the episode with **Play from 13:40**.
- **Continue on YouTube:** click the extension icon. It lists the episodes you played lately in Apple Podcasts. Click **Watch from 40:30** to open the YouTube video at that time. If the helper does not know the video yet, the button is **Search on YouTube**.
- **Newest wins:** a position moves to the other side only when it is newer than the last position from that side.

It works for any show you follow in Apple Podcasts that also posts full episodes on YouTube. There is nothing to configure: the first time you watch an episode, the helper finds the show and remembers the channel.

## Requirements

- A Mac with Apple Podcasts signed in to the same Apple Account as your iPhone, and **Settings → Sync Library** turned on in Podcasts.
- Google Chrome (or another Chromium browser that can load unpacked extensions: Arc, Brave, Edge).
- An iPhone with Apple Podcasts, Shortcuts, and iCloud Drive.
- Apple's command line tools, for `python3` and `clang`: `xcode-select --install`.

Tested on macOS 26 and iOS 26.

## Install

```bash
git clone https://github.com/Savar-G/podcast-sync.git
cd podcast-sync
./scripts/install.sh
```

1. **Helper.** `install.sh` builds a small background app, **Podcast Sync Helper**, and starts it at login. macOS asks once to let it "access data from other apps". Click **Allow**: this lets it read the Apple Podcasts library on your Mac.
2. **Chrome extension.** Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose the `extension` folder. Reload any open YouTube tabs.

   A setup page opens when you load the extension. It checks each step by itself and shows what is left to do. To open it again, right-click the Podcast Sync icon and choose **Options**.

   <img src="docs/setup-page.png" alt="The setup page: two steps done, three to do" width="450">

3. **iPhone shortcut (fallback).** Usually your iPhone gets the position by itself. For the times it cannot (for example, Podcasts plays on your Mac), on your Mac run:
   ```bash
   python3 scripts/make_shortcut.py && open "shortcut/Resume Podcast.shortcut"
   ```
   Click **Add Shortcut**. iCloud syncs it to your iPhone. Run it once on the iPhone and allow its prompts. For one tap, add it to your Home Screen or the Action Button, or make an automation: *When AirPods connect → Run Resume Podcast*.

## How it works

```
 Chrome extension ──► Podcast Sync Helper (127.0.0.1:47321, on your Mac)
  reads the YouTube      │ ├─ reads the Apple Podcasts library on this Mac (read-only)
  player time            │ │    iCloud keeps your iPhone's position in it
                         │ ├─ on pause: moves Apple Podcasts on this Mac to the same second
                         │ │    Podcasts sends it to iCloud ──► Apple Podcasts on iPhone
                         │ └─ writes a "resume here" link to iCloud Drive (fallback)
                         ▼
             iCloud Drive/Shortcuts/podcast-sync/resume.txt ──► "Resume Podcast" on iPhone
```

- **Matching.** YouTube and podcast titles often differ ("From HOA Management to $4B…" on YouTube is "Bringing AI to the Real Economy" in Podcasts). The helper matches on length, publish date, and shared title words, usually the guest's name. It allows for up to 5 minutes of extra ads in the audio. On a test set of 33 recent videos from 5 shows, it matched every full episode and rejected every clip.
- **Fresh iPhone positions.** The Mac pulls positions from iCloud only while the Podcasts app runs. When you open YouTube, the helper opens Podcasts hidden, so the sync (about 3 seconds) is done before you pick a video. Then the jump is instant. It does this at most once in 5 minutes, and only after it knows one of your shows. It quits Podcasts after 10 idle minutes. It never quits a Podcasts window that you opened.
- **No tap on the iPhone.** Apple Podcasts sends a changed position to iCloud at once, and your iPhone gets it from there. So when you pause or leave a matched video, the helper tells Apple Podcasts on your Mac to load that episode, paused, and to go to the same second. Nothing plays and no window opens. These are the same requests that the Podcasts notification buttons and Control Center send. A small tool, `podcasts-remote` (built from [`scripts/podcasts_remote.c`](scripts/podcasts_remote.c)), sends them. The helper then reads the library to make sure that Podcasts recorded the new position. Only then does the toast say **Sent to iPhone**.
  - It skips the push if Podcasts plays on your Mac, if the spot is within 15 seconds of the Podcasts position, if it is in the last minute of the episode (Podcasts would mark it as played), or if you listened in Apple Podcasts after the video last moved (an old paused tab that you close does not undo a newer iPhone listen).
  - At most one push per episode every 20 seconds. If you pause again during that time, the newest spot goes when the time is up.
  - The extension waits up to 3 seconds for the result. If Podcasts must start first, the push can take longer: then the toast says **Ready on iPhone**, and the push still completes.
- **Thumbnails.** The extension sends the video IDs on the page to the helper, 60 at a time. The helper answers only for videos it matched before, from the Podcasts library on your Mac. It makes no network request and does not open Podcasts.
- **Learned offsets.** Some shows put a different intro or ads in the video. Each **−15 s** / **+15 s** press adds to that show's offset in the helper's state file. The helper uses it for the jump on YouTube, for the push to the iPhone, and for the iPhone link.
- **The marker** uses the same check as the jump, so it also shows when your YouTube position is newer. It sits inside YouTube's progress bar and follows theater mode, fullscreen, and the mini player.
- **The iPhone link** is a standard Apple Podcasts link with a time (`…?i=<episode>&t=820`), which Apple Podcasts opens at that time. The helper writes it on every pause, so the **Resume Podcast** shortcut always works as the fallback.

## Privacy and security

- **Everything stays on your Mac and in your own iCloud.** No servers, no accounts, no analytics.
- The helper opens the Podcasts library **read-only**. It never writes to the library file. To move an episode to your YouTube spot, it asks the Podcasts app, as if you moved the slider. Set `push_to_podcasts` to `false` to stop this.
- `podcasts-remote` uses MediaRemote, a private macOS framework. It sends commands only to Apple Podcasts, never to the app that plays now. Apple can change MediaRemote at any time. If it stops working, the helper falls back to the Shortcut link.
- For the setup page, the helper checks if a shortcut named "Resume Podcast" exists (`shortcuts list`), and reads the time Podcasts last synced with iCloud. It does not save or send this data.
- The only network requests: the helper loads the public YouTube page of a video you open (for its channel, length, and date), and Apple's public podcast lookup API when an episode is too new for your Mac library. For **Continue on YouTube**, the helper also loads the public upload feed of YouTube channels it already knows and the pages of new uploads on them, and the popup loads show artwork from Apple's image server.
- The helper listens on `127.0.0.1` only. It accepts requests from this extension (its ID is pinned in `manifest.json`) or from a local tool such as `curl`. It refuses web pages, other extensions, and DNS-rebinding attempts.
- The extension can talk only to `http://127.0.0.1:47321` and runs only on `youtube.com`.
- Local files: `~/Library/Application Support/podcast-sync/` (match cache, `podcasts-remote`), `~/Library/Logs/podcast-sync.log`, and the link file in iCloud Drive.

## Settings (optional)

Click the extension icon. Under **When you open an episode**, choose:

- **Jump to the iPhone position** (default).
- **Only show the marker.** The video does not jump. The toast shows a **Jump to 40:30** button, and the mark stays on the progress bar.

No config is needed. To pin a channel or fix a show whose audio is always ahead of or behind the video, copy [`config.example.json`](config.example.json) to `~/.config/podcast-sync/config.json`, then run `./scripts/install.sh` again.

| Key | Meaning |
|---|---|
| `shows[].offset_seconds` | YouTube time minus Podcasts time, for example `90`. The **−15 s** / **+15 s** buttons add to it (at most 10 minutes either way). |
| `shows[].youtube_channels`, `apple_ids` | Pin a YouTube channel ID to Apple Podcasts show IDs. |
| `min_video_seconds` | Shorter videos (clips) are ignored. Default `600`. |
| `podcasts_idle_quit_seconds` | How long a hidden Podcasts app stays open. Default `600`. |
| `push_to_podcasts` | On pause, move Apple Podcasts on this Mac to your YouTube spot, so the iPhone gets it through iCloud with no tap. Default `true`. With `false`, use the **Resume Podcast** shortcut. |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Nothing happens on YouTube | Reload the tab: Chrome adds the extension only to pages loaded after you install it. Click the extension icon to see the helper status. |
| "macOS has not allowed it to read Podcasts" | Open **System Settings → Privacy & Security** and allow Podcast Sync Helper, or run `./scripts/install.sh` again and click **Allow**. |
| The video does not jump | Your last YouTube session for that episode is newer than your iPhone listen, or you are within 15 seconds of the iPhone position. |
| The toast says "Ready on iPhone", not "Sent to iPhone" | Podcasts plays on your Mac, the spot is in the last minute, or Podcasts had to start first. Use the **Resume Podcast** shortcut. To see why, run `curl -H 'X-Podsync: 1' 127.0.0.1:47321/status` and look at `pushToPodcasts`. |
| The iPhone shows an old position after "Sent to iPhone" | Close Apple Podcasts on the iPhone and open it again: it loads positions from iCloud when it opens. Make sure that **Sync Library** is on in Podcasts settings on both devices. |
| The Shortcut says the file could not be opened | In the Files app, open **iCloud Drive → Shortcuts → podcast-sync**, tap `resume.txt` once, then touch and hold the folder and choose **Keep Downloaded**. |
| Anything else | `tail -f ~/Library/Logs/podcast-sync.log` |

## Uninstall

```bash
./scripts/uninstall.sh
```

Then remove the extension in `chrome://extensions` and the shortcut in Shortcuts.

## Development

```bash
python3 -m unittest discover -s tests     # matcher on real titles, sync rules, HTTP security, Podcasts timing
npm install && npx playwright-core install chromium
./scripts/e2e.sh                          # real Chromium + extension + YouTube, against a fake library
```

`e2e.sh` stops your installed helper, runs a throwaway one with a fake Podcasts library, and starts yours again. It never touches your real state, Podcasts app, or iCloud files. A pretend Podcasts app takes as long to sync as the real one, so the test also measures the resume time.

| Path | What |
|---|---|
| `helper/podsync/` | The helper: Python standard library only |
| `extension/` | Chrome extension (Manifest V3) |
| `scripts/` | Install, uninstall, Shortcut builder, app launcher, `podcasts_remote.c`, e2e runner |
| `tests/` | Unit tests and fixtures; `tests/e2e/` browser test |

Podcast Sync is not affiliated with Apple, Google, or YouTube. It reads data that Apple Podcasts stores on your Mac, and Apple can change that format at any time.

## License

[MIT](LICENSE)
