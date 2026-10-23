(() => {
  'use strict';
  const key = 'brsy-appearance';
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let mode = 'dark';
  try { mode = localStorage.getItem(key) || 'dark'; } catch {}
  if (!['dark', 'light', 'system'].includes(mode)) mode = 'dark';
  function apply() {
    const theme = mode === 'system' ? (media.matches ? 'dark' : 'light') : mode;
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#101012' : '#f4f4f5');
    window.dispatchEvent(new Event('appearancechange'));
  }
  window.brsyAppearance = {
    get: () => mode,
    set(value) {
      if (!['dark', 'light', 'system'].includes(value)) return;
      mode = value;
      try { localStorage.setItem(key, value); } catch {}
      apply();
    }
  };
  media.addEventListener('change', () => { if (mode === 'system') apply(); });
  apply();
})();
