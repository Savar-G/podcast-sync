// Runs on youtube.com. Two jobs:
//  1. When a matched episode opens, jump to where Apple Podcasts left off (if that is newer).
//  2. While you watch, report the position so the helper can hand it to your iPhone.
(() => {
  const HEARTBEAT_MS = 15000;
  const MIN_JUMP = 15;
  const WARM_EVERY_MS = 5 * 60 * 1000; // across all tabs
  const BUSY_TOAST_MS = 600; // show "Checking…" only if the resume is slower than this
  const state = { videoId: null, token: 0, resolving: false, lastBeat: 0 };
  const bound = new WeakSet();

  const watchId = () => (location.pathname === '/watch' ? new URLSearchParams(location.search).get('v') : null);
  const player = () => document.querySelector('#movie_player');
  const video = () => document.querySelector('#movie_player video.html5-main-video') || document.querySelector('#movie_player video');
  const adShowing = () => !!player()?.classList.contains('ad-showing');
  const title = () =>
    (document.querySelector('ytd-watch-metadata h1')?.textContent || document.title.replace(/ - YouTube$/, '')).trim();

  function ask(type, payload) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type, payload }, (r) => {
          void chrome.runtime.lastError; // helper down or extension reloaded: stay quiet
          resolve(r || null);
        });
      } catch {
        resolve(null);
      }
    });
  }

  function waitFor(fn, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now();
      const tick = () => {
        const v = fn();
        if (v || Date.now() - start > timeoutMs) return resolve(v || null);
        setTimeout(tick, 250);
      };
      tick();
    });
  }

  // Start Apple Podcasts (hidden) now, so its iCloud sync is done before you pick a video.
  function warm() {
    try {
      chrome.storage.local.get('warmAt', ({ warmAt } = {}) => {
        if (chrome.runtime.lastError || Date.now() - (warmAt || 0) < WARM_EVERY_MS) return;
        chrome.storage.local.set({ warmAt: Date.now() });
        ask('warm', {});
      });
    } catch {
      // extension reloaded: this old content script can no longer talk to it
    }
  }

  // ---- toast ---------------------------------------------------------------
  let toastEl = null;
  let toastTimer = null;

  function toast(text, { actionLabel, onAction, ms = 3000, busy = false } = {}) {
    const host = player() || document.body;
    if (!toastEl || !host.contains(toastEl)) {
      toastEl?.remove();
      toastEl = document.createElement('div');
      toastEl.className = 'podsync-toast';
      toastEl.setAttribute('role', 'status');
      host.appendChild(toastEl);
    }
    toastEl.replaceChildren();
    const dot = document.createElement('span');
    dot.className = busy ? 'podsync-dot podsync-busy' : 'podsync-dot';
    const label = document.createElement('span');
    label.textContent = text;
    toastEl.append(dot, label);
    if (actionLabel) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = actionLabel;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        onAction?.();
        hideToast();
      });
      toastEl.append(btn);
    }
    requestAnimationFrame(() => toastEl?.classList.add('podsync-show'));
    clearTimeout(toastTimer);
    if (ms) toastTimer = setTimeout(hideToast, ms);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    toastEl?.classList.remove('podsync-show');
  }

  // ---- reporting (YouTube -> Apple Podcasts) --------------------------------
  function report(event) {
    const v = video();
    const id = state.videoId;
    if (!v || !id || state.resolving || adShowing()) return;
    const t = v.currentTime;
    if (!Number.isFinite(t) || t < 1) return;
    ask('progress', { videoId: id, currentTime: t, event, title: title(), duration: Number.isFinite(v.duration) ? v.duration : null }).then(
      (r) => {
        if (r?.matched && event === 'pause') toast(`${r.pushed ? 'Sent to iPhone' : 'Ready on iPhone'} at ${r.label}`, { ms: 2500 });
      }
    );
  }

  function bind(v) {
    if (!v || bound.has(v)) return;
    bound.add(v);
    v.addEventListener('pause', () => !v.ended && report('pause'));
    v.addEventListener('ended', () => report('ended'));
    let seekTimer;
    v.addEventListener('seeked', () => {
      clearTimeout(seekTimer);
      seekTimer = setTimeout(() => report('seeked'), 1500);
    });
    v.addEventListener('timeupdate', () => {
      if (!v.paused && Date.now() - state.lastBeat > HEARTBEAT_MS) {
        state.lastBeat = Date.now();
        report('heartbeat');
      }
    });
  }

  // ---- resuming (Apple Podcasts -> YouTube) ---------------------------------
  async function onPage() {
    const id = watchId();
    if (id === state.videoId) return;
    state.videoId = id;
    const token = ++state.token;
    hideToast();
    if (!id) return;

    state.resolving = true; // hold reports so YouTube's own resume can't count as "newer"
    try {
      const m = await ask('match', { videoId: id, currentTime: 0, title: title() });
      if (token !== state.token || !m?.matched) return;

      const busy = setTimeout(() => token === state.token && toast('Checking Apple Podcasts…', { ms: 0, busy: true }), BUSY_TOAST_MS);
      const r = await ask('resume', { videoId: id, currentTime: video()?.currentTime || 0, title: title() });
      clearTimeout(busy);
      if (token !== state.token) return;
      if (r?.action !== 'seek') return hideToast();

      const v = await waitFor(() => {
        const el = video();
        return el && el.readyState >= 1 && !adShowing() && watchId() === id ? el : null;
      }, 120000);
      if (!v || token !== state.token) return hideToast();
      if (Math.abs(v.currentTime - r.time) < MIN_JUMP) return hideToast();

      const before = v.currentTime;
      v.currentTime = r.time;
      toast(`Resumed at ${r.label} from Apple Podcasts`, {
        actionLabel: 'Undo',
        onAction: () => {
          v.currentTime = before;
        },
        ms: 7000,
      });
    } finally {
      if (token === state.token) state.resolving = false;
    }
  }

  document.addEventListener('yt-navigate-start', () => {
    report('navigate');
    warm();
  });
  document.addEventListener('yt-navigate-finish', onPage);
  document.addEventListener('visibilitychange', () => document.visibilityState === 'hidden' && report('hidden'));
  window.addEventListener('pagehide', () => report('unload'));
  setInterval(() => {
    bind(video());
    if (watchId() !== state.videoId) onPage();
  }, 1000);
  bind(video());
  warm();
  onPage();
})();
