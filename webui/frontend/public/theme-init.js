// Runs before first paint: pick the Clarity theme (cds-theme) from the saved
// choice, else the OS preference, and set color-scheme to match.
(function () {
  var saved = null;
  try { saved = localStorage.getItem('lj-theme'); } catch (e) {}
  var dark = saved ? saved === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  var theme = dark ? 'dark' : 'light';
  document.documentElement.style.colorScheme = theme;
  document.addEventListener('DOMContentLoaded', function () {
    document.body.setAttribute('cds-theme', theme);
  });
  document.documentElement.setAttribute('data-theme', theme);
})();
