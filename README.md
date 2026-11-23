# ClipAI

Mobile-friendly FastAPI website prototype for a planned AI short-video editor.

## Run locally
```bash
pip install -r requirements.txt
uvicorn main:app --reload
```
Open http://127.0.0.1:8000

## Deploy
This repository includes `render.yaml` for a Render web service. Connect the GitHub repository in Render and deploy the detected Blueprint.

## Current capabilities
- Responsive Ukrainian-language interface.
- YouTube URL validation.
- Fetches public video metadata using YouTube oEmbed.
- Health endpoint at `/health`.

## Not implemented yet
- Downloading or ingesting video streams from YouTube.
- AI moment selection.
- Video cropping/rendering.
- MP4 export and clip storage.

A YouTube URL alone does not provide authorized access to the original media stream. Real processing must use a media source the user owns or is authorized to process, plus a worker with video-processing dependencies and sufficient compute.
