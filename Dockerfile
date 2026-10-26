FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY navimow2mqtt ./navimow2mqtt

# /data holds the OAuth token file; it must stay writable for the service user.
RUN useradd --system --no-create-home navimow2mqtt \
    && mkdir /data && chown navimow2mqtt /data
USER navimow2mqtt

EXPOSE 8099
VOLUME /data
ENV NAVIMOW_TOKEN_FILE=/data/token.json \
    STATS_FILE=/data/stats.json \
    TRAIL_FILE=/data/trail.json \
    MAPS_DIR=/data/maps
CMD ["python", "-m", "navimow2mqtt"]
