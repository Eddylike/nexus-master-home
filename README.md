# NEXUS Master Home 4D

**Dein lokales KI-Zuhause.** NEXUS Master Home ist eine selbst gehostete Oberfläche, in der mehrere KI-Agenten zusammenarbeiten: komplett auf deinem eigenen Rechner, mit lokalem Sprachmodell, ohne Cloud-Zwang und ohne laufende Kosten.

## Features

- **4D-Dämmerungs-Design**: dunkles Glas-Layout mit warmen Akzenten, gut lesbar, mit ruhigen Animationen.
- **Agenten-Team**
  - **NEXUS**: Zentrale, KI-Hub und Steuerung.
  - **Hermes**: Planung, Ausführung und Werkzeuge.
  - **Aether**: separate Prüfung und Qualitätskontrolle.
  - **NEXUS GPT**: Chat, Analyse, Wissen und Beratung.
- **Live-Hub**: Live-Ereignisstrom aller Agenten, filterbar nach Agent.
- **Terminal**: eingebettetes, einklappbares Live-Terminal über dem Eingabefeld, zeigt die echten Befehlsausgaben der Agenten.
- **Dock und Apps**: Schnellstart für Dateien, Mail, Studio, Medien und mehr.
- **Autobuild**: Backlog-gesteuerte Selbstverbesserung mit Tests, Guards und Rollback.
- **Sicherheitsgrenzen**: Guards, Freigabelisten und Audit, damit nichts unkontrolliert ausgeführt wird.

## Voraussetzungen

- Windows 10/11 (PowerShell) oder Linux
- Python 3.11 oder neuer
- [Ollama](https://ollama.com) lokal installiert
- Modell: `ollama pull qwen3.5:9b`
- Empfohlen: 16 GB RAM, eine GPU mit 8 GB oder mehr

## Installation und Start

```powershell
git clone https://github.com/Eddylike/nexus-master-home.git
cd nexus-master-home
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # Werte anpassen
ollama serve             # falls Ollama noch nicht läuft
.\start_nexus_all.ps1    # oder: python run_server.py
```

Linux: `./start_nexus.sh`, Status mit `./status_nexus.sh`, Stopp mit `./stop_nexus.sh`.

Danach im Browser öffnen: **http://127.0.0.1:8299/**

## Ports

| Dienst | Port |
|---|---|
| NEXUS Master Home (Web und API) | 8299 |
| Ollama | 11434 |

## Tests

```powershell
python -m pytest -q
```

## Sicherheitshinweise

- `python run_server.py` lauscht standardmäßig nur auf `127.0.0.1`. Die Startskripte (`start_nexus.ps1`, `start_nexus.sh`) setzen bewusst `NEXUS_HOST=0.0.0.0`, damit das Handy (`/m`) im LAN/Tailscale zugreifen kann. Gib den Server nicht ungeschützt ins Internet frei.
- POST-Requests von fremden Webseiten werden abgelehnt (CSRF-Schutz über `Origin`/`Sec-Fetch-Site`). Weitere Browser-Origins nur explizit über `NEXUS_CORS_ORIGINS` (kommagetrennt) freigeben.
- Zocker-Modus: Skriptpfad über `NEXUS_ZOCKER_SCRIPT`, Standard ist `zocker_bombe.ps1` im Ordner über dem Repo.
- Geheimnisse wie Tokens, App-Passwörter und Geräteadressen gehören ausschließlich in `.env`. Diese Datei wird nicht committet.
- IP-Adressen im Code sind Platzhalter aus dem Dokumentationsbereich (`192.0.2.x`). Trage deine eigenen Geräte über `.env` bzw. die Konfiguration ein.
- Chatverläufe, Gedächtnisdaten, Logs und Uploads (`workspace/`) sind nicht Teil des Repos und bleiben lokal.
- Große 3D-Avatar- (`.vrm`) und Videodateien sind nicht enthalten. Die Oberfläche funktioniert ohne sie, mit Fallback.

## Lizenz

MIT License

Copyright (c) 2026 Eddylike 

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, 
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.