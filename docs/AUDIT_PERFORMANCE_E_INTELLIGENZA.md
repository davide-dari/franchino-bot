# Audit Prestazioni & Intelligenza — Franchino Bot

Obiettivo: rendere Franchino il più veloce possibile e davvero intelligente quando qualcuno gli chiede qualcosa (voce o chat).
Analisi statica del codice + misure CPU locali + ricostruzione del budget temporale sul target Oracle Ampere A1 (2 vCPU / 12 GB).

## 1. TL;DR — le 5 cose che contano

| # | Problema | Impatto | Fix |
|---|---|---|---|
| 1 | Il cervello LLM non risponde MAI: num_predict 220 + timeout 4.5s su 2 vCPU ARM => JSON troncato => json.loads fallisce => fallback euristico | Il bot sembra intelligente solo grazie alle regex: l'AI generativa è di fatto morta | num_predict ~90-110, num_ctx 2048, timeout reale 8s solo per intent conversazionali, stream:true, structured output |
| 2 | In chat il bot non capisce niente: on_message (main.py:527-575) gestisce solo la liveness | Percepito come "scemo" da chi scrive invece di parlare | Unificare chat e voce in un Router che chiama lo stesso brain |
| 3 | ~1.2s di attesa inerte: 650ms di silenzio fisso + 1 round-trip HTTP extra per ogni domanda (_check_ollama_status) + TTS generato tutto prima di partire | Latenza percepita inutilmente alta | Endpointing adattivo ~400ms, cache TTL sullo status Ollama, TTS in streaming verso ffmpeg |
| 4 | Over-subscription CPU nel docker-compose: limiti 2.0 + 1.5 + 0.5 = 4.0 CPU su una macchina con 2 vCPU | Ollama litiga con DSP audio e ffmpeg => latenze a caso e picchi | Ripartire i limiti (ollama 1.3 / bot 0.55 / pg 0.15) + OLLAMA_FLASH_ATTENTION=1, KV_CACHE_TYPE=q8_0 |
| 5 | Playlist YouTube rotte (sources.py:522-555): 18 righe irraggiungibili dopo un raise => ritorna None | TypeError a valle, funzione dichiarata ma inesistente | De-indentare il blocco |

## 2. Metodo e baseline

