#!/bin/bash
# ========================================================
# Visualizzazione dei log live di Franchino Bot
# ========================================================

cd "$(dirname "$0")"

DOCKER_CMD="docker compose"
if ! docker compose version &> /dev/null; then
    if sudo docker compose version &> /dev/null; then
        DOCKER_CMD="sudo docker compose"
    fi
fi

echo "📋 Visualizzazione log di Franchino (premi Ctrl+C per uscire)..."
$DOCKER_CMD logs -f --tail=100
