"""HAUS5T - Sicherheitsstufen fuer Nachrichten des Menschen.

SIMULATION/Reflexionsspiel, keine Therapie. Die Erkennung ersetzt keine
medizinische Einschaetzung; bei akuter Gefahr wird das Spiel angehalten und
auf professionelle menschliche Hilfe verwiesen.
"""
from __future__ import annotations

import re

_CRISIS = re.compile(
    r"(suizid|selbstmord|bring(e)?\s+mich\s+um|mich\s+umbringen|will\s+nicht\s+mehr\s+leben|"
    r"nicht\s+mehr\s+leben\s+wollen|leben\s+beenden|beende\s+mein\s+leben|"
    r"selbstverletz|ritze(n)?\b|aufschneiden|erhaengen|erhängen|erschiessen|erschießen|"
    r"von\s+der\s+bruecke|von\s+der\s+brücke|springen\s+wollen|"
    r"moechte\s+sterben|möchte\s+sterben|will\s+sterben|sterben\s+wollen)",
    re.I,
)
_ELEVATED = re.compile(
    r"(depress|hoffnungslos|ausweglos|einsam|sehr\s+allein|ueberfordert|überfordert|"
    r"keine\s+kraft|kann\s+nicht\s+mehr|panik|angstattacke|es\s+geht\s+mir\s+schlecht|"
    r"ich\s+weine|traurig|verzweifelt|leer\s+im\s+inneren)",
    re.I,
)
_CRISIS_MESSAGE = (
    "Ich halte hier an. Was du beschreibst, klingt nach einer akuten Belastung, "
    "und dieses Spiel ist dafuer nicht der richtige Ort. Bitte wende dich jetzt an "
    "einen Menschen: Telefonseelsorge 0800 111 0 111 (kostenlos, rund um die Uhr) "
    "oder 0800 111 0 222. Bei unmittelbarer Gefahr ruf bitte 112 an. "
    "Du bist nicht allein; professionelle Hilfe ist erreichbar."
)


def distress_level(text: str) -> str:
    """'none' | 'elevated' | 'crisis' fuer Nachrichten des MENSCHEN."""
    value = str(text or "")
    if _CRISIS.search(value):
        return "crisis"
    if _ELEVATED.search(value):
        return "elevated"
    return "none"


def safety_action(level: str) -> dict:
    """none -> weiter; elevated -> nicht vertiefen, Pause anbieten; crisis -> anhalten."""
    if level == "none":
        return {"continue": True}
    if level == "elevated":
        return {"continue": True, "deepen": False, "offer_pause": True}
    if level == "crisis":
        return {
            "continue": False,
            "pause": True,
            "refer_human_help": True,
            "message": _CRISIS_MESSAGE,
        }
    raise ValueError("unbekannte Sicherheitsstufe: " + str(level))
