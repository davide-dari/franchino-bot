# Addestrare la voce di Franchino

Come passare da una voce *che imita* Franchino (edge-tts + DSP, quello che c'è
oggi in `player.py`) a un modello **addestrato** che dice qualsiasi frase con il
suo timbro.

> ⚠️ **Prima di tutto, due verità scomode**
>
> 1. **L'addestramento non può girare sulla Oracle.** Il server è una Ampere A1
>    con 2 vCPU e nessuna GPU, e 1,4 core sono già di Ollama. Serve una GPU:
>    Colab (gratis, T4) o un Mac Apple Silicon con PyTorch/MPS.
> 2. **Questa è la voce di una persona reale e identificabile.** Il dataset e i
>    modelli che ne derivano non vanno pubblicati né committati: `franchino-bot`
>    è un repository pubblico. La cartella del dataset è già in `.gitignore`.

---

## 1. Cosa produce il builder

```bash
python scripts/build_voice_dataset.py --input video.mp4 --out dataset
```

| Cartella | Contenuto | A cosa serve |
|---|---|---|
| `dataset/wavs/` | clip mono **44.1 kHz**, normalizzate a −20 dB RMS | master di riferimento (RVC, archivio) |
| `dataset/piper/wavs/` + `metadata.csv` | **22.05 kHz** + `file|testo` | Piper (e va bene anche per un fine-tune XTTS) |
| `dataset/gpt_sovits/` + `dataset.list` | **32 kHz** + `path|speaker|lingua|testo` | GPT-SoVITS e modelli multilingua a 32k |
| `dataset/report.txt` | quanto parlato pulito hai estratto, scarti compresi | capire se basta prima di addestrare |

Opzioni utili:

```bash
# Franchino parla sopra la musica: separa prima la voce (lento, serve demucs)
python scripts/build_voice_dataset.py --input v.mp4 --out dataset --separate-vocals

# Trascrivi automaticamente le clip (serve a Piper e ai modelli TTS)
python scripts/build_voice_dataset.py --input v.mp4 --out dataset --transcribe

# Frasi molto veloci o incise: clip più corte
python scripts/build_voice_dataset.py --input v.mp4 --out dataset --min-len 2 --max-len 8
```

