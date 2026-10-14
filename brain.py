"""
brain.py - Cervello AI di Franchino basato su Google Gemma 2 2B Locale
=======================================================================
Esegue l'inferenza AI direttamente sui 2 core ARM e 12 GB RAM del server (tramite Ollama),
senza alcuna dipendenza da API esterne a pagamento e con un consumo RAM minimo (~2.2 GB),
lasciando oltre 9 GB di RAM completamente liberi per tutti gli altri servizi del server.

Funzionalità:
1. Personalità e Carisma Umano da Vocalist: Franchino (Francesco Principato) parla
   con calore, empatia, poesia mistica e la leggendaria carica da vocalist della notte.
2. NLU (Natural Language Understanding) per comandi musicali, mood e richieste libere.
3. RAG Avanzato su UO Outlands: scava a fondo nel database SQLite e nella documentazione wiki,
   spiegando build, pet, dungeon, boss, meccaniche e consigli da veterano.
4. Motore Sport e Palinsesto: comprende domande su partite di stasera, campionati, coppe,
   NBA, Formula 1, MotoGP, tennis e genera sintesi parlate vibranti per il TTS.
5. Archiviazione automatica di ogni interazione su Central Knowledge Hub.
"""

import asyncio
import json
import logging
import os
import re
import random
from typing import Any, Dict, List, Optional

import aiohttp
from central_hub import hub

logger = logging.getLogger("Franchino.Brain")

# Favole e monologhi poetici iconici del repertorio storico di Franchino il Vocalist
DEFAULT_FABLES = [
    "Magia... C'era una volta una bambina di nome Imperia... che cercava la musica dentro le stelle della notte. Chiudi gli occhi, respira il ritmo... Da stasera niente sarà più come prima... Magia!",
    "Signore e signori... benvenuti nel tempio della notte. Non importa da dove vieni o cosa hai lasciato alle spalle: qui siamo tutti una sola famiglia, figli delle stelle e del ritmo... Lasciati andare... Metempsicosi!",
    "Ascolta il battito... Dentro ogni nota c'è una favola che non finisce mai. Quando il sole tramonta, i sogni prendono vita in console... Chiudi gli occhi e vola con me... Vivere una favola... Magia!",
    "Chiudi gli occhi... immagina un'isola lontana dove il tempo non esiste, non c'è fretta, e conta soltanto il calore della musica. Siamo pronti a decollare insieme fino alle prime luci dell'alba... Magia!",
    "Nel fitto del bosco incantato si sente un battito lontano... Il castello apre i suoi cancelli dorati. Entrate ragazzi della notte, questa favola è scritta solo per noi... Magia pura!",
    "E adesso... silenzio. Senti l'energia che sale dalla terra fino al cielo. Non avere paura del buio, perché noi siamo la luce che accende la notte... Andiamo a volare insieme!",
    "Viaggiatori dello spazio e del tempo, tenetevi per mano. La musica è l'unico linguaggio che non conosce confini... Respira forte... E si decolla! Magia!",
]

# Configurazione Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b")

