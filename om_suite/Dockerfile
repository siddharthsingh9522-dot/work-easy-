FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Chromium + driver are only needed for the GST Query screen (Phase 3). Build with
# --build-arg INSTALL_CHROME=0 to skip them while that module is not in use.
ARG INSTALL_CHROME=1
RUN if [ "$INSTALL_CHROME" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver fonts-liberation \
      && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd -m appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

ENV APP_ENV=production PORT=10000 CHROME_BIN=/usr/bin/chromium CHROMEDRIVER_PATH=/usr/bin/chromedriver
EXPOSE 10000
CMD gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 1 --threads 12 --timeout 900
