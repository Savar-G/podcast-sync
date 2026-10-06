# Podcast Sync

**Watch a podcast on YouTube at your desk. Leave, and keep listening on your iPhone from the same second. Come back, and YouTube picks up where your iPhone stopped.**

Free and open source. No account, no subscription, and no YouTube Premium.

<img src="docs/toast-resumed.png" alt="A small message on a YouTube video: Resumed at 40:30 from Apple Podcasts, with an Undo button" width="372">

## The problem

Many podcasts publish each episode twice: as a video on YouTube and as audio in Apple Podcasts. The two apps do not know about each other.

So you watch 40 minutes on YouTube, then you leave the house. You open Apple Podcasts on your iPhone, and it starts at 0:00. You scrub to 40:00 by hand. When you come home, you do it again in the other direction.

Podcast Sync does this for you, in both directions.

## What it does

**From YouTube to your iPhone.** Pause the video, then leave. A small message on the video says **Sent to iPhone at 41:40**. Open Apple Podcasts on your iPhone and press Play. It continues from 41:40.

<img src="docs/toast-sent.png" alt="Message on the video: Sent to iPhone at 41:40" width="230">

**From your iPhone to YouTube.** Open the same episode on YouTube. The video jumps to where you stopped on your iPhone. If you do not want the jump, click **Undo**.

**It finds the episode for you.** YouTube and Apple Podcasts often give the same episode different titles. Podcast Sync compares the length, the date, and the guest's name, so you do not set anything up for each show. It works with any show you follow in Apple Podcasts that also posts full episodes on YouTube.

### Extras

- **A mark on the progress bar.** A small purple mark on the YouTube progress bar shows where you stopped on your iPhone. Point at it to see the time. Click it to jump there.

  <img src="docs/marker.png" alt="A purple mark on the YouTube progress bar, with the label: iPhone, 40:30, 1 min ago" width="660">

- **Fix the timing with one click.** Some shows add a different intro or ads to the video. If the video starts a little early or late, click **−15 s** or **+15 s**. Podcast Sync remembers this for that show.

  <img src="docs/toast-nudge.png" alt="Message: Saved. This show's video runs 15 s ahead of the audio. Buttons: −15 s, +15 s, Undo" width="580">

- **Continue on YouTube.** Click the Podcast Sync icon in Chrome. It lists the episodes you played lately on your iPhone. Click **Watch from 40:30** to open the video at that time. To remove an episode from the list, click its **×**. It stays hidden until you play it again.
- **Progress on thumbnails.** On the YouTube home page and in search results, a thin purple bar under a video shows how far you got in Apple Podcasts.
- **Your choice.** If you do not want the video to jump by itself, click the Podcast Sync icon and choose **Only show the marker**.

## What you need

- **A Mac.** Podcast Sync runs on your Mac. It is tested on macOS 26 and iOS 26.
- **An iPhone** with Apple Podcasts.
- **Google Chrome** on the Mac. Other Chromium browsers (Arc, Brave, Edge) also work.
- **The same Apple Account** in Apple Podcasts on the Mac and on the iPhone.
- **Sync Library turned on** in Apple Podcasts, on both devices:
  - On the Mac: open **Podcasts → Settings → General**.
  - On the iPhone: open **Settings → Apps → Podcasts**.

Your Mac must be awake when you pause a video, because the Mac sends the position to your iPhone.

## Set it up (about 10 minutes)

You type a few commands in **Terminal**, the Mac app for typed commands. To open it, press **⌘ Space**, type `Terminal`, and press **Return**. To run a command, paste it into the Terminal window and press **Return**.

1. **Install Apple's free developer tools.** Run this command:

   ```bash
   xcode-select --install
   ```

   A window opens. Click **Install** and wait until it finishes. If Terminal says the tools are already installed, go to the next step.

2. **Download Podcast Sync.** On this page, click the green **Code** button, then **Download ZIP**. Double-click the ZIP file to open it. Move the folder to a place where you keep files, for example **Documents**.

   Podcast Sync runs from this folder, so keep it there. If you move it later, do step 3 again.

3. **Install the helper.** The helper is a small app that runs in the background on your Mac. In Terminal, type `cd ` (with a space at the end). Drag the Podcast Sync folder from Finder onto the Terminal window, then press **Return**. Then run:

   ```bash
   ./scripts/install.sh
   ```

   macOS asks if **Podcast Sync Helper** may access data from other apps. Click **Allow**. This lets the helper read your play positions from Apple Podcasts.

4. **Add the extension to Chrome.**
   1. In Chrome, go to `chrome://extensions`.
   2. Turn on **Developer mode** (top right).
   3. Click **Load unpacked** and choose the `extension` folder inside the Podcast Sync folder.

   A setup page opens. It checks each step by itself and shows what is left to do.

   <img src="docs/setup-page.png" alt="The setup page with five steps: start the helper, allow access, turn on Sync Library, the iPhone shortcut, and try it" width="450">

