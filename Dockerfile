FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./

RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

# Default service: API (no dev reload). Use the same image with a different command
# for background workers, for example:
#   command: python -m scripts.run_email_worker
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
