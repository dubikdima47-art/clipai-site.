# ClipAI — handoff for Claude

## Links
- Live website: https://clipai-site.onrender.com
- GitHub repository: https://github.com/dubikdima47-art/clipai-site.
- Default branch: main
- Hosting: Render Web Service, service name: clipai-site
- Render service dashboard: https://dashboard.render.com/web/srv-dc26n9m7bikc738l9jk0

## Product goal
Build a mobile-friendly Ukrainian-language website. User pastes a YouTube video URL; the service should process that URL (not require the user to upload the video manually), identify up to 10 strong moments, produce 30–60 second vertical 9:16 MP4 clips, no subtitles, and provide downloadable results.

Only process videos through a provider/API and media access that is authorized and complies with YouTube/provider terms. Do not implement scraping or bypass access controls.

## Stack / deployment
- Python FastAPI backend in main.py
- Static HTML/CSS/JS frontend in static/index.html
- Render deployment from render.yaml
- requirements.txt includes FastAPI, Uvicorn, httpx, Pydantic, python-multipart
- render.yaml installs FFmpeg with apt-get during build
- /health returns service status and whether ffmpeg is present

## Existing backend
- POST /api/inspect: YouTube oEmbed metadata lookup; Google Drive URL ID recognition only (does not download Drive media).
- POST /api/youtube/jobs: expects JSON {url, clips, duration}; requires environment variable WAYINVIDEO_API_KEY; sends a job to WayinVideo API.
- GET /api/youtube/jobs/{job_id}: polls the external provider and maps results to {status, clips}.
- POST /api/upload: separate local-file upload route, max 300 MB, FFmpeg creates evenly spaced clips (NOT AI highlight detection).
- GET /api/jobs/{job_id}: local upload-job status.
- GET /api/jobs/{job_id}/download: ZIP archive for local upload workflow.
- Local upload jobs and output are stored in /tmp and in-memory; Render restarts/ephemeral filesystem can lose them.

## Important status / caveats
- Recent commits added a WayinVideo URL-to-clips integration:
  - cf185a550cbf415a927a54243afff941e1c8ef6c — API integration
  - 669ec20131a9a0755d0b6468caa4dcfab4ef4833 — UI controls and result links
  - 944ca7cb4d7302db4d258009a1f584e021c2245f — make YouTube workflow primary
- The WayinVideo integration is NOT yet proven end-to-end. Verify the current official API docs, endpoint paths, authentication, request payload, response schema, pricing/credits, YouTube URL eligibility, and export link format before claiming it works.
- Current code assumes base URL https://wayinvideo-api.wayin.ai/api/v2, POST /clips, GET /clips/results/{job_id}, Bearer auth, and a particular JSON schema. Treat these as unverified assumptions until tested.
- Current UI has a YouTube processing button and a separate file-upload fallback. The /api/inspect result text is still misleading for YouTube because it says to upload a file even when metadata lookup succeeds; fix this.
- The page polls every 30 seconds. Improve timeout, retry/backoff, cancellation, and visible provider error reporting.
- Validate that returned export_link values are safe/valid HTTPS URLs before rendering download links.
- Avoid logging API keys. Keep secrets only in Render Environment Variables; never commit them or ask user to paste them in chat.
- A provider API key was accidentally exposed in a chat. Treat that key as compromised: user must revoke it and create a new one. Do not reuse it. Add the replacement only as WAYINVIDEO_API_KEY in Render Environment.
- The README.md may be stale relative to current code; update it after validating the implementation.

## First tasks for Claude
1. Inspect the full repository and current deployment; do not overwrite working code blindly.
2. Verify WayinVideo's current official docs and whether the provider genuinely supports processing a YouTube URL under the user's account/plan.
3. Compare actual API schema with main.py and fix mismatches.
4. Add safe validation, robust job polling, clear errors, and correct frontend status messages.
5. Add tests/mocks for provider create-job and result polling.
6. Check Render logs and /health after deploy. Do not claim success until a real authorized test URL has completed and returned working MP4 links.
7. Update README.md and this handoff file with verified facts only.

## User's acceptance criteria
- Paste YouTube URL directly; no manual video-file upload for the main workflow.
- Up to 10 clips, each 30–60 seconds.
- AI-selected highlights (not evenly spaced slices).
- Vertical 9:16 MP4, no subtitles.
- Clear progress, useful errors, downloadable MP4 clips.
- Mobile-friendly Ukrainian interface.

## How to run locally
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```
Open http://127.0.0.1:8000
