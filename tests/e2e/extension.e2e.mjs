// End-to-end: real Chromium + the extension + real YouTube + the running helper.
//
//   node tests/e2e/extension.e2e.mjs [videoId] [expectedSeconds]
//
// Run it through scripts/e2e.sh, which starts a throwaway helper with a fake library.
// Needs `npm install` and `npx playwright-core install chromium` once.
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync } from 'node:fs';
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
        self.__podsyncTimes.push({ path: new URL(String(url)).pathname, ms: Math.round(performance.now() - t) });
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
  const skipAds = setInterval(() => {
    page
      .locator('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button')
      .first()
      .click({ timeout: 300 })
      .catch(() => {});
  }, 1000);

  // 0. You open YouTube. The helper starts Podcasts hidden, so the iCloud sync runs while you browse.
  await page.goto('https://www.youtube.com/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(5000);
  check('opening YouTube warmed up Apple Podcasts', /warming: launching Podcasts hidden/.test(readLog()), helperLog ? '' : 'set HELPER_LOG');

  // 1. Apple Podcasts -> YouTube
  const t0 = Date.now();
  await page.goto(`https://www.youtube.com/watch?v=${videoId}`, { waitUntil: 'domcontentloaded' });
  const jumped = await page
    .waitForFunction(
      (want) => {
        const v = document.querySelector('#movie_player video');
        const ad = document.querySelector('#movie_player.ad-showing');
        return v && !ad && Math.abs(v.currentTime - want) < 30 ? v.currentTime : false;
      },
      expected,
      { timeout: 120000, polling: 250 }
    )
    .then((h) => h.jsonValue())
    .catch(() => null);
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

  // 3. Reload: YouTube is now newer than Apple Podcasts, so nothing should jump
  toasts.length = 0;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(15000);
  const after = await page.evaluate(() => document.querySelector('#movie_player video')?.currentTime ?? -1);
  check('newer YouTube position is not overwritten', !toasts.some((t) => /Resumed/.test(t)), `currentTime=${after.toFixed(1)}, toasts=${JSON.stringify(toasts)}`);

  clearInterval(pollToasts);
  clearInterval(skipAds);
} finally {
  await ctx.close();
}

const failed = results.filter((r) => !r.ok).length;
console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
process.exit(failed ? 1 : 0);
