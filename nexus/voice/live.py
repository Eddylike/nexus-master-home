"""Echte Live-Daten für Sprachgespräche (ohne Konto/Schlüssel), mit Quellenangabe.

Wetter: Open-Meteo (geocoding-api.open-meteo.com + api.open-meteo.com). Übertragen wird nur der
genannte Ortsname bzw. dessen Koordinaten. Nicht gefundene Orte werden ehrlich gemeldet, nie geraten.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8

WEATHER_Q = re.compile(r"\b(wetter|temperatur|regnet|regen|schnee|schneit|wie warm|wie kalt|grad drau(?:ß|ss)en|sonnig)\b", re.I)
PLACE = re.compile(r"\b(?:in|im|für|fuer|am|bei|von|aus)\s+(?:der\s+|dem\s+|den\s+)?((?:[A-ZÄÖÜ][\wäöüß.-]+)(?:\s+(?:an der|am|im|bei)?\s*[A-ZÄÖÜ][\wäöüß.-]+)*)")

WMO = {0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "neblig", 48: "neblig mit Reif",
       51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen", 56: "gefrierender Niesel",
       57: "gefrierender Niesel", 61: "leichter Regen", 63: "Regen", 65: "starker Regen", 66: "gefrierender Regen",
       67: "gefrierender Regen", 71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall",
       77: "Schneegriesel", 80: "leichte Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
       85: "Schneeschauer", 86: "starke Schneeschauer", 95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel"}


STATES = ("Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", "Hamburg", "Hessen",
          "Mecklenburg-Vorpommern", "Niedersachsen", "Nordrhein-Westfalen", "Rheinland-Pfalz", "Saarland",
          "Sachsen-Anhalt", "Sachsen", "Schleswig-Holstein", "Thüringen")


def is_weather(text: str) -> bool:
    return bool(WEATHER_Q.search(str(text or "")))


def _fold(s: str) -> str:
    s = str(s or "").lower().replace("th", "t").replace("h", "")
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z]", "", s)


def state_in(text: str) -> str | None:
    """Bundesland auch bei verhörter Schreibweise ("Türingen", "Turingen")."""
    words = re.findall(r"[\wäöüß]+(?:-[\wäöüß]+)*", str(text or ""))
    for st in STATES:
        f = _fold(st)
        if any(_fold(w) == f for w in words) or any(_fold(w).endswith(f) and len(_fold(w)) > len(f) for w in words):
            return st
    return None


def place_of(text: str) -> str | None:
    text = str(text or "")
    st = state_in(text)
    if st:
        # "Friedersdorf-Türingen" / "Friedersdorf in Türingen": Bundesland abtrennen
        for w in re.findall(r"[\wäöüß]+(?:-[\wäöüß]+)+", text):
            head, tail = w.rsplit("-", 1)
            if _fold(tail) == _fold(st):
                text = text.replace(w, head)
    m = PLACE.search(text)
    if not m:
        return None
    place = re.sub(r"[.?!,]+$", "", m.group(1)).strip()
    words = place.split()
    while len(words) > 1 and (words[-1] in ("in", "im") or (st and _fold(words[-1]) == _fold(st))):
        words.pop()
    place = " ".join(words)
    return place if 2 <= len(place) <= 60 else None


def _get(url: str, params: dict) -> dict:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "NEXUS/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _variants(place: str) -> list[str]:
    out = [place]
    # Spracherkennung verliert Umlaute ("Turinger" -> "Thüringer"): einfache Rückabbildung.
    for a, b in (("u", "ü"), ("a", "ä"), ("o", "ö"), ("Tu", "Thü")):
        if a in place:
            out.append(place.replace(a, b, 1))
    words = [w for w in place.split() if len(w) >= 4]
    if len(words) > 1:
        out += words
    return list(dict.fromkeys(out))


def geocode(place: str, hint: str = "") -> dict | None:
    want = state_in(hint)
    for name in _variants(place):
        try:
            res = (_get(GEO, {"name": name, "count": 5, "language": "de"}) or {}).get("results") or []
        except Exception:
            continue
        if res:
            de = [r for r in res if r.get("country_code") == "DE"]
            state = [r for r in de if want and r.get("admin1") == want]
            return (state or de or res)[0]
    return None


def day_offset(text: str) -> tuple[int, str]:
    """0 = heute, 1 = morgen, 2 = uebermorgen (nur aus dem Wortlaut, nichts geraten)."""
    t = str(text or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    if re.search(r"\buebermorgen\b", t):
        return 2, "Übermorgen"
    if re.search(r"\bmorgen\b", t):
        return 1, "Morgen"
    return 0, "Heute"


def weather(text: str, default_place: str | None = None) -> dict:
    place = place_of(text) or default_place
    if not place:
        return {"success": True, "spoken": "Für welchen Ort möchtest du das Wetter wissen?", "needs": "place",
                "source": None}
    try:
        geo = geocode(place, str(text or ""))
    except Exception as exc:
        return {"success": False, "error": "GEOCODE_" + type(exc).__name__,
                "spoken": "Ich komme gerade nicht an den Wetterdienst."}
    if not geo:
        return {"success": True, "place": place, "found": False, "source": "open-meteo geocoding",
                "spoken": f"Den Ort {place} finde ich beim Wetterdienst nicht. Nenn mir bitte eine Stadt in der Nähe."}
    try:
        offset, label = day_offset(text)
        days = max(1, offset + 1)
        w = _get(FORECAST, {"latitude": geo["latitude"], "longitude": geo["longitude"], "timezone": "auto",
                            "forecast_days": days,
                            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
                            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
    except Exception as exc:
        return {"success": False, "error": "FORECAST_" + type(exc).__name__,
                "spoken": "Ich komme gerade nicht an den Wetterdienst."}
    cur, day = w.get("current") or {}, w.get("daily") or {}

    def nth(k, i=0):
        v = day.get(k) or []
        return v[i] if i < len(v) else None

    def first(k):
        return nth(k, 0)

    name = geo.get("name") + (f" ({geo['admin1']})" if geo.get("admin1") and geo.get("country_code") == "DE" else "")
    if offset > 0:
        tmax, tmin = nth("temperature_2m_max", offset), nth("temperature_2m_min", offset)
        code, precip = nth("weather_code", offset), nth("precipitation_probability_max", offset)
        if tmax is None:
            spoken = f"Fuer {label.lower()} habe ich in {name} gerade keine Prognose bekommen."
        else:
            desc = WMO.get(code, "unbekannte Wetterlage")
            spoken = f"{label} wird es in {name} {desc}, zwischen {round(tmin) if tmin is not None else '?'} und {round(tmax)} Grad"
            if precip is not None:
                spoken += f", Regenrisiko {precip} Prozent"
            spoken += "."
        return {"success": True, "found": True, "place": name, "spoken": spoken, "source": "open-meteo.com",
                "day": label, "offset": offset,
                "data": {"tmax": tmax, "tmin": tmin, "code": code, "precip": precip,
                         "lat": geo["latitude"], "lon": geo["longitude"]}}
    desc = WMO.get(cur.get("weather_code"), "unbekannte Wetterlage")
    tcur, feels = cur.get("temperature_2m"), cur.get("apparent_temperature")
    parts = [f"In {name} ist es gerade {desc} bei {round(tcur)} Grad"
             + (f", gefuehlt {round(feels)}" if feels is not None and abs(feels - tcur) >= 2 else "") + "."]
    if first("temperature_2m_max") is not None:
        parts.append(f"Heute zwischen {round(first('temperature_2m_min'))} und {round(first('temperature_2m_max'))} Grad"
                     + (f", Regenrisiko {first('precipitation_probability_max')} Prozent" if first("precipitation_probability_max") is not None else "") + ".")
    return {"success": True, "found": True, "place": name, "spoken": " ".join(parts), "source": "open-meteo.com",
            "day": label, "offset": 0,
            "data": {"time": cur.get("time"), "temperature": tcur, "apparent": feels, "code": cur.get("weather_code"),
                     "wind_kmh": cur.get("wind_speed_10m"), "lat": geo["latitude"], "lon": geo["longitude"]}}
