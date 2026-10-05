"""The "Shots" tab of the dashboard: tagging each shot, the points table, the scoring heat map.

The ball cannot be found in the video, so the coach tags the shots himself:
    1. watch the game in the page and stop it when a shot is taken
    2. press "Shot here": the picture of that moment appears with every player labelled
    3. click the shooter in the picture (or pick him from a list, or type team and number)
    4. press MADE or MISSED
The tool adds where the shot was taken from and whether it was a 2 or a 3. All of
that logic, and the file the shots are kept in, is in shots.py; this file is only the page.

The video shown is the game video itself when the browser can play it, otherwise the
tracked video (see shots.watch_video for why).
"""
import functools

import dash_common
import shots
from dash_common import gr

WORTH = {"Work it out from the position": None, "2 points": 2, "3 points": 3}
KINDS = {"Field goal": "field goal", "Free throw": "free throw"}

# The only JavaScript of the page. When "Shot here" is pressed it stops the video and
# writes the video's current time (in seconds) into the time box; Python then shows that
# moment exactly as if the time had been typed. If it ever fails, the time box is left
# as it was, and typing the time still works.
VIDEO_TIME_JS = """(time) => {
    const player = document.querySelector('#hoops-shots-video video');
    if (!player) { return time; }
    player.pause();
    return String(player.currentTime);
}"""

HOW_TO = ("1. Click the video on the left to play it; click it again to stop. Stop it when a shot is taken, best just "
          "before the shooter leaves the floor. (The bar under the video moves to any time of the game.)\n"
          "2. Press \"Shot here\". The picture of that moment appears on the right, with every player the tool is following in a box.\n"
          "3. Click the shooter in the picture. (Not in a box? Type his team and number instead.)\n"
          "4. Press MADE or MISSED. The tool fills in where the shot was taken from and whether it is a 2 or a 3.\n"
          "The \"Period\" above the video is only written next to each shot in the list: change it when a new period starts.")
STAND_IN = ("PROVISIONAL POSITIONS. The court of this game was marked with stand-in clicks, not by you. Where a shot was "
            "taken from, the 2 or 3 worked out from it, and the scoring heat map are provisional until you do step 2 in "
            "the Court set-up tab yourself, process the game again, and then tag the shots again.")
HONESTY = ("How far to trust this:\n"
           "- A position is an estimate. On the test clip it was within 1 to 2 ft; it is typically 4 to 8 ft off when the "
           "shooter's legs are hidden behind another player. So a 2 or 3 worked out close to the three-point line can be "
           "wrong: such shots are pointed out in the list, and you can always set 2 or 3 yourself.\n"
           "- Players nearest the camera often have no position (their feet are below the picture). A shot with "
           "\"position unknown\" counts in the points table but is not on the scoring heat map.\n"
           "- A percentage from a handful of shots means little.\n"
           "- Everything here is what you tagged. The tool does not find shots by itself.")


def careful(function):
    """The same function, but a problem the coach can put right is shown as a message and nothing on the page changes.

    (Raising gr.Error instead would cover every part of the page the button updates with an "Error" badge.)
    """
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (shots.Problem, SystemExit) as problem:     # SystemExit: config.py's plain "SD card" messages
            gr.Warning(str(problem), duration=20, title="Please check")
            return gr.skip()
        except OSError as problem:                          # a write to the SD card failed (card full, gone or damaged)
            gr.Warning(f"That could not be saved: {problem}\nCheck the SD card and press the button again.",
                       duration=20, title="Not saved")
            return gr.skip()
    return wrapped


# ---------------------------------------------------------------- what the parts of the page show

