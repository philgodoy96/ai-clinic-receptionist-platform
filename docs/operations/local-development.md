# Local Development

This document explains how to run the AI Clinic Receptionist Platform locally.

## Requirements

- Python 3.12+
- Docker
- Docker Compose
- Git

## Environment Variables

Create a local environment file from the example file:

    Copy-Item .env.example .env

The local `.env` file is ignored by Git and must not be committed.

## Running with Python

Create and activate a virtual environment:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1

Install dependencies:

    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"

Run the API:

    python -m uvicorn app.main:app --reload

Open:

    http://127.0.0.1:8000/health

Expected response:

    {
      "status": "ok",
      "service": "ai-clinic-receptionist-platform",
      "environment": "local"
    }

## Running with Docker Compose

Start all local services:

    docker compose up --build

The API will be available at:

    http://127.0.0.1:8000

Health endpoint:

    http://127.0.0.1:8000/health

RabbitMQ management UI:

    http://127.0.0.1:15672

Default local RabbitMQ credentials:

    Username: clinic
    Password: clinic

Stop services:

    docker compose down

## Running Tests

Run tests:

    python -m pytest -q

Run lint checks:

    python -m ruff check .

Run type checks:

    python -m mypy app tests

## Current Limitations

This stage only includes the project scaffold and health endpoint.

The following components are planned but not implemented yet:

- Database models
- Alembic migrations
- Redis appointment holds
- RabbitMQ workers
- Chat conversation flow
- Retell tool endpoints
- Observability metrics
- OpenTelemetry tracing
- Admin/demo endpoints