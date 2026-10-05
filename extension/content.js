// Runs on youtube.com. Three jobs:
//  1. When a matched episode opens, jump to where Apple Podcasts left off (if that is newer).
//  2. Mark the iPhone position on the progress bar ("iPhone was here").
//  3. While you watch, report the position so the helper can hand it to your iPhone.
(() => {
  const HEARTBEAT_MS = 15000;
  const MIN_JUMP = 15;
  const WARM_EVERY_MS = 5 * 60 * 1000; // across all tabs
  const BUSY_TOAST_MS = 600; // show "Checking…" only if the resume is slower than this
  const state = { videoId: null, token: 0, resolving: false, lastBeat: 0 };
  const settings = { onOpen: 'jump' }; // 'jump' | 'marker' (popup: "When you open an episode")
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

  // "2 h ago" from a unix time in seconds.
  function ago(unix) {
    if (!unix) return '';
    const s = Math.max(0, Date.now() / 1000 - unix);
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
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

  function loadSettings() {
    return new Promise((resolve) => {
      try {
        chrome.storage.sync.get({ onOpen: 'jump' }, (s) => {
          void chrome.runtime.lastError;
          if (s?.onOpen) settings.onOpen = s.onOpen;
          resolve(settings);
        });
      } catch {
        resolve(settings);
      }
    });
  }

  // ---- toast ---------------------------------------------------------------
  let toastEl = null;
  let toastTimer = null;
  let toastMs = 0;
  let toastHeld = false; // pointer or keyboard focus is on the toast

  // actions: [{ label, ariaLabel?, onClick, keepOpen? }]
  function toast(text, { actions = [], actionLabel, onAction, ms = 3000, busy = false } = {}) {
    if (actionLabel) actions = [{ label: actionLabel, onClick: onAction }];
    const host = player() || document.body;
    if (!toastEl || !host.contains(toastEl)) {
      toastEl?.remove();
      toastEl = document.createElement('div');
      toastEl.className = 'podsync-toast';
      toastEl.setAttribute('role', 'status');
      // Keep it open while you point at it or tab through its buttons.
      const hold = (on) => {
        toastHeld = on;
        on ? clearTimeout(toastTimer) : restartToastTimer();
      };
      toastEl.addEventListener('pointerenter', () => hold(true));
      toastEl.addEventListener('pointerleave', () => hold(toastEl.contains(document.activeElement)));
      toastEl.addEventListener('focusin', () => hold(true));
      toastEl.addEventListener('focusout', (e) => !toastEl.contains(e.relatedTarget) && hold(toastEl.matches(':hover')));
      host.appendChild(toastEl);
    }
    toastEl.inert = false;
    requestAnimationFrame(() => toastEl?.classList.add('podsync-show'));
    toastMs = ms;
    restartToastTimer();
    // Same buttons as now (e.g. after a -15 s press): change only the text, so keyboard focus stays.
    if (actions.length && toastEl.podsyncActions === actions) {
      toastEl.querySelector('.podsync-text').textContent = text;
      return;
    }
    toastEl.podsyncActions = actions;
    toastEl.replaceChildren();
    const dot = document.createElement('span');
    dot.className = busy ? 'podsync-dot podsync-busy' : 'podsync-dot';
    const label = document.createElement('span');
    label.className = 'podsync-text';
    label.textContent = text;
    toastEl.append(dot, label);
    for (const a of actions) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = a.label;
      if (a.ariaLabel) btn.setAttribute('aria-label', a.ariaLabel);
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!a.keepOpen) hideToast();
        a.onClick?.();
      });
      toastEl.append(btn);
    }
  }

  function restartToastTimer() {
    clearTimeout(toastTimer);
    if (toastMs && !toastHeld) toastTimer = setTimeout(hideToast, toastMs);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    toastHeld = false;
    if (!toastEl) return;
    toastEl.classList.remove('podsync-show');
    if (toastEl.contains(document.activeElement)) document.activeElement.blur();
    toastEl.inert = true; // hidden buttons must not take keyboard focus
  }

  // "40:30" / "1:02:09"
  function fmtTime(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
  }

  // "15 s", "1 min 30 s"
  function fmtSpan(seconds) {
    const s = Math.round(Math.abs(seconds));
    if (s < 60) return `${s} s`;
    return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`;
  }

  function offsetSentence(offset) {
    if (Math.abs(offset) < 0.5) return "Saved. This show's video and audio are in sync.";
    return `Saved. This show's video runs ${fmtSpan(offset)} ${offset > 0 ? 'ahead of' : 'behind'} the audio.`;
  }

  // The jump landed early or late: move the video, and teach the helper this show's offset.
  async function nudge(v, delta, actions) {
    const id = state.videoId || marker.videoId;
    v.currentTime = Math.max(0, v.currentTime + delta);
    const r = await ask('nudge', { videoId: id, delta });
    if (!r?.saved) return toast('Could not save that for this show.', { actions, ms: 7000 });
    if (marker.videoId === id) {
      marker.time = Math.max(0, marker.time + delta); // the iPhone position moved with the offset
      marker.label = fmtTime(marker.time);
      marker.key = '';
      updateMarkerText();
      placeMarker();
    }
    toast(offsetSentence(r.offset), { actions, ms: 7000 });
  }

  function jumpTo(v, time, label) {
    const before = v.currentTime;
    v.currentTime = time;
    const actions = [
      { label: '−15 s', ariaLabel: 'Back 15 seconds, and remember it for this show', keepOpen: true, onClick: () => nudge(v, -15, actions) },
      { label: '+15 s', ariaLabel: 'Forward 15 seconds, and remember it for this show', keepOpen: true, onClick: () => nudge(v, 15, actions) },
      { label: 'Undo', ariaLabel: 'Undo the jump', onClick: () => (v.currentTime = before) },
    ];
    toast(`Resumed at ${label} from Apple Podcasts`, { actions, ms: 7000 });
  }

  // ---- "iPhone was here" marker on the progress bar -------------------------
  const marker = { el: null, hit: null, tip: null, videoId: null, time: 0, label: '', lastPlayed: null, key: '' };

  function buildMarker() {
    const el = document.createElement('div');
    el.className = 'podsync-marker';
    const hit = document.createElement('button');
    hit.type = 'button';
    hit.className = 'podsync-marker-hit';
    hit.draggable = false;
    const tip = document.createElement('span');
    tip.className = 'podsync-marker-tip';
    tip.id = 'podsync-marker-tip';
    tip.setAttribute('role', 'tooltip');
    hit.setAttribute('aria-describedby', tip.id);
    el.append(hit, tip);

    // The marker sits inside YouTube's progress bar: keep YouTube's own seek and drag
    // from starting under it, and seek to the exact iPhone position instead.
    for (const type of ['pointerdown', 'mousedown', 'touchstart', 'dblclick']) hit.addEventListener(type, (e) => e.stopPropagation());
    hit.addEventListener('dragstart', (e) => e.preventDefault());
    hit.addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      const v = video();
      if (v) jumpTo(v, marker.time, marker.label);
    });
    // Enter and Space press the button; do not let YouTube also play/pause.
    const keys = (e) => (e.key === 'Enter' || e.key === ' ') && e.stopPropagation();
    hit.addEventListener('keydown', keys);
    hit.addEventListener('keyup', keys);
    // While you point at the marker, show its tooltip instead of YouTube's preview.
    hit.addEventListener('pointerenter', () => {
      updateMarkerText();
      player()?.classList.add('podsync-marker-hover');
    });
    hit.addEventListener('pointerleave', () => player()?.classList.remove('podsync-marker-hover'));
    hit.addEventListener('focus', updateMarkerText);
    Object.assign(marker, { el, hit, tip });
  }

  function updateMarkerText() {
    if (!marker.el) return;
    const when = ago(marker.lastPlayed);
    marker.tip.textContent = ['iPhone', marker.label, when].filter(Boolean).join(' · ');
    marker.hit.setAttribute('aria-label', `Jump to the iPhone position, ${marker.label}${when ? `, played ${when}` : ''}`);
  }

  function setMarker(videoId, r) {
    Object.assign(marker, { videoId, time: r.markerTime, label: r.markerLabel, lastPlayed: r.lastPlayed, key: '' });
    if (!marker.el) buildMarker();
    chapterCache.key = ''; // another video may have other chapters
    updateMarkerText();
    placeMarker();
  }

  function clearMarker() {
    marker.videoId = null;
    marker.el?.remove();
    player()?.classList.remove('podsync-marker-hover');
  }

  // Where a fraction of the video sits on the bar, in % of its width. With chapters, YouTube
  // draws one segment per chapter with small gaps between them, so x is not linear in time.
  // Segment widths are proportional to chapter lengths: walk them. The layout is cached by bar width.
  let chapterCache = { key: '', segs: [] };
  function barPercent(bar, fraction) {
    const width = bar.clientWidth;
    const hovers = bar.querySelectorAll('.ytp-chapter-hover-container');
    const key = `${width}|${hovers.length}`;
    if (key !== chapterCache.key) {
      const left = bar.getBoundingClientRect().left;
      const segs = [...hovers].map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.left - left, w: r.width }));
      chapterCache = { key, segs: segs.filter((r) => r.w > 0) };
    }
    const segs = chapterCache.segs;
    if (!width || segs.length < 2) return fraction * 100;
    let rest = fraction * segs.reduce((sum, r) => sum + r.w, 0);
    for (const r of segs) {
      if (rest <= r.w) return ((r.x + rest) / width) * 100;
      rest -= r.w;
    }
    return 100;
  }

  // Cheap: called on timeupdate. Touches the DOM only when something changed.
  function placeMarker() {
    if (!marker.videoId || !marker.el) return;
    const bar = player()?.querySelector('.ytp-progress-bar');
    const v = video();
    if (!bar || !v) return;
    if (marker.el.parentElement !== bar) bar.appendChild(marker.el); // YouTube re-rendered the bar
    const d = v.duration;
    const ok = Number.isFinite(d) && d > 0 && !adShowing();
    const pct = ok ? Math.min(100, Math.max(0, barPercent(bar, marker.time / d))) : 0;
    const near = Math.abs(v.currentTime - marker.time) < MIN_JUMP;
    const key = `${ok}|${pct.toFixed(3)}|${near}`;
    if (key === marker.key) return;
    marker.key = key;
    marker.el.hidden = !ok || near;
    marker.el.style.left = `${pct}%`;
    marker.el.dataset.edge = pct < 8 ? 'start' : pct > 92 ? 'end' : '';
    if (marker.el.hidden) player()?.classList.remove('podsync-marker-hover');
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
      placeMarker();
      if (!v.paused && Date.now() - state.lastBeat > HEARTBEAT_MS) {
        state.lastBeat = Date.now();
        report('heartbeat');
      }
    });
    v.addEventListener('durationchange', placeMarker);
    v.addEventListener('seeking', placeMarker); // at once, not when the seek has buffered
    // A new video in the same player (autoplay, mini player queue): drop the old marker.
    v.addEventListener('loadstart', () => marker.videoId && watchId() !== marker.videoId && clearMarker());
  }

  // ---- resuming (Apple Podcasts -> YouTube) ---------------------------------
  async function onPage() {
    const id = watchId();
    if (id === state.videoId) return;
    state.videoId = id;
    const token = ++state.token;
    hideToast();
    if (!id) return; // left the watch page: the mini player may still show this video, so keep its marker
    if (id !== marker.videoId) clearMarker();

    state.resolving = true; // hold reports so YouTube's own resume can't count as "newer"
    try {
      const [m] = await Promise.all([ask('match', { videoId: id, currentTime: 0, title: title() }), loadSettings()]);
      if (token !== state.token || !m?.matched) return;

      const busy = setTimeout(() => token === state.token && toast('Checking Apple Podcasts…', { ms: 0, busy: true }), BUSY_TOAST_MS);
      const r = await ask('resume', { videoId: id, currentTime: video()?.currentTime || 0, title: title() });
      clearTimeout(busy);
      if (token !== state.token) return;
      if (r?.markerTime != null) setMarker(id, r);
      if (r?.action !== 'seek') return hideToast();

      const v = await waitFor(() => {
        const el = video();
        return el && el.readyState >= 1 && !adShowing() && watchId() === id ? el : null;
      }, 120000);
      if (!v || token !== state.token) return hideToast();
      placeMarker();
      if (Math.abs(v.currentTime - r.time) < MIN_JUMP) return hideToast();

      if (settings.onOpen === 'marker') {
        toast(`Played on iPhone ${ago(r.lastPlayed)}`, {
          actions: [{ label: `Jump to ${r.label}`, onClick: () => jumpTo(v, r.time, r.label) }],
          ms: 10000,
        });
        return;
      }
      jumpTo(v, r.time, r.label);
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
    placeMarker();
  }, 1000);
  bind(video());
  warm();
  onPage();
})();
