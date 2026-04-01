# Adaptive Tutor 

## Stack
- FastAPI + Celery + Redis + Postgres
- RAG ingestion 
- Lesson video pipeline 
- React + Vite frontend orchestration UI

## Quick Start
1. Copy env:
   - `cp .env.example .env`
2. Start stack:
   - `docker compose up --build`
3. Open frontend:
   - `http://localhost:5173`
4. API docs:
   - `http://localhost:8000/docs`

## Migrations
- Auto schema init is run by backend startup (`python app/migrate.py`).
- Alembic files are available in `backend/alembic` and `backend/alembic/versions`.

## Smoke Test
After stack is up:
- `python backend/scripts/demo_smoke.py`

## Tests
- `cd backend`
- `pytest`

## Key Endpoints
- `GET /health`
- `GET /health/ready`
- `POST /adaptive/start`
- `POST /adaptive/step`
- `POST /quiz/submit`
- `POST /rag/upload`
- `GET /sessions/{session_id}`
- `GET /jobs/{task_id}`

## Troubleshooting
- `health/ready` shows dependency status for db/redis/worker.
- If PDFs fail to parse, confirm `pypdf` installed in backend image.
- If lesson stays pending, check worker logs and Manim dependencies.
- If TTS fails, lesson still completes with silent video (`PARTIAL`).
