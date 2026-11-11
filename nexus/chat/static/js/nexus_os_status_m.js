/* NEXUS OS status card for the PWA (/m). Source: /api/nexus-os/status (v2).
   Every row shows the measured state; disconnected phone = neutral/yellow, never red-critical.
   Replaces the generic "Health <score>" header chip with SYSTEM_HEALTH + OPERATING_MODE. */
(function(){
  var ROWS=[['CORE','CORE'],['OPERATING_MODE','OPERATING MODE'],['WOZIPC','WOZIPC'],['WOZIPC_LOCAL_AI','WOZIPC LOCAL AI'],
    ['PHONE_DEVICE','PHONE DEVICE'],['TERMUX','TERMUX'],['PHONE_LOCAL_AI','PHONE LOCAL AI'],
    ['GLOBAL_LOCAL_AI','GLOBAL LOCAL AI'],['ACTIVE_AI_PROVIDER','ACTIVE AI PROVIDER'],['CHATGPT','CHATGPT'],
    ['SHARED_ROOM','SHARED ROOM'],['VOICE','VOICE'],['NETWORK','NETWORK'],['SYSTEM_HEALTH','SYSTEM HEALTH'],['CRITICAL_FAILURE','CRITICAL FAILURE']];
  var LROWS=[['PHONE_CORE','LINK PHONE ↔ CORE'],['CORE_WOZIPC','LINK CORE ↔ WOZIPC'],['CORE_CHATGPT','LINK CORE ↔ CHATGPT']];
  var C={g:'#3dff9a',y:'#ffd166',a:'#ffb347',r:'#ff6b6b',n:'#9aa7bd',b:'#8ea3c8'};
  function color(k,c){ var s=c&&(c.state||c.status);
    if(k==='CRITICAL_FAILURE') return c&&c.value?C.r:C.g;
    if(k==='OPERATING_MODE') return s==='FULL'?C.g:(s==='OFFLINE'?C.r:C.a);
    if(k==='ACTIVE_AI_PROVIDER') return s&&s!=='UNKNOWN'&&s!=='NONE'?C.g:C.r;
    if(s==='ONLINE'||s==='HEALTHY') return C.g; if(s==='OPTIONAL'||s==='AVAILABLE') return C.b; if(s==='UNAVAILABLE') return C.y; if(s==='DEGRADED') return C.a;
    if(s==='DISCONNECTED'||s==='UNAVAILABLE_TEMPORARILY') return C.y; if(s==='FAILED'||s==='CRITICAL') return C.r;
    if(s==='OFFLINE') return (k==='CORE'||k==='GLOBAL_LOCAL_AI'||k==='SYSTEM_HEALTH')?C.r:C.a; return C.n; }
  function label(k,c){ if(!c) return 'UNKNOWN'; var s=c.state||c.status;
    if(k==='ACTIVE_AI_PROVIDER') return c.value||c.status;
    if(s==='DISCONNECTED'||s==='UNAVAILABLE'||s==='UNAVAILABLE_TEMPORARILY') return 'NOT REACHABLE · Reason: '+(c.reason||s);
    if((s==='UNKNOWN'||s==='OFFLINE'||s==='FAILED'||s==='AVAILABLE')&&c.reason) return s+' · '+c.reason; return s; }
  async function refresh(){
    var rows=document.getElementById('mOsRows'), tag=document.getElementById('mOsHealthTag'), foot=document.getElementById('mOsFoot');
    var d=null; try{ var r=await fetch('/api/nexus-os/status',{cache:'no-store'}); d=await r.json(); }catch(e){ d=null; }
    var chip=document.getElementById('mHealth');
    if(!d||!d.components){ if(rows) rows.innerHTML='<div class="m-dash-row"><span class="k">STATUS</span><span class="v unk">UNKNOWN</span></div>';
      if(tag) tag.textContent='UNKNOWN'; if(chip){ chip.textContent='Status UNKNOWN'; chip.classList.remove('ok'); chip.classList.add('warn'); } return; }
    if(rows){ rows.innerHTML=''; ROWS.forEach(function(x){ var c=d.components[x[0]]; var row=document.createElement('div'); row.className='m-dash-row';
      var k=document.createElement('span'); k.className='k'; k.textContent=x[1]; var v=document.createElement('span'); v.className='v';
      v.textContent=label(x[0],c); v.style.color=color(x[0],c);
      row.title=c?((c.detail||'')+'\nsource: '+(c.source||'?')+'\nchecked_at: '+(c.checked_at||'?')):'';
      row.appendChild(k); row.appendChild(v); rows.appendChild(row); });
      LROWS.forEach(function(x){ var c=(d.LINKS||{})[x[0]]; var row=document.createElement('div'); row.className='m-dash-row';
      var k=document.createElement('span'); k.className='k'; k.textContent=x[1]; var v=document.createElement('span'); v.className='v';
      v.textContent=label(x[0],c); v.style.color=color(x[0],c);
      row.title=c?((c.detail||'')+'\nsource: '+(c.source||'?')+'\nchecked_at: '+(c.checked_at||'?')):'';
      row.appendChild(k); row.appendChild(v); rows.appendChild(row); }); }
    if(tag){ tag.textContent=(d.SYSTEM_HEALTH||'UNKNOWN'); tag.style.color=color('SYSTEM_HEALTH',{status:d.SYSTEM_HEALTH}); }
    if(foot) foot.textContent='Stand '+(d.timestamp||'?')+(d.age_sec!=null?(' ('+d.age_sec+' s)'):'')+(d.stale?' · VERALTET':'')+' · Quelle /api/nexus-os/status';
    if(chip){ chip.textContent=(d.SYSTEM_HEALTH||'UNKNOWN')+' · '+(d.OPERATING_MODE||'?');
      chip.classList.toggle('ok',d.SYSTEM_HEALTH==='HEALTHY'); chip.classList.toggle('warn',d.SYSTEM_HEALTH!=='HEALTHY'); }
  }
  function start(){ refresh(); setTimeout(refresh,1500); setInterval(refresh,30000); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
