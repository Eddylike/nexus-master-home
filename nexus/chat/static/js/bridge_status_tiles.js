/* NEXUS Master status tiles + NEXUS SHELL / ORBITAL status block (v2, 2026-10-06).
   Single data source: /api/nexus-os/status (real probes, each field with source + checked_at).
   No hardcoded tile text, no global "SYSTEM OK": every value comes from a measured field.
   UNKNOWN stays UNKNOWN. ChatGPT is OPTIONAL and never degrades the shown state.
   A disconnected phone is shown neutral/yellow ("NOT REACHABLE · Reason: ..."), never red-critical. */
(function(){
  async function jget(url){ try{ const r=await fetch(url,{cache:'no-store'}); if(!r.ok && r.status!==200) return null; return await r.json(); }catch(e){ return null; } }
  function el(id){ return document.getElementById(id); }

  var ROWS = [
    ['CORE','CORE'], ['OPERATING_MODE','OPERATING MODE'], ['WOZIPC','WOZIPC'], ['WOZIPC_LOCAL_AI','WOZIPC LOCAL AI'],
    ['PHONE_DEVICE','PHONE DEVICE'], ['TERMUX','TERMUX'], ['PHONE_LOCAL_AI','PHONE LOCAL AI'],
    ['GLOBAL_LOCAL_AI','GLOBAL LOCAL AI'], ['ACTIVE_AI_PROVIDER','ACTIVE AI PROVIDER'], ['CHATGPT','CHATGPT'],
    ['SHARED_ROOM','SHARED ROOM'], ['VOICE','VOICE'], ['NETWORK','NETWORK'], ['BRIDGE','BRIDGE'],
    ['SYSTEM_HEALTH','SYSTEM HEALTH'], ['CRITICAL_FAILURE','CRITICAL FAILURE']
  ];
  var LINK_ROWS = [['PHONE_CORE','LINK PHONE ↔ CORE'], ['CORE_WOZIPC','LINK CORE ↔ WOZIPC'], ['CORE_CHATGPT','LINK CORE ↔ CHATGPT']];
  var GREEN='#0f9d58', YELLOW='#b45309', AMBER='#c2570a', RED='#d14343', GREY='#64748b', BLUE='#3a6ea5';
  var CRITICAL_ROWS = {CORE:1, GLOBAL_LOCAL_AI:1, SYSTEM_HEALTH:1};

  function colorFor(key, c){
    var s = c && (c.state || c.status);
    if(key === 'CRITICAL_FAILURE') return (c && c.value) ? RED : GREEN;
    if(key === 'OPERATING_MODE') return s === 'FULL' ? GREEN : (s === 'OFFLINE' ? RED : AMBER);
    if(key === 'ACTIVE_AI_PROVIDER') return (s && s !== 'UNKNOWN' && s !== 'NONE') ? GREEN : RED;
    switch(s){
      case 'ONLINE': case 'HEALTHY': return GREEN;
      case 'OPTIONAL': case 'AVAILABLE': return BLUE;
      case 'UNAVAILABLE': return YELLOW;
      case 'DEGRADED': return AMBER;
      case 'DISCONNECTED': case 'UNAVAILABLE_TEMPORARILY': return YELLOW;
      case 'FAILED': case 'CRITICAL': return RED;
      case 'OFFLINE': return CRITICAL_ROWS[key] ? RED : AMBER;
      default: return GREY; // UNKNOWN
    }
  }
  function labelFor(key, c){
    if(!c) return 'UNKNOWN';
    var s = c.state || c.status;
    if(key === 'ACTIVE_AI_PROVIDER') return c.value || c.status;
    if(s === 'DISCONNECTED' || s === 'UNAVAILABLE' || s === 'UNAVAILABLE_TEMPORARILY') return 'NOT REACHABLE · Reason: ' + (c.reason || s);
    if((s === 'UNKNOWN' || s === 'OFFLINE' || s === 'FAILED' || s === 'AVAILABLE') && c.reason) return s + ' · ' + c.reason;
    return s;
  }
  function tip(c){
    if(!c) return 'keine Daten';
    var t = [];
    if(c.detail) t.push(c.detail);
    if(c.reason) t.push('reason: ' + c.reason);
    if(c.dependency) t.push('dependency: ' + c.dependency);
    t.push('source: ' + (c.source || '?'));
    t.push('checked_at: ' + (c.checked_at || '?'));
    if(c.last_known) t.push('last_known: ' + c.last_known + ' (veraltet)');
    return t.join('\n');
  }

  function setTile(id, text, color, title){
    var t = el(id); if(!t) return;
    t.textContent = text; t.style.color = color || '#1e3550'; t.title = title || '';
  }

  function renderShell(data){
    var head = el('nx-shell-head'), rows = el('nx-shell-rows'), foot = el('nx-shell-foot');
    if(!head || !rows) return;
    if(!data || !data.components){
      head.textContent = 'NEXUS OS · STATUS UNKNOWN'; head.style.color = GREY; rows.innerHTML='';
      if(foot) foot.textContent = 'Status-Endpoint nicht abrufbar -> alle Felder UNKNOWN'; return;
    }
    var id = data.identity || {};
    head.textContent = (id.system_name || 'NEXUS OS') + ' · ' + (data.SYSTEM_HEALTH || 'UNKNOWN') + ' · ' + (data.OPERATING_MODE || 'UNKNOWN');
    head.style.color = colorFor('SYSTEM_HEALTH', {status: data.SYSTEM_HEALTH});
    rows.innerHTML = '';
    ROWS.forEach(function(r){
      var key = r[0], c = (data.components||{})[key];
      var line = document.createElement('div');
      line.style.display = 'flex'; line.style.justifyContent = 'space-between'; line.style.gap = '10px';
      var name = document.createElement('span'); name.textContent = r[1]; name.style.color = BLUE;
      var val = document.createElement('span'); val.textContent = labelFor(key, c); val.style.color = colorFor(key, c);
      val.style.textAlign = 'right';
      line.appendChild(name); line.appendChild(val);
      line.title = tip(c);
      rows.appendChild(line);
    });
    LINK_ROWS.forEach(function(r){
      var key = r[0], c = (data.LINKS||{})[key];
      var line = document.createElement('div');
      line.style.display = 'flex'; line.style.justifyContent = 'space-between'; line.style.gap = '10px';
      var name = document.createElement('span'); name.textContent = r[1]; name.style.color = BLUE;
      var val = document.createElement('span'); val.textContent = labelFor(key, c); val.style.color = colorFor(key, c);
      val.style.textAlign = 'right';
      line.appendChild(name); line.appendChild(val); line.title = tip(c); rows.appendChild(line);
    });
    if(foot){
      var rc = data.recheck || {};
      var parts = [];
      parts.push('Stand: ' + (data.timestamp || '?') + (data.age_sec != null ? (' (' + data.age_sec + ' s alt)') : ''));
      if(data.stale) parts.push('VERALTET -> UNKNOWN');
      parts.push('Re-Check alle ' + (rc.interval_sec || '?') + ' s' + (rc.monitor_running ? '' : ' (Monitor aus)'));
      parts.push('Lead: ' + (id.lead_agent || '-'));
      parts.push('ChatGPT optional, zaehlt nicht fuer Health');
      foot.textContent = parts.join(' · ');
    }
  }

  async function tick(){
    var shell = await jget('/api/nexus-os/status');
    var comps = (shell && shell.components) || {};

    // Tiles: each from its own measured field (no static text)
    var sh = comps.SYSTEM_HEALTH;
    setTile('nx-tile-health', 'Health ' + ((shell && shell.SYSTEM_HEALTH) || 'UNKNOWN') + ' · ' + ((shell && shell.OPERATING_MODE) || '?'),
            colorFor('SYSTEM_HEALTH', {status: shell && shell.SYSTEM_HEALTH}), tip(sh));
    var ai = comps.WOZIPC_LOCAL_AI;
    setTile('nx-tile-ollama', 'Ollama ' + (ai ? (ai.status + (ai.model ? ' · ' + ai.model : '')) : 'UNKNOWN'), colorFor('WOZIPC_LOCAL_AI', ai), tip(ai));
    var net = comps.NETWORK;
    var funnelTxt = 'Funnel UNKNOWN';
    if(net){
      var fOn = net.funnel === true ? 'on' : (net.funnel === false ? 'off' : '?');
      var pub = net.public_code != null ? (' · public ' + net.public_code) : ' · public ?';
      funnelTxt = 'Funnel ' + fOn + pub + ' · ' + net.status;
    }
    setTile('nx-tile-funnel', funnelTxt, colorFor('NETWORK', net), tip(net));
    var br = comps.BRIDGE;
    var brTxt = br ? ('Bridge ' + br.status + (br.auth_enforced ? ' · Auth 401' : '') + (br.process_level ? ' · ' + br.process_level : '')) : 'Bridge UNKNOWN';
    setTile('nx-tile-perm', brTxt, colorFor('BRIDGE', br), tip(br));

    var sid = (window.NEXUS_SESSION_ID || localStorage.getItem('nexus_session_id') || 'active');
    var room = await jget('/api/shared-room/status?session_id='+encodeURIComponent(sid));
    var rr = el('chipRoomRound'); if(rr) rr.textContent = room && room.success ? ('Round '+((room.last_message&&room.last_message.round)||0)+'/'+(room.max_rounds||4)) : 'Round ?/4';
    var rc = el('chipRoom');
    if(rc){
      var srC = comps.SHARED_ROOM, apC = comps.ACTIVE_AI_PROVIDER;
      var lm = room && room.last_message;
      var lines = [
        'Shared Room: ' + (srC ? srC.status : 'UNKNOWN') + (srC && srC.reason ? (' · Reason: ' + srC.reason) : '') + (srC && srC.detail ? (' · ' + srC.detail) : ''),
        'Session ' + sid + ': ' + (room && room.success ? ((room.message_count || 0) + ' Nachrichten · Runde ' + ((lm && lm.round) || 0) + '/' + (room.max_rounds || '?')) : 'nicht lesbar'),
        lm ? ('Letzte: ' + (lm.sender_name || lm.sender_type || '?') + ' · ' + (lm.timestamp || '?')) : 'Letzte: -',
        'Teilnehmer aktiv: ' + ((room && room.participants_present && room.participants_present.length) ? room.participants_present.join(', ') : '-'),
        'Aktiver KI-Provider: ' + ((apC && (apC.value || apC.status)) || (shell && shell.ACTIVE_AI_PROVIDER) || 'UNKNOWN'),
        'Quelle: ' + (srC ? srC.source : '?') + ' · ' + (srC ? srC.checked_at : '?')
      ];
      rc.title = lines.join('\n');
      var rs = srC ? srC.status : 'UNKNOWN';
      rc.className = 'chip ' + (rs === 'ONLINE' ? 'ok' : (rs === 'UNKNOWN' ? '' : 'warn'));
    }

    renderShell(shell);
    var chip = el('chipNexusOs');
    if(chip){
      var name = (shell && shell.identity && shell.identity.system_name) || 'NEXUS OS';
      var h = (shell && shell.SYSTEM_HEALTH) || 'UNKNOWN';
      chip.textContent = name + ' · ' + h + (shell && shell.OPERATING_MODE ? (' · ' + shell.OPERATING_MODE) : '');
      chip.className = 'chip ' + (h === 'HEALTHY' ? 'ok' : 'warn');
      chip.title = tip(sh);
    }
  }

  function initToggle(){
    var btn = el('btnNexusShell'), panel = el('nx-shell-panel');
    if(!btn || !panel) return;
    btn.addEventListener('click', function(){
      var hidden = panel.classList.contains('hide');
      panel.classList.toggle('hide', !hidden);
      btn.textContent = hidden ? 'NEXUS SHELL \u25B4' : 'NEXUS SHELL \u25BE';
    });
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', function(){ initToggle(); tick(); }); else { initToggle(); tick(); }
  setInterval(tick, 15000);
})();
