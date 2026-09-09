@echo off
REM One-click launcher: starts Redis, API, worker and frontend.
cd /d "%~dp0"

docker start aco-shopify-redis >nul 2>&1
if errorlevel 1 docker run -d --name aco-shopify-redis -p 127.0.0.1:6379:6379 redis:7.4-alpine >nul

start "AI Operator - Backend" cmd /k "cd backend && set QUEUE_ENABLED=true&& set REDIS_URL=redis://127.0.0.1:6379/0&& set RQ_REQUIRED_QUEUES=autopilot,integrations,reports&& .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001"
start "AI Operator - Worker" cmd /k "cd backend && .venv\Scripts\rq.exe worker -w rq.worker.SimpleWorker --url redis://127.0.0.1:6379/0 autopilot integrations reports"
start "AI Operator - Frontend" cmd /k "cd frontend && npm run dev -- -p 3002"

echo Starting AI Commerce Operator...
timeout /t 7 >nul
start http://localhost:3002/dashboard
