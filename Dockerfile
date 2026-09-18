FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

USER root

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_HEADLESS=true \
    PLAYWRIGHT_USE_CHROME=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && chmod 0755 /usr/bin/tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs /app/storage \
    && (id pwuser >/dev/null 2>&1 || useradd --create-home --uid 1000 --shell /bin/bash pwuser) \
    && chown -R pwuser /app

USER pwuser

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "run_bot.py"]
