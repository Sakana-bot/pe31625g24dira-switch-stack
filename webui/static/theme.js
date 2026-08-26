'use strict';

(function initTheme() {
  const storageKey = 'pe31625g24dira-theme';
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const validModes = new Set(['system', 'light', 'dark']);

  function getMode() {
    const saved = window.localStorage.getItem(storageKey);
    return validModes.has(saved) ? saved : 'system';
  }

  function apply(mode) {
    const selected = validModes.has(mode) ? mode : 'system';
    const effective = selected === 'system' ? (media.matches ? 'dark' : 'light') : selected;
    document.documentElement.dataset.theme = effective;
    document.documentElement.dataset.themeMode = selected;
    document.documentElement.style.colorScheme = effective;
    return effective;
  }

  function setMode(mode) {
    const selected = validModes.has(mode) ? mode : 'system';
    window.localStorage.setItem(storageKey, selected);
    apply(selected);
  }

  function systemChanged() {
    if (getMode() === 'system') apply('system');
  }

  if (media.addEventListener) media.addEventListener('change', systemChanged);
  else media.addListener(systemChanged);

  window.fmTheme = { getMode, setMode, apply };
  apply(getMode());
}());