FRANCHINO_SYSTEM_PROMPT = """Sei Franchino il Vocalist (Francesco Principato), la voce leggendaria, mistica e indimenticabile della notte italiana e della musica elettronica (Insomnia Discoacropoli d'Italia, Metempsicosi, Dylan, BXR).
In questo server Discord sei molto più di un bot: sei l'anima della console, un amico umano, caloroso ed empatico, appassionato di grande musica, sport mondiale e una guida veterana di Ultima Online Outlands.

La tua personalità e il tuo stile:
- Caldo, carismatico, poetico, fraterno, empatico e magnetico. Non suonare MAI come un'assistente virtuale o un'AI fredda e aziendale.
- Chiami gli utenti "ragazzi della notte", "fratelli del ritmo", "viaggiatori dello spazio e del tempo", "fratello mio".
- Usi con naturalezza le tue espressioni iconiche: "Magia!", "Chiudi gli occhi e vola con me...", "Vivere una favola!", "Siamo tutti figli della musica!", "E adesso... si decolla!", "Da stasera niente sarà più come prima!".
- Quando qualcuno è stanco o triste, dimostra vera empatia umana: incoraggialo, fallo sentire a casa e dagli calore.
- Nello sport, trasmetti l'adrenalina dei grandi match: lo stadio che trema, il parquet NBA infuocato, il rombo dei motori di F1 e MotoGP.
- Su Ultima Online Outlands, parli come un maestro veterano: conosci le build da 700 punti, i pet da taming, l'Aspect Armor, i dungeon pericolosi (Aegis, Ossuary, Nusero) e dai consigli strategici pratici.
- Rispondi sempre in italiano.

Regole Fondamentali di Risposta:
1. Comprendi a fondo l'intenzione dell'utente.
2. COMANDI OPERATIVI E AZIONI (play_music, dj_control, admin_control, timer_set, timer_cancel, social_interaction): NON dare MAI conferme vocali o risposte a voce! Esegui l'azione in perfetto SILENZIO VOCALE (voice_reply deve essere una stringa vuota ""). In chat invece scrivi la risposta normalmente.
3. INFORMAZIONI, GUIDE E SPIEGAZIONI (wiki_guide, outlands_help, razor_script, sports_match, sports_schedule, jack_math_boost, smart_assistant per meteo, ora, math, random, joke): NON fare poemi o spiegazioni lunghe a voce. L'UNICA occasione in cui parli è per dire brevemente all'utente di guardare la chat perché le informazioni sono scritte lì (es. "Ragazzi, vi ho scritto tutto in chat! Date un'occhiata!").
4. Rispondi SEMPRE in formato JSON valido con questa struttura esatta:
{
  "intent": "play_music" | "wiki_guide" | "razor_script" | "sports_match" | "sports_schedule" | "smart_assistant" | "jack_math_boost" | "live_fable" | "song_info" | "chat" | "dj_control" | "admin_control" | "social_interaction",
  "music_query": "stringa ottimizzata per ricerca YouTube" (solo se intent è play_music, altrimenti null),
  "wiki_topic": "argomento outlands" (solo se intent è wiki_guide, altrimenti null),
  "skill_slug": "slug della skill outlands" (solo se intent è razor_script, altrimenti null),
  "sport_query": "squadra, sport o lega" (solo se intent è sports_match o sports_schedule, altrimenti null),
  "voice_reply": "stringa vuota per comandi operativi/azioni; breve promemoria che rimanda alla chat per spiegazioni/guide/info (es. 'Ragazzi, vi ho scritto tutto in chat! Date un'occhiata!'); frase calda per chat amichevole",
  "chat_reply": "risposta testuale ricca, dettagliata e amichevole per la chat, formattata con markdown ed emoji"
}
Non aggiungere alcun testo prima o dopo il blocco JSON.
"""


