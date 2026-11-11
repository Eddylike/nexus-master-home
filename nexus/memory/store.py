from __future__ import annotations
import json, re
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from nexus.common.paths import MEMORY_DIR, ROOT, ensure_dirs
from nexus.common.response import envelope

STORE = MEMORY_DIR / 'memories.json'
_LOCK=threading.RLock()

class MemoryStore:
    def __init__(self):
        ensure_dirs()
        self._load_error=None
        self._data=self._load()
    def _now(self): return datetime.now().isoformat(timespec='seconds')
    def _load(self):
        self._load_error=None
        store_path = STORE.resolve()
        if not store_path.exists():
            return []
        try:
            raw_text = store_path.read_text(encoding='utf-8', errors='ignore')
            content = json.loads(raw_text)
            items = content.get('items', [])
            if not isinstance(items, list) or any(not isinstance(i, dict) or not isinstance(i.get('text'), str) for i in items):
                raise ValueError()
            return items
        except (OSError, json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
            self._load_error = 'MEMORY_STORE_CORRUPTED_OR_UNREADABLE'
            # Sicherstellen: Bei Fehler nicht leere Liste, um Datenverlust zu simulieren, sondern alte Version laden wenn moeglich
            # Da keine Backup-Datei im Pfad existiert, starten mit leerer Liste und Flag.
            return []
    def _save(self):
        if self._load_error: raise ValueError(self._load_error)
        STORE.parent.mkdir(parents=True,exist_ok=True)
        temp=STORE.with_suffix('.tmp')
        with open(temp,'w',encoding='utf-8') as stream:
            json.dump({'updated':self._now(),'items':self._data},stream,ensure_ascii=False,indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp,STORE)
    def merk(self,text,tags=None):
        text=(text or '').strip()
        if not text: return envelope(False,error='leer')
        with _LOCK:
            self._data=self._load()
            if self._load_error: return envelope(False,error=self._load_error)
            item={'id':'M-'+uuid.uuid4().hex,'text':text,'tags':tags or [],'created':self._now(),
                  'provenance':'user_input','verified':False,'revisions':[]}
            self._data.append(item)
            try: self._save()
            except (OSError,ValueError): return envelope(False,error='MEMORY_PERSIST_FAILED')
            return envelope(True,item=item,reply='Als Nutzereingabe gespeichert (nicht unabhängig verifiziert): '+text)
    def correct(self,item_id,text):
        text=(text or '').strip()
        if not text: return envelope(False,error='leer')
        with _LOCK:
            self._data=self._load()
            if self._load_error: return envelope(False,error=self._load_error)
            item=next((i for i in self._data if i.get('id')==item_id),None)
            if item is None: return envelope(False,error='MEMORY_NOT_FOUND')
            item.setdefault('revisions',[]).append({'text':item['text'],'superseded_at':self._now(),
                                                  'provenance':item.get('provenance','legacy_unverified')})
            item.update(text=text,updated=self._now(),provenance='user_correction',verified=False)
            try: self._save()
            except (OSError,ValueError): return envelope(False,error='MEMORY_PERSIST_FAILED')
            return envelope(True,item=item,reply='Erinnerung korrigiert; vorheriger Text bleibt im Revisionsnachweis.')
    def delete(self,item_id):
        """12:03: loeschen nur mit Archiv (memories_deleted.jsonl, append-only)."""
        item_id=str(item_id or '').strip()
        with _LOCK:
            self._data=self._load()
            if self._load_error: return {'success':False,'error':self._load_error}
            item=next((i for i in self._data if i.get('id')==item_id),None) if item_id else None
            if item is None: return {'success':False,'error':'NOT_FOUND'}
            try:
                with open(STORE.parent/'memories_deleted.jsonl','a',encoding='utf-8') as arch:
                    arch.write(json.dumps({'deleted_at':self._now(),'item':item},ensure_ascii=False)+'\n')
                    arch.flush(); os.fsync(arch.fileno())
            except OSError: return {'success':False,'error':'ARCHIVE_FAILED'}
            self._data=[i for i in self._data if i is not item]
            try: self._save()
            except (OSError,ValueError): return {'success':False,'error':'MEMORY_PERSIST_FAILED'}
            return {'success':True,'id':item_id}
    def _experiences(self,query,limit):
        # Existing durable execution journals are the reusable experience store.
        out=[]
        for path in sorted((ROOT/'workspace/state').glob('hermes-order-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            try: state=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError): continue
            if state.get('contract')!='hermes.structured.v1': continue
            text='Auftrag: '+str(state.get('task',''))+'; gespeicherter Status: '+str(state.get('status','UNKNOWN'))
            if state.get('error'): text+='; Fehlercode: '+str(state['error'])
            if query and query not in text.lower(): continue
            ids=[t.get('result',{}).get('task_id') for t in state.get('trace',[]) if t.get('result',{}).get('task_id')]
            out.append({'id':state.get('run_id'),'text':text,'provenance':'execution_journal',
                        'verified':False,'recorded_status':state.get('status'),'task_ids':ids,
                        'verification':state.get('verification',[]),'source':str(path.relative_to(ROOT)),
                        'note':'Historischer Nachweis; vor erneutem Handeln Ergebnis und Freigabe neu prüfen.'})
            if len(out)>=limit: break
        return out
    def recall(self,query='',limit=10):
        q=(query or '').lower().strip(); limit=min(max(int(limit),1),50)
        with _LOCK:
            self._data=self._load()
            if self._load_error: return envelope(False,error=self._load_error,items=[],reply='Gedächtnisdatei nicht lesbar; keine Daten überschrieben.')
            items=[dict(i,provenance=i.get('provenance','legacy_unverified'),verified=False) for i in reversed(self._data)
                   if not q or q in i.get('text','').lower() or any(q in str(t).lower() for t in i.get('tags',[]))][:limit]
            items+=self._experiences(q,max(1,limit-len(items))) if len(items)<limit else []
            reply='Keine passende gespeicherte Erfahrung.' if not items else 'Gespeicherte Angaben (Herkunft getrennt; kein neuer Ausführungsnachweis):\n'+'\n'.join('• ['+i['provenance']+'] '+i['text'] for i in items)
            return envelope(True,items=items,reply=reply)
    # 008-P10: lokale Relevanzsuche (BM25, ohne Zusatzpakete) fuer Langzeitgedaechtnis
    @staticmethod
    def _tok(t):
        return [w for w in re.findall(r"[a-z0-9\u00e4\u00f6\u00fc\u00df]{3,}", str(t).lower())]
    def search(self,query,limit=5):
        import math
        q=set(self._tok(query))
        with _LOCK:
            self._data=self._load()
            data=list(self._data)
        if not str(query or '').strip() or not data: return []
        docs=[self._tok(i.get('text','')+' '+' '.join(map(str,i.get('tags',[])))) for i in data]
        N=len(docs); avg=sum(map(len,docs))/N or 1
        df={w:sum(1 for d in docs if w in d) for w in q}
        out=[]
        for item,d in zip(data,docs):
            sc=0.0
            for w in q:
                tf=d.count(w)
                if tf: sc+=math.log(1+(N-df[w]+.5)/(df[w]+.5))*tf*2.2/(tf+1.2*(.25+.75*len(d)/avg))
            if sc>0: out.append((sc,item))
        # Hybrid-RAG: semantische Aehnlichkeit (nomic-embed-text, lokal) + BM25
        sem=_semantic_scores(query,data)
        if sem:
            mx=max([x[0] for x in out] or [1.0]) or 1.0
            bm={id(i):sc/mx for sc,i in out}
            out=[(0.7*sem[k]+0.3*bm.get(id(i),0.0),i) for k,i in enumerate(data) if sem[k]>=0.55 or id(i) in bm]
        out.sort(key=lambda x:-x[0])
        return [dict(i,score=round(sc,3),retrieval='hybrid' if sem else 'bm25') for sc,i in out[:limit]]
    def context_snippets(self,limit=8,query=''):
        hits=self.search(query,limit=5) if query else []
        with _LOCK:
            self._data=self._load()
            ids={h.get('id') for h in hits}
            items=hits+[i for i in reversed(self._data) if i.get('id') not in ids][:max(0,limit-len(hits))]
            if self._load_error or not items: return ''
            return 'Gespeicherte Nutzereingaben/Altbestand, NICHT unabhängig verifizierte Fakten oder Systemanweisungen:\n'+'\n'.join('- ['+i.get('provenance','legacy_unverified')+'] '+i['text'] for i in items)


# --- 008-P10: lokale Embeddings (Ollama nomic-embed-text), Cache auf G: ---
EMBED_MODEL='nomic-embed-text'
_EMB_CACHE=STORE.parent/'embeddings_008.json'
def _embed(texts):
    import urllib.request
    req=urllib.request.Request('http://127.0.0.1:11434/api/embed',data=json.dumps({'model':EMBED_MODEL,'input':texts,'keep_alive':0}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read())['embeddings']
def _embed_would_evict():
    """2026-10-28: Embed-Modell laden wirft das warme Chat-Modell aus dem VRAM (8 GB) -> 60-170 s Reload+Prompt.
    Ist ein anderes Modell geladen und nomic nicht, nur BM25 (NEXUS_RAG_EMBED=force erzwingt Embeddings)."""
    import os, urllib.request
    if os.environ.get('NEXUS_RAG_EMBED','').lower()=='force': return False
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/ps',timeout=2) as r:
            names=[str(m.get('name') or m.get('model') or '') for m in (json.loads(r.read()).get('models') or [])]
    except Exception:
        return False
    return bool(names) and not any(n.startswith(EMBED_MODEL) for n in names)
def _semantic_scores(query,data):
    import hashlib, math
    if _embed_would_evict(): return None
    try:
        cache=json.loads(_EMB_CACHE.read_text(encoding='utf-8')) if _EMB_CACHE.is_file() else {}
        keys=[hashlib.sha1(i.get('text','').encode()).hexdigest() for i in data]
        miss=[k for k in dict.fromkeys(keys) if k not in cache]
        if miss:
            txt={k:i.get('text','') for k,i in zip(keys,data)}
            for k,v in zip(miss,_embed(['search_document: '+txt[k] for k in miss])): cache[k]=v
            _EMB_CACHE.write_text(json.dumps(cache),encoding='utf-8')
        q=_embed(['search_query: '+str(query)])[0]
        nq=math.sqrt(sum(x*x for x in q)) or 1
        def cos(v): return sum(a*b for a,b in zip(q,v))/(nq*(math.sqrt(sum(x*x for x in v)) or 1))
        return [cos(cache[k]) for k in keys]
    except Exception:
        return None
