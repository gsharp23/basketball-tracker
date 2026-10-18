"""The map's tiles, fetched from OpenStreetMap once and kept on the laptop (web/teams.html).

    GET /api/map/tile/{z}/{x}/{y}.png    the tile, from data/_dashboard/tiles/ when it is there, else fetched from
                                         tile.openstreetmap.org (with this program's name as User-Agent, as their
                                         policy asks) and kept. Zoom levels 6 to 15 only. Offline, a tile that was
                                         never fetched answers 404 and Leaflet leaves that square blank.

The one place in the server that talks to the internet, and only while the Teams map is open.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import config

router = APIRouter(prefix="/api/map")

TILES = config.DATA_DIR / "_dashboard" / "tiles"
USER_AGENT = "HoopsTracker/1.0 (a coach's local dashboard; github.com/gsharp23/basketball-tracker)"
ZOOM = (6, 15)


@router.get("/tile/{z}/{x}/{y}.png")
def tile(z: int, x: int, y: int):
    if not (ZOOM[0] <= z <= ZOOM[1]) or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(404, "No such tile.")
    path = TILES / str(z) / str(x) / f"{y}.png"
    if not path.is_file():
        try:
            import requests
            reply = requests.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png", headers={"User-Agent": USER_AGENT}, timeout=10)
            if not reply.ok or not reply.content:
                raise HTTPException(404, "The map tile could not be fetched.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(reply.content)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(404, "No internet: this part of the map was never fetched.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})
