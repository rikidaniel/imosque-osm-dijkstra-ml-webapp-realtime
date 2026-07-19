FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/backend

RUN groupadd --system imosque && useradd --system --gid imosque --home /srv/imosque imosque

COPY requirements.txt ./requirements.txt
COPY requirements-realtime.txt ./requirements-realtime.txt
RUN pip install --upgrade pip && pip install -r requirements-realtime.txt

COPY app ./app
RUN mkdir -p /srv/data/osm_cache /srv/outputs && chown -R imosque:imosque /srv

USER imosque
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
