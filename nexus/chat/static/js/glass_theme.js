/* MH4D-GLAS 2026-11-10: Schalter "Glas an/aus" (localStorage nxGlass, ?glass=0|1). Ohne Funktionseingriff. */
(function () {
  "use strict";
  var KEY = "nxGlass", root = document.documentElement;
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  var q = /[?&]glass=([01])/.exec(location.search);
  function on() { return q ? q[1] === "1" : stored() !== "0"; }
  function apply(v) { root.classList.toggle("nx-glass-off", !v); var b = document.getElementById("nxGlassToggle"); if (b) { b.setAttribute("aria-pressed", v ? "true" : "false"); b.textContent = "Glas: " + (v ? "an" : "aus"); } }
  apply(on());
  function mount() {
    var box = document.querySelector("#nxGlassSystemControls .nx-glass-system-controls");
    if (!box) return setTimeout(mount, 500);
    if (document.getElementById("nxGlassToggle")) return;
    var b = document.createElement("button"); b.type = "button"; b.id = "nxGlassToggle"; b.className = "chip"; b.title = "Glasoptik (Verlauf Blau/Mint/Lila) an/aus";
    b.addEventListener("click", function () { var v = root.classList.contains("nx-glass-off"); try { localStorage.setItem(KEY, v ? "1" : "0"); } catch (e) {} q = null; apply(v); });
    box.appendChild(b); apply(!root.classList.contains("nx-glass-off"));
  }
  mount();
  window.nxGlass = function (v) { try { localStorage.setItem(KEY, v ? "1" : "0"); } catch (e) {} q = null; apply(!!v); };
})();
