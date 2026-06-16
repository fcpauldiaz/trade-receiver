FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

COPY pyproject.toml README.md requirements.txt ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY start.sh ./

RUN pip install --no-cache-dir -e . \
    && chmod +x start.sh \
    && mkdir -p data

EXPOSE 8000

CMD ["./start.sh"]
