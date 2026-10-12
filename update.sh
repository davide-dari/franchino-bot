#!/bin/bash
# ========================================================
# Riavvio e aggiornamento del container Franchino
# ========================================================

cd "$(dirname "$0")"

DOCKER_CMD="docker compose"
if ! docker compose version &> /dev/null; then
    if sudo docker compose version &> /dev/null; then
        DOCKER_CMD="sudo docker compose"
    fi
fi

echo "🔄 Ricostruzione dell'immagine del bot Franchino..."
$DOCKER_CMD build --no-cache franchino-bot
$DOCKER_CMD up -d --force-recreate

echo "🧠 Verifica modello Gemma 2 2B..."
$DOCKER_CMD exec ollama ollama pull gemma2:2b || true

echo "✅ Franchino e l'Hub Centrale sono stati aggiornati e riavviati!"
$DOCKER_CMD ps
