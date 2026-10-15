FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY engine/ ./engine/
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir fastapi "uvicorn[standard]" sqlalchemy alembic pydantic pydantic-settings pyjwt bcrypt httpx "psycopg[binary]" python-multipart

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
