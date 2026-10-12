#!/bin/bash
# ========================================================
# Arresto del container Franchino Bot
# ========================================================

cd "$(dirname "$0")"

DOCKER_CMD="docker compose"
if ! docker compose version &> /dev/null; then
    if sudo docker compose version &> /dev/null; then
        DOCKER_CMD="sudo docker compose"
    fi
fi

echo "🛑 Arresto del container Franchino..."
$DOCKER_CMD down

echo "✅ Franchino è stato fermato."
