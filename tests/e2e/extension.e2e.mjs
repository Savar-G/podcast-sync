// End-to-end: real Chromium + the extension + real YouTube + the running helper.
//
//   node tests/e2e/extension.e2e.mjs [videoId] [expectedSeconds]
//
// Run it through scripts/e2e.sh, which starts a throwaway helper with a fake library.
// Needs `npm install` and `npx playwright-core install chromium` once.
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir, homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_CORE || 'playwright-core');

const here = path.dirname(fileURLToPath(import.meta.url));
const ext = path.resolve(here, '../../extension');
const videoId = process.argv[2] || 'w3-nMklTFjY';
const expected = Number(process.argv[3] || 2430);
const shots = process.env.SHOTS || tmpdir();
const handoff =
  process.env.HANDOFF ||
  path.join(homedir(), 'Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/podcast-sync/resume.json');
const helperLog = process.env.HELPER_LOG || '';
const readLog = () => (helperLog ? readFileSync(helperLog, 'utf8') : '');
const library = process.env.LIBRARY || ''; // the fake Podcasts library; the helper re-reads it when it changes
// "Listen on the iPhone": move the fake library's position and make it the newest play.
const playOnIPhone = (seconds) => {
  const rows = JSON.parse(readFileSync(library, 'utf8'));
  for (const r of rows) Object.assign(r, { playhead: seconds, last_played_ago: 0 });
  writeFileSync(library, JSON.stringify(rows));
};

// Where the "iPhone was here" marker is, and what it says.
const markerInfo = (page) =>
  page.evaluate(() => {
    const el = document.querySelector('#movie_player .ytp-progress-bar .podsync-marker');
    const bar = document.querySelector('#movie_player .ytp-progress-bar');
    const v = document.querySelector('#movie_player video');
    if (!el || !bar) return { exists: false };
    const hit = el.querySelector('.podsync-marker-hit');
    const tip = el.querySelector('.podsync-marker-tip');
    const r = hit.getBoundingClientRect();
    const b = bar.getBoundingClientRect();
    return {
      exists: true,
      hidden: el.hidden || getComputedStyle(el).display === 'none',
      x: r.left + r.width / 2,
      y: r.top + r.height / 2,
      barLeft: b.left,
      barWidth: b.width,
      duration: v?.duration,
      currentTime: v?.currentTime,
      paused: v?.paused,
      tip: tip.textContent,
      tipOpacity: Number(getComputedStyle(tip).opacity),
      aria: hit.getAttribute('aria-label'),
    };
  });
// The bar x of a video time, the way YouTube draws it (chapters are segments with gaps).
const barXOf = (page, t) =>
  page.evaluate((t) => {
    const bar = document.querySelector('#movie_player .ytp-progress-bar');
    const d = document.querySelector('#movie_player video').duration;
    const b = bar.getBoundingClientRect();
    const segs = [...bar.querySelectorAll('.ytp-chapter-hover-container')].map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0);
    if (segs.length < 2) return b.left + (b.width * t) / d;
    let rest = (t / d) * segs.reduce((s, r) => s + r.width, 0);
    for (const r of segs) {
      if (rest <= r.width) return r.left + rest;
      rest -= r.width;
    }
    return b.right;
  }, t);
const scrubberX = (page) =>
  page.evaluate(() => {
    const r = document.querySelector('#movie_player .ytp-scrubber-button')?.getBoundingClientRect();
    return r ? r.left + r.width / 2 : null;
  });
const nearTime = (page, want, slack = 3) =>
  page
    .waitForFunction(
      ([want, slack]) => {
        const v = document.querySelector('#movie_player video');
        return v && !document.querySelector('#movie_player.ad-showing') && Math.abs(v.currentTime - want) < slack;
      },
      [want, slack],
      { timeout: 20000, polling: 200 }
    )
    .then(() => true)
    .catch(() => false);
const currentTime = (page) => page.evaluate(() => document.querySelector('#movie_player video')?.currentTime ?? -1);

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
};

const ctx = await chromium.launchPersistentContext(mkdtempSync(path.join(tmpdir(), 'podsync-e2e-')), {
  channel: 'chromium',
  headless: true,
  viewport: { width: 1280, height: 800 },
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, '--autoplay-policy=no-user-gesture-required', '--mute-audio'],
});

