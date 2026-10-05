// Setup page. While the page is visible it asks the helper for /status every 2 s
// and shows what is left to do. Each step turns done by itself.
(() => {
  const POLL_MS = 2000;
  const TIMEOUT_MS = 4000;
  const SHORTCUT_UNKNOWN_AFTER_MS = 8000; // the helper says "unknown" this long: stop waiting
  const SYNC_KEY = 'setupSyncLibraryConfirmed';
  const STEPS = [
    ['helper', 'Start the helper'],
    ['access', 'Allow access to Apple Podcasts'],
    ['sync', 'Turn on Sync Library'],
    ['shortcut', 'Set up the iPhone shortcut'],
    ['try', 'Try it'],
  ];
  const SR_STATE = { done: 'Done.', todo: 'To do.', waiting: 'Waiting.', blocked: 'Not ready yet.' };

  const $ = (id) => document.getElementById(id);
  const announcer = $('announce');

  let status; // undefined: not asked yet; null: helper not reachable; object: last /status
  let shown = {}; // step -> state on screen
  let firstRender = true;
  let syncConfirmed = false;
  let shortcutUnknownSince = null;
  let timer = null;
  let inFlight = false;

  // ---- helper --------------------------------------------------------------
  function askStatus() {
    return new Promise((resolve) => {
      const t = setTimeout(() => resolve(null), TIMEOUT_MS);
      const done = (r) => {
        clearTimeout(t);
        resolve(r && r.ok ? r : null);
      };
      try {
        chrome.runtime.sendMessage({ type: 'status' }, (r) => {
          void chrome.runtime.lastError;
          done(r);
        });
      } catch {
        done(null);
      }
    });
  }

  async function poll() {
    if (inFlight) return;
    inFlight = true;
    try {
      status = await askStatus();
    } finally {
      inFlight = false;
    }
    render();
  }

  function start() {
    if (timer) return;
    poll();
    timer = setInterval(poll, POLL_MS);
  }

  function stop() {
    clearInterval(timer);
    timer = null;
  }

  // ---- state ---------------------------------------------------------------
  // Each step has a state (the mark: blocked | waiting | todo | done) and a variant
  // (which text to show).
  function compute(s) {
    const up = !!s;
    const out = {};

    out.helper = s === undefined ? ['waiting', 'waiting'] : up ? ['done', 'done'] : ['todo', 'todo'];

    if (!up) out.access = ['blocked', 'blocked'];
    else if (s.libraryReadable === true) out.access = ['done', 'done'];
    else if (s.libraryReadable === false) out.access = ['todo', 'todo'];
    else out.access = ['waiting', 'waiting'];

    if (up && s.podcastsSync === true) out.sync = ['done', 'done-auto'];
    else if (syncConfirmed) out.sync = ['done', 'done-manual'];
    else out.sync = ['todo', 'todo'];

    const pushOn = up && s.pushToPodcasts && s.pushToPodcasts.enabled === true;
    if (!up) out.shortcut = ['blocked', 'blocked'];
    else if (!('shortcutInstalled' in s)) out.shortcut = ['todo', 'old'];
    else if (s.shortcutInstalled === true) out.shortcut = ['done', 'done'];
    // The helper sends YouTube positions to the iPhone by itself: the shortcut is only a backup.
    else if (pushOn) out.shortcut = ['done', 'optional'];
    else if (s.shortcutInstalled === false) out.shortcut = ['todo', 'todo'];
    else {
      shortcutUnknownSince ??= Date.now();
      const giveUp = Date.now() - shortcutUnknownSince > SHORTCUT_UNKNOWN_AFTER_MS;
      out.shortcut = giveUp ? ['todo', 'unknown'] : ['waiting', 'waiting'];
    }
    if (!up || typeof s.shortcutInstalled === 'boolean') shortcutUnknownSince = null;

    if (!up) out.try = ['blocked', 'blocked'];
    else if (s.lastResume || s.lastHandoff) out.try = ['done', 'done'];
    else out.try = ['waiting', 'waiting'];

    return out;
  }

  // ---- render --------------------------------------------------------------
  function render() {
    const view = compute(status);
    let doneCount = 0;

    STEPS.forEach(([key, name], i) => {
      const [state, variant] = view[key];
      const el = $(`step-${key}`);
      if (state === 'done') doneCount++;

      el.dataset.state = state;
      for (const block of el.querySelectorAll('[data-when]')) {
        block.classList.toggle('on', block.dataset.when.split(' ').includes(variant));
      }
      for (const line of el.querySelectorAll('[data-for]')) {
        line.classList.toggle('on', line.dataset.for === variant);
      }
      el.querySelector('.sr-state').textContent = SR_STATE[state];

      const was = shown[key];
      if (state === 'done' && was !== 'done' && !firstRender) {
        el.classList.remove('just-done');
        void el.offsetWidth; // restart the animation
        el.classList.add('just-done');
        announce(`Step ${i + 1} is done: ${name}.`);
      } else if (state !== 'done') {
        el.classList.remove('just-done');
      }
      shown[key] = state;
    });

    renderTry(status);
    $('sync-confirm').checked = syncConfirmed;

    $('progress-fill').style.width = `${(doneCount / STEPS.length) * 100}%`;
    $('progress-label').textContent =
      status === undefined ? 'Checking your setup…' : `${doneCount} of ${STEPS.length} steps done`;
    const allDone = doneCount === STEPS.length;
    if (allDone && $('all-done').hidden && !firstRender) announce('You are all set.');
    $('all-done').hidden = !allDone;
    firstRender = status === undefined;
  }

  function renderTry(s) {
    const resume = s && s.lastResume;
    const handoff = s && s.lastHandoff;
    result(
      $('result-resume'),
      resume && ['It worked: resumed at ', resume.label, ' from Apple Podcasts.'],
      resume && detail(resume.episode, resume.at)
    );
    result(
      $('result-handoff'),
      handoff && ['Ready on your iPhone at ', handoff.time, '.'],
      handoff && detail(handoff.episode, handoff.saved_at)
    );
  }

  function result(el, parts, small) {
    if (!parts) {
      el.hidden = true;
      return;
    }
    const [before, time, after] = parts;
    const strong = document.createElement('strong');
    strong.textContent = time || '';
    const note = document.createElement('small');
    note.textContent = small;
    el.replaceChildren(before, strong, after, note);
    el.hidden = false;
  }

  function detail(episode, at) {
    return [episode, at ? ago(at) : ''].filter(Boolean).join(' · ');
  }

  function ago(unixSeconds) {
    const s = Math.max(0, Date.now() / 1000 - unixSeconds);
    if (s < 60) return 'just now';
    const m = Math.round(s / 60);
    if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60);
    if (h < 24) return h === 1 ? '1 hour ago' : `${h} hours ago`;
    const d = Math.round(h / 24);
    return d === 1 ? 'yesterday' : `${d} days ago`;
  }

  function announce(text) {
    announcer.textContent = '';
    setTimeout(() => {
      announcer.textContent = text;
    }, 50);
  }

  // ---- Sync Library: confirmed by hand -------------------------------------
  const store = {
    async get() {
      try {
        if (chrome.storage?.local) return !!(await chrome.storage.local.get(SYNC_KEY))[SYNC_KEY];
        return localStorage.getItem(SYNC_KEY) === '1';
      } catch {
        return false;
      }
    },
    async set(on) {
      try {
        if (chrome.storage?.local) await chrome.storage.local.set({ [SYNC_KEY]: on });
        else localStorage.setItem(SYNC_KEY, on ? '1' : '0');
      } catch {
        /* the box still works for this visit */
      }
    },
  };

  $('sync-confirm').addEventListener('change', (e) => {
    syncConfirmed = e.target.checked;
    store.set(syncConfirmed);
    render();
  });

  // ---- copy buttons --------------------------------------------------------
  for (const btn of document.querySelectorAll('button.copy')) {
    let reset;
    btn.addEventListener('click', async () => {
      const text = btn.dataset.copy;
      let ok = true;
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        ok = false;
        const code = btn.parentElement.querySelector('code');
        const range = document.createRange();
        range.selectNodeContents(code);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
      }
      btn.textContent = ok ? 'Copied' : 'Press ⌘C';
      btn.classList.toggle('copied', ok);
      announce(ok ? 'Copied to the clipboard.' : 'The command is selected. Press Command C to copy it.');
      clearTimeout(reset);
      reset = setTimeout(() => {
        btn.textContent = 'Copy';
        btn.classList.remove('copied');
      }, 1800);
    });
  }

  // ---- go ------------------------------------------------------------------
  document.addEventListener('visibilitychange', () => (document.visibilityState === 'visible' ? start() : stop()));

  store.get().then((on) => {
    syncConfirmed = on;
    render();
    if (document.visibilityState === 'visible') start();
  });
})();
