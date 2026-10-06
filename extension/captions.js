// Runs in the YouTube page's own JavaScript world (manifest: "world": "MAIN"), because
// only the page can use the player API. content.js cannot: it runs in an isolated world.
//
// One job: when content.js asks, return the current video's caption words with times,
// so the helper can line the video up with Apple's transcript of the episode.
//
// A direct fetch of a caption track now returns an empty body (YouTube wants a token).
// So, if captions are off, we turn them on for a moment through the player, hidden:
// the player then requests /api/timedtext with its token. We read that URL from the
// resource timing entries, fetch it again as JSON, and turn captions off again.
// If you already have captions on, we change nothing and reuse the player's request.
//
// It loads at document_start, so it listens before content.js can ask.
//
// Messages (window.postMessage, same window and origin only):
//   content.js -> here: { source: 'podsync', type: 'captions', id, videoId }
//   here -> content.js: { source: 'podsync-main', type: 'captions', id, videoId, words?: [[s, 'w']], error? }
(() => {
  if (window.__podsyncCaptions) return;
  window.__podsyncCaptions = true;

  const MAX_WORDS = 60000;
  const MAX_WORD_CHARS = 100;
  const WAIT_MS = 6000; // for the player's caption request
  const STYLE_ID = 'podsync-hide-captions';
  let busy = false;

  const isTimedtext = (name, videoId) => {
    try {
      const u = new URL(name, location.href);
      return (
        u.hostname === 'www.youtube.com' &&
        u.pathname === '/api/timedtext' &&
        u.searchParams.get('v') === videoId &&
        !u.searchParams.has('tlang') // a translation is not what was said
      );
    } catch {
      return false;
    }
  };

  const findUrl = (videoId) => {
    const hits = performance.getEntriesByType('resource').filter((e) => isTimedtext(e.name, videoId));
    return hits.length ? hits[hits.length - 1].name : null;
  };

  // The resource timing buffer can be full on a long YouTube session; an observer still sees new entries.
  function waitForUrl(videoId, ms) {
    return new Promise((resolve) => {
      let observer = null;
      let poll = 0;
      let timer = 0;
      const finish = (url) => {
        observer?.disconnect();
        clearInterval(poll);
        clearTimeout(timer);
        resolve(url || null);
      };
      try {
        observer = new PerformanceObserver((list) => {
          const hit = list.getEntries().find((e) => isTimedtext(e.name, videoId));
          if (hit) finish(hit.name);
        });
        observer.observe({ type: 'resource' });
      } catch {
        observer = null;
      }
      poll = setInterval(() => {
        const url = findUrl(videoId);
        if (url) finish(url);
      }, 250);
      timer = setTimeout(() => finish(findUrl(videoId)), ms);
    });
  }

  const option = (p, name, extra) => {
    try {
      return extra ? p.getOption('captions', name, extra) : p.getOption('captions', name);
    } catch {
      return undefined;
    }
  };

  // The spoken language first: auto-generated (asr) tracks are in it and have word times.
  function pickTrack(list) {
    return (
      list.find((t) => t?.kind === 'asr') ||
      list.find((t) => /^en/i.test(t?.languageCode || '')) ||
      list[0] ||
      null
    );
  }

  // YouTube remembers caption choices in localStorage; keep the user's choice as it was.
  function saveCaptionPrefs() {
    const saved = {};
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && /caption|subtitle/i.test(k)) saved[k] = localStorage.getItem(k);
      }
    } catch {
      return null;
    }
    return saved;
  }

  function restoreCaptionPrefs(saved) {
    if (!saved) return;
    try {
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i);
        if (k && /caption|subtitle/i.test(k) && !(k in saved)) localStorage.removeItem(k);
      }
      for (const [k, v] of Object.entries(saved)) localStorage.setItem(k, v);
    } catch {
      // storage blocked: nothing to restore
    }
  }

  function hideCaptions() {
    if (document.getElementById(STYLE_ID)) return () => {};
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = '#movie_player .ytp-caption-window-container { display: none !important; }';
    document.documentElement.appendChild(style);
    return () => setTimeout(() => style.remove(), 500);
  }

  // Captions off: turn them on, hidden, until the player has asked for them; then off again.
  async function borrowCaptions(p, videoId) {
    const prefs = saveCaptionPrefs();
    const unhide = hideCaptions();
    try {
      const found = waitForUrl(videoId, WAIT_MS);
      p.loadModule('captions');
      let list = [];
      for (let i = 0; i < 8 && !list.length; i++) {
        const withAsr = option(p, 'tracklist', { includeAsr: true });
        list = (Array.isArray(withAsr) && withAsr.length ? withAsr : option(p, 'tracklist')) || [];
        if (!list.length) await new Promise((r) => setTimeout(r, 125));
      }
      p.setOption('captions', 'track', pickTrack(list) || { languageCode: 'en', kind: 'asr' });
      return await found;
    } finally {
      try {
        p.unloadModule('captions');
      } catch {
        // player gone (navigation): nothing to undo
      }
      restoreCaptionPrefs(prefs);
      setTimeout(() => restoreCaptionPrefs(prefs), 1000); // in case the player saves late
      unhide();
    }
  }

  // json3 events -> [[seconds, word]]. A segment with several words (manual captions)
  // spreads them over its time until the next segment.
  function toWords(json) {
    const out = [];
    for (const e of json?.events || []) {
      if (!Array.isArray(e.segs)) continue;
      const start = Number(e.tStartMs) || 0;
      const end = start + (Number(e.dDurationMs) || 0);
      const segs = e.segs.map((s) => ({ t: start + (Number(s?.tOffsetMs) || 0), text: String(s?.utf8 || '') }));
      segs.forEach((s, i) => {
        const words = s.text.split(/\s+/).filter(Boolean);
        if (!words.length) return;
        const next = i + 1 < segs.length ? segs[i + 1].t : Math.max(end, s.t);
        const step = words.length > 1 ? Math.max(0, next - s.t) / words.length : 0;
        words.forEach((w, k) => {
          const t = Math.round(s.t + k * step) / 1000;
          if (out.length < MAX_WORDS && Number.isFinite(t) && t >= 0 && t < 86400) out.push([t, w.slice(0, MAX_WORD_CHARS)]);
        });
      });
    }
    return out;
  }

  async function capture(videoId) {
    const p = document.querySelector('#movie_player');
    if (!p || typeof p.getVideoData !== 'function' || p.getVideoData()?.video_id !== videoId) return { error: 'player' };
    if (p.classList.contains('ad-showing')) return { error: 'ad' };
    let url = findUrl(videoId);
    if (!url) {
      const current = option(p, 'track');
      const captionsOn = !!(current && current.languageCode);
      // Captions on: the player fetches them by itself; do not touch anything.
      url = captionsOn ? await waitForUrl(videoId, WAIT_MS) : await borrowCaptions(p, videoId);
    }
    if (!url) return { error: 'no_captions' };
    const u = new URL(url, location.href);
    u.searchParams.set('fmt', 'json3');
    const res = await fetch(u.href, { credentials: 'include' });
    if (!res.ok) return { error: 'fetch' };
    const text = await res.text();
    if (!text) return { error: 'empty' };
    const words = toWords(JSON.parse(text));
    return words.length ? { words } : { error: 'no_captions' };
  }

  window.addEventListener('message', async (e) => {
    if (e.source !== window || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || d.source !== 'podsync' || d.type !== 'captions') return;
    if (typeof d.id !== 'string' || d.id.length > 40 || typeof d.videoId !== 'string' || !/^[\w-]{11}$/.test(d.videoId)) return;
    let out;
    if (busy) {
      out = { error: 'busy' };
    } else {
      busy = true;
      try {
        out = await capture(d.videoId);
      } catch {
        out = { error: 'failed' };
      } finally {
        busy = false;
      }
    }
    window.postMessage({ source: 'podsync-main', type: 'captions', id: d.id, videoId: d.videoId, ...out }, location.origin);
  });
})();
