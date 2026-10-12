# ☁️ Guida al Deploy di Franchino su Oracle Cloud ARM (Ampere A1) con Docker

Questa guida spiega passo-passo come ospitare **Franchino** su una macchina virtuale **ARM a 64 bit (Ampere A1)** su **Oracle Cloud**, sfruttando il piano **Always Free (gratuito per sempre)** tramite Docker e collegando direttamente il repository GitHub.

---

## 🌟 Architettura delle Risorse (Allocazione RAM & CPU)
* **Server Fisico:** Oracle Cloud VM.Standard.A1.Flex (2 OCPU / 4 vCPU, 12 GB RAM totali).
* **Consumo Totale:**
  - `central-hub-db` (Postgres 16 Alpine): ~35 MB RAM
  - `ollama` (Google Gemma 2 2B Instruct): ~2.0 GB RAM
  - `franchino-bot` (Python 3.13, Discord, Audio DSP): ~250 MB RAM
  - **RAM Totale Usata:** ~2.3 GB su 12 GB (**Oltre 9.5 GB di RAM liberi (79%) per tutti i tuoi servizi futuri!**)
* **CPU a Riposo:** 0.0% - 0.1% CPU.

---

## 🚀 PASSO 1: Connettersi via SSH al Server Oracle

Dal terminale del tuo Mac:
```bash
ssh -i /percorso/della/tua_chiave.key ubuntu@161.118.165.43
```
*(Se utilizzi Oracle Linux, sostituisci `ubuntu` con `opc`)*.

---

## 📦 PASSO 2: Installare Docker & Clonare il Repository GitHub

Sul server Oracle:

1. Se Docker non è ancora installato, installalo rapidamente:
   ```bash
   sudo apt-get update && sudo apt-get install -y git docker.io docker-compose-v2
   sudo usermod -aG docker $USER
   newgrp docker
   ```

2. Clona il repository da GitHub:
   ```bash
   git clone https://github.com/davide-dari/franchino-bot.git
   cd franchino-bot
   ```

3. Crea il file `.env` con il tuo Token Discord:
   ```bash
   cp .env.example .env
   nano .env
   ```
   *(Incolla il tuo `DISCORD_TOKEN` e salva premendo `Ctrl+O`, poi `Invio`, poi `Ctrl+X`)*.

4. *(Opzionale ma consigliato per YouTube)* Invia i cookies dal tuo Mac:
   Dal tuo Mac, esegui:
   ```bash
   ./invia_cookies_oracle.sh
   ```

---

## ⚡ PASSO 3: Avvio con un solo comando!

Dentro alla cartella `~/franchino-bot` sul server Oracle:
```bash
chmod +x *.sh
./start.sh
```

Lo script `./start.sh` farà tutto in automatico:
- Costruisce ed avvia i container Docker in background (`franchino-bot`, `central-hub-db`, `ollama`).
- Inizializza il database relazionale PostgreSQL per l'Hub Centrale.
- Scarica in locale il modello AI Google `gemma2:2b` dentro Ollama.

---

## 🎮 Comandi di Gestione sul Server

Dentro la cartella `~/franchino-bot`:

| Comando | Descrizione |
|---|---|
| `./logs.sh` | Mostra i log in tempo reale di Franchino (Ctrl+C per uscire) |
| `./start.sh` | Avvia tutti i servizi in background e verifica Gemma 2 2B |
| `./stop.sh` | Ferma in sicurezza tutti i container senza perdere i dati del DB |
| `./update.sh` | Ricostruisce il bot e applica gli aggiornamenti del codice |

---

## 🔄 Come Aggiornare il Bot in Futuro dal Repository

Ogni volta che fai modifiche e pushi su GitHub, sul server ti basta digitare:
```bash
cd ~/franchino-bot
git pull
./update.sh
```
In pochi secondi Docker ricompila il bot e lo riavvia h24 senza toccare i dati del database o i modelli AI!

---

## 💬 Comandi Slash Discord Dedicati all'Hub e all'AI

Una volta online, potrai usare direttamente nella chat di Discord:
- `/hub_stats`: Mostra le statistiche in tempo reale dell'Hub Centrale (interazioni salvate, utenti unici, intent più frequenti, stato backend PostgreSQL).
- `/hub_recent`: Mostra le ultime 5 richieste o comandi vocali archiviati con le risposte fornite da Franchino.
- `/favola_live [tema]`: Chiede a Gemma 2 2B di comporre ed interpretare all'istante una favola della notte inedita nello stile mistico di Franchino il Vocalist!

---

## 🌐 Connettere altri Progetti Futuri all'Hub Centrale

Il database dell'Hub Centrale è esposto sulla porta `5432` di `127.0.0.1` (localhost) della macchina virtuale.
Qualsiasi altro progetto Python, Node.js, Go o microservizio Docker che installerai sul server può connettersi e condividere i dati usando:

```text
Host: 127.0.0.1
Port: 5432
User: franchino_admin
Pass: franchino_secure_pass
Database: franchino_hub
URL: postgresql://franchino_admin:franchino_secure_pass@127.0.0.1:5432/franchino_hub
```
