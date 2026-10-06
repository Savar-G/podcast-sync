const helper = document.getElementById('helper');
const last = document.getElementById('last');

chrome.runtime.sendMessage({ type: 'status' }, (s) => {
  void chrome.runtime.lastError;
  if (!s?.ok) {
    helper.className = 'row bad';
    helper.lastElementChild.textContent = 'Helper not running. Run scripts/install.sh.';
    return;
  }
  helper.className = 'row ok';
  helper.lastElementChild.textContent = s.libraryReadable
    ? 'Connected to Apple Podcasts'
    : 'Helper is running, but macOS has not allowed it to read Podcasts yet. See the README.';
  const h = s.lastHandoff;
  if (!h) {
    last.textContent = 'Nothing sent to your iPhone yet.';
    return;
  }
  const ago = Math.max(0, Math.round((Date.now() / 1000 - h.saved_at) / 60));
  last.replaceChildren(
    'Last sent to iPhone: ',
    Object.assign(document.createElement('span'), { className: 'time', textContent: h.time }),
    ` in ${h.episode} (${h.show}), ${ago ? `${ago} min ago` : 'just now'}.`
  );
});

// ---- Continue on YouTube ------------------------------------------------
// GET the fast list first (no Podcasts app refresh), then ask once more with
// {refresh: true} so positions from the iPhone arrive a few seconds later.

const list = document.getElementById('recent');
const note = document.getElementById('recent-note');
const liveStatus = document.getElementById('recent-status');
const updating = document.getElementById('updating');
const undoBar = document.getElementById('undo');
const undoText = document.getElementById('undo-text');
const undoBtn = document.getElementById('undo-btn');

// ---- Hidden episodes ------------------------------------------------------
// The × on a row hides that episode until you play it again (a newer lastPlayed).
// The list lives in chrome.storage.local; the helper gets it with each request, so it
// can fill the freed places. Old entries drop off: the list only covers 14 days anyway.
const HIDDEN_KEY = 'hiddenRecent';
const HIDDEN_TTL_MS = 30 * 86400 * 1000;
const hidden = {}; // trackId -> {lastPlayed, at}
const hiddenReady = new Promise((resolve) => {
  try {
    chrome.storage.local.get(HIDDEN_KEY, (r) => {
      void chrome.runtime.lastError;
      const now = Date.now();
      for (const [id, v] of Object.entries(r?.[HIDDEN_KEY] || {})) {
        if (/^\d{1,20}$/.test(id) && Number.isFinite(v?.lastPlayed) && now - v.at < HIDDEN_TTL_MS) hidden[id] = v;
      }
      resolve();
    });
  } catch {
    resolve();
  }
});
const saveHidden = () => chrome.storage.local.set({ [HIDDEN_KEY]: hidden }, () => void chrome.runtime.lastError);
const hiddenPayload = () => Object.fromEntries(Object.entries(hidden).map(([id, v]) => [id, v.lastPlayed]));
const isHidden = (row) => {
  const h = hidden[String(row.trackId)];
  return !!h && (row.lastPlayed || 0) <= h.lastPlayed + 1;
};
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};

function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

function fmtAgo(unix) {
  if (!unix) return '';
  const min = Math.max(0, Math.round((Date.now() / 1000 - unix) / 60));
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

function ask(payload, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), timeoutMs);
    chrome.runtime.sendMessage({ type: 'recent', payload }, (r) => {
      void chrome.runtime.lastError;
      clearTimeout(timer);
      resolve(r ?? null);
    });
  });
}

function safeUrl(url, hostSuffix) {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' && (u.hostname === hostSuffix || u.hostname.endsWith(`.${hostSuffix}`)) ? u.href : null;
  } catch {
    return null;
  }
}

function open(url) {
  const safe = safeUrl(url, 'youtube.com');
  if (!safe) return;
  chrome.tabs.create({ url: safe });
  window.close();
}

function artwork(row) {
  const box = el('div', { className: 'art' });
  const initial = (row.show || '?').trim().charAt(0).toUpperCase();
  const src = safeUrl(row.artwork, 'mzstatic.com');
  if (!src) {
    box.textContent = initial;
    return box;
  }
  const img = el('img', { src, alt: '', width: 48, height: 48, loading: 'lazy', decoding: 'async', referrerPolicy: 'no-referrer' });
  img.addEventListener('error', () => box.replaceChildren(initial));
  box.append(img);
  return box;
}

function closeIcon() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 10 10');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', 'M1 1l8 8M9 1L1 9');
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '1.6');
  path.setAttribute('stroke-linecap', 'round');
  svg.append(path);
  return svg;
}

function hideRow(row, li) {
  const id = String(row.trackId);
  hidden[id] = { lastPlayed: row.lastPlayed || 0, at: Date.now() };
  saveHidden();
  const done = () => {
    shown = '';
    render(lastResp);
    // A newer helper fills the freed place with the next recent episode.
    ask({ hidden: hiddenPayload() }, 15000).then((r) => r && Array.isArray(r.episodes) && !r.error && render(r));
  };
  if (reduceMotion) done();
  else {
    li.classList.add('leaving');
    setTimeout(done, 170);
  }
  undoText.textContent = 'Hidden until you play it again.';
  undoBtn.onclick = () => {
    delete hidden[id];
    saveHidden();
    undoBar.hidden = true;
    shown = '';
    render(lastResp);
    list.querySelector(`button.watch[data-track="${CSS.escape(id)}"]`)?.focus();
  };
  undoBar.hidden = false;
  undoBtn.focus();
  liveStatus.textContent = `Hidden: ${row.episode}.`;
}