try {
  const sw = ctx.serviceWorkers()[0] || (await ctx.waitForEvent('serviceworker', { timeout: 15000 }));
  check('extension loaded', !!sw, sw?.url());
  // Time every helper request the extension makes (the service worker does the fetch).
  await sw.evaluate(() => {
    const f = self.fetch.bind(self);
    self.__podsyncTimes = [];
    self.fetch = async (url, opts) => {
      const t = performance.now();
      try {
        return await f(url, opts);
      } finally {
        let ids = null;
        try {
          ids = JSON.parse(opts?.body || 'null')?.videoIds?.length ?? null;
        } catch {}
        self.__podsyncTimes.push({ path: new URL(String(url)).pathname, ms: Math.round(performance.now() - t), ids });
      }
    };
  });
  const helperTimes = (p) => sw.evaluate((p) => self.__podsyncTimes.filter((t) => t.path === p).map((t) => t.ms), p).catch(() => []);

  const page = await ctx.newPage();
  const toasts = [];
  const pollToasts = setInterval(async () => {
    const t = await page.evaluate(() => document.querySelector('.podsync-toast.podsync-show')?.textContent || '').catch(() => '');
    if (t && toasts[toasts.length - 1] !== t) toasts.push(t);
  }, 150);
  // Logged-out YouTube may show an ad first. Skip it like a person would, so the test does not wait on it.
  // Headless Chromium sometimes leaves an ad paused; press play on it, as a person would.
  const skipAds = setInterval(() => {
    page
      .locator('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button')
      .first()
      .click({ timeout: 300 })
      .catch(() => {});
    page
      .evaluate(() => {
        const v = document.querySelector('#movie_player.ad-showing video');
        if (v?.paused) v.play().catch(() => {});
      })
      .catch(() => {});
  }, 1000);

  // 0. You open YouTube. The helper starts Podcasts hidden, so the iCloud sync runs while you browse.
  await page.goto('https://www.youtube.com/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);
  check('opening YouTube warmed up Apple Podcasts', /warming: launching Podcasts hidden/.test(readLog()), helperLog ? '' : 'set HELPER_LOG');

  // 1. Apple Podcasts -> YouTube
  const t0 = Date.now();
  await page.goto(`https://www.youtube.com/watch?v=${videoId}`, { waitUntil: 'domcontentloaded' });
  const waitForJump = (timeout) =>
    page
      .waitForFunction(
        (want) => {
          const v = document.querySelector('#movie_player video');
          const ad = document.querySelector('#movie_player.ad-showing');
          return v && !ad && Math.abs(v.currentTime - want) < 30 ? v.currentTime : false;
        },
        expected,
        { timeout, polling: 250 }
      )
      .then((h) => h.jsonValue())
      .catch(() => null);
  let jumped = await waitForJump(60000);
  if (jumped == null && (await page.evaluate(() => !!document.querySelector('#movie_player.ad-showing')))) {
    console.log('note: an ad did not play in headless Chromium; loading the page once more');
    await page.reload({ waitUntil: 'domcontentloaded' });
    jumped = await waitForJump(60000);
  }
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  check('YouTube jumped to the Apple Podcasts position', jumped != null, `currentTime=${jumped?.toFixed?.(1)} after ${secs}s`);
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(shots, 'e2e-1-resumed.png') });
  check('toast said where it resumed', toasts.some((t) => /Resumed at .* from Apple Podcasts/.test(t)), JSON.stringify(toasts));
  const [resumeMs] = await helperTimes('/resume');
  const [matchMs] = await helperTimes('/match');
  console.log(`TIMING  open-to-jump=${secs}s  /match=${matchMs}ms  /resume=${resumeMs}ms`);
  check('resume after a warm-up is fast', resumeMs != null && resumeMs < 600, `/resume took ${resumeMs} ms`);
  check('no "Checking" toast when the resume is fast', !toasts.some((t) => /Checking Apple Podcasts/.test(t)), JSON.stringify(toasts));
  await page.waitForFunction(() => document.querySelector('.podsync-marker')?.hidden, null, { timeout: 3000 }).catch(() => {});
  let mk = await markerInfo(page);
  check('marker is on the progress bar, hidden while the video is at it', mk.exists && mk.hidden, JSON.stringify(mk));

  // 2. YouTube -> Apple Podcasts: watch a bit further, then pause
  await page.evaluate(async () => {
    const v = document.querySelector('#movie_player video');
    v.currentTime = 2500;
    await Promise.race([v.play().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
  });
  await page.waitForTimeout(2500);
  const pausedAt = await page.evaluate(() => {
    const v = document.querySelector('#movie_player video');
    // Headless Chromium does not always start playback; then pause() fires no event.
    if (v.paused) v.dispatchEvent(new Event('pause'));
    else v.pause();
    return v.currentTime;
  });
  const readyToast = await page
    .waitForFunction(() => /Ready on iPhone/.test(document.querySelector('.podsync-toast')?.textContent || ''), null, {
      timeout: 15000,
    })
    .then(() => true)
    .catch(() => false);
  await page.screenshot({ path: path.join(shots, 'e2e-2-paused.png') });
  let info = { seconds: NaN, url: '(no file)' };
  try {
    info = JSON.parse(readFileSync(handoff, 'utf8'));
  } catch {}
  check(
    'pause wrote the iPhone link',
    Math.abs(info.seconds - pausedAt) <= 2 && info.url.includes('&t='),
    `${info.url} (paused at ${pausedAt.toFixed(1)})`
  );
  check('toast confirmed the handoff', readyToast || toasts.some((t) => /Ready on iPhone at/.test(t)));

  // 2b. The "iPhone was here" marker. Go back to 20:00: far from the marker, so it shows.
  await page.evaluate(() => (document.querySelector('#movie_player video').currentTime = 1200));
  await nearTime(page, 1200, 5);
  await page.waitForTimeout(1200);
  mk = await markerInfo(page);
  const wantX = await barXOf(page, expected);
  check('marker shows at the iPhone position', mk.exists && !mk.hidden && Math.abs(mk.x - wantX) < 2, `x=${mk.x?.toFixed(1)} want ${wantX.toFixed(1)}`);
  await page.mouse.move(mk.x, mk.y - 120); // wake the player controls
  await page.waitForTimeout(300);
  await page.mouse.move(mk.x, mk.y, { steps: 4 });
  await page.waitForTimeout(400);
  mk = await markerInfo(page);
  check('hovering the marker shows its tooltip', mk.tipOpacity > 0.9 && /^iPhone · 40:30 · (just now|\d+ min ago)$/.test(mk.tip), `"${mk.tip}" opacity=${mk.tipOpacity}`);
  check('the marker has an accessible name', /^Jump to the iPhone position, 40:30/.test(mk.aria || ''), mk.aria);
  const box = await page.locator('#movie_player').boundingBox();
  await page.screenshot({ path: path.join(shots, 'e2e-3-marker-hover.png'), clip: { x: box.x, y: box.y + box.height - 140, width: box.width, height: 140 } });
  await page.mouse.click(mk.x, mk.y);
  const clicked = await nearTime(page, expected, 2);
  await page.mouse.move(mk.x, mk.y - 60); // keep the controls (and the scrubber) drawn
  await page.waitForTimeout(500);
  const knob = await scrubberX(page);
  check(
    'clicking the marker seeks to the iPhone position, under the marker',
    clicked && knob != null && Math.abs(knob - mk.x) < 3,
    `currentTime=${(await currentTime(page)).toFixed(1)}, scrubber x=${knob?.toFixed(1)}, marker x=${mk.x.toFixed(1)}`
  );

  // YouTube's own scrubbing still works next to it.
  const quarter = mk.barLeft + mk.barWidth * 0.25;
  await page.mouse.move(quarter, mk.y - 120);
  await page.waitForTimeout(300);
  await page.mouse.click(quarter, mk.y);
  const wantQuarter = mk.duration * 0.25;
  check('YouTube scrubbing still works', await nearTime(page, wantQuarter, 30), `currentTime=${(await currentTime(page)).toFixed(1)}, want ~${wantQuarter.toFixed(0)}`);

  // Keyboard: Tab-focus and Enter jump there, and do not also play or pause the video.
  await page
    .waitForFunction(() => {
      const el = document.querySelector('.podsync-marker');
      return el && !el.hidden && !document.querySelector('#movie_player.ad-showing');
    }, null, { timeout: 30000 })
    .catch(() => {});
  const before = await page.evaluate(() => {
    document.querySelector('.podsync-marker-hit').focus();
    return {
      paused: document.querySelector('#movie_player video').paused,
      focused: document.activeElement?.className,
      t: document.querySelector('#movie_player video').currentTime,
    };
  });
  await page.keyboard.press('Enter');
  const keyJump = await nearTime(page, expected, 2);
  const pausedAfter = await page.evaluate(() => document.querySelector('#movie_player video').paused);
  check(
    'Enter on the focused marker jumps there',
    keyJump && before.paused === pausedAfter,
    `focused=${before.focused} t=${before.t.toFixed(1)} -> ${(await currentTime(page)).toFixed(1)}, paused ${before.paused} -> ${pausedAfter}`
  );

  // 3. Reload: YouTube is now newer than Apple Podcasts, so nothing should jump
  const seeksBefore = (readLog().match(/ resume \S+ at /g) || []).length; // the helper logs each jump it orders
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(15000);
  const after = await page.evaluate(() => document.querySelector('#movie_player video')?.currentTime ?? -1);
  const seeksAfter = (readLog().match(/ resume \S+ at /g) || []).length;
  check('newer YouTube position is not overwritten', seeksAfter === seeksBefore && Math.abs(after - expected) > 30, `currentTime=${after.toFixed(1)}, jumps ordered: ${seeksAfter - seeksBefore}`);
  mk = await markerInfo(page);
  check('the marker still shows when YouTube is newer', mk.exists && /40:30/.test(mk.tip), JSON.stringify(mk));

  // 3b. Theater mode resizes the bar; the marker follows it.
  await page.keyboard.press('t');
  await page.waitForTimeout(1500);
  mk = await markerInfo(page);
  const theaterX = await barXOf(page, expected);
  check('marker follows theater mode', mk.exists && Math.abs(mk.x - theaterX) < 2, `x=${mk.x?.toFixed(1)} want ${theaterX.toFixed(1)}, bar ${mk.barWidth?.toFixed(0)}px`);
  await page.keyboard.press('t');
  await page.waitForTimeout(1000);

  // Fullscreen, too.
  await page.keyboard.press('f');
  await page.waitForTimeout(2000);
  mk = await markerInfo(page);
  const fsX = await barXOf(page, expected);
  const fullscreen = await page.evaluate(() => !!document.fullscreenElement);
  check('marker follows fullscreen', fullscreen && mk.exists && Math.abs(mk.x - fsX) < 2, `fullscreen=${fullscreen} x=${mk.x?.toFixed(1)} want ${fsX.toFixed(1)}`);
  await page.keyboard.press('f');
  await page.waitForTimeout(1500);

  // 3c. YouTube's in-page navigation: another video drops the marker, Back brings it back.
  const other = page
    .locator(`#secondary a[href^="/watch?v="]:not([href*="${videoId}"])`)
    .filter({ visible: true })
    .first();
  await other.click({ timeout: 10000 }).catch(() => {});
  await page.waitForFunction((id) => !location.search.includes(id), videoId, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(3000);
  const gone = await markerInfo(page);
  await page.goBack();
  await page.waitForTimeout(6000);
  mk = await markerInfo(page);
  check('marker survives in-page navigation', !gone.exists && mk.exists && /40:30/.test(mk.tip), `other video: ${gone.exists}, back: ${mk.exists}`);

  // 3d. The mini player ("i") keeps playing this episode on the home page: the marker stays.
  const isMini = () => page.evaluate(() => location.pathname !== '/watch' && !!document.querySelector('ytd-miniplayer[active], ytd-app[miniplayer-is-active]'));
  for (let i = 0; i < 3 && !(await isMini()); i++) {
    await page.evaluate(() => document.activeElement?.blur()); // YouTube ignores shortcut keys while a link has focus
    await page.keyboard.press('i');
    await page.waitForTimeout(3000);
  }
  mk = await markerInfo(page);
  const mini = await isMini();
  const miniX = mk.exists ? await barXOf(page, expected) : NaN;
  check('marker stays in the mini player', mini && mk.exists && !mk.hidden && Math.abs(mk.x - miniX) < 2, `mini=${mini} ${JSON.stringify(mk)}`);
  await page.screenshot({ path: path.join(shots, 'e2e-3d-mini-player.png') });

  // 4. "Only show the marker": no jump; the toast offers it instead.
  // Choose it in the popup, as a person would.
  const popup = await ctx.newPage();
  await popup.setViewportSize({ width: 312, height: 260 });
  await popup.goto(new URL('popup.html', sw.url()).href);
  await popup.getByLabel('Only show the marker').check();
  await popup.waitForTimeout(300);
  const stored = await sw.evaluate(() => chrome.storage.sync.get('onOpen'));
  await popup.screenshot({ path: path.join(shots, 'e2e-4-popup-setting.png') });
  await popup.emulateMedia({ colorScheme: 'dark' });
  await popup.screenshot({ path: path.join(shots, 'e2e-4-popup-setting-dark.png') });
  await popup.close();
  check('the popup saves "Only show the marker"', stored.onOpen === 'marker', JSON.stringify(stored));
  playOnIPhone(3000);
  toasts.length = 0;
  await page.goto(`https://www.youtube.com/watch?v=${videoId}`, { waitUntil: 'domcontentloaded' });
  const offered = await page
    .waitForFunction(() => [...document.querySelectorAll('.podsync-toast.podsync-show button')].find((b) => b.textContent === 'Jump to 50:00'), null, {
      timeout: 60000,
      polling: 250,
    })
    .then(() => true)
    .catch(() => false);
  const noJump = Math.abs((await currentTime(page)) - 3000) > 30;
  mk = await markerInfo(page);
  await page.screenshot({ path: path.join(shots, 'e2e-4-marker-only.png') });
  check('"Only show the marker" does not jump, and offers it', offered && noJump && /50:00/.test(mk.tip || ''), JSON.stringify(toasts));
  await page.locator('.podsync-toast button', { hasText: 'Jump to 50:00' }).click().catch(() => {});
  check('"Jump to 50:00" jumps', await nearTime(page, 3000, 2), `currentTime=${(await currentTime(page)).toFixed(1)}`);
  await sw.evaluate(() => chrome.storage.sync.set({ onOpen: 'jump' }));

  // 5. Teach the offset: the "Resumed" toast has -15 s / +15 s. Each press seeks and is saved for the show.
  const toastText = () => page.evaluate(() => document.querySelector('.podsync-toast .podsync-text')?.textContent || '');
  const toastButton = (label) => page.locator('.podsync-toast.podsync-show button', { hasText: label });
  const buttons = await page.evaluate(() => [...document.querySelectorAll('.podsync-toast.podsync-show button')].map((b) => b.textContent));
  check('the resume toast has -15 s, +15 s, and Undo', JSON.stringify(buttons) === JSON.stringify(['−15 s', '+15 s', 'Undo']), JSON.stringify(buttons));
  await toastButton('+15 s').click();
  const moved = await nearTime(page, 3015, 2);
  await page.waitForFunction(() => /^Saved\./.test(document.querySelector('.podsync-toast .podsync-text')?.textContent || ''), null, { timeout: 5000 }).catch(() => {});
  check('+15 s seeks and says what it learned', moved && (await toastText()) === "Saved. This show's video runs 15 s ahead of the audio.", await toastText());
  // Keyboard: Enter on +15 s, and focus stays on the button for the next press.
  await toastButton('+15 s').focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => /30 s ahead/.test(document.querySelector('.podsync-toast .podsync-text')?.textContent || ''), null, { timeout: 5000 }).catch(() => {});
  const focusKept = await page.evaluate(() => document.activeElement?.textContent);
  check('a second press adds up, and keeps keyboard focus', (await nearTime(page, 3030, 2)) && /30 s ahead/.test(await toastText()) && focusKept === '+15 s', `${await toastText()} focus=${focusKept}`);
  await toastButton('−15 s').click();
  await page.waitForFunction(() => /15 s ahead/.test(document.querySelector('.podsync-toast .podsync-text')?.textContent || ''), null, { timeout: 5000 }).catch(() => {});
  const box5 = await page.locator('#movie_player').boundingBox();
  await page.screenshot({ path: path.join(shots, 'e2e-5-nudge-toast.png'), clip: { x: box5.x, y: box5.y + box5.height - 140, width: box5.width, height: 140 } });
  mk = await markerInfo(page);
  check('-15 s takes one press back; the marker moves with the offset', (await nearTime(page, 3015, 2)) && /^iPhone · 50:15/.test(mk.tip || ''), `${await toastText()} marker="${mk.tip}"`);

  // The learned +15 s applies to the iPhone link ...
  const pausedNudged = await page.evaluate(() => {
    const v = document.querySelector('#movie_player video');
    if (v.paused) v.dispatchEvent(new Event('pause'));
    else v.pause();
    return v.currentTime;
  });
  await page.waitForTimeout(2500);
  let nudged = { seconds: NaN, url: '' };
  try {
    nudged = JSON.parse(readFileSync(handoff, 'utf8'));
  } catch {}
  check('the iPhone link uses the learned offset', Math.abs(nudged.seconds - (pausedNudged - 15)) <= 2, `paused at ${pausedNudged.toFixed(1)}, link t=${nudged.seconds}`);

  // ... and to the next jump from the iPhone: Podcasts 55:00 is video 55:15.
  playOnIPhone(3300);
  toasts.length = 0;
  await page.goto(`https://www.youtube.com/watch?v=${videoId}`, { waitUntil: 'domcontentloaded' });
  const jumpedNudged = await page
    .waitForFunction(() => /Resumed at 55:15/.test(document.querySelector('.podsync-toast.podsync-show')?.textContent || ''), null, { timeout: 60000 })
    .then(() => true)
    .catch(() => false);
  check('the next jump uses the learned offset', jumpedNudged && (await nearTime(page, 3315, 2)), `currentTime=${(await currentTime(page)).toFixed(1)}`);

  // 6. Thumbnails: a purple bar on episodes you started in Apple Podcasts (55:00 of 1:02:09 now).
  const thumbOf = (id) =>
    page.evaluate((id) => {
      const bars = [...document.querySelectorAll('.podsync-thumb')];
      const b = bars.find((x) => x.dataset.id === id);
      if (!b) return { count: bars.length };
      // Fake YouTube's red "watched" bar next to ours: ours must move up and stay clear of it.
      const red = document.createElement(b.parentElement.localName === 'yt-thumbnail-view-model' ? 'yt-thumbnail-overlay-progress-bar-view-model' : 'ytd-thumbnail-overlay-resume-playback-renderer');
      red.style.cssText = 'position:absolute;left:0;bottom:0;height:4px;width:30%;background:#f00;display:block';
      const bottomAlone = getComputedStyle(b).bottom;
      b.parentElement.append(red);
      const bottomWithRed = getComputedStyle(b).bottom;
      red.remove();
      const fill = b.firstElementChild.getBoundingClientRect().width / b.getBoundingClientRect().width;
      return {
        count: bars.length,
        others: bars.filter((x) => x.dataset.id !== id).length,
        host: b.parentElement.localName,
        label: b.getAttribute('aria-label'),
        title: b.title,
        fill: Math.round(fill * 1000) / 1000,
        color: getComputedStyle(b.firstElementChild).backgroundColor,
        bottomAlone,
        bottomWithRed,
      };
    }, id);
  const waitThumb = (id) =>
    page.waitForFunction((id) => [...document.querySelectorAll('.podsync-thumb')].some((x) => x.dataset.id === id), id, { timeout: 20000 }).catch(() => {});
  const wantFill = 3300 / 3729;
  for (const [name, url] of [
    ['search', `https://www.youtube.com/results?search_query=${encodeURIComponent('David Senra Alexander Taubman Long Lake')}`],
    ['channel', 'https://www.youtube.com/@DavidSenra/videos'],
  ]) {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await waitThumb(videoId);
    const th = await thumbOf(videoId);
    await page.screenshot({ path: path.join(shots, `e2e-6-thumbnails-${name}.png`) });
    check(
      `${name} page: purple bar on the started episode only`,
      th.label === 'Apple Podcasts: 55:00 of 1:02:09' && th.title === th.label && Math.abs(th.fill - wantFill) < 0.02 && th.others === 0 && th.color === 'rgb(177, 80, 226)',
      JSON.stringify(th)
    );
    check(`${name} page: it stacks above YouTube's red bar`, th.bottomAlone === '0px' && th.bottomWithRed === '4px', `${th.host}: ${th.bottomAlone} -> ${th.bottomWithRed}`);
  }
  const knownCalls = await sw.evaluate(() => self.__podsyncTimes.filter((t) => t.path === '/known'));
  check(
    'thumbnails are asked in batches of at most 60, and fast',
    knownCalls.length > 0 && knownCalls.every((c) => c.ids > 0 && c.ids <= 60 && c.ms < 500),
    `${knownCalls.length} calls: ${knownCalls.map((c) => `${c.ids} ids/${c.ms} ms`).join(', ')}`
  );

  clearInterval(pollToasts);
  clearInterval(skipAds);
} finally {
  await ctx.close();
}

const failed = results.filter((r) => !r.ok).length;
console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
process.exit(failed ? 1 : 0);
