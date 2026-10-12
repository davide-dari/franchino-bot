#!/bin/bash
# ========================================================
# Avvio di Franchino Bot tramite Docker Compose
# Con Central Hub (PostgreSQL) e Cervello AI Locale (Gemma 2 2B)
# ========================================================

set -e
cd "$(dirname "$0")"

echo "========================================================"
echo "🚀 Avvio di Franchino Bot (Docker su Oracle Cloud ARM)"
echo "========================================================"

# Verifica esistenza del file .env locale o nella cartella superiore
if [ ! -f ".env" ]; then
    if [ -f "../.env" ]; then
        echo "📋 Trovato file .env nella cartella principale, lo copio qui..."
        cp ../.env .env
    else
        echo "⚠️ ATTENZIONE: File .env non trovato in questa cartella!"
        echo "Creane uno con il tuo token Discord prima di continuare:"
        echo "    echo 'DISCORD_TOKEN=il_tuo_token_qui' > .env"
        echo ""
        read -p "Vuoi inserire il token adesso? (s/n): " insert_token
        if [ "$insert_token" = "s" ] || [ "$insert_token" = "S" ]; then
            read -p "Incolla il DISCORD_TOKEN: " user_token
            echo "DISCORD_TOKEN=$user_token" > .env
            echo "COMMAND_PREFIX=!" >> .env
            echo "✅ File .env creato con successo!"
        else
            echo "❌ Avvio annullato. Crea il file .env e riprova."
            exit 1
        fi
    fi
fi

# Comando docker compose (gestisce sia con che senza sudo)
DOCKER_CMD="docker compose"
if ! docker compose version &> /dev/null; then
    if sudo docker compose version &> /dev/null; then
        DOCKER_CMD="sudo docker compose"
    else
        echo "❌ Docker Compose non trovato! Esegui prima: ./install_docker_oracle.sh"
        exit 1
    fi
fi

echo "🔨 Costruzione dell'immagine e avvio di tutti i servizi (Bot, Database, Ollama)..."
$DOCKER_CMD up -d --build

echo ""
echo "🧠 Inizializzazione Cervello AI Locale (Google Gemma 2 2B)..."
echo "⏳ Verifica stato del container Ollama..."
for i in {1..15}; do
    if $DOCKER_CMD exec ollama ollama list &>/dev/null; then
        echo "✅ Servizio Ollama attivo!"
        break
    fi
    sleep 2
done

echo "📥 Verifica / Download del modello Google 'gemma2:2b' (100% Locale, Free)..."
$DOCKER_CMD exec ollama ollama pull gemma2:2b || echo "⚠️ Il modello verrà scaricato in background."

echo ""
echo "========================================================"
echo "✅ Franchino e l'Hub Centrale sono attivi in background!"
echo "========================================================"
$DOCKER_CMD ps

echo ""
echo "Comandi utili:"
echo " - Per vedere i log live:      ./logs.sh"
echo " - Per fermare tutti i servizi: ./stop.sh"
echo " - Per riavviare/aggiornare:    ./update.sh"
echo " - Statistiche Hub su Discord:  /hub_stats"
echo "========================================================"