5. **Try it.** Open an episode on YouTube, watch a minute, and pause it. Open Apple Podcasts on your iPhone. The episode is at the same spot.

### Optional: a backup shortcut for the iPhone

Usually your iPhone gets the position by itself. Sometimes it cannot: for example, when Apple Podcasts is playing on your Mac at the same time. Then the message on the video says **Ready on iPhone** instead of **Sent to iPhone**. For those times, add a shortcut. In Terminal, in the Podcast Sync folder, run:

```bash
python3 scripts/make_shortcut.py && open "shortcut/Resume Podcast.shortcut"
```

Click **Add Shortcut**. It appears on your iPhone after a moment. On the iPhone, run **Resume Podcast** once and allow its questions. When you see **Ready on iPhone**, tap the shortcut. Apple Podcasts opens the episode with **Play from 13:40**.

## Questions

**Does it cost anything?** No. It is free and open source.

**Do I need YouTube Premium?** No. You watch on YouTube in Chrome as usual, and you listen in Apple Podcasts.

**Does my data go anywhere?** No. Everything stays on your Mac and in your own iCloud. There are no servers, accounts, or analytics. See [Privacy and security](#privacy-and-security).

**Does it change my podcast library?** Only the play position of the episode you watched. It moves the position the same way as when you drag the slider in Apple Podcasts.

**Does my Mac need to be on?** Yes, and awake. The Mac does the work when you pause a video.

**Does it work on Windows, Android, or Spotify?** No. It needs a Mac, an iPhone, and Apple Podcasts.

**What if YouTube and the podcast are a few seconds apart?** Click **−15 s** or **+15 s** once. Podcast Sync remembers the difference for that show.

## Something is not working

| What you see | What to do |
|---|---|
| Nothing happens on YouTube | Reload the YouTube page. Chrome adds the extension only to pages that you open after you install it. |
| The extension says the helper is not running | Do step 3 of the setup again. |
| The extension says macOS has not allowed it to read Podcasts | Open **System Settings → Privacy & Security** and allow **Podcast Sync Helper**. Or do step 3 again and click **Allow**. |
| The video does not jump | You watched further on YouTube than on your iPhone, so YouTube has the newer position. Or the two positions are less than 15 seconds apart. |
| The message says **Ready on iPhone**, not **Sent to iPhone** | Apple Podcasts was playing on your Mac, or you stopped in the last minute of the episode. Use the [backup shortcut](#optional-a-backup-shortcut-for-the-iphone). |
| The iPhone shows an old position | Close Apple Podcasts on the iPhone and open it again. Check that **Sync Library** is on, on both devices. |
| The shortcut says it cannot open a file | On the iPhone, open the **Files** app and go to **iCloud Drive → Shortcuts → podcast-sync**. Tap `resume.txt` once. Then touch and hold the **podcast-sync** folder and choose **Keep Downloaded**. |
| Terminal says "permission denied" | Run `zsh scripts/install.sh` instead of `./scripts/install.sh`. |

If you still have a problem, [open an issue](https://github.com/Savar-G/podcast-sync/issues).

## Remove it

In Terminal, in the Podcast Sync folder, run:

```bash
./scripts/uninstall.sh
```

Then remove the extension in `chrome://extensions`, and remove the **Resume Podcast** shortcut if you added it.

---

# For developers

## How it works

Podcast Sync has three parts:

- A **Chrome extension** that reads the time from the YouTube player.
- A **helper**: a small Python program on your Mac. It runs at login inside a tiny signed app, **Podcast Sync Helper**, and listens on `127.0.0.1:47321`.
- **Apple Podcasts on your Mac**, which syncs play positions with your iPhone through iCloud.

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

- **Matching.** The helper loads the public YouTube page of the video for its channel, length, and upload date. It compares these with episodes in your Apple Podcasts library: length (it allows up to 5 minutes of extra ads in the audio), publish date, and shared title words, usually the guest's name. For example, "From HOA Management to $4B…" on YouTube is "Bringing AI to the Real Economy" in Apple Podcasts. After the first match, the helper remembers which show the YouTube channel publishes. On a test set of 33 recent videos from 5 shows, it matched every full episode and rejected every clip.
- **Fresh iPhone positions.** The Mac gets positions from iCloud only while the Podcasts app runs. When you open YouTube, the helper opens Podcasts hidden, so the sync (about 3 seconds) is done before you pick a video. It does this at most once in 5 minutes, and only after it knows one of your shows. It quits Podcasts after 10 idle minutes. It never quits a Podcasts window that you opened.
- **No tap on the iPhone.** Apple Podcasts sends a changed position to iCloud at once, and the iPhone gets it from there. So on pause, the helper tells Apple Podcasts on your Mac to load that episode, paused, at the same second. Nothing plays and no window opens. A small tool, `podcasts-remote` (built from [`scripts/podcasts_remote.c`](scripts/podcasts_remote.c)), sends these commands. The helper then reads the library to confirm the new position. Only then does the message say **Sent to iPhone**.
  - It skips the push if Podcasts is playing on your Mac, if the spot is within 15 seconds of the Podcasts position, if it is in the last minute of the episode (Podcasts would mark it as played), or if you listened in Apple Podcasts after the video last moved. So an old paused tab that you close cannot undo a newer iPhone listen.
  - At most one push per episode every 20 seconds. If you pause again during that time, the newest spot goes when the time is up.
- **Newest wins.** A position moves to the other side only if it is newer than the last position from that side.
- **Learned offsets.** Each **−15 s** / **+15 s** click adds to that show's offset in the helper's state. The offset applies to the jump, the push to the iPhone, and the iPhone link.
- **Thumbnails.** The extension sends the video IDs on a page to the helper, at most 60 at a time. The helper answers only for videos it matched before, from your Mac's library. It makes no network request.
- **The fallback link** is a standard Apple Podcasts link with a time (`…?i=<episode>&t=820`). The helper writes it on every pause, so the **Resume Podcast** shortcut always works.

## Privacy and security

- **Everything stays on your Mac and in your own iCloud.** No servers, accounts, or analytics.
- The helper opens the Apple Podcasts library **read-only**. It never writes to the library file. To move an episode to your YouTube spot, it asks the Podcasts app, as if you moved the slider. Set `push_to_podcasts` to `false` to turn this off.
- `podcasts-remote` uses MediaRemote, a private macOS framework. It sends commands only to Apple Podcasts, never to the app that is playing now. Apple can change MediaRemote at any time. If it stops working, the helper falls back to the shortcut link.
- **Network requests:**
  - The helper loads the public YouTube page of a video you open.
  - It uses Apple's public podcast lookup API when an episode is too new for your Mac library.
  - For **Continue on YouTube**, it loads the public upload feeds of YouTube channels it already knows, and the pages of new uploads on them. The popup loads show artwork from Apple's image server.
- For the setup page, the helper checks if a shortcut named "Resume Podcast" exists (`shortcuts list`), and reads the time Podcasts last synced with iCloud. It does not save or send this data.
- The helper listens on `127.0.0.1` only. It accepts requests from this extension (its ID is pinned in `manifest.json`) or from a local tool such as `curl`. It refuses web pages, other extensions, and DNS-rebinding attempts.
- The extension can talk only to `http://127.0.0.1:47321`, and it runs only on `youtube.com`.
- **Local files:** `~/Library/Application Support/podcast-sync/` (match cache, the helper app, `podcasts-remote`), `~/Library/Logs/podcast-sync.log`, and the link file in iCloud Drive.

## Configuration (optional)

No configuration is needed. To pin a channel or set a fixed offset for a show, copy [`config.example.json`](config.example.json) to `~/.config/podcast-sync/config.json`, then run `./scripts/install.sh` again.

| Key | Meaning |
|---|---|
| `shows[].offset_seconds` | YouTube time minus Podcasts time, for example `90`. The **−15 s** / **+15 s** buttons add to it (at most 10 minutes either way). |
| `shows[].youtube_channels`, `apple_ids` | Pin a YouTube channel ID to Apple Podcasts show IDs. |
| `min_video_seconds` | Videos shorter than this (clips) are ignored. Default `600`. |
| `podcasts_idle_quit_seconds` | How long a hidden Podcasts app stays open. Default `600`. |
| `push_to_podcasts` | On pause, move Apple Podcasts on this Mac to your YouTube spot, so the iPhone gets it with no tap. Default `true`. With `false`, use the **Resume Podcast** shortcut. |

**Debugging:** `tail -f ~/Library/Logs/podcast-sync.log` shows the helper's log. `curl -H 'X-Podsync: 1' 127.0.0.1:47321/status` shows its state, including why a push was skipped (`pushToPodcasts`).

## Development

```bash
git clone https://github.com/Savar-G/podcast-sync.git
cd podcast-sync
python3 -m unittest discover -s tests     # matcher on real titles, sync rules, HTTP security, Podcasts timing
npm install && npx playwright-core install chromium
./scripts/e2e.sh                          # real Chromium + extension + YouTube, against a fake library
```

`e2e.sh` runs a throwaway helper on its own port (47399) with a fake Podcasts library, and loads a test copy of the extension that talks to that port. Your installed helper keeps running, and the test never touches your real state, Podcasts app, or iCloud files. A pretend Podcasts app takes as long to sync as the real one, so the test also measures the resume time.

| Path | What |
|---|---|
| `helper/podsync/` | The helper (Python standard library only). New features go in `helper/podsync/features/` and register themselves. |
| `extension/` | Chrome extension (Manifest V3) |
| `scripts/` | Install, uninstall, shortcut builder, app launcher, `podcasts_remote.c`, e2e runner |
| `tests/` | Unit tests and fixtures; `tests/e2e/` is the browser test |

Podcast Sync is not affiliated with Apple, Google, or YouTube. It reads data that Apple Podcasts stores on your Mac, and Apple can change that format at any time.

## License

[MIT](LICENSE)