function item(row, i) {
  const titleId = `ep-${i}`;
  const pct = row.duration ? Math.min(100, Math.max(0, (row.playhead / row.duration) * 100)) : 0;
  const position = row.duration ? `${fmtTime(row.playhead)} of ${fmtTime(row.duration)}` : fmtTime(row.playhead);
  const ago = fmtAgo(row.lastPlayed);
  const button = el('button', {
    type: 'button',
    className: row.videoId ? 'watch' : 'watch search',
    textContent: row.videoId ? `Watch from ${row.label}` : 'Search on YouTube',
  });
  button.setAttribute('aria-describedby', titleId);
  button.dataset.track = String(row.trackId);
  button.addEventListener('click', () => open(row.url));

  const bar = el('div', { className: 'bar' }, el('span'));
  bar.firstChild.style.width = `${pct.toFixed(1)}%`;
  bar.setAttribute('aria-hidden', 'true');

  // "Show · 2 h ago" on top keeps "40:30 of 1:02:09" and the button on one line.
  const kicker = el('p', { className: 'kicker' }, el('span', { className: 'show', textContent: row.show, title: row.show }));
  if (ago) kicker.append(el('span', { className: 'ago', textContent: `· ${ago}` }));
  const hide = el('button', { type: 'button', className: 'hide', title: 'Hide until you play it again' }, closeIcon());
  hide.setAttribute('aria-label', `Hide ${row.episode}`);
  kicker.append(hide);

  const li = el(
    'li',
    { className: 'item' },
    artwork(row),
    el(
      'div',
      { className: 'meta' },
      kicker,
      el('p', { className: 'title', id: titleId, textContent: row.episode, title: row.episode }),
      el('div', { className: 'foot' }, el('div', { className: 'progress' }, bar, el('p', { className: 'sub', textContent: position })), button)
    )
  );
  hide.addEventListener('click', () => hideRow(row, li));
  return li;
}

function skeleton() {
  note.replaceChildren();
  list.setAttribute('aria-busy', 'true');
  const line = (cls) => el('div', { className: `line ${cls}` });
  list.replaceChildren(
    ...[0, 1, 2].map(() =>
      el('li', { className: 'item skeleton' }, el('div', { className: 'art' }), el('div', { className: 'meta' }, line('short'), line('long'), line('mid'), line('pill')))
    )
  );
  list.querySelectorAll('li').forEach((li) => li.setAttribute('aria-hidden', 'true'));
  liveStatus.textContent = 'Loading recent episodes…';
}

function showNote(text, retry) {
  list.removeAttribute('aria-busy');
  list.replaceChildren();
  const children = [el('p', { textContent: text })];
  if (retry) {
    const b = el('button', { type: 'button', className: 'watch search', textContent: 'Try Again' });
    b.addEventListener('click', load);
    children.push(b);
  }
  note.replaceChildren(el('div', { className: 'note' }, ...children));
  liveStatus.textContent = text;
}

const signature = (rows) => JSON.stringify(rows.map((r) => [r.trackId, Math.round(r.playhead), r.url, r.artwork]));
let shown = '';
let lastResp = null;

function render(resp) {
  if (resp?.error === 'not found') {
    showNote('Your helper is older than this extension. Run scripts/install.sh again.', true);
    return false;
  }
  if (!resp || !Array.isArray(resp.episodes)) {
    showNote('Can’t reach the Podcast Sync helper. Make sure it is running, then try again.', true);
    return false;
  }
  if (resp.error === 'library_unreadable') {
    showNote('macOS has not allowed the helper to read Apple Podcasts yet. See the README.', true);
    return false;
  }
  lastResp = resp;
  const rows = resp.episodes.filter((r) => !isHidden(r)); // older helpers do not filter
  if (!rows.length) {
    showNote(resp.episodes.length ? 'Nothing else to continue. Hidden episodes come back when you play them again.' : 'Nothing played lately in Apple Podcasts.');
    shown = '';
    return true;
  }
  const sig = signature(rows);
  if (sig === shown) return true;
  const focused = document.activeElement?.dataset?.track;
  note.replaceChildren();
  list.removeAttribute('aria-busy');
  list.replaceChildren(...rows.map(item));
  shown = sig;
  if (focused) list.querySelector(`button[data-track="${CSS.escape(focused)}"]`)?.focus();
  const n = rows.length;
  liveStatus.textContent = `${n} recent ${n === 1 ? 'episode' : 'episodes'}.`;
  return true;
}

async function load() {
  shown = '';
  skeleton();
  await hiddenReady;
  const ok = render(await ask({ hidden: hiddenPayload() }, 15000));
  if (!ok || !lastResp?.episodes?.length) return; // nothing to refresh: the helper is down, or nothing was played
  updating.classList.add('on');
  const fresh = await ask({ refresh: true, hidden: hiddenPayload() }, 30000);
  updating.classList.remove('on');
  if (fresh && Array.isArray(fresh.episodes) && !fresh.error) render(fresh);
}

load();
