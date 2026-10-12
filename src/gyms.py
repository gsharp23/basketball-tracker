"""The gym library: the courts the team plays on, measured once and used again.

    data/_library/gyms.json
        {"gyms": {"<id>": {"name": "Home gym", "court": {"length_ft": 84.5, "width_ft": 41, "lane_ft": 10,
                                                              "free_throw_ft": 19, "three_point_ft": 19.75, "preset": "custom"},
                           "team_id": null, "measured": "2026-10-12", "note": ""}}}
The court dict is what court_setup.json's "court" holds, so a gym chosen on the first click page is
copied straight in. A gym is saved from the landmark page ("Save this court as a gym") after its size
was measured from the clicks, or typed. Plain functions over one JSON file; no web code here.
"""
import datetime
import re

import config

FILE = config.DATA_DIR / "_library" / "gyms.json"


def slug(name):
    text = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return text[:50] or "gym"


def load():
    """{"gyms": {id: {"name", "court", "team_id", "measured", "note"}}} (an empty library when there is no file)."""
    saved = config.load_json(FILE) if FILE.exists() else {}
    gyms = saved.get("gyms") if isinstance(saved, dict) and isinstance(saved.get("gyms"), dict) else {}
    clean = {}
    for key, row in gyms.items():
        if not isinstance(row, dict) or not isinstance(row.get("court"), dict):
            continue
        clean[str(key)] = {"name": str(row.get("name") or key), "court": dict(row["court"]),
                           "team_id": row.get("team_id") or None, "measured": str(row.get("measured") or ""),
                           "note": str(row.get("note") or "")[:200]}
    return {"gyms": clean}


def save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    config.save_json(FILE, data, indent=2)


def find(name):
    """The id of the gym with that name (ignoring case), or None."""
    wanted = str(name or "").strip().lower()
    for key, row in load()["gyms"].items():
        if row["name"].lower() == wanted:
            return key
    return None


def save_gym(name, court, team_id=None, note=""):
    """Add a gym, or update the court of the gym with that name. Returns (id, gym)."""
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("Give the gym a name, like Home gym or Riverside High.")
    data = load()
    key = find(name) or slug(name)
    base, n = key, 2
    while key in data["gyms"] and data["gyms"][key]["name"].lower() != name.lower():
        key, n = f"{base}-{n}", n + 1
    data["gyms"][key] = {"name": name, "court": dict(court), "team_id": team_id or data["gyms"].get(key, {}).get("team_id"),
                         "measured": datetime.date.today().isoformat(), "note": str(note or "")[:200]}
    save(data)
    return key, data["gyms"][key]


def court_words(court):
    """"84.5 x 41 ft, lane 10 ft, three-point line 19.75 ft" for a court dict."""
    def feet(value):
        value = float(value)
        return str(int(value)) if value.is_integer() else f"{value:g}"
    words = f"{feet(court['length_ft'])} x {feet(court['width_ft'])} ft"
    if court.get("lane_ft"):
        words += f", lane {feet(court['lane_ft'])} ft"
    if court.get("three_point_ft"):
        words += f", three-point line {feet(court['three_point_ft'])} ft"
    return words
