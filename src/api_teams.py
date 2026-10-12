"""The Teams library and the Team page: what each opponent runs, how they defend us, their players.

Routes (beside dash_api's GET/POST /api/teams and PUT /api/teams/{id}, which the pages use for
"Add a team" and the notes)
    GET /api/teams/library?view=opponents|ours|all
        The cards of teams.html and its compare table.
        opponents  the teams we played this season, each built from this season's games against them
        all        every team, built from every game against them
        ours       our own tendencies the same way round, one card per season that has games
        -> {"view", "season": {"id", "name"} or null, "cards": [CARD...],
            "compare": {"columns": [offense play types], "rows": [{"id", "name", "cells": {type: {"share",
            "points_each"}}, "main_defense"}]}}
        CARD (an opponent also has "place", "lat", "lon", "links", "info" and "versus": library.head_to_head without
        its rows, the record over every season's schedule; the reply also has "home", our gym's place for the map):
              {"id", "name", "short", "colour", "link" ("team" or "season"), "games", "record" ({wins, losses,
        ties, text}), "labelled" (possessions with an offense label), "main_defense" / "main_offense"
        ({"label", "share"} or null), "press_share" (% of made baskets followed by a press, or null),
        "points_per_game" (theirs against us; ours for "ours"), "defense_mix" ({"zone", "man", "press"}
        counts of the labelled defenses), "offense" (play_shares rows)}
    GET /api/teams/{team_id}/summary
        library.team_summary(team_id) plus "latest_game" (the stem of the newest game against them, for
        "Label possessions"), "playlists" ({"offense": {type: stem}, "defense": {type: stem}}: the newest
        game with possessions of that type, for the row links), press "when" (the quarter they pressed
        in most) and press "turnover_pct" (how often we turned it over against the press), and a
        "thumbnail_url" on each game when outputs/<stem>/thumbnail.jpg exists.

Every number comes from library.py (team_summary, play_shares, record_of, per_game) and possessions.py
(press_moments). Nothing here writes a file.
"""
from fastapi import APIRouter, HTTPException

import config
import library
import possessions

router = APIRouter(prefix="/api/teams")

VIEWS = ("opponents", "ours", "all")
PERIOD_WORDS = {"1": "1st quarter", "2": "2nd quarter", "3": "3rd quarter", "4": "4th quarter", "OT": "overtime"}

# The defense-mix bar groups the coach's defense names into three families by their words.
MIX_WORDS = {"zone": ("zone",), "man": ("man",), "press": ("press", "trap")}


def mix_family(label):
    """"2-3 zone" -> "zone", "Man-to-man" -> "man", "Half-court trap" -> "press", anything else -> None."""
    words = label.lower()
    for family, hints in MIX_WORDS.items():
        if any(hint in words for hint in hints):
            return family
    return None


def tendencies(rows, side):
    """What one team tends to do over the given games (library.game_row rows), as a card shows it.

    side "them": the opponent's tendencies (what they run, the defense they play against us, how often
    they press after their made baskets). side "us": our own, the same way the other way round.
    Returns {"offense", "defense" (library.play_shares rows), "press" ({pressed, baskets, share, when,
    turnover_pct}), "labelled", "defense_mix"}.
    """
    offense_pairs, defense_pairs, moments = [], [], []
    for row in rows:
        who = library.sides(row["stem"])
        mine, other = (who["their_team"], who["our_team"]) if side == "them" else (who["our_team"], who["their_team"])
        if not mine:
            continue
        records, all_shots = library.possessions_of(row["stem"]), library.shots_of(row["stem"])
        offense_pairs += [(record, all_shots) for record in records if record.get("team") == mine]
        defense_pairs += [(record, all_shots) for record in records if record.get("team") == other]
        if other:
            scorer_shots = [shot for shot in all_shots if shot.get("team") == mine]
            moments += possessions.press_moments(records, scorer_shots, other)
    defense = library.play_shares(defense_pairs, "defense")
    mix = {"zone": 0, "man": 0, "press": 0}
    for row in defense:
        family = mix_family(row["label"])
        if family:
            mix[family] += row["count"]
    return {"offense": library.play_shares(offense_pairs, "offense"), "defense": defense,
            "press": press_summary(moments),
            "labelled": sum(1 for record, _ in offense_pairs if record.get("offense")), "defense_mix": mix}


def press_summary(moments):
    """{pressed, baskets, share, when, turnover_pct} from possessions.press_moments().

    "when" is the quarter most of the presses came in ("4th quarter"), None when they never pressed;
    "turnover_pct" how many of the possessions they pressed we turned over.
    """
    pressed = [moment for moment in moments if moment["pressed"]]
    when = None
    if pressed:
        periods = [moment["period"] for moment in pressed if moment["period"]]
        if periods:
            top = max(set(periods), key=periods.count)
            when = PERIOD_WORDS.get(top, f"period {top}")
    return {"pressed": len(pressed), "baskets": len(moments),
            "share": round(100 * len(pressed) / len(moments)) if moments else None, "when": when,
            "turnover_pct": round(100 * sum(1 for moment in pressed if moment["turnover"]) / len(pressed)) if pressed else None}


