from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from uuid import uuid4
import httpx, re, subprocess, shutil, zipfile, tempfile, os

BASE = Path(__file__).parent
WORK = Path("/tmp/clipai_jobs")
WORK.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 300 * 1024 * 1024
jobs = {}
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
    return {"ok": True, "service": "ClipAI", "ffmpeg": shutil.which("ffmpeg") is not None}

@app.post("/api/inspect")
async def inspect_video(data: InspectRequest):
    url = str(data.url)
    video_id = youtube_id(url)
    if video_id and len(video_id) == 11:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
        if response.status_code != 200:
            raise HTTPException(422, "Не вдалося отримати дані відео. Перевір посилання або доступність відео.")
        info = response.json()
        return {"source": "youtube", "video_id": video_id, "title": info.get("title"), "author": info.get("author_name"), "thumbnail": info.get("thumbnail_url")}
    file_id = drive_id(url)
    if file_id:
        return {"source": "google_drive", "file_id": file_id, "title": "Відео Google Drive", "author": "Google Drive", "thumbnail": None, "message": "Посилання розпізнано. Для обробки завантаж відеофайл, на який маєш дозвіл."}
    raise HTTPException(400, "Встав посилання на YouTube або Google Drive.")

def process_video(job_id: str, source: str, clips: int, duration: int):
    folder = WORK / job_id
    output = folder / "clips"
    output.mkdir(parents=True, exist_ok=True)
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(source)], capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            raise RuntimeError("Не вдалося прочитати відеофайл.")
        total = float(probe.stdout.strip())
        if total < duration:
            raise RuntimeError(f"Відео коротше за обрану тривалість ({duration} с).")
        actual = min(clips, max(1, int(total // duration)))
        # Select evenly spaced windows across the full video. This is an initial
        # automatic cutter, not semantic/AI highlight detection.
        starts = [((total - duration) * i / max(1, actual - 1)) for i in range(actual)]
        for i, start in enumerate(starts, 1):
            target = output / f"clip_{i:02d}.mp4"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(source),
                   "-t", str(duration), "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", str(target)]
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if run.returncode != 0:
                raise RuntimeError("Помилка FFmpeg під час створення кліпу.")
            jobs[job_id]["progress"] = round(i / actual * 100)
        archive = folder / "ClipAI_clips.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for item in sorted(output.glob("*.mp4")):
                z.write(item, item.name)
        jobs[job_id].update(status="done", progress=100, clips=actual, download=f"/api/jobs/{job_id}/download")
    except Exception as exc:
        jobs[job_id].update(status="error", error=str(exc))
    finally:
        try:
            os.remove(source)
        except OSError:
            pass

@app.post("/api/upload")
async def upload_video(background_tasks: BackgroundTasks, file: UploadFile = File(...), clips: int = Form(10), duration: int = Form(45)):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise HTTPException(503, "На сервері ще не встановлено FFmpeg. Онови сервіс після завершення деплою.")
    if not 1 <= clips <= 20 or not 30 <= duration <= 60:
        raise HTTPException(400, "Кількість кліпів: 1–20; тривалість: 30–60 секунд.")
    allowed = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, "Підтримуються MP4, MOV, MKV, WEBM та AVI.")
    job_id = str(uuid4())
    folder = WORK / job_id
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / ("source" + suffix)
    size = 0
    try:
        with source.open("wb") as dest:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "Максимальний розмір відео — 300 МБ.")
                dest.write(chunk)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()
    jobs[job_id] = {"status": "processing", "progress": 0}
    background_tasks.add_task(process_video, job_id, str(source), clips, duration)
    return {"job_id": job_id, "status": "processing", "message": "Відео завантажено. Створюємо вертикальні кліпи."}

@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Завдання не знайдено. Можливо, сервер перезапустився.")
    return jobs[job_id]

@app.get("/api/jobs/{job_id}/download")
async def download_clips(job_id: str):
    archive = WORK / job_id / "ClipAI_clips.zip"
    if job_id not in jobs or jobs[job_id].get("status") != "done" or not archive.exists():
        raise HTTPException(404, "Архів ще не готовий.")
    return FileResponse(archive, filename="ClipAI_clips.zip", media_type="application/zip")