def results(video):
    """The list of shots, the "shot to change" box, the points table and the "whose map" box."""
    data, teams, court = shots.load_shots(video), shots.load_teams(video), shots.court_size(video)
    pick_shot = [(f"Shot {shot['id']} at {shots.show_time(shot['time_s'])}: "
                  f"{shots.who_text(teams, shot['team'], shot['number'], shot['tag'])}, {'made' if shot['made'] else 'missed'}",
                  shot["id"]) for shot in data["shots"]]
    whose = [(f"{team} team (all its players)", team) for team in teams["labels"]]
    seen = set()
    for shot in data["shots"]:
        key = (shot["team"], shots.player_key(shot))
        if key not in seen:
            seen.add(key)
            whose.append((shots.who_text(teams, shot["team"], shot["number"], shot["tag"]), f"{key[0]}|{key[1]}"))
    return (shots.shot_rows(data, teams, court), gr.Dropdown(choices=pick_shot, value=None),
            shots.points_table(data, teams), gr.Dropdown(choices=whose))


def moment(video, watching, index, chosen=None):
    """One moment of the game: its look number, its time, the labelled picture, and the players in it as a list."""
    moments, teams = shots.load_moments(video), shots.load_teams(video)
    players = shots.players_at(video, index)
    choices = []
    for player in players:
        if player["team"] is None:
            text = f"no team found (tag {player['tag']})"
        else:
            text = shots.who_text(teams, player["team"], player["number"], player["tag"])
        choices.append((text, player["tag"]))
    if chosen not in [player["tag"] for player in players]:
        chosen = None
    return (index, shots.show_time(shots.look_time(moments, index, watching)),
            shots.moment_picture(video, index, chosen), gr.Dropdown(choices=choices, value=chosen))


# ---------------------------------------------------------------- what the buttons do

def open_game(video):
    """Fill the tab for the chosen game. Called when the page opens and when another game is picked."""
    empty = (None, None, None, "0:00.0", None, gr.Dropdown(choices=[], value=None), gr.Radio(choices=[], value=None), "",
             "1", "", [], gr.Dropdown(choices=[], value=None), [], gr.Dropdown(choices=[], value=None),
             gr.Radio(choices=[], value=None))
    if not video:
        return (dash_common.note("There are no videos in the clips folder yet."),) + empty
    try:
        teams = shots.load_teams(video)
        path, watching = shots.watch_video(video)
        status = dash_common.note(HOW_TO)
        if not (shots.game_folder(video) / "positions.json").exists():
            status += dash_common.warning(
                "The court positions of this game have not been worked out yet (mark the court on the Court set-up tab, "
                "then carry on processing on the Games tab). Shots can be tagged now, but each one will be "
                "\"position unknown\": you choose 2 or 3 points yourself, and it will not be on the scoring heat map.")
        elif dash_common.stand_in_court(video):
            status += dash_common.warning(STAND_IN)
        if watching == "tracked":
            status += dash_common.note("The browser cannot play this game's video file, so the tracked video is shown instead. "
                                       "The small number above each box there is a player tag, NOT a jersey number.")
        team_choice = gr.Radio(choices=teams["labels"], value=None)
        shot_rows, pick_shot, points, whose = results(video)
        return (status, str(path), watching, None, "0:00.0", None, gr.Dropdown(choices=[], value=None), team_choice, "",
                "1", "", shot_rows, pick_shot, points,
                gr.Dropdown(choices=whose.choices, value=teams["labels"][0]), team_choice)
    except (shots.Problem, SystemExit) as problem:
        return (dash_common.warning(str(problem)),) + empty


@careful
def go_to(video, watching, time_text, chosen):
    """Show the moment at the time in the time box (typed, or written there by VIDEO_TIME_JS)."""
    index = shots.look_at(shots.load_moments(video), shots.parse_time(time_text), watching)
    return moment(video, watching, index, chosen)


@careful
def step(seconds, video, watching, shown, chosen):
    """Move the picture back or forward by a time (the smallest step is one look, about 0.1 s)."""
    moments = shots.load_moments(video)
    looks = round(seconds * moments["fps"] / moments["stride"]) or (1 if seconds > 0 else -1)
    return moment(video, watching, min(max((shown or 0) + looks, 0), len(moments["frames"]) - 1), chosen)


