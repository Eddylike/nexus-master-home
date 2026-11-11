(function(){
  "use strict";
  const N = window.NEXUS;
  N.live = {
    session: new URLSearchParams(location.search).get("session_id"), seen: new Set(), busy: false,
    init(apply) {
      this.apply = apply;
      ["btn-play","btn-step","btn-reset"].forEach(id=>{document.getElementById(id).hidden=true;});
      const pause=document.getElementById("btn-pause");if(pause)pause.hidden=true;
      document.getElementById("boot").classList.add("gone");
      N.scene.setActive(true);N.camera.setIdle();
      N.store.patch({connection:{source:"sharedroom",status:"connecting",label:"LIVE · verbindet"}});
      Object.keys(N.store.state.agents).forEach(id=>N.store.setAgent(id,{status:"unknown",load:null}));
      const chip=document.getElementById("chip-source");chip.dataset.state="live";chip.textContent="LIVE · verbindet";
      this.poll();this.timer=setInterval(()=>this.poll(),800);
    },
    async poll(){
      if(this.busy||document.hidden)return;this.busy=true;
      try{
        const url="/api/shared-room/events"+(this.session?"?session_id="+encodeURIComponent(this.session):"");
        const response=await fetch(url,{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);
        const data=await response.json();if(!data.success)throw Error(data.error||"Quelle nicht verfügbar");
        if(this.run && this.run!==data.run_id){this.seen.clear();N.store.reset();}
        this.run=data.run_id;this.session=data.session_id;
        document.getElementById("chip-source").textContent="LIVE · "+data.session_id;
        N.store.patch({connection:{source:"sharedroom",status:"live",label:"LIVE · SharedRoom"}});
        const incoming=(data.messages||[]).map(raw=>Object.assign(N.adapter.SharedRoomAdapter.mapMessage(raw),{sequence:(raw.metadata||{}).sequence||0})).concat(data.events||[]);
        incoming.sort((a,b)=>(a.sequence||0)-(b.sequence||0));
        for(const event of incoming){if(!this.seen.has(event.id)){this.apply(event);this.seen.add(event.id);}}
        for(const id of ["nexus","hermes"]){
          const done=["PASS","PARTIAL","BLOCKED"].includes(data.status);
          N.store.setAgent(id,{status:!data.run_id?"unknown":done?data.status.toLowerCase():data.phase||"active",active:!!data.run_id&&!done,load:null});
        }
        document.getElementById("chip-discussion").textContent=data.status+" · "+(data.phase||"Keine gemeinsame Sitzung");
        window.__nexusLive={session_id:data.session_id,run_id:data.run_id,status:data.status,message_ids:(data.messages||[]).map(m=>m.message_id),event_ids:(data.events||[]).map(e=>e.id),received_at:Date.now()};
      }catch(error){document.getElementById("chip-source").textContent="VERBINDUNG UNBEKANNT · "+error.message;}
      finally{this.busy=false;}
    }
  };
})();