def top_share(rows):
    """The play type with the biggest share: {"label", "share"}, or None when nothing is labelled."""
    if not rows:
        return None
    best = max(rows, key=lambda row: (row["share"], row["count"]))
    return {"label": best["label"], "share": best["share"]}


def card(identity, rows, side):
    """One card of the Teams page. identity: {"id", "name", "short", "colour", "link"}."""
    found = tendencies(rows, side)
    return dict(identity, games=len(rows), record=library.record_of(rows), labelled=found["labelled"],
                main_defense=top_share(found["defense"]), main_offense=top_share(found["offense"]),
                press_share=found["press"]["share"],
                points_per_game=library.per_game(rows, "points_them" if side == "them" else "points_us"),
                defense_mix=found["defense_mix"], offense=found["offense"])


def compare_table(cards):
    """The compare table: one column per offense play type anyone has run, one row per card."""
    columns = [label for label in library.load_play_types()["offense"]
               if any(row["label"] == label for each in cards for row in each["offense"])]
    extra = sorted({row["label"] for each in cards for row in each["offense"]} - set(columns))
    rows = []
    for each in cards:
        cells = {row["label"]: {"share": row["share"], "points_each": row["points_each"]} for row in each["offense"]}
        rows.append({"id": each["id"], "name": each["name"], "cells": cells,
                     "main_defense": each["main_defense"]["label"] if each["main_defense"] else None})
    return {"columns": columns + extra, "rows": rows}


def opponent_cards(season_id):
    """A card per opponent: this season's games against them (season_id), or every game (None)."""
    teams = library.load_teams()["teams"]
    by_team = {}
    for game in library.games_in(season_id=season_id):
        if game["info"]["opponent_id"]:
            by_team.setdefault(game["info"]["opponent_id"], []).append(library.game_row(game))
    cards = []
    for team_id, team in sorted(teams.items(), key=lambda item: item[1]["name"].lower()):
        rows = by_team.get(team_id, [])
        if season_id is not None and not rows:
            continue                                  # not played this season
        identity = {"id": team_id, "name": team["name"], "short": team["short"], "colour": team["colour"], "link": "team"}
        versus = library.head_to_head(team_id)
        cards.append(dict(card(identity, rows, "them"), place=team["place"], lat=team["lat"], lon=team["lon"],
                          links=team["links"], info=team["info"], versus=dict(versus, rows=None)))
    return cards


def our_cards():
    """A card per season with games: our own tendencies that season."""
    seasons = library.load_seasons()["seasons"]
    cards = []
    for season_id, season in sorted(seasons.items(), key=lambda item: item[1]["start"], reverse=True):
        rows = [library.game_row(game) for game in library.games_in(season_id=season_id)]
        if not rows:
            continue
        name = season["roster"]["name"] or "Our team"
        identity = {"id": season_id, "name": f"{name} · {season['name']}", "short": library.short_name(name)[:3] or "US",
                    "colour": "#141a24", "link": "season"}
        cards.append(card(identity, rows, "us"))
    return cards


@router.get("/library")
def teams_library(view: str = "opponents"):
    if view not in VIEWS:
        raise HTTPException(400, "The view is Opponents, Our team or All seasons.")
    try:
        if view == "ours":
            cards, season = our_cards(), None
        else:
            season_id = library.current_season_id() if view == "opponents" else None
            cards = opponent_cards(season_id)
            season = {"id": season_id, "name": library.load_seasons()["seasons"][season_id]["name"]} if season_id else None
    except library.Problem as error:
        raise HTTPException(400, str(error))
    return {"view": view, "season": season, "cards": cards, "compare": compare_table(cards), "home": library.load_teams()["home"]}


def playlists(rows, label_key):
    """{play type: stem of the newest game with a possession of that type}, for the row links of the Team page."""
    found = {}
    for row in rows:                                   # oldest first, so the newest game wins
        who = library.sides(row["stem"])
        team = who["their_team"] if label_key == "offense" else who["our_team"]
        for record in library.possessions_of(row["stem"]):
            if record.get("team") == team and record.get(label_key):
                found[library.play_label(label_key, record[label_key])] = row["stem"]
    return found


@router.get("/{team_id}/summary")
def team_summary(team_id: str):
    try:
        data = library.team_summary(team_id)
    except library.Problem as error:
        raise HTTPException(400, str(error))
    rows = data["games"]
    data["latest_game"] = rows[-1]["stem"] if rows else None
    data["playlists"] = {"offense": playlists(rows, "offense"), "defense": playlists(rows, "defense")}
    data["press"].update({key: value for key, value in tendencies(rows, "them")["press"].items() if key in ("when", "turnover_pct")})
    for row in rows:
        picture = config.OUTPUTS_DIR / row["stem"] / "thumbnail.jpg"
        row["thumbnail_url"] = f"/files/outputs/{row['stem']}/thumbnail.jpg" if picture.is_file() else None
    return data
