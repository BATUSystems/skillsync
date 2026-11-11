/* dashboard — motyw przed pierwszym rysowaniem: zapamiętany wybór ("light"/"dark") albo motyw systemu.
   Bez pamięci przeglądarki strona działa na motywie systemu. */
(() => {
  "use strict";
  let saved = null;
  try { saved = localStorage.getItem("dashboard:theme") || localStorage.getItem("status-board:theme"); } catch { /* brak pamięci */ }
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
})();
