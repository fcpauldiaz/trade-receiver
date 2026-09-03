FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

COPY pyproject.toml README.md requirements.txt ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir . \
    && mkdir -p data data/desktop \
    && python -c "from app.libsql_dialect import SQLiteDialect_libsql; from sqlalchemy.dialects import registry; registry.register('sqlite.libsql', 'app.libsql_dialect', 'SQLiteDialect_libsql'); registry.load('sqlite.libsql')"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
