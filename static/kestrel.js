window.Kestrel = (function () {
  'use strict';
  var state = { lang: localStorage.getItem('kestrel_lang') || 'zh',
                theme: localStorage.getItem('kestrel_theme') ||
                       (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') };
  function applyTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
  }
  function init() {
    applyTheme();
    document.getElementById('main').textContent = 'Kestrel shell OK';
  }
  document.addEventListener('DOMContentLoaded', init);
  return { state: state, applyTheme: applyTheme };
})();
