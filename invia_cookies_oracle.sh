#!/bin/bash
# ================================================================
# Script per esportare i cookies di YouTube dal browser sul tuo Mac
# e trasferirli sul server Oracle Cloud in modo semplice e sicuro
# ================================================================

echo "========================================================"
echo "🍪 Esportazione cookies YouTube per Franchino"
echo "========================================================"
echo ""
echo "Per risolvere il blocco 'Sign in to confirm you're not a bot'"
echo "su Oracle Cloud, Franchino ha bisogno dei tuoi cookies YouTube."
echo ""

# Richiedi i dati di connessione al server
read -p "Inserisci l'IP del tuo server Oracle Cloud: " SERVER_IP
read -p "Inserisci il percorso della tua chiave SSH (es: ~/chiave.key): " KEY_PATH
read -p "Inserisci l'utente SSH (default: opc): " SERVER_USER
SERVER_USER=${SERVER_USER:-opc}

COOKIES_FILE="$1"

if [ -z "$COOKIES_FILE" ]; then
    echo ""
    echo "========================================================"
    echo "📋 Dove trovare il file cookies.txt:"
    echo "========================================================"
    echo ""
    echo "METODO (Consigliato - Estensione Browser):"
    echo ""
    echo "1. Installa l'estensione per Chrome o Firefox:"
    echo "   Chrome: https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc"
    echo "   Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/"
    echo ""
    echo "2. Vai su https://www.youtube.com e accedi con il tuo account Google"
    echo ""
    echo "3. Clicca sull'icona dell'estensione nel browser"
    echo "   e seleziona 'Export' o 'Cookies for this Site'"
    echo ""
    echo "4. Salva il file come 'cookies.txt' e trascina il file"
    echo "   sulla finestra di questo terminale oppure incolla il percorso:"
    echo ""
    read -p "Percorso del file cookies.txt: " COOKIES_FILE
fi

if [ ! -f "$COOKIES_FILE" ]; then
    echo "❌ File non trovato: $COOKIES_FILE"
    exit 1
fi

echo ""
echo "✅ File cookies trovato: $COOKIES_FILE"
echo "📤 Invio al server Oracle Cloud in corso..."
# Prova a inviare in ~/franchino-bot e in fallback in ~/oracle_arm_deploy
scp -i "$KEY_PATH" "$COOKIES_FILE" "${SERVER_USER}@${SERVER_IP}:~/franchino-bot/cookies.txt" 2>/dev/null || \
scp -i "$KEY_PATH" "$COOKIES_FILE" "${SERVER_USER}@${SERVER_IP}:~/oracle_arm_deploy/cookies.txt"

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================================"
    echo "🎉 Cookies trasferiti con successo!"
    echo "========================================================"
    echo ""
    echo "Ora riavvia Franchino sul server con:"
    echo "  ssh -i $KEY_PATH ${SERVER_USER}@${SERVER_IP}"
    echo "  cd ~/franchino-bot && ./update.sh"
    echo "========================================================"
else
    echo "❌ Trasferimento fallito. Controlla IP, utente e chiave SSH."
fi
