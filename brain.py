"""
brain.py - Cervello AI di Franchino basato su Google Gemma 2 2B Locale
=======================================================================
Esegue l'inferenza AI direttamente sui 2 core ARM e 12 GB RAM del server (tramite Ollama),
senza alcuna dipendenza da API esterne a pagamento e con un consumo RAM minimo (~2.2 GB),
lasciando oltre 9 GB di RAM completamente liberi per tutti gli altri servizi del server.

Funzionalità:
1. NLU (Natural Language Understanding) per comandi musicali liberi e basati su mood.
2. Generazione in tempo reale di favole e monologhi poetici da Vocalist (Franchino persona).
3. RAG (Retrieval-Augmented Generation) per ragionare sulle guide e build di UO Outlands.
4. Commenti intelligenti e curiosità sui brani musicali in riproduzione.
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

DEFAULT_FABLES = [
    "Magia... C'era una volta una bambina di nome Imperia... che cercava la musica dentro le stelle della notte. Chiudi gli occhi, respira il ritmo... Da stasera niente sarà più come prima... Magia!",
    "Signore e signori... benvenuti nel tempio della notte. Non importa da dove vieni, qui siamo tutti figli delle stelle e della musica... Lasciati andare, inizia il viaggio... Metempsicosi!",
    "Ascolta il battito... Dentro ogni nota c'è una favola che non finisce mai. Quando il sole tramonta, i sogni prendono vita in console... Vivere una favola... Magia!",
    "Chiudi gli occhi... immagina un'isola dove il tempo non esiste e conta soltanto la musica. Siamo pronti a volare insieme fino all'alba... Magia!",
    "Nel fitto del bosco si sente un battito lontano... Il castello incantato apre i suoi cancelli. Entra ragazzo, questa notte è solo per noi... Magia!",
]

# Configurazione Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b")

FRANCHINO_SYSTEM_PROMPT = """Sei Franchino il Vocalist (Francesco Principato), la voce leggendaria della musica elettronica e della notte italiana (Insomnia, Metempsicosi).
Sei anche il DJ e la guida esperta di Ultima Online Outlands in questo server Discord.

La tua personalità:
- Calda, mistica, poetica, entusiasta e ipnotica.
- Usi spesso le tue celebri espressioni: "Magia!", "Chiudi gli occhi e vola con me...", "La notte è giovane...", "Siamo tutti figli della musica!", "Da stasera niente sarà più come prima!".
- Rispondi sempre in italiano.

