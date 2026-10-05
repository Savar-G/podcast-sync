// Popup settings, kept in chrome.storage.sync. content.js reads them on youtube.com.
//   onOpen: 'jump' (default) jumps to the iPhone position; 'marker' only marks it on the progress bar.
(() => {
  const inputs = [...document.querySelectorAll('#settings input[name="onOpen"]')];
  chrome.storage.sync.get({ onOpen: 'jump' }, ({ onOpen }) => {
    for (const i of inputs) i.checked = i.value === onOpen;
  });
  for (const i of inputs) {
    i.addEventListener('change', () => i.checked && chrome.storage.sync.set({ onOpen: i.value }));
  }
})();