Dipendenze: **solo `ffmpeg`** (già nell'immagine Docker). `demucs` e
`faster-whisper` sono opzionali e si installano con `pip` solo se attivi
`--separate-vocals` / `--transcribe`.

### Quanto materiale serve

| Modello | Parlato pulito | Note |
|---|---|---|
| RVC | **10-15 min** | nessuna trascrizione necessaria |
| XTTS-v2 (fine-tune) | **10-30 min** | multilingua, italiano incluso; licenza Coqui **non commerciale** |
| GPT-SoVITS | 1-10 min | ⚠️ lingue ufficiali: cinese/inglese/giapponese/coreano/cantonese — **italiano non ufficiale** |
| Piper (fine-tune) | **30-60 min** | è l'unico che poi gira **in tempo reale su 2 vCPU** |

Il report del builder ti dice subito a che punto sei e con quale modello puoi
già partire.

---

## 2. Le tre ricette

### A) Piper — l'unica voce che può suonare **in diretta** sul tuo server

È la scelta pragmatica: modello piccolo, inferenza **~1x tempo reale su un core**,
zero GPU a runtime, si integra in `player.py` al posto di edge-tts.

1. Prepara il dataset a 22.05 kHz con le trascrizioni:
   ```bash
   python scripts/build_voice_dataset.py --input v.mp4 --out dataset --transcribe --whisper-model small
   ```
2. Fine-tuning su GPU seguendo la pipeline VITS di Piper (`piper` → repo
   `rhasspy/piper`, cartella `training/`): servono i `metadata.csv` + i wav.
   Su Colab una voce richiede qualche ora.
3. Ottieni un `voce.onnx` + `.onnx.json` (pochi MB).
4. Sul server: `piper` in un container leggero, chiamato al posto di edge-tts.

**Compromesso da mettere in conto**: la qualità è più "robotica" di XTTS. Con
Franchino però aiuta la DSP chain già presente (equalizzatore + compressore +
eco), che puoi ritagliare sulla voce nuova.

### B) XTTS-v2 fine-tune — qualità alta, ma solo per contenuti pre-renderizzati

Multilingua con **italiano supportato ufficialmente**. Ottimo per favole, saluti
e frasi canoniche; su 2 vCPU però è **10-30x più lento del tempo reale**, quindi
non può rispondere in diretta.

1. Dataset a 22.05 kHz con trascrizioni (come sopra).
2. Fine-tune su Colab/Mac con la ricetta `XTTS fine-tuning` (script ufficiale
   Coqui `GPT_trainer`): ~10-30 min di audio, qualche ora di GPU.
3. Generi **offline** le frasi fisse → mp3 → le metti nella cache TTS che già
   esiste (`player.py`, `WARM_TTS_PHRASES` e `/tmp/franchino_cache`).

**Vantaggio**: le frasi canoniche partono a latenza **zero** e con la voce vera.
**Limite**: le risposte libere restano su edge-tts.

> Licenza: Coqui Public Model License — uso consentito ma **non commerciale**.
> Se un giorno il bot diventa commerciale, questa strada va rivista.

### C) RVC — cambia il timbro a una voce qualsiasi

RVC non genera: **converte** l'audio di edge-tts nel timbro di Franchino. È il
modo per avere *qualsiasi frase* con la sua voce senza addestrare un TTS da zero.

1. Dataset: bastano i `dataset/wavs/` (44.1 kHz), **senza trascrizioni**.
2. Addestra un modello RVC su Colab (10-15 min di audio, 30-60 min di GPU).
3. Inferenza: troppo lenta su 2 vCPU per il live (~2-5x RT), ma perfetta per
   pre-renderizzare le frasi fisse.

---

## 3. Cosa mettere in produzione (e cosa no)

Sul tuo hardware l'architettura sensata resta **a due livelli**, quella già
impostata per il TTS:

| Contenuto | Motore | Latenza |
|---|---|---|
| Risposte live (chat, comandi, domande) | edge-tts (oggi) oppure **Piper fine-tunato** | 0,3 s |
| Frasi canoniche, favole, saluti, sigla | **XTTS/RVC pre-renderizzato** in cache | 0 ms |
| Clonazione in tempo reale | ❌ non praticabile su 2 vCPU | 15 s - 3 min |

Se il fine-tune Piper riesce bene, si può sostituire edge-tts del tutto e avere
**una sola voce, quella vera, in tempo reale**. È l'unico percorso che porta a
"qualsiasi frase con la sua voce" senza GPU a runtime.

---

## 4. Se vuoi provare una voce prima di addestrare

Prima di investire ore di GPU conviene capire se il timbro è convincete:

1. Estrai 20-30 secondi della voce più pulita che hai (senza musica).
2. Fai un test **zero-shot** con XTTS-v2 su Colab (bastano 10 secondi di
   riferimento, nessun training): la frase è detta con quel timbro.
3. Se il risultato convince, si addestra; se no, si risparmiano le ore.

Nel frattempo, la strada a costo zero che migliora subito l'imitazione attuale è
**tarare la DSP chain** sui dati reali: il builder ti dà già l'audio pulito per
misurare pitch medio e formanti e correggere i filtri di `player.py` (che oggi
sono a orecchio).

---

## 5. Riferimenti nel codice

- Raccolta e pulizia del dataset: `scripts/build_voice_dataset.py`
- Test del builder (senza rete): `test_voice_dataset.py`
- TTS attuale + cache: `player.py` (`generate_tts_file`, `WARM_TTS_PHRASES`)
- Catena DSP: `player.py`, sezione "Catena DSP Franchino il Vocalist"

---

## 6. Taratura DSP misurata (fatta, e rifatta)

La catena di effetti di `player.py` era tarata a orecchio. Ora è tarata sui dati:
`scripts/analyze_voice.py` misura **F0** (autocorrelazione) e **LTAS** (energia
per banda d'ottava) e ne calcola la distanza fra due file.

### Riferimento corretto

Le **11 registrazioni** estratte dalla voce vera (16,2 s: sei "Magia!", quattro
"una favola", un "grazie") sono il riferimento definitivo: voce parlata, senza
musica, con la pulizia misurata e verificata.

**F0 mediana: 157 Hz.**

> ⚠️ **Correzione di una taratura precedente.** La prima stima usava 10 secondi
> ricavati da tracce dance, dove Franchino **urla** sopra la base: misurava
> 197 Hz. Su quel riferimento sbagliato avevo preferito `DiegoNeural` (193 Hz).
> Con le registrazioni pulite il dato vero è 157 Hz, e `GiuseppeNeural` a −6 Hz
> (155 Hz) era già quasi perfetto. La voce primaria è quindi tornata Giuseppe.

| | Giuseppe −6 Hz | Diego 0 Hz | **Giuseppe −6 + catena nuova** |
|---|---|---|---|
| Pitch (riferimento 157 Hz) | 155 Hz (−0,2 st) | 193 Hz (**+3,6 st**) | **152 Hz (−0,6 st)** |
| Distanza 250-4000 Hz, senza EQ | 2,24 dB | 2,32 dB | — |
| Distanza con la catena | 1,72 dB (precedente) | — | **1,16 dB** |

Catena finale (in `player.py`):

```
equalizer=f=250:t=q:w=1.0:g=-3      # la sintesi ha troppo poco corpo a 250 Hz
equalizer=f=500:t=q:w=1.2:g=1.5     # dov'è il picco reale della sua voce
equalizer=f=2000:t=q:w=1.2:g=-2.5   # la sintesi esagera fra 2 e 4 kHz
equalizer=f=4000:t=q:w=1.5:g=-2
```

Restano invariati compressore, eco e volume (sono firma della persona, non
timbro). Le bande **63/125 Hz** e **8 kHz** sono escluse dal confronto: demucs
svuota i bassi e l'mp3 della sorgente taglia gli alti.

### Loudness delle frasi registrate

Le registrazioni avevano **5,8 LU di scarto** fra loro: due "Magia!" andavano
da −14,0 a −19,8 LUFS, cioè uno sembrava il quadruplo dell'altro. In un canale
vocale è un errore di mixing evidente. `scripts/mine_phrases.py` ora normalizza
ogni clip a **−16 LUFS** con gain statico e limiter (non `loudnorm` dinamico:
su 1-2 secondi pomperebbe). Dopo: scarto **0,6 LU**.

### Come rifare la misura

```bash
pip install numpy        # unica dipendenza dello script
python scripts/analyze_voice.py riferimento.wav voce_bot.wav --compare
```

Ascolto A/B: `dataset/_voce_dsp/CONFRONTO_prima_diego.mp3` e
`CONFRONTO_dopo_giuseppe.mp3` (cartella ignorata da git).
