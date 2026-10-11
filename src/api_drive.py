"""The Google Drive routes: "Run on Colab" and bringing the results back (used by season.html, games.html, settings.html).

    GET  /api/drive                  drive_link.summary(): connected, folder, what is in the inbox and outbox, the model
                                     in Drive, Colab's last log line, the last import, every game on Colab with its status
    POST /api/drive/check            look at the folder again (the same answer as GET; nothing is cached, so the
                                     button is a plain re-read)
    POST /api/drive/send/{stem}      copy the game into the inbox (a thread; GET status shows how far and the time left)
                                     -> {"message", "copying", "files", "court_sent"}; 409 "not connected" when Drive is
                                     not connected, 400 with plain words otherwise
    GET  /api/drive/status/{stem}    drive_link.status(): {"connected", "state", "words", "message", "progress"
                                     {"how_far", "left", "stage_words"...}, "can_import", ...}; when Drive is not
                                     connected it answers 200 with "connected": false and the reason
    POST /api/drive/import/{stem}    bring the results back now -> {"state", "copied", "kept", "deleted_video", "message"};
                                     400 while Colab is not done

The results also come back by themselves: drive_link.start_watching() runs a thread inside the dashboard
(started when the dashboard starts) that looks in the outbox every minute and imports what Colab finished.
The logic is all in drive_link.py; nothing here touches the files.
"""
from fastapi import APIRouter, HTTPException

import drive_link
import library

router = APIRouter(prefix="/api/drive")
router.add_event_handler("startup", drive_link.start_watching)


def problem(error):
    """A library.Problem as the browser gets it: "not connected" is a 409 (the pages look for those words), the rest 400."""
    text = str(error)
    return HTTPException(409 if "not connected" in text else 400, text)


@router.get("")
def drive():
    return drive_link.summary()


@router.post("/check")
def check():
    reply = drive_link.summary()
    reply["checked"] = True
    return reply


@router.post("/send/{stem}")
def send(stem: str):
    try:
        return drive_link.send_game(stem)
    except library.Problem as error:
        raise problem(error)
    except (OSError, SystemExit) as error:
        raise HTTPException(400, f"The game could not be sent: {error}")


@router.get("/status/{stem}")
def status(stem: str):
    try:
        return drive_link.status(stem)
    except library.Problem as error:
        raise problem(error)


@router.post("/import/{stem}")
def bring_back(stem: str):
    try:
        return drive_link.import_results(stem)
    except library.Problem as error:
        raise problem(error)
    except (OSError, SystemExit) as error:
        raise HTTPException(400, f"The results could not be brought back: {error}")
