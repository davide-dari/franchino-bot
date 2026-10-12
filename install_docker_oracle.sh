#!/bin/bash
# ====================================================================
# Installazione automatica di Docker e Docker Compose su Oracle Cloud ARM
# Supporta: Oracle Linux (ol 8/9), Ubuntu ARM, Debian ARM (Ampere A1)
# ====================================================================

set -e

echo "========================================================"
echo "🐳 Installazione Docker su Oracle Cloud ARM (Ampere A1)"
echo "========================================================"

# Rilevamento della distribuzione Linux
OS_ID=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="$ID"
fi

echo "🔍 Sistema operativo rilevato: ${PRETTY_NAME:-$OS_ID} (ID: $OS_ID, Arch: $(uname -m))"

# 1. Verifica se Docker è già presente
if command -v docker &> /dev/null; then
    echo "✅ Docker è già installato nel sistema: $(docker --version)"
else
    # 2. Gestione specifica per Oracle Linux (ol / rhel / centos)
    if [ "$OS_ID" = "ol" ] || [ "$OS_ID" = "rhel" ] || [ "$OS_ID" = "centos" ]; then
        echo "📦 Rilevato Oracle Linux! Configurazione tramite repository DNF..."
        
        # Installa i plugin DNF necessari
        sudo dnf install -y dnf-plugins-core curl
        
        # Aggiunge il repository ufficiale Docker CE
        sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        
        # Installa Docker CE, containerd e il plugin Docker Compose
        # (--allowerasing risolve eventuali conflitti con podman o versioni preesistenti di runc)
        echo "📥 Installazione di Docker CE e Docker Compose..."
        sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin --nobest --allowerasing

    # 3. Gestione per Ubuntu / Debian o altre distribuzioni
    else
        echo "📥 Installazione tramite script ufficiale Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        rm -f get-docker.sh
    fi
fi

# 4. Abilitazione e avvio del demone Docker
echo "⚙️ Abilitazione e avvio del servizio Docker..."
sudo systemctl enable --now docker

# 5. Aggiunge l'utente corrente al gruppo docker
CURRENT_USER=$(whoami)
echo "👤 Aggiunta dell'utente '$CURRENT_USER' al gruppo docker..."
sudo usermod -aG docker "$CURRENT_USER"

echo ""
echo "========================================================"
echo "🎉 Docker e Docker Compose sono stati installati con successo!"
echo "Versione Docker:         $(docker --version 2>/dev/null || sudo docker --version)"
echo "Versione Docker Compose: $(docker compose version 2>/dev/null || sudo docker compose version)"
echo "========================================================"
echo ""
echo "👉 PROSSIMO PASSO:"
echo "Esegui ora il comando per applicare i permessi:"
echo "    newgrp docker"
echo "E poi avvia Franchino con:"
echo "    ./start.sh"
echo "========================================================"
