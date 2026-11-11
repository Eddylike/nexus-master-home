/* MH4D-03 2026-11-10: Chat-Titel bekommt immer den vollen Text als Tooltip (Ellipsis im Header). Keine Funktionsaenderung. */
(function () {
  "use strict";
  function sync() { var t = document.getElementById("chatTitle"); if (t) { var s = (t.textContent || "").trim(); if (t.title !== s) t.title = s; } }
  function init() { var t = document.getElementById("chatTitle"); if (!t) return setTimeout(init, 500); sync(); new MutationObserver(sync).observe(t, { childList: true, characterData: true, subtree: true }); }
  init();
})();