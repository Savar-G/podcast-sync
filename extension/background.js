// Relays requests from the YouTube page to the local helper. The page itself
// cannot reach 127.0.0.1 (CORS / private network rules); the extension can.
const HELPER = 'http://127.0.0.1:47321';
const POSTS = { match: '/match', resume: '/resume', progress: '/progress' };

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'status') {
    fetch(`${HELPER}/status`, { headers: { 'X-Podsync': '1' } })
      .then((r) => r.json())
      .then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  const path = POSTS[msg?.type];
  if (!path) return false;
  fetch(`${HELPER}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Podsync': '1' },
    body: JSON.stringify(msg.payload),
  })
    .then((r) => r.json())
    .then(sendResponse, (e) => sendResponse({ error: String(e) }));
  return true;
});
