# ========================================================
# Dockerfile ottimizzato per Oracle Cloud ARM (Ampere A1)
# Architettura: linux/arm64 (o linux/amd64 multi-arch)
# ========================================================

FROM node:22-bookworm-slim AS node_base
FROM python:3.13-slim-bookworm

# Evita la scrittura dei file .pyc e forza l'output immediato nei log
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Copia il binario di Node.js v22 LTS (yt-dlp richiede obbligatoriamente Node >= 22.0.0!)
COPY --from=node_base /usr/local/bin/node /usr/local/bin/node

# Installa pacchetti di sistema richiesti:
# - ffmpeg: decodifica e streaming audio
# - libopus0: codec vocale nativo per Discord
# - flac: richiesto da SpeechRecognition su architettura ARM64 (aarch64)
# - ca-certificates: per certificati SSL sicuri
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    flac \
    libstdc++6 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && node --version \
    && flac --version

# Imposta la directory di lavoro nel container
WORKDIR /app

# Copia e installa le dipendenze Python (incluso yt-dlp-ejs e discord-ext-voice-recv)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia tutto il codice sorgente e i cookie nel container
COPY . .

# Assicura che la cartella data sia sempre scrivibile dal container
RUN mkdir -p /app/data /tmp/franchino_cache && chmod -R 777 /app/data /tmp/franchino_cache

# Comando di avvio del bot Franchino
CMD ["python", "main.py"]