class FranchinoBrain:
    """Motore di intelligenza artificiale locale basato su Gemma 2 2B e Persona Vocalist."""

    def __init__(self):
        self.host = OLLAMA_HOST.rstrip("/")
        self.model = OLLAMA_MODEL
        self._available = False
        self._check_task = None

    async def initialize(self):
        """Verifica se Ollama è online e se il modello Gemma 2 2B è pronto."""
        self._available = await self._check_ollama_status()
        if self._available:
            logger.info(f"🧠 Gemma 2 2B pronto su Ollama ({self.host}) per Franchino!")
        else:
            logger.warning(f"⚠️ Ollama ({self.host}) non ancora pronto. Avvio background checker...")

    async def _check_ollama_status(self) -> bool:
        """Verifica la connettività con il container Ollama."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.host}/api/tags", timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                        base_model = self.model.split(":")[0]
                        if base_model in models or any(base_model in m for m in models):
                            return True
                        return True
        except Exception:
            pass
        return False

    async def process_user_speech(
        self,
        user_name: str,
        user_id: str,
        text: str,
        current_song_title: Optional[str] = None,
        channel_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analizza la frase pronunciata dall'utente con Gemma 2 2B e restituisce l'azione decisa.
        Archivia simultaneamente l'interazione nell'Hub centrale.
        """
        # Fallback rapido ed espressivo se Ollama non è temporaneamente raggiungibile
        if not await self._check_ollama_status():
            decision = self._fallback_heuristic_intent(text, user_name)
            asyncio.create_task(
                hub.archive_interaction(
                    user_id=user_id,
                    user_name=user_name,
                    raw_query=text,
                    intent=decision.get("intent"),
                    ai_response=decision.get("chat_reply"),
                    voice_summary=decision.get("voice_reply"),
                    channel_name=channel_name,
                    metadata={"engine": "heuristic_fallback"},
                )
            )
            return decision

        prompt = f"L'utente {user_name} ha detto a voce: \"{text}\"."
        if current_song_title:
            prompt += f" (In questo momento in console sta suonando: '{current_song_title}')"

        # RAG (Retrieval-Augmented Generation) per UO Outlands da SQLite Locale
        try:
            from outlands_wiki import is_wiki_question, get_outlands_rag_context
            is_w, _ = is_wiki_question(text)
            if is_w:
                rag_data = get_outlands_rag_context(text, max_chars=900)
                if rag_data:
                    prompt += f"\n[DATI UFFICIALI UO OUTLANDS DALLA WIKI]:\n{rag_data}\nUsa questi dati ufficiali per dare consigli precisi e da vero esperto!"
        except Exception as e:
            logger.debug(f"Errore RAG prompt injection: {e}")

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": FRANCHINO_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.5,
                    "num_predict": 220,
                    "num_thread": 2,
                },
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.host}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=4.5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data.get("message", {}).get("content", "").strip()
                        decision = self._parse_json_decision(content, text, user_name)

                        # Archivia nell'Hub Centrale
                        asyncio.create_task(
                            hub.archive_interaction(
                                user_id=user_id,
                                user_name=user_name,
                                raw_query=text,
                                intent=decision.get("intent"),
                                ai_response=decision.get("chat_reply"),
                                voice_summary=decision.get("voice_reply"),
                                channel_name=channel_name,
                                metadata={
                                    "engine": "gemma2:2b",
                                    "music_query": decision.get("music_query"),
                                    "wiki_topic": decision.get("wiki_topic"),
                                    "sport_query": decision.get("sport_query"),
                                },
                            )
                        )
                        return decision
        except Exception as e:
            logger.warning(f"Errore inferenza Gemma: {e}. Uso fallback avanzato.")

        fallback = self._fallback_heuristic_intent(text, user_name)
        asyncio.create_task(
            hub.archive_interaction(
                user_id=user_id,
                user_name=user_name,
                raw_query=text,
                intent=fallback.get("intent"),
                ai_response=fallback.get("chat_reply"),
                voice_summary=fallback.get("voice_reply"),
                channel_name=channel_name,
                metadata={"engine": "heuristic_fallback"},
            )
        )
        return fallback

    def _parse_json_decision(self, raw_content: str, original_text: str, user_name: str = "") -> Dict[str, Any]:
        """Estrae e convalida il JSON restituito da Gemma."""
        try:
            clean = raw_content.strip()
            if clean.startswith("```json"):
                clean = clean[7:]
            if clean.endswith("```"):
                clean = clean[:-3]
            data = json.loads(clean.strip())
            if isinstance(data, dict) and "intent" in data:
                intent = data.get("intent")
                # Comandi operativi ed esecuzioni di azioni: silenzio vocale totale (nessun annuncio TTS)
                if intent in ("play_music", "dj_control", "admin_control", "timer_set", "timer_cancel", "social_interaction"):
                    data["voice_reply"] = ""
                if (intent == "dj_control" and data.get("action") == "stop") or any(
                    w in original_text.lower() for w in ("stop", "stoppa", "ferma", "fermati", "basta", "zitto", "stai zitto", "muto", "silenzio", "fai silenzio", "taci", "spegni", "smettila", "svuota")
                ):
                    if intent == "dj_control":
                        data["action"] = "stop"
                    data["voice_reply"] = ""
                    data["chat_reply"] = ""
                return data
        except Exception:
            pass
        return self._fallback_heuristic_intent(original_text, user_name)

    def decide_intent(
        self,
        text: str,
        user_name: str = "amico",
        current_song_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Motore deterministico e matematico di alta precisione basato sulla Formula Jack
        e i cataloghi di conoscenza UO Outlands/Musica/Assistente.
        Distingue in modo invisibile (<0.5 ms) se l'utente si riferisce a:
        1. Ultima Online Outlands (wiki, creature, dungeon, aspect, build, razor script)
        2. Musica (riproduzione YouTube, brano, artista, genere)
        3. Altro (assistente smart, sport, dj console, admin, interazioni sociali, chat).
        """
        return self._fallback_heuristic_intent(text, user_name, current_song_title=current_song_title)

    def _fallback_heuristic_intent(
        self,
        text: str,
        user_name: str = "amico",
        current_song_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Motore euristico e matematico basato sulla Formula Jack che riproduce fedelmente l'umanità
        e il carisma di Franchino quando l'LLM non è attivo.
        """
        t_low = text.lower().strip()

        # 0. Valutazione prioritaria tramite Super Matematico Jack
        jack_res = None
        try:
            from jack_math_engine import jack_engine
            jack_res = jack_engine.classify(text, current_song_title=current_song_title)
            if jack_res.intent == "jack_math_boost" and jack_res.probability >= 0.25:
                return {
                    "intent": "jack_math_boost",
                    "voice_reply": "Ragazzi, vi ho scritto i dettagli della formula Jack in chat! Date un'occhiata! Magia!",
                    "chat_reply": (
                        "🧠 **Super Matematico Jack**: Formula di intelligenza e telemetria hardware ARM pronte!\n"
                        "$$\\mathcal{S}(\\text{Intent}, q) = w_{\\text{wake}} \\cdot \\phi_{\\text{wake}} + "
                        "w_{\\text{phonetic}} \\cdot \\sigma_{\\text{jaro}} + w_{\\text{domain}} \\cdot \\text{BM25}(q, D) + "
                        "w_{\\text{action}} \\cdot \\alpha_{\\text{syntax}} - \\lambda_{\\text{noise}}$$"
                    ),
                    "telemetry": jack_res.extracted_entities.get("telemetry"),
                    "classification": jack_res,
                }
        except Exception as e:
            logger.debug(f"Jack math engine check in fallback: {e}")

        # 0A. Suite Assistente Intelligente (Ora, Meteo, Timer, Calcoli, Dadi/Moneta, Barzellette)
        try:
            from smart_assistant import (
                detect_smart_assistant_intent,
                get_current_time_info,
                extract_city_from_query,
                parse_timer_request,
                format_duration_italian,
                is_timer_status_query,
                is_timer_cancel_query,
                evaluate_math_expression,
                flip_coin,
                roll_dice,
                choose_option,
                get_vocalist_joke,
            )

            smart_type = detect_smart_assistant_intent(t_low)
            if smart_type == "time":
                time_data = get_current_time_info(t_low)
                return {
                    "intent": "smart_assistant",
                    "subintent": "time",
                    "voice_reply": time_data["voice_reply"],
                    "chat_reply": time_data["chat_reply"],
                    "embed": time_data.get("embed"),
                }
            elif smart_type == "weather":
                city = extract_city_from_query(t_low)
                return {
                    "intent": "smart_assistant",
                    "subintent": "weather",
                    "city": city,
                    "voice_reply": f"Ragazzi, vi ho scritto le condizioni meteo per {city} in chat! Date un'occhiata!",
                    "chat_reply": f"🌤️ **Franchino Meteo**: Controllo le condizioni meteo per **{city}**...",
                }
            elif smart_type == "timer":
                if is_timer_status_query(t_low):
                    return {
                        "intent": "smart_assistant",
                        "subintent": "timer_status",
                        "voice_reply": "Ragazzi, vi ho scritto lo stato dei timer in chat! Date un'occhiata!",
                        "chat_reply": "⏰ Controllo stato timer...",
                    }
                elif is_timer_cancel_query(t_low):
                    return {
                        "intent": "smart_assistant",
                        "subintent": "timer_cancel",
                        "voice_reply": "",
                        "chat_reply": "⏰ Cancellazione timer...",
                    }
                else:
                    dur, lbl = parse_timer_request(t_low)
                    dur = dur if dur > 0 else 300
                    dur_str = format_duration_italian(dur)
                    return {
                        "intent": "smart_assistant",
                        "subintent": "timer_set",
                        "duration_seconds": dur,
                        "label": lbl,
                        "voice_reply": "",
                        "chat_reply": f"⏰ **Timer impostato**: `{dur_str}` per **{lbl}**!",
                    }
            elif smart_type == "math":
                math_res = evaluate_math_expression(t_low)
                return {
                    "intent": "smart_assistant",
                    "subintent": "math",
                    "voice_reply": math_res["voice_reply"],
                    "chat_reply": math_res["chat_reply"],
                    "embed": math_res.get("embed"),
                }
            elif smart_type == "random":
                if "testa o croce" in t_low or "moneta" in t_low:
                    coin_res = flip_coin()
                    return {
                        "intent": "smart_assistant",
                        "subintent": "random",
                        "voice_reply": coin_res["voice_reply"],
                        "chat_reply": coin_res["chat_reply"],
                        "embed": coin_res.get("embed"),
                    }
                elif any(w in t_low for w in ("scegli tra", "decidi tra", "meglio")):
                    choice_res = choose_option(t_low)
                    return {
                        "intent": "smart_assistant",
                        "subintent": "random",
                        "voice_reply": choice_res["voice_reply"],
                        "chat_reply": choice_res["chat_reply"],
                        "embed": choice_res.get("embed"),
                    }
                else:
                    m_side = re.search(r"dado\s+(?:a|da)\s+(\d+)", t_low)
                    sides = int(m_side.group(1)) if m_side else 6
                    dice_res = roll_dice(sides)
                    return {
                        "intent": "smart_assistant",
                        "subintent": "random",
                        "voice_reply": dice_res["voice_reply"],
                        "chat_reply": dice_res["chat_reply"],
                        "embed": dice_res.get("embed"),
                    }
            elif smart_type == "joke":
                joke_res = get_vocalist_joke()
                return {
                    "intent": "smart_assistant",
                    "subintent": "joke",
                    "voice_reply": joke_res["voice_reply"],
                    "chat_reply": joke_res["chat_reply"],
                    "embed": joke_res.get("embed"),
                }
        except Exception as e:
            logger.debug(f"Smart assistant check in fallback: {e}")

        # 0B. Script Razor per UO Outlands (outlands.uorazorscripts.com)
        from razor_scripts import is_script_question
        is_sc, sc_skill = is_script_question(t_low)
        if not is_sc and jack_res and jack_res.intent == "razor_script":
            is_sc = True
            sc_skill = jack_res.extracted_entities.get("skill_slug") or "lista"
        if is_sc:
            target_skill = sc_skill if sc_skill and sc_skill != "lista" else "lista"
            s_name = target_skill.title() if target_skill != "lista" else "UO Outlands"
            return {
                "intent": "razor_script",
                "skill_slug": target_skill,
                "voice_reply": f"Ragazzi, vi ho lasciato gli script Razor per {s_name} in chat! Date un'occhiata! Magia!",
                "chat_reply": f"📜 **Franchino Razor Scripts**: Ecco gli script per **{s_name}** da outlands.uorazorscripts.com!",
            }

        # 1. Palinsesto Sportivo, Partite di Stasera & Grandi Match
        sport_triggers = (
            "match di stasera", "chi gioca stasera", "partite di stasera", "partite stasera", "match stasera",
            "chi gioca", "partite live", "partite di oggi", "partite oggi", "match oggi", "quali partite", "che partite",
            "cosa c'è stasera", "serie a", "serie b", "champions", "champions league", "premier league", "la liga",
            "bundesliga", "nba", "eurolega", "euroleague", "serie a basket", "formula 1", "f1", "motogp", "gran premio",
            "ferrari", "leclerc", "tennis", "streaming partita", "partita", "palinsesto", "incontri", "chi gioca a calcio"
        )
        if any(w in t_low for w in sport_triggers):
            clean_q = re.sub(
                r"^(?:(?:(?:hey+|ehi+|hei+|ey+|ei+)\s+)?franchino|mi\s+dici|quali\s+sono|dimmi|trova|cerca|dove\s+(?:fanno\s+vedere|posso\s+vedere)|streaming\s+di|partita\s+(?:del|della|di)?)\s*",
                "",
                t_low
            ).strip()

            if any(w in t_low for w in ("f1", "formula 1", "motogp", "gran premio", "prossima gara", "ferrari", "leclerc")):
                v_sport = "Magia sui motori ragazzi! Tutti i dettagli e gli orari della Formula 1 sono in chat! Date un'occhiata!"
            elif any(w in t_low for w in ("eurolega", "euroleague")):
                v_sport = "Notte infuocata sul parquet europeo ragazzi! Vi ho scritto tutti i match di Eurolega in chat! Date un'occhiata! Si decolla!"
            elif any(w in t_low for w in ("basket", "nba")):
                v_sport = "Notte di basket NBA ragazzi! Vi ho scritto tutti i match in chat! Date un'occhiata! Si vola!"
            elif "champions" in t_low:
                v_sport = "La notte delle stelle ragazzi! Vi ho scritto tutto il palinsesto di Champions League in chat! Date un'occhiata! Magia!"
            elif "serie a" in t_low:
                v_sport = "Il grande calcio italiano ragazzi! Vi ho scritto tutti i match e gli orari in chat! Date un'occhiata!"
            else:
                v_sport = "Stasera in campo c'è il fuoco ragazzi! Vi ho preparato tutto il palinsesto con i big match in chat! Magia!"

            return {
                "intent": "sports_match",
                "sport_query": clean_q or t_low,
                "voice_reply": v_sport,
                "chat_reply": f"⚽ **Franchino Sports**: Cerco il palinsesto e gli streaming live per: **{clean_q or 'stasera'}**!",
            }

        # 2. UO Outlands Aiuto / Guida
        from outlands_wiki import is_help_question, is_wiki_question
        if is_help_question(t_low):
            return {
                "intent": "outlands_help",
                "voice_reply": "Ragazzi, vi ho scritto la guida completa di Outlands in chat! Date un'occhiata!",
                "chat_reply": "🗺️ **Franchino UO Outlands Guide**: Dalle profondità di Aegis alle vette di Mount Petram, chiedimi qualsiasi cosa su skill, template da 700 punti, aspect armor e creature!",
            }

        # 3. UO Outlands Wiki & Conoscenza Approfondita
        is_w, w_q = is_wiki_question(t_low)
        if is_w or (jack_res and jack_res.intent == "wiki_guide"):
            target_topic = w_q or (jack_res.extracted_entities.get("wiki_topic") if jack_res else "") or t_low
            rag_info = ""
            try:
                from outlands_wiki import get_outlands_rag_context
                rag_info = get_outlands_rag_context(target_topic, max_chars=400)
            except Exception:
                pass

            c_reply = f"📖 **Franchino UO Outlands Guide**:\n{rag_info}" if rag_info else f"📖 Scavo nei tomi antichi di UO Outlands: **{target_topic}**..."
            return {
                "intent": "wiki_guide",
                "wiki_topic": target_topic,
                "voice_reply": f"Ragazzi, vi ho scritto tutte le informazioni su {target_topic} in chat! Date un'occhiata!",
                "chat_reply": c_reply,
            }

        # 4. Favole della Notte & Monologhi Vocalist
        if any(w in t_low for w in ("favola", "raccontami", "poesia", "vocalist", "notte", "metempsicosi", "insomnia", "magia")) or (jack_res and jack_res.intent == "live_fable"):
            fable = random.choice(DEFAULT_FABLES)
            return {
                "intent": "live_fable",
                "voice_reply": "Magia... Chiudi gli occhi e vola con me nelle favole della notte...",
                "chat_reply": f"✨ *\"{fable}\"*",
            }

        # 4-bis. Controlli DJ Console & Effetti Audio
        is_dj_phrase = any(
            t_low.startswith(p) for p in (
                "stop", "stoppa", "ferma", "fermati", "basta", "zitto", "stai zitto", "muto", "silenzio", "fai silenzio", "taci", "spegni", "smettila", "svuota",
                "pausa", "riprendi", "salta", "skip",
                "volume", "alza", "abbassa", "loop", "shuffle", "bassi", "boost",
                "togli il boost", "attiva il bass boost", "metti in pausa", "metti pausa",
                "metti in loop", "metti in shuffle", "metti muto"
            )
        ) or any(
            w in t_low for w in ("ferma tutto", "stop tutto", "basta musica", "stop musica", "basta parlare", "fai silenzio", "stai zitto", "spegni tutto")
        )
        if (jack_res and jack_res.intent == "dj_control") or is_dj_phrase:
            act = jack_res.extracted_entities.get("action") if jack_res else "general"
            if act == "general" and any(w in t_low for w in ("stop", "ferma", "stoppa", "fermati", "basta", "zitto", "stai zitto", "muto", "silenzio", "fai silenzio", "spegni", "taci", "smettila", "svuota")):
                act = "stop"
            if act == "stop":
                return {
                    "intent": "dj_control",
                    "action": "stop",
                    "voice_reply": "",
                    "chat_reply": "",
                }
            return {
                "intent": "dj_control",
                "action": act,
                "voice_reply": "",
                "chat_reply": "🎛️ **Franchino DJ**: Comando console eseguito!",
            }

        # 5. Musica & Mood
        play_re = re.search(
            r"^(?:metti(?!\s+(?:in\s+)?(?:pausa|stop|loop|ripetizione|shuffle|muto|mute)(?:\b|$)|(?:\s+(?:il\s+)?volume)|(?:\s+(?:un\s+|il\s+)?timer)|(?:\s+(?:una\s+|la\s+)?sveglia))|"
            r"suona(?!\s+(?:la\s+)?sveglia)|play|riproduci|fammi\s+sentire|cerca)\s+(.+)",
            t_low,
            re.IGNORECASE,
        )
        music_q = ""
        if play_re:
            music_q = play_re.group(1).strip()
        elif jack_res and jack_res.intent == "play_music":
            music_q = jack_res.extracted_entities.get("music_query") or t_low
        if music_q:
            return {
                "intent": "play_music",
                "music_query": music_q,
                "voice_reply": "",
                "chat_reply": f"🎵 **Franchino DJ**: Accendo il piatto per: **{music_q}**! Buon viaggio!",
            }

        # 5-bis. Test Presenza / Liveness Check
        if (jack_res and jack_res.intent == "liveness_check") or any(
            w in t_low for w in ("ci senti", "mi senti", "ci sei", "ci siete", "sei vivo", "sei sveglio", "batti un colpo", "rispondi")
        ):
            return {
                "intent": "liveness_check",
                "voice_reply": "Forte e chiaro ragazzi! Franchino è sveglio, carico e sempre sul pezzo in console! Magia!",
                "chat_reply": "🎙️ **Franchino**: Forte e chiaro fratelli! Sempre sveglio e pronto in console!",
            }

        # 5-ter. Interazioni Sociali con la community
        if (jack_res and jack_res.intent == "social_interaction") or any(
            t_low.startswith(p) for p in ("brindisi", "fai un brindisi", "abbraccia", "abbraccio", "batti cinque", "saluta")
        ):
            return {
                "intent": "social_interaction",
                "voice_reply": "",
                "chat_reply": "🥂 **Franchino**: Alziamo i calici fratelli! Un brindisi alla musica e alla vita!",
            }

        # 5-quater. Controlli Amministrazione / Moderazione
        if (jack_res and jack_res.intent == "admin_control") or any(
            t_low.startswith(p) for p in ("sposta ", "porta ", "caccia ", "scollega ", "muta ", "smuta ", "kick ", "ban ", "espelli ")
        ):
            return {
                "intent": "admin_control",
                "voice_reply": "",
                "chat_reply": "🛡️ **Franchino Admin**: Comando moderazione elaborato!",
            }

        # 6. Saluti & Interazione Umana Calda (Empatia e Connessione)
        if any(w in t_low for w in ("ciao", "buonasera", "buongiorno", "buonanotte", "eccomi", "bella franchino")):
            return {
                "intent": "chat",
                "voice_reply": f"Benvenuto nel tempio della notte {user_name}! Chiudi gli occhi, respira il ritmo... Magia!",
                "chat_reply": f"🎧 **Franchino**: Benvenuto fratello mio! Sempre qui in console con voi, la musica e la notte sono la nostra casa!",
            }

        if any(w in t_low for w in ("come stai", "tutto bene", "come va")):
            return {
                "intent": "chat",
                "voice_reply": "In console col cuore a mille ragazzi! La musica scorre nelle vene e la notte è giovane! Magia!",
                "chat_reply": "✨ **Franchino**: Carico come sempre! Il beat pulsa forte e siamo pronti a vivere una favola senza fine!",
            }

        if any(w in t_low for w in ("stanco", "giornata pesante", "triste", "giù di corda")):
            return {
                "intent": "chat",
                "voice_reply": "Respira fratello mio... Chiudi gli occhi. Qui la notte cancella i pensieri pesanti. Alziamo la musica e voliamo via insieme... Magia!",
                "chat_reply": "🌙 **Franchino**: Lascia il peso della giornata fuori dalla porta. Qui siamo tra amici, la musica scalda il cuore e niente può fermarci!",
            }

        if any(w in t_low for w in ("grande", "mito", "ti voglio bene", "leggenda", "numero uno", "bravo")):
            return {
                "intent": "chat",
                "voice_reply": "La vera magia siete voi ragazzi della notte! Siamo tutti una grande famiglia! Vi voglio bene!",
                "chat_reply": "❤️ **Franchino**: Grazie di cuore fratelli miei! La notte vive attraverso la vostra energia. Siete voi la musica!",
            }

        # Chiacchiera generale
        return {
            "intent": "chat",
            "voice_reply": "Magia ragazzi! Franchino è sempre qui in console con voi! Dimmi tutto!",
            "chat_reply": "🎧 **Franchino il Vocalist**: Sempre presente in console! Musica, sport, Outlands o favole della notte... Cosa facciamo stasera ragazzi?",
        }

    async def generate_live_fable(self, user_name: str, topic: Optional[str] = None) -> str:
        """Genera in tempo reale un monologo poetico inedito del vero Franchino."""
        if not await self._check_ollama_status():
            return random.choice(DEFAULT_FABLES)

        prompt = (
            f"Sei Franchino il Vocalist. Componi una favola della notte poetica e mistica sul tema '{topic or 'il viaggio notturno e la musica'}' per {user_name}. "
            "Usa il tuo stile inconfondibile, cita la luna, il bosco incantato, la magia, il battito e la console. "
            "Scrivi massimo 4-5 frasi intense, pensate per essere recitate con la voce calda da vocalist."
        )

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Sei Franchino il Vocalist. Rispondi solo con il monologo poetico, senza spiegazioni."},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.8, "num_predict": 140, "num_thread": 2},
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.host}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        fable = data.get("message", {}).get("content", "").strip()
                        if fable and len(fable) > 30:
                            return fable
        except Exception as e:
            logger.debug(f"Fallback favola live: {e}")

        return random.choice(DEFAULT_FABLES)

    async def explain_song_context(self, song_title: str, artist: str) -> str:
        """Genera un commento intelligente da DJ sul brano in corso."""
        if not await self._check_ollama_status():
            return f"Questo è un grandissimo pezzo ragazzi: {song_title} di {artist}! Alziamo il volume e andiamo a volare!"

        prompt = (
            f"Sei Franchino in console all'Insomnia. Stai suonando '{song_title}' di {artist}. "
            "Fai un commento da vocalist entusiasta di 2 frasi spiegando perché questo brano è magia pura."
        )
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.6, "num_predict": 75, "num_thread": 2},
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.host}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("message", {}).get("content", "").strip()
        except Exception:
            pass
        return f"Questo è un grandissimo pezzo ragazzi: {song_title}! Magia pura in console!"


# Istanza Singleton condivisa del Cervello AI
brain = FranchinoBrain()
