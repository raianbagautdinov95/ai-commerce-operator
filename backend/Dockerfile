FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

# The port is a default, not a constant. A platform that assigns one — Railway
# and most PaaS do — passes it as PORT, and a service that ignores it answers
# nothing on the port the health check is aimed at.
ENV PORT=8000
EXPOSE 8000

# Reads PORT too. A health check hardcoded to 8000 while the server listens
# elsewhere reports unhealthy forever, and an orchestrator that restarts
# unhealthy containers turns that into a loop.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health',timeout=3)"

# `exec` so uvicorn replaces the shell and keeps PID 1 — otherwise SIGTERM
# stops at the shell and the container is killed rather than shut down.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