### 2.1 Percorso di una richiesta oggi
Discord voice -> FranchinoVoiceSink.write() ~1-3 us/pacchetto (OK) -> _monitor_speech_loop() tick 40ms (OK)
-> 650ms di silenzio fisso (attesa inerte #1) -> _process_utterance() audioop+AGC+downsample (~1-2ms, OK)
-> recognize_google cloud (400-900ms) -> _handle_transcribed_text() ~1900 righe, 146 blocchi if, 32 regex
   -> Jack engine (5.8ms cold, ~0ms cached) + is_wiki_question 0.28ms
   -> fast-path regex (timer, stop, saluti, soundboard, play/queue)
   -> brain.process_user_speech() -> _check_ollama_status() round-trip HTTP #2 (50-200ms)
      -> POST /api/chat 4.5s timeout, 220 token
      -> speak_tts(): edge-tts su file COMPLETO (300-700ms) -> ffmpeg 7 filtri + play

### 2.2 Misure CPU locali (Mac ARM, Python 3.13, venv del progetto)
| Funzione | Costo medio | Nota |
|---|---|---|
| jack_engine.classify() cold (400 query uniche) | 5.8 ms | lru_cache(1024) attivo: a caldo ~0 ms |
| outlands_wiki.is_wiki_question() | 0.276 ms | |
| outlands_wiki.normalize_uo_speech() | 0.034 ms | |

Conclusione: la CPU non è il collo di bottiglia. Il bot è già ottimizzato lato Python: si perde tempo in attese di rete e budget mal dimensionati.
Su Ampere A1 single-core i valori vanno moltiplicati ~1.5-2x: restano irrilevanti rispetto ai secondi persi altrove.

### 2.3 Suite di test (baseline pre-modifica)
1085 passed, 14 failed (21.3s). I 14 fallimenti sono ambientali, non di codice: SQLite non riesce ad aprire
data/outlands_knowledge.db in sandbox (sidecar WAL non scrivibile) e 1 test richiede rete.
Baseline da non peggiorare: >= 1085 passed.

## 3. Problema #1: perché il cervello AI non risponde

brain.py:110-215 — process_user_speech() è l'unico punto in cui Gemma 2 2B viene interrogato.

    payload = {"model": self.model, "format": "json", "stream": False,
               "options": {"temperature": 0.5, "num_predict": 220, "num_thread": 2}}
    timeout=aiohttp.ClientTimeout(total=4.5)

Budget su 2 vCPU ARM (da confermare con `ollama run gemma2:2b --verbose`):
| Stadio | Costo | Cumulato |
|---|---|---|
| Prompt eval, KV cache fredda (system prompt ~1400 token) | 20-40 s | fuori budget |
| Prompt eval, KV in cache (keep_alive=24h già impostato) | 0.6-1.2 s | 1.2 s |
| Generazione 220 token @ 8-12 tok/s | 18-27 s | ~20-28 s |
| Timeout configurato | - | 4.5 s |

Con 4.5s il modello produce ~40-55 token: il JSON con 6 campi non si chiude, _parse_json_decision() (brain.py:218)
va in eccezione e ricade su _fallback_heuristic_intent. Ogni domanda passa dalle regex.
Stessa sorte per generate_live_fable (num_predict 140, timeout 4.0s) e explain_song_context (75, 3.5s).

### Fix consigliati (in ordine)
1. Spacchettare il JSON: chiedere solo {"intent": "...", "slots": {...}} (~25-40 token) e comporre
   voice_reply/chat_reply in Python dai template per intent. Da 220 a ~40 token = -80% di generazione.
2. format come JSON Schema (Ollama >= 0.5): grammatica vincolata, zero allucinazioni di formato.
3. num_ctx: 2048 esplicito (default gemma2:2b = 8192 => KV 4x più grande, prompt eval più lento).
4. Due timeout: 1.2s per i turni che sembrano comando, 8-10s in streaming per i turni conversazionali.
5. stream:true + TTS a frase: appena arriva la prima frase completa di voice_reply parte il TTS.
   Latenza percepita da "20s (mai)" a "~1.5-2s".
6. Jack engine come router primario (5.8ms, oggi chiamato solo dopo ~150 righe di regex):
   se probability >= 0.55 si esegue e basta, LLM solo per chat/spiegazioni.

## 4. Problema #2: la chat testuale è muta

main.py:527-575 gestisce solo liveness_triggers ("ci sei", "sei vivo"...). Quindi:
- "franchino ci sei?" -> risponde
- "franchino che ore sono?" -> silenzio
- "franchino come si fa la build del tamer?" -> silenzio (serve "!outlands ...")

Tutta l'intelligenza (Jack + RAG SQLite + LLM + meteo + timer + sport) è irraggiungibile da tastiera, tranne 68 comandi hybrid.

Fix: estrarre un FranchinoRouter.route(text, user, source="voice"|"chat") -> Intent e chiamarlo da on_message.
Benefici: bot intelligente anche in chat, test unitari del router senza Discord/voce, telemetria per intent.
Refactor incrementale: prima ondata su smart_assistant, wiki_guide, sports_*, chat (~90% delle domande reali).

## 5. Latenza: dove vanno i millisecondi

Domanda vocale generica, stato stabile, oggi:
| Fase | Oggi | Dopo | Intervento |
|---|---|---|---|
| Silenzio di fine frase | 650 ms | 400 ms | SILENCE_THRESHOLD_SEC adattivo (5.1) |
| STT Google cloud | 400-900 ms | 150-300 ms con whisper locale (5.2) | |
| Round-trip _check_ollama_status | 50-200 ms (fino a 2s) | ~0 ms | cache TTL 60s (5.3) |
| Classificazione Jack | 5.8 ms | 5.8 ms | già ottima |
| LLM (se serve) | 4.5s => timeout => fallback | 1.5-2.5s alla prima frase | streaming + token budget |
| notify_channel | 80-200 ms | in parallelo | create_task |
| edge-tts | 300-700 ms su file completo | 150-250 ms alla prima frase | streaming (5.4) |
| Attesa stop player | 0-350 ms | ~20 ms | stop() + evento (5.4) |
| Totale comando semplice | ~1.6-2.5 s | ~0.8-1.2 s | |
| Totale domanda conversazionale | ~2.5s ma con risposta stupida | ~2-3s con risposta AI vera | |

### 5.1 Endpointing adattivo (-250 ms)
voice_commands.py:384 ha 650ms fissi. Con il pre-roll già presente (PREROLL_CHUNKS=25):
    silence_needed = self.SILENCE_THRESHOLD_SEC
    if phrase_dur > 1.5 and silence_dur >= 0.40:
        silence_needed = 0.40
Primo passo a basso rischio: SILENCE_THRESHOLD_SEC = 0.50.

### 5.2 STT: da cloud a locale (-200/-600 ms + resilienza)
recognize_google (voice_commands.py:1006) è HTTP verso Google senza API key: nessuno SLA, rate limit, no offline, 400-900ms.
Su 2 vCPU ARM faster-whisper small int8: ~350-600ms per 3s di audio; tiny/base ~120-250ms, in-process.
Percorso: wake word locale (openWakeWord/Vosk, 5-15ms/frame) -> faster-whisper locale -> Google solo come fallback.
Oggi ogni frase di chiunque nel canale viene trascritta in cloud (voice_commands.py:1015).
Attenzione: contendere CPU con Ollama => vedi §6.

### 5.3 Una sola sessione HTTP e status Ollama in cache
brain.py crea una nuova aiohttp.ClientSession() ad ogni chiamata (97, 170, 665, 693) e _check_ollama_status()
fa GET /api/tags prima di ogni inferenza con timeout 2s.
    class FranchinoBrain:
        _session = None
        _status_ok_at = 0.0
        async def _check_ollama_status(self):
            if time.monotonic() - self._status_ok_at < 60.0:
                return True
            ...  # poi self._status_ok_at = time.monotonic()
Guadagno: -50/-200 ms per domanda + keep-alive TCP. Da applicare anche a sports_scraper.py (3 sessioni).

### 5.4 TTS: streaming invece di "genera tutto, poi suona"
player.py:447-520 scarica l'intero mp3 (300-700ms), player.py:559 aspetta fino a 350ms che il player si fermi, poi ffmpeg.
1. Streaming: edge_tts.stream() -> pipe verso ffmpeg -> play() sul primo chunk (~150-250ms).
2. Cache: oggi md5 del testo; aggiungere preriscaldamento delle frasi canoniche al boot (DEFAULT_FABLES, saluti) => 0 ms.
3. Stop player: stop() + attesa dell'evento after invece del polling => -0/-350 ms.
4. 7 filtri ffmpeg (4 eq + acompressor + aecho + volume): cachare il file POST-DSP, oggi si rifà il DSP ad ogni play.

## 6. Docker: 4 CPU richieste su 2 disponibili
central-hub-db 0.5 + ollama 2.0 + franchino-bot 1.5 = 4.0 su 2 vCPU.
In contentione il kernel penalizza tutti: Ollama fa 2 thread di calcolo puro e il sink audio (20ms/pacchetto)
e ffmpeg finiscono in starvation => "ogni tanto lagga / balbetta".
Proposta: ollama 1.30 (+ OLLAMA_NUM_THREADS=2, OLLAMA_FLASH_ATTENTION=1, OLLAMA_KV_CACHE_TYPE=q8_0),
franchino-bot 0.55, central-hub-db 0.15.
Inoltre benchmark reale del modello:
    for m in gemma2:2b qwen2.5:1.5b-instruct llama3.2:1b gemma3:1b; do
      ollama run $m --verbose "Rispondi in italiano in una frase: che ore sono?" 2>&1 | grep "eval rate"; done
qwen2.5:1.5b-instruct è tipicamente 2-3x più veloce di gemma2:2b a parità di qualità su frasi brevi in italiano.

## 7. Diventare più intelligenti nel tempo
central_hub archivia già ogni interazione (raw_query, intent, ai_response, voice_summary, metadata). Oggi è solo in scrittura.

### 7.1 Cache semantica degli intenti (massimo ritorno)
1. Al boot carica da Postgres gli ultimi ~5000 (raw_query, intent).
2. Embedding per query (nomic-embed-text / all-minilm, ~10ms).
3. Nuova richiesta: similarità coseno. sim > 0.93 => riusa intent e music_query archiviati, zero LLM.
4. 0.80 < sim < 0.93 => inietta i 3 esempi più simili come few-shot: il modello impara il gergo del server.
Le richieste ripetute (60-70% in una community) diventano istantanee e il modello migliora sulle nuove.

### 7.2 Memoria conversazionale breve
Oggi ogni richiesta è stateless: "build del tamer" -> "e per il magery?" fallisce
(esiste solo l'hack _pending_wake con timeout 10s per i comandi musicali).
Deque di 3 turni per user_id con TTL 3 minuti, passati come messaggi (~60 token di prompt eval).

### 7.3 RAG da keyword a ibrido
get_outlands_rag_context(text, max_chars=900) fa retrieval locale (sub-ms, ottimo). Migliorie:
rerank BM25 + embedding e top-k (i 900 char oggi sono il primo match, non il migliore);
citazione obbligatoria di page_title + URL nel contesto (meno allucinazioni); chunking per sezione, non per pagina.

### 7.4 Risposte canoniche
smart_assistant (ora/meteo/math/timer) è deterministico e veloce: non deve mai passare dall'LLM.
Verificare che i fast-path scattino prima di process_user_speech: oggi Jack è chiamato a riga 2850
ma ci sono ~1600 righe di regex prima. Jack come router primario risolve anche questo.

## 8. Piano in 3 ondate

### Ondata 1 — Quick win (mezza giornata, rischio basso)
1. Fix codice irraggiungibile playlist YouTube (sources.py:535-555)
2. Cache TTL 60s su _check_ollama_status + sessione aiohttp condivisa (brain.py)
3. num_predict 220->100 + num_ctx 2048 + timeout 8s + stream + parse incrementale (brain.py:145-200)
4. Endpointing 650->500 ms (voice_commands.py:384)
5. Ripartizione CPU in compose + OLLAMA_FLASH_ATTENTION + KV_CACHE_TYPE=q8_0
6. Warm cache TTS all'avvio (player.py:447)
7. Telemetria per stadio: log STT=xms LLM=xms TTS=xms intent=x (nuovo latency.py + 3 call-site)

### Ondata 2 — Architettura (2-3 giorni)
8. FranchinoRouter condiviso chat+voce, estratto da _handle_transcribed_text, Jack come router primario
9. TTS in streaming edge-tts -> ffmpeg (prima frase ~200ms) + cache del file post-DSP
10. on_message -> brain per le domande testuali
11. Benchmark modelli e scelta definitiva

### Ondata 3 — Intelligenza incrementale (1 settimana)
12. Cache semantica degli intenti su central_hub + few-shot dinamico
13. Memoria conversazionale breve per utente
14. STT locale (faster-whisper) + wake word locale (openWakeWord)
15. RAG ibrido con rerank e citazioni

## 9. Cosa misurare
Una riga di log per richiesta:
req_id=<id> src=voice intent=smart_assistant conf=0.46 stt=642ms jack=6ms llm=0ms tts=310ms first_audio=980ms total=1.21s fallback=heuristic
Con 200 righe si risponde a tutto: intent dominante, timeout LLM, peso dell'STT cloud, fallback TTS.

## 10. Riepilogo
| Intervento | Sforzo | Guadagno latenza | Guadagno intelligenza |
|---|---|---|---|
| Ondata 1 | ~4 h | -0.4/-0.7 s per richiesta | da 0% a ~100% di risposte LLM reali |
| Ondata 2 | 2-3 g | -0.6/-1.2 s (streaming + locale) | chat testuale intelligente |
| Ondata 3 | 1 sett. | -0.3/-0.5 s (cache semantica) | apprendimento dal gergo del server |

Se si può fare una cosa sola: l'intervento #3 dell'ondata 1. Oggi l'AI non parla — tutto il resto è secondario.