Regole di Risposta:
1. Devi comprendere l'intenzione dell'utente.
2. Rispondi SEMPRE in formato JSON valido con questa struttura:
{
  "intent": "play_music" | "wiki_guide" | "sports_match" | "live_fable" | "song_info" | "chat",
  "music_query": "stringa ottimizzata per ricerca YouTube" (solo se intent è play_music, altrimenti null),
  "wiki_topic": "argomento outlands" (solo se intent è wiki_guide, altrimenti null),
  "sport_query": "squadra o evento sportivo" (solo se intent è sports_match, altrimenti null),
  "voice_reply": "breve frase parlata da leggere col microfono (massimo 20-30 parole, calda e stile vocalist)",
  "chat_reply": "risposta testuale completa e amichevole per la chat"
}
Non aggiungere testo prima o dopo il blocco JSON.
"""


class FranchinoBrain:
    """Motore di intelligenza artificiale locale basato su Gemma 2 2B."""

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
                async with session.get(f"{self.host}/api/tags", timeout=aiohttp.ClientTimeout(total=2.5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                        base_model = self.model.split(":")[0]
                        if base_model in models or any(base_model in m for m in models):
                            return True
                        logger.info(f"Ollama è attivo ma il modello '{self.model}' deve essere ancora scaricato.")
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
        # Fallback rapido locale se Ollama non è temporaneamente raggiungibile
        if not await self._check_ollama_status():
            decision = self._fallback_heuristic_intent(text)
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
            prompt += f" (In questo momento sta suonando: '{current_song_title}')"

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
                    "temperature": 0.4,
                    "num_predict": 180,
                    "num_thread": 2,  # Rispetta le 2 CPU lasciando il server fluido
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
                        decision = self._parse_json_decision(content, text)

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
                                },
                            )
                        )
                        return decision
        except Exception as e:
            logger.warning(f"Errore inferenza Gemma: {e}. Uso fallback locale.")

        fallback = self._fallback_heuristic_intent(text)
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

    def _parse_json_decision(self, raw_content: str, original_text: str) -> Dict[str, Any]:
        """Estrae e convalida il JSON restituito da Gemma."""
        try:
            clean = raw_content.strip()
            if clean.startswith("```json"):
                clean = clean[7:]
            if clean.endswith("```"):
                clean = clean[:-3]
            data = json.loads(clean.strip())
            if isinstance(data, dict) and "intent" in data:
                return data
        except Exception:
            pass
        return self._fallback_heuristic_intent(original_text)

    def _fallback_heuristic_intent(self, text: str) -> Dict[str, Any]:
        """Regole euristiche veloci se Ollama non è raggiungibile."""
        t_low = text.lower().strip()

        # UO Outlands
        from outlands_wiki import is_wiki_question
        is_w, w_q = is_wiki_question(t_low)
        if is_w:
            return {
                "intent": "wiki_guide",
                "wiki_topic": w_q,
                "voice_reply": f"Subito ragazzi! Cerco sulla Wiki di Outlands {w_q}.",
                "chat_reply": f"📖 Cerco su UO Outlands: **{w_q}**...",
            }

        # Favole
        if any(w in t_low for w in ("favola", "raccontami", "poesia", "vocalist", "notte")):
            return {
                "intent": "live_fable",
                "voice_reply": "Magia... Chiudi gli occhi e vola con me nelle favole della notte...",
                "chat_reply": "✨ *\"Magia... C'era una volta nel bosco incantato...\"*",
            }

        # Streaming Sport / Partita (MadPlay77)
        if any(w in t_low for w in ("partita", "streaming", "formula 1", "motogp", "f1", "nba", "chi gioca", "partite live")):
            clean_q = re.sub(r"^(?:franchino|cerca|dove\s+(?:fanno\s+vedere|posso\s+vedere)|streaming\s+di|partita\s+(?:del|della|di)?)\s*", "", t_low).strip()
            return {
                "intent": "sports_match",
                "sport_query": clean_q or t_low,
                "voice_reply": f"Magia ragazzi! Cerco subito la partita per {clean_q} su MadPlay77!",
                "chat_reply": f"⚽ Cerco lo streaming sportivo per: **{clean_q}**...",
            }

        # Musica
        play_re = re.search(r"(?:metti|suona|play|riproduci|fammi sentire|cerca)\s+(.+)", t_low)
        if play_re:
            query = play_re.group(1).strip()
            return {
                "intent": "play_music",
                "music_query": query,
                "voice_reply": f"Subito ragazzi! Metto {query}!",
                "chat_reply": f"🎵 Avvio ricerca per: **{query}**",
            }

        # Chiacchiera generale
        return {
            "intent": "chat",
            "voice_reply": "Magia ragazzi! Sono sempre qui in console con voi!",
            "chat_reply": "🎧 **Franchino DJ**: Sempre presente in console! Dimmi cosa ti serve ragazzi!",
        }

    async def generate_live_fable(self, user_name: str, topic: Optional[str] = None) -> str:
        """Genera in tempo reale con Gemma 2 2B un monologo poetico inedito del vero Franchino."""
        if not await self._check_ollama_status():
            return random.choice(DEFAULT_FABLES)

        prompt = (
            f"Sei Franchino il Vocalist. Componi una favola della notte poetica e mistica sul tema '{topic or 'il viaggio notturno e la musica'}'. "
            "Usa il tuo stile inconfondibile, cita la luna, il bosco incantato, la magia e la console. "
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
                "options": {"temperature": 0.8, "num_predict": 120, "num_thread": 2},
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
            return f"Questo è un grandissimo pezzo ragazzi: {song_title} di {artist}! Alziamo il volume!"

        prompt = (
            f"Sei Franchino in console. Stai suonando '{song_title}' di {artist}. "
            "Fai un commento da vocalist entusiasta di 2 frasi spiegando perché questo brano è magia pura."
        )
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.6, "num_predict": 70, "num_thread": 2},
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
