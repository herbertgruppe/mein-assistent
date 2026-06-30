FROM python:3.14-slim

WORKDIR /app

# Sicherheits-Upgrades (CVE-2026-55200 libssh2, HBE-1465) — vor App-Deps patchen
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# System-Abhängigkeiten (inkl. WeasyPrint für PDF-Generierung)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Python-Abhängigkeiten zuerst (Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code kopieren
COPY . .

# Verzeichnisse für persistente Daten anlegen
RUN mkdir -p transcripts/incoming \
             transcripts/processed \
             transcripts/archive \
             transcripts/protocols \
             transcripts/protocols_final \
             transcripts/meeting_prep \
             transcripts/wip \
             transcripts/protocol_cache \
             input_docs \
             data \
             auth \
             users \
             config

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
  CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