def need_moment(shown):
    if shown is None:
        raise shots.Problem("First stop the video at the shot and press \"Shot here\".")


def picked(video, shown, tag):
    """What to show once a player is picked: his team, his number, the picture with him marked, and what the tool knows."""
    need_moment(shown)
    player = next((each for each in shots.players_at(video, shown) if each["tag"] == tag), None)
    if player is None:
        raise shots.Problem("That player is not in this picture. Click a box, or type the team and number.")
    teams = shots.load_teams(video)
    if player["team"] is None:
        text = (f"Picked: player tag {tag}. The tool could not tell his team: choose it, and type his number if you can read it.")
    else:
        text = "Picked: " + shots.who_text(teams, player["team"], player["number"], tag) + ". "
        if player["number"] is None:
            text += "His number was not read: type it if you can read it. "
        found = shots.find_position(shots.load_moments(video), teams, shown, tag, player["team"], player["number"])
        if found is None:
            text += "No position for him at this moment: for a field goal, choose 2 points or 3 points yourself."
        else:
            spot = {"x_ft": found[0], "y_ft": found[1], "worth_from": "position"}
            court = shots.court_size(video)
            text += f"A field goal from here counts {shots.judge(spot, court)['worth']}: {shots.position_text(spot, court)}."
    return (tag, gr.Radio(value=player["team"]), player["number"] or "", shots.moment_picture(video, shown, tag),
            dash_common.note(text))


@careful
def pick_in_picture(video, shown, click: gr.SelectData):
    """A click in the picture: pick the player whose box was clicked."""
    need_moment(shown)
    tag = shots.tag_at_point(shots.players_at(video, shown), click.index[0], click.index[1])
    if tag is None:
        raise shots.Problem("That click was not inside a player's box. Click inside a box. "
                            "If the shooter has no box, type his team and number instead.")
    return picked(video, shown, tag)


@careful
def pick_in_list(video, shown, tag):
    if tag is None:
        return gr.skip(), gr.skip(), gr.skip(), shots.moment_picture(video, shown) if shown is not None else None, ""
    return picked(video, shown, tag)


@careful
def save(made, video, shown, tag, team, number, kind, worth, period):
    """MADE or MISSED was pressed: record the shot, clear the form for the next one, refresh the tables."""
    need_moment(shown)
    shot = shots.add_shot(video, shown, team, number, made, KINDS[kind], WORTH[worth], period, tag)
    text = "Saved. " + shots.shot_text(shot, shots.load_teams(video), shots.court_size(video))
    text += "\nWrong? Press \"Undo the last change\" below, or change it in the list."
    text += "\nFor the next shot, play the video on and press \"Shot here\" again."
    # the picture and the form are emptied, so that the next shot cannot be saved on this moment by mistake
    return (dash_common.note(text), None, None, gr.Dropdown(choices=[], value=None), gr.Radio(value=None), "",
            "Field goal", list(WORTH)[0]) + results(video)


@careful
def load_edit(video, shot_id):
    """Fill the "change a shot" boxes with what is recorded for the shot picked."""
    if shot_id is None:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
    shot = shots.find_shot(shots.load_shots(video), shot_id)
    worth = list(WORTH)[0] if shot["worth_from"] != "coach" else f"{shot['worth']} points"
    return ("Made" if shot["made"] else "Missed", "Free throw" if shot["kind"] == "free throw" else "Field goal", worth,
            shot["period"], shot["team"], shot["number"] or "")


@careful
def save_edit(video, shot_id, result, kind, worth, period, team, number):
    if shot_id is None or result not in ("Made", "Missed"):
        raise shots.Problem("First pick the shot to change, under \"Which shot\".")
    shot = shots.edit_shot(video, shot_id, {"made": result == "Made", "kind": KINDS.get(kind), "worth": WORTH.get(worth),
                                            "period": period, "team": team, "number": number})
    text = "Changed. " + shots.shot_text(shot, shots.load_teams(video), shots.court_size(video))
    return (dash_common.note(text),) + results(video)


