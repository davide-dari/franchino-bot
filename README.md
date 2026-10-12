<div align="center">

# 🎧 FRANCHINO BOT
### *L'Intelligenza Artificiale della Notte & Music DJ per Discord*

<img src="assets/franchino.jpg" width="460" alt="Franchino Cyborg AI" style="border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);" />

> *"Magia... Chiudi gli occhi e vola con me nelle favole della notte... Da stasera niente sarà più come prima!"* ✨🌌

[![Discord.py](https://img.shields.io/badge/discord.py-v2.7.0-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Gemma_2_2B-000000?logo=ollama&logoColor=white)](https://ollama.ai/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16_Alpine-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 🌟 Visione & Funzionalità Principali

**Franchino** è un bot Discord di nuova generazione che unisce musica ad alta fedeltà, interazione vocale in tempo reale, un archivio dati persistente e un **cervello di intelligenza artificiale locale (Google Gemma 2 2B)** ispirato alla voce leggendaria del vocalist Francesco Principato.

### 🧠 1. Cervello AI Locale (Google Gemma 2 2B Instruct)
* **100% Gratuito & Illimitato**: Gira direttamente sul server tramite **Ollama**. Nessun costo di abbonamento, nessuna dipendenza da API esterne a pagamento (OpenAI/ElevenLabs/Anthropic).
* **NLU Musicale per Mood**: Interpreta richieste naturali e descrittive (es. *"Mettimi qualcosa di ipnotico per esplorare un dungeon"* o *"Metti una bella afro"*) traducendole in ricerche audio mirate.
* **Franchino Persona & Favole Live**: Compone all'istante monologhi notturni e favole mistiche nello stile storico della Metempsicosi e dell'Insomnia.
* **DJ Song Commentary**: Quando chiedi *"Chi canta?"*, analizza la traccia e racconta aneddoti ed emozioni sul pezzo in esecuzione.
* **Zero-Downtime Fallback**: Heuristic engine ultra-rapido con regex se il modello è in caricamento.

---

### 🗄️ 2. Central Knowledge Hub & Multi-Project Vault
* **Archivio Permanente**: Ogni richiesta vocale o testuale, intento e risposta fornita viene salvata permanentemente per arricchire la memoria del bot.
* **Architettura Condivisa sul Server**: Basato su **PostgreSQL 16 Alpine** (con fallback trasparente su SQLite WAL locale), esposto su `127.0.0.1:5432`.
* **Hub Centrale per Progetti Futuri**: Qualsiasi altra applicazione, bot o microservizio ospitato sullo stesso server può connettersi e condividere lo stesso database centralizzato (`postgresql://franchino_admin:franchino_secure_pass@127.0.0.1:5432/franchino_hub`).

---

### 🎙️ 3. Motore Vocale & Riconoscimento Audio Lossless
* **Pre-Roll Ring Buffer (300ms)**: Elimina completamente il taglio dei fonemi iniziali quando l'utente inizia a parlare.
* **Elaborazione Audio Nativa 48kHz**: Nessun artefatto di downsampling, rimozione del DC-bias offset e AGC (Automatic Gain Control) soft.
* **Multi-Candidate STT**: Analisi parallela multi-ipotesi di SpeechRecognition per cogliere il wake word anche con rumore di fondo.
* **Comando STOP Prioritario e Immediato**: Con *"Franchino stop"* interrompe all'istante sintesi vocale TTS, canzone attiva, effetti e svuota la coda senza frasi prolisse.
* **Timbro Vocalist & DSP Effetti**: Effetto eco profondo da console live per favole e annunci.

---

### ⚔️ 4. Super-Esperto Ultima Online Outlands
* **Knowledge Base Offline Indicizzata**: Riconoscimento di build, dungeon, boss, armi, skill ed eventi speciali del server UO Outlands.
* **RAG (Retrieval-Augmented Generation)**: Sintesi intelligente delle informazioni estratte dalla wiki in risposte concise e leggibili per Discord.

---

### 🎵 5. Player Musicale HD (YouTube & Spotify)
* **YouTube**: Supporto per singoli video, playlist e ricerca testuale automatica.
* **Spotify**: Supporto completo per brani, album, playlist (funziona sia con credenziali ufficiali che con fallback parser integrato).
* **Console Interattiva con Pulsanti**: Play/Pausa, Skip, Stop, Loop, Shuffle, Volume, Coda paginata e Disconnessione rapida.

---

## 🏛️ Architettura Risorse & Docker (Oracle Cloud ARM)

Il progetto è ottimizzato specificamente per **Oracle Cloud Always Free (Ampere A1 - 2 OCPU / 12 GB RAM)**:

| Servizio Docker | Descrizione | RAM Utilizzata | Allocazione CPU |
|---|---|---|---|
| `franchino-bot` | Bot Discord, Audio DSP, STT, Player | ~250 MB | Max 1.5 CPU |
| `central-hub-db` | PostgreSQL 16 Alpine (Hub Dati) | ~35 MB | Max 0.5 CPU |
| `ollama` | Motore AI Locale con Gemma 2 2B | ~2.0 GB | Max 2.0 CPU |
| **TOTALE USATO** | **Tutti e 3 i container attivi** | **~2.3 GB / 12 GB** | **0.1% a riposo** |
| **RISORSE LIBERE** | **Disponibili per altri progetti** | **~9.7 GB (79%+)** | **Fluido & Freddo** |

---

## 🚀 Avvio Rapido con Docker

### 1. Clona il Repository
```bash
git clone https://github.com/davide-dari/franchino-bot.git
cd franchino-bot
```

### 2. Configura il Token di Discord
Copia il file di esempio e inserisci il tuo token:
```bash
cp .env.example .env
nano .env
```
*(Inserisci il tuo `DISCORD_TOKEN` ottenuto dalla [Discord Developer Portal](https://discord.com/developers/applications))*.

### 3. Avvia Tutti i Servizi
```bash
chmod +x *.sh
./start.sh
```

Lo script `./start.sh`:
1. Costruisce ed avvia i container Docker (`franchino-bot`, `central-hub-db`, `ollama`).
2. Verifica e scarica automaticamente il modello Google `gemma2:2b` in locale.
3. Inizializza il database dell'Hub Centrale.

---

## 🎮 Comandi di Gestione

| Script | Funzione |
|---|---|
| `./logs.sh` | Visualizza i log in tempo reale del bot (Ctrl+C per uscire) |
| `./start.sh` | Avvia tutti i servizi in background e verifica l'AI |
| `./stop.sh` | Ferma in sicurezza tutti i container senza perdere i dati del DB |
| `./update.sh` | Ricostruisce il bot e applica gli aggiornamenti del codice |

---

## 💬 Comandi Slash & Vocali Principali

### Comandi Slash Chat (`/`) e Testuali (`!`)
- `/partita [squadra o evento]`: Cerca partite o eventi sportivi live (Calcio, NBA, F1, MotoGP, Tennis) su MadPlay77 e restituisce il link per guardare lo streaming! (Alias: `!partita`, `/sport`, `!sport`).
- `/outlands [argomento]`: Cerca build, dungeon, boss o meccaniche su wiki.uooutlands.com e ottieni la guida tradotta in italiano! (Alias: `!outlands`, `/wiki`, `!wiki`, `!uo`).
- `/play [query o link]`: Riproduce un brano o playlist da YouTube/Spotify.
- `/hub_stats`: Mostra le statistiche dell'Hub Centrale (interazioni salvate, utenti unici, intent).
- `/hub_recent`: Mostra lo storico delle richieste vocali/testuali archiviate.
- `/favola_live [tema]`: Chiede a Gemma 2 di improvvisare una favola notturna con voce da Vocalist.
- `/volume [1-100]`: Regola il volume di riproduzione.
- `/skip` / `/stop` / `/pause` / `/resume` / `/queue`: Controlli player musicale.

### Comandi Vocali (Microfono Discord)
Pronuncia il wake word **"Franchino"** seguito dal comando:
- *"Franchino cerca la partita del Milan"* (oppure *"Franchino dove fanno vedere la Formula 1?"*)
- *"Franchino spiegami come funziona la wiki di outlands per il tamer"*
- *"Franchino metti Gigi D'Agostino"* (oppure *"Franchino metti un pezzo afro"*)
- *"Franchino stop"* (interrompe istantaneamente qualsiasi suono e discorso)
- *"Franchino raccontami una favola"*
- *"Franchino chi canta?"*
- *"Franchino alza il volume"* / *"Franchino salta traccia"*

---

## 🛡️ Sicurezza & File Sensibili

I seguenti file sono protetti e ignorati da Git tramite `.gitignore`:
- `.env` (Token del bot e chiavi API)
- `cookies.txt` (Sessione YouTube per yt-dlp)
- `data/*.db` (Archivi locali SQLite)
- `data/postgres/` e `data/ollama/` (Volumi fisici dei container)

---

## 📄 Licenza

Rilasciato sotto licenza [MIT](LICENSE). Realizzato per celebrare la notte, la musica e la magia della console! 🎧✨
