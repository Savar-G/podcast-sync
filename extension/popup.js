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