@careful
def delete(video, shot_id):
    if shot_id is None:
        raise shots.Problem("First pick the shot to delete.")
    shots.delete_shot(video, shot_id)
    return (dash_common.note(f"Deleted shot {shot_id}. To get it back, press \"Undo the last change\"."),) + results(video)


@careful
def undo(video):
    shots.undo(video)
    return (dash_common.note("The last change was taken back. (Press Undo again to put it back.)"),) + results(video)


def show_map(video, whose):
    """Draw the scoring heat map for a team or a player. The two team pictures are always saved on the SD card too."""
    try:
        data, teams, court = shots.load_shots(video), shots.load_teams(video), shots.court_size(video)
        paths = {team: shots.heat_map_path(video, team) for team in teams["labels"]}
        if not data["shots"] and not any(path.exists() for path in paths.values()):
            return None         # nothing tagged and nothing saved earlier: nothing to draw
        pictures = {team: shots.draw_heat_map(data, court, team, path=paths[team]) for team in teams["labels"]}
        team, _, player = (whose or "").partition("|")
        if team not in pictures:
            return None
        if not player:
            return pictures[team]
        return shots.draw_heat_map(data, court, team, player, teams["names"].get((team, player), ""))
    except (shots.Problem, SystemExit):
        return None             # open_game has already said what is wrong with this game


# ---------------------------------------------------------------- the page

