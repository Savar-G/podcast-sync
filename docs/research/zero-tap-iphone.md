# Research: YouTube → iPhone without the Shortcut tap

Goal: when you pause a matched video, the position reaches Apple Podcasts on the iPhone by itself.
Idea: make Apple Podcasts on the Mac record the position. Podcasts then uploads it to iCloud, and the iPhone gets it.

Tested on macOS 26.6.2 (Apple Silicon), Podcasts 4025.700, with one test episode that was not started.
All checks read the library read-only (`ZMTEPISODE.ZPLAYHEAD`, `ZLASTDATEPLAYED`, `ZPLAYSTATELASTMODIFIEDDATE`) and the Podcasts log (`log show --predicate 'process == "Podcasts"'`).

## Result

This works, with no prompt, no sound, and no window:

1. Give Podcasts a one-episode playback queue with `MRMediaRemoteSetAppPlaybackQueueForPlayer`, and ask it not to start playback.
2. Send `SeekToPlaybackPosition` (command 24) to `com.apple.podcasts` with `MRMediaRemoteSendCommandToApp`.

The library shows the new position about 0.5 s later. Podcasts then runs its "UPP" (universal playback position) sync and uploads the position to Apple in about 0.3 s more. The log shows `Sending merged items to server … bktm=00:02:0.00` and `Transaction did finish`.
11 of 11 pushes were confirmed in the library. The tool took 0.4–0.7 s for each push. Podcasts did not make sound at any time (CoreAudio `IsRunningOutput` stayed 0, and the player stayed in `PausedState`).

`scripts/podcasts_remote.c` does this. `helper/podsync/features/push_to_podcasts.py` decides when to call it.

## What we tried

| # | Approach | Result |
|---|---|---|
| 1 | `open -g "podcasts://podcasts.apple.com/podcast/id<collection>?i=<track>&t=<s>"` | No playback. `ZPLAYHEAD` did not change in 20 s. The Podcasts window came on screen (not in front), also with `-g`. |
| 2a | MediaRemote reads from a compiled CLI: `MRMediaRemoteGetNowPlayingInfo`, `…IsPlaying`, `…PID` | Refused. `mediaremoted` logs `Operation not permitted` (`kMRMediaRemoteFrameworkErrorDomain` code 3) for a client with `entitlements=0`. This is the macOS 15.4+ limit. |
| 2b | `MRMediaRemoteSendCommand`, `MRMediaRemoteSetElapsedTime` (to the "now playing" app) | Not run. The "now playing" app was another app, and these calls would have paused or moved its media. They cannot aim at Podcasts. |
| 2c | `MRMediaRemoteSendCommandToApp(…, "com.apple.podcasts", …)` | Allowed without entitlements. `Pause` and `SeekToPlaybackPosition` reach Podcasts (status 0). |
| 2d | `MRMediaRemoteSetAppPlaybackQueueForPlayer`, queue type 5, identifier `podcasts://playItem?storeTrackId=<track>&storeCollectionId=<collection>`, `IsRequestingImmediatePlayback = false` | Allowed. Podcasts loads the episode, paused, at its saved position. The call order and arguments come from the Podcasts notification extension (`otool -tV`). A `playhead=` parameter in the identifier is ignored. |
| 2c + 2d | Load paused, then seek | **Works.** See above. |
| 3 | Other prompt-free signals | CoreAudio `kAudioHardwarePropertyProcessObjectList` + `kAudioProcessPropertyIsRunningOutput` (macOS 14.2+) tells if Podcasts makes sound, with no prompt. Podcasts also writes `widgetNowPlayingInfo` (`currentEpisodeID`, `isPlaying`) to its group-container preferences. |
| 4 | Mac Shortcuts (`shortcuts run`) | Not needed. Podcasts gives Shortcuts "Play or Pause Episode" (`PlayPauseWidgetIntent`), but no seek and no "mark as unplayed". You would have to add a Mac shortcut once, and it would play sound for a moment. |
| – | Load a dylib into `/usr/bin/perl` to get around the read limit | Not tried. It works around an OS protection, and the session policy refused it. It is not necessary. |

Not tested: a push when Podcasts is not running. The helper always starts Podcasts hidden first (the code that already exists for iPhone → YouTube), then pushes.

## Side effects seen

- **Near the end, Podcasts marks the episode as played.** A seek to 23 s before the end set `ZPLAYSTATE` to played. A later seek back did not undo it. So the helper never pushes into the last 60 s.
- **The first push starts the episode.** An unplayed episode becomes "in progress" (`ZPLAYSTATE` 2 → 1, `ZISNEW` 1 → 0) and shows in Up Next, as if you had played it.
- **The episode that was loaded before gets a new "last played" date.** Its position does not change. This happens once, when the Mac player changes to a different episode.
- After the first push, the Mac player shows the YouTube episode, paused.

## Limits

- MediaRemote is a private framework. A macOS update can close it. The helper then falls back to the Shortcut link, which it writes on every pause.
- The iPhone loads the new position when Apple Podcasts on the iPhone syncs, usually when you open it.
