"""Evidence views and chat-to-Hermes routing; NEXUS remains the controller.

No model-generated task IDs or capability claims are promoted to evidence.
Only explicit file contracts, checked project orders and deterministic source-backed
reports are executed. A project goal is never a blanket write grant: every planned step
must pass the existing path, tool and permission boundaries first.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from nexus.common.paths import ROOT

STATUSES = ('PLANNED','EXECUTED','VERIFIED','PASS','PARTIAL','BLOCKED','FAILED')
TASK_ID = re.compile(r'NX-\d{8}-\d{4,}')

# Imperative PC-command phrasing ("führe ipconfig aus", "ping ausführen"). These are
# never executable without a verified Hermes contract, and the chat model must not be
# allowed to fabricate their output. Ambiguous or non-command objects are exempt.
_EXEC_VERB = re.compile(
    r"\b(?:f(?:ü|ue)hre|f(?:ü|ue)hrt|starte(?:let)?)\s+(?:bitte\s+)?"
    r"(?:den\s+befehl\s+|das\s+programm\s+|die\s+pr(?:ü|ue)fung\s+)?"
    r"([a-z0-9][a-z0-9._@:/+\-]{0,60})"
    r"(?:[\s\S]{0,80}?\s*aus\b\s*\.?$)",
    re.I | re.S,
)
_EXEC_NOUN = re.compile(r"\b([a-z0-9][a-z0-9._@:/+\-]{0,60})\s+ausf(?:ü|ue)hren\b", re.I)
_EXEC_SKIP = re.compile(
    r"\b(den|die|das|dem|der|eine|einen|ein|einer|eines|deine|dein|meine|mein|"
    r"ihre|ihr|seine|sein|es|unsere|diese|dieser|euer|bitte|nun|jetzt|mal|alle|alles|sämtliche|saemtliche|jeder|jede|jeden|jedes|mehrere|verschiedene|beliebige|zuerst|"
    r"danach|plan|pläne|plaene|auftrag|aufgabe|vorhaben|arbeit|bericht|test|probe|"
    r"pr(?:ü|ue)fung|skript|script|programm|werkzeug\w*|musik|video|folge|suche|recherche|recherchen|"
    r"analyse|aktion|handlung|bewegung|schritt|via|zu|an|aus)\b",
    re.I,
)

_EXEC_LEAD = re.compile(r"(?:(?:bitte|nun|jetzt|nexus|hermes|und|dann)[,\s]+)*", re.I)


def _exec_command_request(text: str) -> str | None:
    """Erkennt imperativ formulierte PC-Befehlsanfragen, nicht aber Resume-/Chat-Wörter.

    Ein PC-Befehl ist ein eigener kurzer Imperativsatz ("Führe ipconfig aus.", "ping
    ausführen"). Nebensätze in längeren Aufträgen ("Falls das System HTML/JS nicht direkt
    ausführen darf, ...") sind keine Befehle und dürfen keinen Befehl "direkt" erzeugen.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = sentence.strip().lstrip("-*•#> ").strip()
        if not s:
            continue
        m = _EXEC_VERB.search(s)
        if m is None:
            m = _EXEC_NOUN.search(s)
            if m is not None and s[m.end():].strip(" .!") != "":
                m = None
        if m is None or _EXEC_LEAD.fullmatch(s[:m.start()]) is None:
            continue
        cmd = m.group(1).strip().lower()
        if cmd and not _EXEC_SKIP.search(cmd):
            return cmd
    return None


# Konkrete, deterministisch erkennbare Fähigkeitslücken eines Entwicklungsauftrags.
# Der Schreibvertrag erlaubt nur .txt/.md/.json im Arbeitsbereich und .py/.md/.json im
# Codebereich; NEXUS-eigener Quellcode und die Oberfläche sind nicht beschreibbar.
_FOREIGN_TECH = re.compile(
    r"(?<![\w.])(typescript|node\.?js|react|vue|svelte|angular|"
    r"java|kotlin|c\+\+|c#|rust|php|unity|godot|apk|exe|"
    r"[\w-]+\.(?:mjs|ts|tsx|jsx|java|cpp|cs|php|exe|apk))(?![\w+#])",
    re.I)
_NEXUS_SELF_CHANGE = re.compile(
    r"\b(?:bau|integrier|einbau|implementier|erweiter|änder|aender)\w*\b[^.!?\n]{0,60}?"
    r"\b(?:in|im|into|an)\s+(?:das\s+|die\s+|den\s+|der\s+)?nexus(?![\w-]*arbeitsbereich)\b"
    r"|\bin\s+(?:das\s+|die\s+)?nexus[\w\s-]{0,30}?\s+(?:ein)?(?:bauen|integrieren|einbauen)\b"
    r"|\bnexus-?(?:quellcode|oberfläche|oberflaeche|ui|weboberfläche|dashboard)\b[^.!?\n]{0,30}?"
    r"\b(?:änder|aender|erweiter|anpass|umbau)\w*",
    re.I)
_HEDGED = re.compile(r"\b(?:falls|wenn|sofern|ob|nicht|kein\w*|ohne|statt|anstatt)\b", re.I)


def _clause_before(text, start, width=60):
    """Satzteil unmittelbar vor einer Fundstelle (bis zum letzten Satz-/Aufzählungsende)."""
    head = text[max(0, start - width):start]
    return re.split(r"[.!?\n;]", head)[-1]


def capability_gaps(text):
    """Benennt die konkret fehlenden Fähigkeiten eines Bau-/Entwicklungsauftrags.

    Verneinte oder bedingte Erwähnungen ("keine Cloud", "falls ... nicht ... darf") zählen
    nicht. Liefert eine Liste (Code, Klartext); leer heißt: innerhalb der Grenzen planbar.
    """
    value = str(text or "")
    gaps = []
    techs = []
    for m in _FOREIGN_TECH.finditer(value):
        if _HEDGED.search(_clause_before(value, m.start(), 30)):
            continue
        name = m.group(1).lower()
        if name not in techs:
            techs.append(name)
    if techs:
        gaps.append(('FILE_TYPE_NOT_RELEASED',
                     'Werkzeugkette für ' + ', '.join(techs[:6]) + ' – der Entwicklungsbereich '
                     'workspace/projects/hermes/code/ unterstützt Python sowie HTML/CSS/JavaScript '
                     '(Browserprojekte, ohne Frameworks/Build-Schritt)'))
    for m in _NEXUS_SELF_CHANGE.finditer(value):
        if _HEDGED.search(_clause_before(value, m.start(), 40)):
            continue
        gaps.append(('NEXUS_SOURCE_WRITE_NOT_RELEASED',
                     'Änderung an NEXUS selbst (Quellcode/Oberfläche) – Hermes darf nur im freigegebenen '
                     'Arbeitsbereich schreiben, nicht in nexus/ oder der NEXUS-Oberfläche'))
        break
    return gaps

def _load(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def persisted_task(task_id, root=ROOT):
    if type(task_id) is not str or not TASK_ID.fullmatch(task_id): return {}
    record=_load(root/'workspace/bridge/tasks'/(task_id+'.json'))
    return record if record.get('id')==task_id else {}

def capability_snapshot(root=ROOT):
    from nexus.bridge.permissions import TASK_PERMISSIONS
    from nexus.master.tools import KNOWN_TOOLS
    from nexus.integrations.hermes_client import invoke
    health=invoke({'operation':'health'})
    caps=invoke({'operation':'capabilities'})
    exposed=set(caps.get('allowed_tasks',[])) if caps.get('success') else set()
    proof=_load(root/'workspace/hermes-core/evidence/ollama-core-e2e-20261009-01.json')
    records=[persisted_task(t,root) for t in proof.get('response',{}).get('task_ids',[])]
    file_test=(proof.get('status')=='PASS' and proof.get('exact_match') is True and len(records)==2
               and [r.get('task') for r in records]==['create_file','read_file']
               and all(r.get('status')=='succeeded' for r in records))
    definitions=[
        ('pc','PC-Steuerung','powershell_registered_action' in TASK_PERMISSIONS,None,None,'Registrierte Aktionen; privilegierte Aktionen nur mit lokaler Freigabe.'),
        ('files','Dateioperationen',{'create_file','read_file'}<=set(TASK_PERMISSIONS),
         bool(health.get('success') and {'create_file','read_file'}<=exposed),file_test,
         'Geprüfter Umfang: neue Textdatei im Hermes-Arbeitsbereich erstellen, lesen, exakt vergleichen.'),
        ('code','Sichere Code-Entwicklung',{'hermes_check_code','hermes_run_code_tests'}<=set(TASK_PERMISSIONS),
         bool(health.get('success') and {'hermes_check_code','hermes_run_code_tests'}<=exposed),None,
         'Ablauf: Entwicklungsziel → Dateiplan nur im Codebereich (workspace/projects/hermes/code) → statische Prüfung hermes_check_code → Testlauf hermes_run_code_tests nur bei freigegebener Codeausführung (NEXUS_CODE_EXEC=1). Kein Shell-Zugriff, keine Änderung außerhalb der freigegebenen Bereiche.'),
        ('browser','Browser-Bedienung','open_url' in KNOWN_TOOLS,None,None,'URL-Öffnen ist registriert; keine nachgewiesene allgemeine Browser-Automation.'),
        ('gmail','Gmail',False,None,None,'Browser-Link vorhanden; authentifizierte Mail-Werkzeuge nicht nachgewiesen.'),
        ('drive','Google Drive',False,None,None,'Browser-Link vorhanden; authentifizierte Datei-Werkzeuge nicht nachgewiesen.'),
        ('calendar','Kalender',False,None,None,'Browser-Link vorhanden; authentifizierte Kalender-Werkzeuge nicht nachgewiesen.'),
        ('github','GitHub',False,None,None,'Lokale Git-Aufträge sind keine nachgewiesene GitHub-API-Anbindung.'),
        ('voice','Sprachsteuerung',(root/'nexus/voice/voice_manager.py').is_file(),None,None,'Server-STT (faster-whisper) und TTS; Nachweis = echte Audiodatei transkribiert.'),
        ('phone','Smartphone-Anbindung','android_devices' in TASK_PERMISSIONS,None,None,'Registriert; hier weder abgefragt noch verändert.'),
        ('agents','Autonome Agenten',(root/'nexus/crew/runner.py').is_file(),None,None,'Crew-Implementierung vorhanden; unbegrenzte Autonomie nicht nachgewiesen.'),
        ('skills','Skill-Erstellung',False,None,None,'Vorhandene Skill-Liste dient der Entdeckung; kein geprüfter Erstellungs-/Aktivierungsablauf.'),
        ('improve','Selbstverbesserung',(root/'nexus/memory/self_improve.py').is_file(),None,None,'Vorschlagsliste vorhanden; produktive Änderungen benötigen separate Prüfung und Freigabe.'),
        ('web','Web-Recherche','web_search' in KNOWN_TOOLS,None,None,'Recherchewerkzeug registriert; Live-Suche wird bei jeder Statusabfrage real ausgeführt.')]
    live={}
    if health.get('success'):
        from nexus.master.capability_probe import run_probes
        live=run_probes(root).get('probes',{})
    rows=[]
    for key,label,registered,reachable,tested,note in definitions:
        probe=live.get(key)
        task_ids=[r['id'] for r in records] if key=='files' and file_test else []
        provenance='registry_and_persisted_receipts' if key=='files' else 'source_registration_only'
        if probe:
            if probe.get('reachable') is not None: reachable=probe['reachable']
            if probe.get('tested') is not None: tested=probe['tested']
            task_ids=list(probe.get('task_ids') or [])+task_ids
            provenance='live_probe'+('_and_persisted_evidence' if probe.get('evidence') else '')
            if probe.get('error'): note=note+f" Letzte Probe: Fehler {probe['error']}."
            elif probe.get('tested') is False and key in ('browser','voice','agents'):
                note=note+' Live-Nachweis (capability-'+key+'-*.json) fehlt oder ist älter als 30 Tage.'
        rows.append({'id':key,'name':label,'registered':registered,'reachable':reachable,
                     'tested':tested,'ready':bool(registered and reachable is True and tested is True),
                     'scope':note,'evidence_task_ids':task_ids,'provenance':provenance,
                     'probe':{k:v for k,v in (probe or {}).items() if k not in ('id',)} if probe else None})
    probe_ids=[i for r in rows for i in r['evidence_task_ids'] if i]
    return {'capabilities':rows,'bridge_reachable':bool(health.get('success')),'live_probe':bool(live),
            'probe_task_ids':probe_ids,
            'note':'Registriert ist kein Ausführungsnachweis. UNKNOWN bedeutet nicht geprüft. '
                   'Ja bei getestet/einsatzbereit stammt aus einer echten Probe (max. 10 Min alt) oder einem gespeicherten PASS-Beleg.'}

def capability_text(snapshot):
    def flag(value): return 'UNKNOWN' if value is None else ('ja' if value else 'nein')
    lines=['Fähigkeiten – quellenbasierter Status:']
    for row in snapshot['capabilities']:
        lines.append(f"- {row['name']}: registriert={flag(row['registered'])}, erreichbar={flag(row['reachable'])}, getestet={flag(row['tested'])}, einsatzbereit={flag(row['ready'])}. {row['scope']}")
    ids=snapshot.get('probe_task_ids') or []
    if snapshot.get('live_probe'):
        lines.append('Live-Probe ausgeführt; Beleg-Task-IDs: '+(', '.join(ids) if ids else 'keine')+'.')
    else:
        lines.append('Keine Live-Probe (Bridge nicht erreichbar); keine neue Task-ID.')
    return '\n'.join(lines)

def development_report(root=ROOT):
    snapshot=capability_snapshot(root)
    lines=['# NEXUS Master Home – belegter Entwicklungsstand','',
           '## Überprüfte Ergebnisse',
           'Quelle: gespeicherte Bridge-Aufträge und der vorhandene Hermes-Live-Nachweis.']
    files=next(r for r in snapshot['capabilities'] if r['id']=='files')
    if files['tested']:
        lines+=['- Ein Hermes-Dateiauftrag wurde erstellt, zurückgelesen und bytegenau verglichen.',
                '- Historische Beleg-Task-IDs: '+', '.join(files['evidence_task_ids'])+'.']
    else: lines+=['- Kein ausreichend belegter historischer Dateiabschluss gefunden.']
    lines+=['','## Registrierte Funktionen und offene Prüfungen']
    lines+=['- Dateiwerkzeuge: registriert; geprüfter Umfang ist der oben genannte Hermes-Dateipfad.',
            '- Weitere PC-, Browser-, Sprach-, Smartphone-, Agenten- und Recherchefunktionen sind hier nicht als einsatzbereit verifiziert.',
            '- Gmail, Drive, Kalender und GitHub: keine in dieser Prüfung belegte authentifizierte Werkzeuganbindung.',
            '- Skill-Erstellung und produktive Selbstverbesserung: kein hier verifizierter Aktivierungsablauf.']
    lines+=['','## Herkunft und Grenzen',
            '- Projektfakten: oben genannte Registrierungen und gespeicherte Nachweise.',
            '- Nutzereingabe: Erstellung dieses sachlichen Berichts.',
            '- Modellannahmen: nicht als Projektfakten übernommen.',
            '- Die Task-IDs dieses neuen Schreib-/Leseauftrags entstehen erst bei der Ausführung und werden in der Abschlussantwort genannt.',
            '- Tokens, Zugangsdaten und private Inhalte gehören nicht in diesen Bericht.','']
    return '\n'.join(lines)

def blocked(error,detail):
    return {'success':False,'intent':'hermes_work','status':'BLOCKED','error':error,
            'reply':'BLOCKED: '+detail+'\nKeine registrierte Task-ID für diesen Auftrag.',
            'data':{'execution_status':'BLOCKED','task_ids':[],'provenance':'nexus_validation'}}

def _response(brain_result, root=ROOT):
    """Render receipt facts, never the model's purported execution summary."""
    status=brain_result.get('status','BLOCKED')
    if status not in STATUSES: status='BLOCKED'
    ids=brain_result.get('task_ids',[])
    records=[persisted_task(t,root) for t in ids]
    if any(not r for r in records): return blocked('TASK_RECORD_MISSING','Ein angegebener Auftragsbeleg fehlt.')
    verification=brain_result.get('verification') or []
    if status=='PASS':
        requirements=brain_result.get('requirements') or []
        if not requirements or len(verification)!=len(requirements):
            return blocked('INCOMPLETE_VERIFICATION','Nicht alle geforderten Ergebnisse sind überprüft.')
        for req,check in zip(requirements,verification):
            create=next((r for r in records if r.get('id')==check.get('create_task_id')), {})
            read=next((r for r in records if r.get('id')==check.get('read_task_id')), {})
            # A file that already existed with byte-identical content is proven by its read receipt
            # alone (it is never rewritten); every other file needs its own create receipt.
            created_ok=(create.get('task')=='create_file' and create.get('status')=='succeeded'
                        and create.get('params')==req)
            reused_ok=check.get('reused') is True and check.get('create_task_id') is None
            if not ((created_ok or reused_ok) and read.get('task')=='read_file' and read.get('status')=='succeeded'
                    and read.get('params')=={'path':req['path']} and read.get('result',{}).get('content')==req['content']
                    and read.get('result',{}).get('truncated') is False and check.get('equal') is True):
                return blocked('VERIFICATION_MISMATCH','Auftragsbelege passen nicht zum geforderten Ergebnis.')
    lines=[status+(': Alle geforderten Dateien erstellt, zurückgelesen und exakt verglichen.' if status=='PASS' else ': Auftrag noch nicht vollständig nachgewiesen.')]
    for r in records: lines.append(f"- {r['task']}: {r['id']} – {r['status']}")
    if not records: lines.append('Keine registrierte Task-ID für diesen Auftrag.')
    for check in verification: lines.append(f"- VERIFIED: {check['path']} – Inhalt identisch, SHA-256 {check['sha256']}")
    if brain_result.get('error'): lines.append('Grund: '+str(brain_result['error']))
    return {'success':status=='PASS','status':status,'intent':'hermes_work','reply':'\n'.join(lines),
            'model':brain_result.get('model'),'error':brain_result.get('error'),'run_id':brain_result.get('run_id'),
            'data':{'execution_status':status,'task_ids':ids,'verification':verification,
                    'tools':[{'name':r['task'],'task_id':r['id'],'status':r['status'],'result':r.get('result')} for r in records],
                    'stages':['PLANNED']+(['EXECUTED'] if records else [])+(['VERIFIED','PASS'] if status=='PASS' else []),
                    'provenance':'persisted_bridge_tasks','run_id':brain_result.get('run_id')}}

_READ_VERB = re.compile(
    r"\b(?:lies|l(?:i|ie)s(?:e)?|read|zeig(?:e)?\s+(?:mir\s+)?(?:den\s+|der\s+|die\s+)?"
    r"(?:inhalt\s+)?(?:der\s+|von\s+|dieser\s+|der\s+datei\s+)?|"
    r"was\s+steht|was\s+ist\s+(?:der\s+)?inhalt|"
    r"inhalt\s+(?:der|von|dieser)\s+datei|dateiinhalt|file\s+content)\b",
    re.I,
)


def _read_path(text):
    quoted = re.findall(r'["„“`]([\w./\\:-]+\.(?:md|txt|json|py))["„“`]', text)
    paths = re.findall(r'(?<![\w./\\:-])([\w./\\:-]+\.(?:md|txt|json|py))\b', text, re.I)
    return list(dict.fromkeys(quoted + paths))


_NAMED_FILE = re.compile(r'(?<![\w./\\:-])([\w./\\:-]+\.(?:md|txt|json|py))\b', re.I)
# Nur finite/imperative Schreibverben (z. B. „erstelle“, „speichere“), keine Partizipien
# wie „erstellten“: das Ziel darf ein bereits erstelltes Eingabefile nicht als
# Pflicht-Lieferobjekt fordern.
_WRITE_VERB = re.compile(
    r"\b(?:erstell(?:e|en|st)?|schreib(?:e|en|st)?|speicher(?:e|en|t)?|"
    r"anleg(?:e|en|st)?|create|write)\b",
    re.I,
)
_READ_CONTEXT = re.compile(
    r"\b(?:lies|liest|lese|lesen|read|zeig(?:e)?|öffne|oeffne|vorhanden\w*)\b", re.I)


def _create_target_paths(text):
    """Benannte Pfade, die das Ziel tatsächlich *erstellen* soll.

    Ein nur zum Lesen genanntes, bereits vorhandenes Eingabefile darf nie als
    Pflicht-Lieferobjekt gefordert werden: der Hermes-create-Vertrag schreibt nur
    neue Dateien und überschreibt nie. Pfade im Kontext eines Lese-Befehls werden
    daher ausgelassen; alle übrigen bleiben Erstell-Ziele.
    """
    stream = [(m.start(), "w") for m in _WRITE_VERB.finditer(text)]
    stream += [(m.start(), "r") for m in _READ_CONTEXT.finditer(text)]
    stream += [(m.start(), "f", m.group(1)) for m in _NAMED_FILE.finditer(text)]
    stream.sort(key=lambda item: item[0])
    targets: list[str] = []
    mode: str | None = None
    for item in stream:
        if item[1] == "w":
            mode = "w"
        elif item[1] == "r":
            mode = "r"
        elif mode != "r":
            targets.append(item[2])
    return list(dict.fromkeys(targets))


def read_request(master, text):
    """Reine Leseaufträge über die geprüfte, schreibfreie Bridge lesen.

    Nur explizite Leseanfragen mit Dateipfad werden behandelt; Schreibaufträge
    laufen über ihren eigenen verifizierten Vertrag. Ergebnis ist der echte,
    bytegenau gelesene Inhalt samt persistierter Task-ID.
    """
    low = (text or '').lower()
    if re.search(r'\b(erstell\w*|schreib\w*|speicher\w*|create|write|überschreib\w*|aendere|ändere)\b', low):
        return None
    if not _READ_VERB.search(low):
        return None
    candidates = _read_path(text)
    if not candidates:
        return None
    from nexus.integrations.hermes_client import invoke, validate_task, ARTIFACT_ROOT
    bare = candidates[0].replace('\\', '/')
    if '/' not in bare and ':' not in bare:
        rooted = ARTIFACT_ROOT + '/' + bare
        if (ROOT / rooted).is_file():
            bare = rooted
    try:
        validate_task('read_file', {'path': bare})
    except Exception:
        return blocked('READ_PATH_NOT_ALLOWED',
                       'Dieser Dateipfad liegt außerhalb des geprüften Hermes-Arbeitsbereichs. '
                       'Es wurde nichts gelesen.')
    result = invoke({'operation': 'submit', 'task': 'read_file', 'params': {'path': bare}})
    tid = result.get('task_id') or ''
    record = persisted_task(tid) if tid else {}
    tools = [{'name': 'read_file', 'task_id': tid or None,
              'status': 'succeeded' if result.get('success') else 'failed', 'result': None}] if tid else []
    if not result.get('success'):
        code = result.get('error_code') or 'READ_FAILED'
        err = 'Datei nicht gefunden.' if result.get('backend_status') == 'failed' else 'Lesen wurde blockiert.'
        return {'success': False, 'intent': 'hermes_work', 'status': 'FAILED', 'error': code,
                'reply': 'FAILED: ' + err + f'\nLeseauftrag {bare} ist nicht abgeschlossen (Fehlercode {code}).'
                        + (f'\nTask-ID: {tid}' if tid else '\nKeine registrierte Task-ID für diesen Auftrag.'),
                'data': {'execution_status': 'FAILED', 'task_ids': [tid] if tid else [],
                         'tools': tools, 'provenance': 'persisted_bridge_tasks'}}
    if not (record.get('task') == 'read_file' and record.get('params') == {'path': bare}
            and record.get('status') == 'succeeded'
            and record.get('result') == result.get('result')):
        return blocked('READ_RECEIPT_MISMATCH', 'Der gespeicherte Lesebeleg fehlt oder passt nicht zum Ergebnis.')
    content = (result.get('result') or {}).get('content')
    if content is None:
        return blocked('READ_CONTENT_MISSING', 'Der Leseauftrag meldet keinen Inhalt.')
    fields = record.get('result') if isinstance(record, dict) else {}
    size = fields.get('size') if isinstance(fields, dict) else None
    if size is None:
        size = (result.get('result') or {}).get('size')
    truncated = fields.get('truncated') is True if isinstance(fields, dict) else False
    note = ' (gekürzt)' if truncated else ''
    body = content.strip()
    reply = f'VERIFIED: DATEI GELESEN – {bare}{note}, {size} Bytes, Task-ID {tid}.'
    if body:
        reply += '\n\n' + body
    return {'success': True, 'intent': 'hermes_work', 'status': 'PASS', 'task_id': tid,
            'reply': reply,
            'data': {'execution_status': 'PASS', 'task_ids': [tid] if tid else [],
                     'tools': [{'name': 'read_file', 'task_id': tid or None, 'status': 'succeeded',
                                'result': {'path': bare, 'size': size, 'truncated': truncated}}],
                     'stages': ['PLANNED', 'EXECUTED', 'VERIFIED', 'PASS'],
                     'provenance': 'persisted_bridge_tasks'}}


def safe_chat_request(text):
    from nexus.integrations.hermes_client import invoke, ARTIFACT_ROOT
    low = text.lower().strip().rstrip('.!?')
    if low in ('zeige nexus-aufträge', 'zeige nexus-auftraege', 'meine aufträge', 'auftragsstatus'):
        from nexus.master.orders_view import orders_snapshot
        snap = orders_snapshot()
        rows = snap['orders'] + snap['project_orders'] + snap['code_orders']
        lines = ['Gespeicherte Aufträge:'] + [f"- {r['status']}: {r['task']}" for r in rows]
        if not rows:
            lines.append('Keine gespeicherten Hermes-Aufträge gefunden.')
        lines.append('Details und Werkzeugbelege findest du unter Aufträge in der Oberfläche.')
        return {'success': True, 'intent': 'hermes_work', 'reply': '\n'.join(lines),
                'data': {'execution_status': 'VERIFIED', 'task_ids': [], 'provenance': 'persisted_order_journals'}}
    if low in ('lokale freigaben', 'zeige lokale freigaben', 'öffne lokale freigaben'):
        return {'success': True, 'intent': 'hermes_work',
                'reply': 'Lokale Freigaben: http://127.0.0.1:8299/approvals — dort den konkreten Auftrag prüfen und freigeben oder ablehnen.',
                'data': {'execution_status': 'PLANNED', 'task_ids': [], 'provenance': 'approval_ui_link'}}
    choices = {
        'liste hermes-dateien': ('list_files', {'path': ARTIFACT_ROOT}),
        'zeige hermes-dateien': ('list_files', {'path': ARTIFACT_ROOT}),
        'prüfe nexus-verbindung': ('ping', {}),
        'pruefe nexus-verbindung': ('ping', {}),
        'zeige nexus-serverstatus': ('inspect_server', {}),
    }
    if low not in choices:
        return None
    task, params = choices[low]
    result = invoke({'operation': 'submit', 'task': task, 'params': params})
    tid = result.get('task_id')
    record = persisted_task(tid)
    if not (result.get('success') and record.get('task') == task and record.get('params') == params
            and record.get('status') == 'succeeded' and record.get('result') == result.get('result')):
        return {'success': False, 'intent': 'hermes_work',
                'reply': 'Nicht abgeschlossen: ' + str(result.get('error_code') or 'RECEIPT_UNVERIFIED')
                         + ('; Task-ID: ' + tid if tid else ''),
                'data': {'execution_status': 'BLOCKED', 'task_ids': [tid] if record else [],
                         'provenance': 'persisted_bridge_tasks'}}
    fields = record['result']
    if task == 'list_files':
        details = '\n'.join('- ' + e['name'] + ('/' if e.get('type') == 'dir' else '')
                            for e in fields.get('entries', [])) or '(leer)'
    elif task == 'ping':
        details = 'Die authentifizierte NEXUS-Bridge antwortet.'
    else:
        details = f"Server {fields.get('server')}, Port {fields.get('port')}, Berechtigungsstufe {fields.get('process_level')}"
    return {'success': True, 'intent': 'hermes_work',
            'reply': 'VERIFIED: ' + details + '\nTask-ID: ' + tid,
            'data': {'execution_status': 'VERIFIED', 'task_ids': [tid], 'provenance': 'persisted_bridge_tasks',
                     'tools': [{'name': task, 'task_id': tid, 'status': record['status'], 'result': fields}]}}


def _unhandled_capability(text):
    # Keywords describe a request; they cannot prove that a capability is absent.
    # In particular, "unabhängig vom PC und Internet" is an offline requirement.
    for match in re.finditer(r"\b(?:powershell|power-shell|shell|cmd|konsole)\b", str(text or ""), re.I):
        if not _HEDGED.search(_clause_before(text, match.start())):
            return "geprüfter Hermes-Shell-/Systemvertrag für diesen konkreten Auftrag"
    return "kein registrierter, pruefbarer Hermes-Werkzeugvertrag"


def _described(text, target):
    """Beschreibt der Auftrag den gewünschten Inhalt (nicht nur einen nackten Dateinamen)?"""
    rest = str(text or '').replace(target, ' ')
    return len(re.findall(r'[A-Za-zÄÖÜäöüß]{2,}', rest)) >= 12


def _capability_blocked(gaps):
    codes = ','.join(dict.fromkeys(code for code, _ in gaps))
    return blocked('CAPABILITY_MISSING:' + codes,
                   'Dieser Auftrag übersteigt die freigegebenen Fähigkeiten. Konkret fehlt: '
                   + '; '.join(label for _, label in gaps) + '. Es wurde nichts geschrieben und nichts '
                   'ausgeführt. Innerhalb der Grenzen möglich: Python-Projekte (isoliert getestet) und '
                   'HTML/CSS/JavaScript-Browserprojekte (Browserprüfung + geprüfte Vorschau) unter '
                   'workspace/projects/hermes/code/<projekt>/ sowie Textdateien unter workspace/projects/hermes/.')


def route_work(master,message,history,session):
    from nexus.master.decision import decide
    from nexus.integrations.hermes_brain import HermesBrain
    from nexus.integrations.hermes_client import validate_task
    from nexus.master.project_order import is_project_order, run as run_project_order
    from nexus.master.code_order import is_code_order, run as run_code_order, write_negated, CREATE_INTENT
    from nexus.master.project_order import has_explicit_content
    from nexus.master.decision import strip_answer_policy
    text=strip_answer_policy(message)
    low=text.lower()
    if low in ('weiter','fortsetzen','auftrag fortsetzen','wiederaufnehmen') and session.get('hermes_order'):
        order=session['hermes_order']
        if order.get('nexus'):
            from nexus.master.nexus_order import run as run_nexus_order
            return run_nexus_order(master,order['task'],session)
        if order.get('web'):
            from nexus.master.web_order import run as run_web_order
            return run_web_order(master,order['task'],session)
        if order.get('code'):
            return run_code_order(master,order['task'],session)
        if order.get('project'):
            return run_project_order(master,order['task'],session,
                                     required_paths=[r.get('path') for r in order.get('requirements',[])
                                                     if isinstance(r,dict)])
        result=HermesBrain(master).run(order['task'],requirements=order['requirements'])
        return _response(result)
    if low in ('weiter','fortsetzen','auftrag fortsetzen','wiederaufnehmen') and session.get('local_order'):
        from nexus.master.local_order import run as run_local_order
        return run_local_order(master,session['local_order']['task'],session)
    if low in ('weiter','fortsetzen','auftrag fortsetzen','wiederaufnehmen'):
        return blocked('NO_PERSISTED_HERMES_ORDER','Kein zu dieser Sitzung gehörender Hermes-Auftrag vorhanden. Alte unkorrelierte Werkzeugzusammenfassungen werden nicht automatisch wiederholt.')
    # Auftrag 003: expliziter Hermes-Auftrag ('Hermes: ...' / '@hermes ...') -> Hermes Agent, kein Fallback.
    from nexus.integrations.hermes_agent import is_hermes_request, strip_prefix, ask as hermes_ask
    if is_hermes_request(text):
        return hermes_ask(strip_prefix(text))
    safe = safe_chat_request(text)
    if safe is not None:
        return safe
    # Local AI/device work has registered tools of its own. Do not force it into
    # a Hermes file contract, browser project, or approval-bound NEXUS self-edit.
    # 27.10. NEXUS+Hermes Skill-Bau und Skill-Aufruf per Chat
    from nexus.master import skill_order as _so
    _sr=_so.maybe_run(text)
    if _sr is not None: return _sr
    if _so.is_build(text): return _so.run(master,text,session)
    from nexus.tasks.local import is_local_work
    if is_local_work(text):
        from nexus.master.local_order import run as run_local_order
        return run_local_order(master,text,session)
    decision=decide(text)
    if decision.get('intent')=='capabilities':
        snapshot=capability_snapshot()
        return {'success':True,'intent':'capabilities','status':'VERIFIED','reply':capability_text(snapshot),
                'data':{**snapshot,'execution_status':'VERIFIED','task_ids':[],'provenance':'registry_and_receipts'}}
    # Auftrag 002: Reparatur bestehender Projektdateien (Backup, Hash, Test, Rollback, Nachweis).
    from nexus.master.repair_order import is_repair_order, run as run_repair_order
    if is_repair_order(text):
        return run_repair_order(master,text,session)
    cmd=_exec_command_request(text)
    if cmd:
        return blocked('NO_VERIFIABLE_WORK_CONTRACT',
                       f'Der Befehl "{cmd}" gehört zur PC-/Systemausführung und benötigt einen geprüften '
                       'Hermes-Werkzeugvertrag sowie gegebenenfalls lokale Freigabe. Konkret nicht verfügbar: '
                       'registrierter Shell-/Systemvertrag. Es wurde nichts ausgeführt. '
                       'Registrierte, freigegebene Aktionen sind über ihre vorgesehenen Schnittstellen erreichbar.')
    read=read_request(master,text)
    if read is not None:
        return read
    create=bool(re.search(r'\b(erstell\w*|schreib\w*|speicher\w*|create|write)\b',low))
    file_request=create and bool(re.search(r'\b(datei|markdown|textdatei|bericht|arbeitsplan)\b|\.(?:txt|md|json)\b',low))
    paths=re.findall(r'(?<![\w./\\:-])([\w./\\:-]+\.(?:md|txt|json|py))\b',text,re.I)
    # Explizite Dateioperation (Inhalt vorgegeben), Entwicklungsauftrag (Python) und
    # übergeordneter Projektauftrag (Text-Ziel) werden getrennt.
    project=create and is_project_order(text,paths)
    code=is_code_order(text,paths)
    from nexus.master.web_order import is_web_order, run as run_web_order
    from nexus.master.nexus_order import is_nexus_change, run as run_nexus_order
    nexus_change=is_nexus_change(text)
    web=is_web_order(text) and not nexus_change
    if nexus_change: code=False
    if web: code=False
    targets=[p.replace('\\','/') for p in _create_target_paths(text)]
    # Eine einzelne Zieldatei ohne "Inhalt:"-Vorgabe, aber mit inhaltlicher Beschreibung ist
    # ein begrenzter Projektauftrag: der Planer leitet den Inhalt innerhalb der Grenzen ab,
    # statt pauschal EXACT_CONTENT_REQUIRED zu melden. Eine nackte Dateiangabe bleibt exakt.
    report_like=bool(re.search(r'entwicklungs(?:bericht|stand)|nachgewiesen\w*\s+fähigkeiten|sachlich\w*\s+bericht',low))
    if (create and not project and not code and not report_like and len(targets)==1
            and not has_explicit_content(text) and _described(text,targets[0])):
        project=True
    build_intent=(decision.get('intent') in ('build','execute','shell','run_script','code')
                  or bool(decision.get('force_tools')))
    if not file_request and not project and not code and not web and not nexus_change:
        if build_intent:
            gaps=capability_gaps(text) if CREATE_INTENT.search(low) and not write_negated(low) else []
            if gaps:
                return _capability_blocked(gaps)
            # 2026-10-26 BLOCKED-Fix: ohne passenden Vertrag normal weiter (Chat/Crew/Tools/Websuche);
            # BLOCKED nur noch fuer echte Shell-/Systemausfuehrung.
            if not _unhandled_capability(text).startswith('geprüfter'):
                return None
            return blocked('NO_VERIFIABLE_WORK_CONTRACT',
                           'Für diesen Arbeitsauftrag fehlt eine registrierte, prüfbare Aktion mit '
                           'Ausführungsnachweis. Konkret nicht verfügbar: ' + _unhandled_capability(text) + '. '
                           'Es wurde nichts ausgeführt; bestehende freigegebene Spezialaktionen bleiben über '
                           'ihre Schnittstellen erreichbar.')
        return None
    # Never interpret examples, negated writes or questions as permission to write.
    # A write verb that is merely followed by "wie" or "beispiel" deeper in the sentence
    # ("... in der festgehalten wird, wie ...") is still a real order: only a negated
    # write or an actual interrogative request blocks execution.
    negated_write = write_negated(low)
    interrogative = bool(re.match(
        r'\s*(?:wie|was|warum|wieso|weshalb|welche[rs]?|wer|wann|wo(?:hin|her)?|'
        r'kann(?:st)?\s+(?:du|man|ich)|k(?:ö|oe)nn\w*\s+(?:wir|ich|du)|erkl(?:ä|ae)r\w*)\b',
        low)) or text.rstrip().endswith('?')
    if negated_write or interrogative:
        return blocked('WRITE_INTENT_NOT_EXPLICIT',
                       'Kein eindeutiger ausführbarer Schreibauftrag: '
                       + ('jeder Schreib-/Bauakt ist verneint.' if negated_write else 'der Text ist als Frage formuliert.'))
    # Fähigkeitsgrenzen VOR jeder Planung prüfen: ein HTML/JS-Spiel oder ein Umbau von NEXUS
    # selbst wird nicht stillschweigend in ein Python- oder Drei-Dateien-Projekt umgedeutet.
    if nexus_change:
        return run_nexus_order(master,text,session)
    if code or web or build_intent:
        gaps=capability_gaps(text)
        if gaps:
            return _capability_blocked(gaps)
    if web:
        return run_web_order(master,text,session)
    if code:
        result=run_code_order(master,text,session)
        if decision.get('intent') in ('build','execute','shell','run_script') or decision.get('force_tools'):
            result['reply']=str(result.get('reply') or '')+(
                '\nNicht Teil dieses Entwicklungsauftrags: privilegierte Aktionen (Shell, Geräte, System) '
                'benötigen weiterhin ihre eigenen Berechtigungen und gegebenenfalls lokale Freigabe.')
        return result
    if project:
        for target in targets:
            rooted=target if '/' in target or ':' in target else 'workspace/projects/hermes/'+target
            try: validate_task('create_file',{'path':rooted,'content':''})
            except Exception:
                return blocked('PATH_NOT_AUTHORIZED',
                               f'Zielpfad {target} liegt außerhalb des freigegebenen Hermes-Arbeitsbereichs '
                               '(erlaubt: workspace/projects/hermes/<name>.txt|md|json). Es wurde nichts geschrieben.')
        result=run_project_order(master,text,session,required_paths=targets)
        if decision.get('intent') in ('build','execute','shell','run_script','code') or decision.get('force_tools'):
            result['reply']=str(result.get('reply') or '')+(
                '\nNicht Teil dieses Projektauftrags: privilegierte Aktionen (Shell, Build, Geräte) '
                'benötigen weiterhin ihre eigenen Berechtigungen und gegebenenfalls lokale Freigabe.')
        return result
    if len(set(paths))>1: return blocked('MULTIPLE_FILES_REQUIRE_CONTRACT','Mehrere Dateien benötigen explizite Anforderungen pro Datei.')
    candidate=paths[0].replace('\\','/') if paths else ''
    report=bool(re.search(r'entwicklungs(?:bericht|stand)|arbeitsplan|nachgewiesen\w*\s+fähigkeiten|sachlich\w*\s+bericht',low))
    if not candidate and report: candidate='nexus_entwicklungsbericht_'+hashlib.sha256(text.encode()).hexdigest()[:10]+'.md'
    if not candidate: return blocked('FILE_NAME_REQUIRED','Dateiname und exakter Inhalt oder ein sachlicher Entwicklungsbericht fehlen.')
    path=candidate if '/' in candidate or ':' in candidate else 'workspace/projects/hermes/'+candidate
    explicit=re.search(r'(?:inhalt|content)\s*:?\s*(?:"([\s\S]*?)"|„([\s\S]*?)“|```(?:text)?\s*\n([\s\S]*?)```)',text,re.I)
    if explicit:
        content=next(g for g in explicit.groups() if g is not None)
    elif report:
        # Stable requirements across retries, even if capability reachability changes.
        old=session.get('hermes_order') or {}
        content=old['requirements'][0]['content'] if old.get('task')==text else development_report()
    else:
        match=re.search(r'(?:inhalt|content)\s*:?\s*(?:"([\s\S]*?)"|„([\s\S]*?)“|```(?:text)?\s*\n([\s\S]*?)```)',text,re.I)
        if not match: return blocked('EXACT_CONTENT_REQUIRED','Bitte den exakten Dateiinhalt nach Inhalt: in Anführungszeichen oder einem Textblock angeben.')
        content=next(g for g in match.groups() if g is not None)
    req={'path':path,'content':content}
    try: validate_task('create_file',req)
    except Exception: return blocked('PATH_NOT_AUTHORIZED','Dieser Pfad liegt außerhalb des geprüften Hermes-Arbeitsbereichs oder ist unzulässig.')
    session['hermes_order']={'task':text,'requirements':[req]}
    # The engine persists this session before model/tool execution via the callback.
    persist=getattr(master,'_persist_chat_order',None)
    if persist: persist()
    result=HermesBrain(master).run(text,requirements=[req])
    return _response(result)

def guard_output(result):
    """Fail closed on execution claims in unverified model output."""
    if result.get('intent') in ('hermes_work','capabilities','skill_run'): return result
    out=dict(result)
    data=dict(out.get('data') or {}) if isinstance(out.get('data'),dict) else {}
    reply=str(out.get('reply') or '')
    # Legacy outputs do not have correlated Bridge receipts. Never bless their IDs.
    suspect_ids=bool(re.search(r'\bNX-[\w-]+|task[ _-]?ids?\s*[:：#]|##?\s*Task[ -]?IDs',reply,re.I))
    own_action=bool(re.search(r'\b(PASS|VERIFIED|erfolgreich\s+(?:erstellt|geschrieben|gelesen|ausgeführt|abgeschlossen)|(?:datei|auftrag)\s+(?:wurde\s+)?(?:erstellt|geschrieben|abgeschlossen)|habe\s+(?:ich\s+)?(?:die\s+)?(?:datei\s+)?(?:erstellt|geschrieben|ausgeführt))\b',reply,re.I))
    fake_exec=bool(re.search(r'\b(?:ergebnis|ausgabe|resultat)\s+des\s+(?:befehls|commands)\b',reply,re.I))
    secret_instruction=bool(re.search(r'niemandem.{0,60}existenz.{0,30}nexus',reply,re.I))
    if suspect_ids or fake_exec or secret_instruction or (out.get('intent') in ('chat',None) and own_action):
        return blocked('UNVERIFIED_MODEL_CLAIM','Die Modellantwort enthielt unbelegte Auftragskennungen, Ausführungsbehauptungen oder Verhaltensvorgaben. Sie wurde nicht als Nachweis übernommen.')
    trace=data.get('tools') or []
    if not trace and re.search(r'\{\s*"name"\s*:\s*"[\w.-]+"\s*,\s*"(?:arguments|parameters|args)"',reply):
        return blocked('FAKE_TOOL_CALL','Die Modellantwort enthielt nur einen als Text ausgegebenen Tool-Aufruf ohne echte Ausfuehrung. Kein Ergebnis vorhanden.')
    try:
        from nexus.crew.direct import result_only as _ro
        out['reply']=_ro(out.get('reply') or '')
        reply=str(out['reply'])
    except Exception: pass
    if trace and data.get('provenance')=='live_tool_result': return out
    # Other legacy actions remain usable but cannot claim full verified completion.
    if own_action and out.get('intent') not in ('chat',None):
        out['reply']='EXECUTED: Werkzeugergebnisse vorhanden; vollständige Auftragsverifikation nicht nachgewiesen.' if trace else 'PLANNED: Kein registrierter Ausführungsnachweis für diese Antwort.'
    data.setdefault('execution_status','EXECUTED' if trace else 'PLANNED')
    data.setdefault('task_ids',[])
    data.setdefault('task_id_note','Keine registrierte Task-ID für diese Antwort.')
    data.setdefault('provenance','legacy_tool_result' if trace else 'model_response_unverified')
    out['data']=data
    return out

