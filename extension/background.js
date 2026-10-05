// Relays requests from the YouTube page to the local helper. The page itself
// cannot reach 127.0.0.1 (CORS / private network rules); the extension can.
const HELPER = 'http://127.0.0.1:47321';

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === 'status') {
    fetch(`${HELPER}/status`, { headers: { 'X-Podsync': '1' } })
      .then((r) => r.json())
      .then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  // Any helper endpoint: {type: 'match'|'resume'|'progress'|<feature>, payload}.
  if (typeof msg?.type !== 'string' || !/^[a-z][a-z-]{0,30}$/.test(msg.type)) return false;
  fetch(`${HELPER}/${msg.type}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Podsync': '1' },
    body: JSON.stringify(msg.payload),
  })
    .then((r) => r.json())
    .then(sendResponse, (e) => sendResponse({ error: String(e) }));
  return true;
});
