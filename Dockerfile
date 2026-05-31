FROM python:3.12-slim

LABEL maintainer="Silas Martin <mail@silasmartin.de>" \
  description="Simple mail service to send form notification mails"

ENV PYTHONUNBUFFERED=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  PIP_NO_CACHE_DIR=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /usr/src/app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Run as an unprivileged user and give it ownership of the data directory.
RUN useradd --create-home --uid 10001 appuser \
  && mkdir -p /usr/src/app/data \
  && chown -R appuser:appuser /usr/src/app
USER appuser

EXPOSE 8004

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8004/health', timeout=3).status==200 else sys.exit(1)"

# 2 workers x 4 threads handles bursts while keeping the footprint small.
CMD ["gunicorn", "--bind", "0.0.0.0:8004", "--workers", "2", "--threads", "4", \
  "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-", "main:app"]
