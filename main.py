from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import uuid4
import httpx

BASE = Path(__file__).parent
app = FastAPI(title="ClipAI")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

class InspectRequest(BaseModel):
    url: HttpUrl

class JobRequest(BaseModel):
    url: HttpUrl
    clips: int = 10
    duration: int = 45

def youtube_id(url: str):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live"):
            return parts[1]
    return None

@app.get("/")
async def home():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
async def health():
    return {"ok": True, "service": "ClipAI"}

@app.post("/api/inspect")
async def inspect_video(data: InspectRequest):
    video_id = youtube_id(str(data.url))
    if not video_id or len(video_id) != 11:
        raise HTTPException(400, "Встав правильне посилання на YouTube.")
    endpoint = "https://www.youtube.com/oembed"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(endpoint, params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
    if response.status_code != 200:
        raise HTTPException(422, "Не вдалося отримати дані відео. Перевір посилання або доступність відео.")
    info = response.json()
    return {"video_id": video_id, "title": info.get("title"), "author": info.get("author_name"), "thumbnail": info.get("thumbnail_url")}

@app.post("/api/jobs")
async def create_job(data: JobRequest):
    if not youtube_id(str(data.url)):
        raise HTTPException(400, "Потрібне посилання на YouTube.")
    if not 1 <= data.clips <= 20 or not 30 <= data.duration <= 60:
        raise HTTPException(400, "Кількість кліпів: 1–20; тривалість: 30–60 секунд.")
    return {
        "job_id": str(uuid4()),
        "status": "waiting_for_authorized_media",
        "message": "Запит збережено. Для реального монтажу потрібне джерело відео, яке користувач має право обробляти. Самого YouTube-посилання недостатньо для отримання відеопотоку."
    }
