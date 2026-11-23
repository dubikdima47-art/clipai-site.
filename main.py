from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import uuid4
import httpx
import re

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

def drive_id(url: str):
    parsed = urlparse(url)
    if parsed.hostname not in ("drive.google.com", "www.drive.google.com", "docs.google.com"):
        return None
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)
    return parse_qs(parsed.query).get("id", [None])[0]

@app.get("/")
async def home():
    return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
async def health():
    return {"ok": True, "service": "ClipAI"}

@app.post("/api/inspect")
async def inspect_video(data: InspectRequest):
    url = str(data.url)
    video_id = youtube_id(url)
    if video_id and len(video_id) == 11:
        endpoint = "https://www.youtube.com/oembed"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(endpoint, params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
        if response.status_code != 200:
            raise HTTPException(422, "Не вдалося отримати дані відео. Перевір посилання або доступність відео.")
        info = response.json()
        return {"source": "youtube", "video_id": video_id, "title": info.get("title"), "author": info.get("author_name"), "thumbnail": info.get("thumbnail_url")}
    file_id = drive_id(url)
    if file_id:
        return {"source": "google_drive", "file_id": file_id, "title": "Відео Google Drive", "author": "Google Drive", "thumbnail": None, "message": "Посилання розпізнано. Перевірка доступу та обробка відеофайлу — наступний етап."}
    raise HTTPException(400, "Встав посилання на YouTube або Google Drive.")

@app.post("/api/jobs")
async def create_job(data: JobRequest):
    url = str(data.url)
    if not youtube_id(url) and not drive_id(url):
        raise HTTPException(400, "Потрібне посилання на YouTube або Google Drive.")
    if not 1 <= data.clips <= 20 or not 30 <= data.duration <= 60:
        raise HTTPException(400, "Кількість кліпів: 1–20; тривалість: 30–60 секунд.")
    return {
        "job_id": str(uuid4()),
        "status": "waiting_for_authorized_media",
        "message": "Посилання прийнято. Реальне завантаження, AI-аналіз і монтаж ще не підключені."
    }