def build(game):
    """Add the "Shots" tab to the "View the stats" page. dashboard.py calls this once, inside its gr.Tabs().

    game is the "Game" box at the top of the page; its value is the chosen video
    ("clips/game1.mp4") or None. Its "change" event fills this tab.
    """
    with gr.Tab("Shots"):
        status = gr.HTML()
        watching = gr.State()       # "game" or "tracked": which video is in the player (see shots.watch_video)
        shown = gr.State()          # the look (moment) the picture shows; this is what a saved shot gets

        with gr.Row():
            with gr.Column():
                period = gr.Radio(shots.PERIODS, value="1", label="Period being played (written next to each shot you tag)")
                video = gr.Video(label="The game: click it to play, click again to stop", interactive=False,
                                 elem_id="hoops-shots-video", height=380, buttons=[])
                here = gr.Button("Shot here: show this moment", variant="primary", elem_classes="hoops-big")
                with gr.Row():
                    time_box = gr.Textbox("0:00.0", label="Time in the video (minutes:seconds)", scale=2)
                    show = gr.Button("Show this time", scale=1)
                with gr.Row():
                    back_1 = gr.Button("1 s back", min_width=80)
                    back_01 = gr.Button("0.1 s back", min_width=80)
                    on_01 = gr.Button("0.1 s on", min_width=80)
                    on_1 = gr.Button("1 s on", min_width=80)
            with gr.Column():
                picture = gr.Image(None, label="The moment of the shot: click the shooter", interactive=False,
                                   format="jpeg", buttons=["fullscreen"])
                shooter = gr.Dropdown(label="... or pick the shooter from the players in the picture", choices=[], value=None)
                with gr.Row():
                    team = gr.Radio(choices=[], label="Shooter's team")
                    number = gr.Textbox(label="Jersey number", placeholder="for example 5")
                with gr.Row():
                    kind = gr.Radio(list(KINDS), value="Field goal", label="Kind of shot")
                    worth = gr.Radio(list(WORTH), value=list(WORTH)[0], label="A field goal counts")
                with gr.Row():
                    made = gr.Button("MADE", variant="primary", elem_classes="hoops-big")
                    missed = gr.Button("MISSED", elem_classes="hoops-big")
                message = gr.HTML()

        gr.Markdown("### The shots tagged so far (in game order; scroll inside the list to see them all)")
        shot_table = gr.Dataframe(headers=shots.SHOT_HEADERS, value=[], interactive=False, wrap=True, max_height=420)
        undo_button = gr.Button("Undo the last change")
        with gr.Accordion("Change or delete a shot", open=False):
            pick_shot = gr.Dropdown(label="Which shot", choices=[], value=None)
            with gr.Row():
                edit_result = gr.Radio(["Made", "Missed"], label="Result")
                edit_kind = gr.Radio(list(KINDS), label="Kind of shot")
                edit_worth = gr.Radio(list(WORTH), label="A field goal counts")
            with gr.Row():
                edit_period = gr.Radio(shots.PERIODS, label="Period")
                edit_team = gr.Radio(choices=[], label="Shooter's team")
                edit_number = gr.Textbox(label="Jersey number")
            with gr.Row():
                edit_save = gr.Button("Save the changes", variant="primary")
                edit_delete = gr.Button("Delete this shot")
            gr.HTML(dash_common.note("The moment and the player clicked in the picture cannot be changed here: "
                                     "delete the shot and tag it again."))

        gr.Markdown("### Points")
        points = gr.Dataframe(headers=shots.POINTS_HEADERS, value=[], interactive=False, max_height=1200)
        gr.HTML(dash_common.note("3/5 means 3 made of 5 tried. A shooter whose number is not known is listed by player tag, "
                                 "so one real player can be on several lines: type the number when you tag him."))

        gr.Markdown("### Scoring heat map: where the points came from")
        with gr.Row():
            # allow_custom_value: a player whose last shot was deleted must not make the box an error
            whose = gr.Dropdown(label="Whose shots (the map appears once a shot is tagged)", choices=[], value=None,
                                allow_custom_value=True, scale=1)
            # no label: the picture has its own title. No "share" button: nothing is uploaded.
            heat_map = gr.Image(None, label="Scoring heat map", show_label=False, interactive=False, format="png", scale=2,
                                buttons=["download", "fullscreen"])
        gr.HTML(dash_common.note(HONESTY))

        # ---- the events
        tables = [shot_table, pick_shot, points, whose]
        the_moment = [shown, time_box, picture, shooter]
        the_pick = [shooter, team, number, picture, message]
        game.change(open_game, game, [status, video, watching, shown, time_box, picture, shooter, team, number, period,
                                      message] + tables + [edit_team]).then(show_map, [game, whose], heat_map)

        here.click(None, time_box, time_box, js=VIDEO_TIME_JS).then(go_to, [game, watching, time_box, shooter], the_moment)
        show.click(go_to, [game, watching, time_box, shooter], the_moment)
        time_box.submit(go_to, [game, watching, time_box, shooter], the_moment)
        for button, seconds in ((back_1, -1), (back_01, -0.1), (on_01, 0.1), (on_1, 1)):
            button.click(functools.partial(step, seconds), [game, watching, shown, shooter], the_moment)

        picture.select(pick_in_picture, [game, shown], the_pick)
        shooter.input(pick_in_list, [game, shown, shooter], the_pick)

        form = [game, shown, shooter, team, number, kind, worth, period]
        after_save = [message, shown, picture, shooter, team, number, kind, worth] + tables
        made.click(functools.partial(save, True), form, after_save).then(show_map, [game, whose], heat_map)
        missed.click(functools.partial(save, False), form, after_save).then(show_map, [game, whose], heat_map)

        edit_boxes = [edit_result, edit_kind, edit_worth, edit_period, edit_team, edit_number]
        pick_shot.input(load_edit, [game, pick_shot], edit_boxes)
        edit_save.click(save_edit, [game, pick_shot] + edit_boxes, [message] + tables).then(show_map, [game, whose], heat_map)
        edit_delete.click(delete, [game, pick_shot], [message] + tables).then(show_map, [game, whose], heat_map)
        undo_button.click(undo, game, [message] + tables).then(show_map, [game, whose], heat_map)
        whose.input(show_map, [game, whose], heat_map)
