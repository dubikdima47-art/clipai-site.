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

WAYIN_API_BASE = "https://wayinvideo-api.wayin.ai/api/v2"

@app.post("/api/youtube/jobs")
async def create_youtube_job(data: JobRequest):
    api_key = os.getenv("WAYINVIDEO_API_KEY")
    if not api_key:
        raise HTTPException(503, "Потрібно додати WAYINVIDEO_API_KEY у Render → Environment.")
    url = str(data.url)
    video_id = youtube_id(url)
    if not video_id or len(video_id) != 11:
        raise HTTPException(400, "Встав посилання саме на одне YouTube-відео.")
    if not 1 <= data.clips <= 10 or not 30 <= data.duration <= 60:
        raise HTTPException(400, "Кількість кліпів: 1–10; тривалість: 30–60 секунд.")
    payload = {
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "project_name": "ClipAI YouTube clips",
        "target_duration": "DURATION_30_60",
        "limit": data.clips,
        "enable_export": True,
        "resolution": "HD_720",
        "enable_caption": False,
        "enable_ai_reframe": True,
        "ratio": "RATIO_9_16"
    }
    headers = {"Authorization": f"Bearer {api_key}", "x-wayinvideo-api-version": "v2"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{WAYIN_API_BASE}/clips", json=payload, headers=headers)
        if response.status_code >= 400:
            detail = "Зовнішній AI-сервіс не прийняв завдання."
            if response.status_code == 403:
                detail = "API-ключ WayinVideo недійсний або не має доступу."
            elif response.status_code in (402, 429):
                detail = "Недостатньо кредитів або перевищено ліміт запитів у WayinVideo."
            raise HTTPException(response.status_code if response.status_code < 500 else 502, detail)
        result = response.json().get("data", {})
        job_id = result.get("id")
        if not job_id:
            raise HTTPException(502, "AI-сервіс не повернув номер завдання.")
        return {"job_id": job_id, "status": result.get("status", "CREATED")}
    except httpx.HTTPError:
        raise HTTPException(502, "Не вдалося зв'язатися із сервісом AI-монтажу.")

@app.get("/api/youtube/jobs/{job_id}")
async def get_youtube_job(job_id: str):
    api_key = os.getenv("WAYINVIDEO_API_KEY")
    if not api_key:
        raise HTTPException(503, "На сервері не налаштовано API-ключ WayinVideo.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,100}", job_id):
        raise HTTPException(400, "Неправильний номер завдання.")
    headers = {"Authorization": f"Bearer {api_key}", "x-wayinvideo-api-version": "v2"}
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.get(f"{WAYIN_API_BASE}/clips/results/{job_id}", headers=headers)
        if response.status_code >= 400:
            raise HTTPException(502, "Не вдалося отримати статус AI-монтажу.")
        data = response.json().get("data", {})
        state = str(data.get("status", "ONGOING")).upper()
        clips = data.get("clips", []) or []
        if state == "SUCCEEDED":
            return {"status": "done", "clips": [
                {"title": item.get("title") or f"Кліп {i+1}", "score": item.get("score"),
                 "download": item.get("export_link"), "begin_ms": item.get("begin_ms"),
                 "end_ms": item.get("end_ms")}
                for i, item in enumerate(clips) if item.get("export_link")
            ]}
        if state == "FAILED":
            return {"status": "error", "error": data.get("error") or "AI-сервіс не зміг обробити це відео."}
        return {"status": "processing", "progress": None, "clips_ready": len(clips)}
    except httpx.HTTPError:
        raise HTTPException(502, "Не вдалося зв'язатися із сервісом AI-монтажу.")

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
