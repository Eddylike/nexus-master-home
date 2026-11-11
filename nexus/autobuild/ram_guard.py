import os
import time
import json
import logging
from pathlib import Path
from datetime import datetime

try:
    import psutil
except ImportError:
    raise SystemExit("FEHLER: psutil fehlt. Bitte erstelle keine Änderungen.")

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "workspace" / "reports"
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE_DIR / "ram_guard_status.json"
LOG_FILE = STATE_DIR / "ram_guard.log"

PAUSE_AT = 92
RESUME_AT = 85
CHECK_INTERVAL = 15
MAX_WAIT = 1800

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

def ram_percent():
    return psutil.virtual_memory().percent

def save_state(paused, ram, reason):
    data = {
        "paused": paused,
        "ram_percent": ram,
        "pause_threshold": PAUSE_AT,
        "resume_threshold": RESUME_AT,
        "reason": reason,
        "timestamp": datetime.now().isoformat()
    }
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    os.replace(temp, STATE_FILE)

def set_autobuild_pause(paused):
    # Ausschließlich vorhandene, bekannte NEXUS-Schnittstellen verwenden.
    try:
        from nexus.autobuild.loop import Autobuild
        engine = Autobuild()

        for name in ("set_paused", "pause", "resume"):
            method = getattr(engine, name, None)
            if callable(method):
                if name == "set_paused":
                    method(paused)
                    return True
                if paused and name == "pause":
                    method()
                    return True
                if not paused and name == "resume":
                    method()
                    return True

    except Exception as exc:
        logging.warning("Autobuild-API nicht verfügbar: %s", exc)

    return False

def main():
    paused = False
    pause_started = None
    logging.info("RAM-Guard gestartet")

    while True:
        ram = ram_percent()

        if ram >= PAUSE_AT and not paused:
            paused = True
            pause_started = time.monotonic()
            api_ok = set_autobuild_pause(True)

            save_state(
                True, ram,
                "RAM-Schutz aktiviert; API-Pause: " + str(api_ok)
            )
            logging.warning("Autobuild pausiert bei %s%% RAM", ram)

        elif paused:
            elapsed = time.monotonic() - pause_started

            if ram <= RESUME_AT and elapsed <= MAX_WAIT:
                api_ok = set_autobuild_pause(False)

                if api_ok:
                    paused = False
                    pause_started = None
                    save_state(False, ram, "RAM wieder ausreichend")
                    logging.info("Autobuild fortgesetzt bei %s%% RAM", ram)

            elif elapsed > MAX_WAIT:
                save_state(
                    True, ram,
                    "Zeitlimit erreicht; manuelle Prüfung erforderlich"
                )
                logging.error("Recovery-Zeitlimit erreicht")
                break

        else:
            save_state(False, ram, "Normalbetrieb")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
