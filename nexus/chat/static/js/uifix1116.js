/* uifix1116 (Nexus-UI, 16.11.2026): Systemwerte in Sidebar, Ziel-Dropdown beendet Crew-Agent. Nur ASCII. */
(function(){
  "use strict";
  function moveChips(n){
    var chips=document.getElementById("statusChips"), sys=document.getElementById("nxGlassNav-system"), nav=document.getElementById("nxGlassNav");
    if(!chips||!sys||!nav){ if((n||0)<40) setTimeout(function(){moveChips((n||0)+1)},500); return; }
    var box=document.getElementById("nxSideSystem");
    if(!box){ box=document.createElement("section"); box.id="nxSideSystem"; box.setAttribute("aria-label","Systemwerte"); var h=document.createElement("h4"); h.textContent="System"; box.appendChild(h); nav.appendChild(box); }
    if(chips.parentElement!==box) box.appendChild(chips);
  }
  function hookTarget(n){
    var sel=document.getElementById("nxGlassTargetSelect");
    if(!sel){ if((n||0)<40) setTimeout(function(){hookTarget((n||0)+1)},500); return; }
    if(sel.dataset.fix1116) return; sel.dataset.fix1116="1";
    sel.addEventListener("change",function(){
      var v=sel.value;
      try{ window.NEXUS_PHASE2_PARTNER=v; localStorage.setItem("nexus_phase2_partner",v); }catch(e){}
      /* echten Server-Zustand pruefen statt Chip (QA 17.11.) */
      fetch("/api/crew/active",{cache:"no-store"}).then(function(r){return r.json()}).then(function(a){
        if(!a || !a.id) return;
        return fetch("/api/crew/active",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:""})}).then(function(){
          var chip=document.getElementById("chipCrew");
          if(chip){ chip.classList.add("hide"); chip.textContent="Crew -"; chip.style.borderColor=""; }
          var inp=document.getElementById("nxInput"); if(inp){ var o=sel.options[sel.selectedIndex]; var nm=((o&&o.text)||v||"NEXUS").replace(/^[^A-Za-z0-9]+/,"").trim()||"NEXUS"; inp.placeholder="Nachricht an "+nm+"\u2026"; }
        });
      }).catch(function(){});
    });
  }
  function go(){ moveChips(0); hookTarget(0); }
  if(document.readyState==="loading") document.addEventListener("DOMContentLoaded",go); else go();
})();