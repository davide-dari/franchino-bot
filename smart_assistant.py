"""
smart_assistant.py - Suite Assistente Intelligente Vocale & Slash stile Alexa / Google Home
========================================================================================
Fornisce a Franchino Bot tutte le funzionalità di un assistente smart personale (stile Alexa / Google Home)
con l'inconfondibile carisma, calore e anima da Vocalist della notte:
1. Ora e Data esatta (fuso orario Europe/Rome).
2. Meteo in tempo reale e previsioni via API Open-Meteo gratuita e senza chiavi.
3. Timer e Promemoria asincroni in background (con squillo Driiiin! e notifica vocale/chat).
4. Calcolatrice matematica sicura (AST parser).
5. Utility casuali: Testa o Croce, Lancio Dadi (D6, D20, ecc.), Scelta tra opzioni.
6. Barzellette, freddure e battute da vocalist.
"""

import ast
import asyncio
import logging
import math
import operator
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp
import discord

logger = logging.getLogger("Franchino.SmartAssistant")

try:
    ROME_TZ = ZoneInfo("Europe/Rome")
except Exception:
    from datetime import timezone, timedelta
    ROME_TZ = timezone(timedelta(hours=2))

# ==============================================================================
# 1. ORA E DATA ESATTA (Europe/Rome)
# ==============================================================================

GIORNI_SETTIMANA = [
    "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"
]

MESI_ANNO = [
    "", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"
]


def get_current_time_info(query: str = "") -> Dict[str, Any]:
    """
    Restituisce ora e data esatta con fuso orario Europe/Rome,
    con embed Discord e risposta vocale da vocalist.
    """
    now = datetime.now(ROME_TZ)
    time_str = now.strftime("%H:%M")
    day_name = GIORNI_SETTIMANA[now.weekday()]
    day_num = now.day
    month_name = MESI_ANNO[now.month]
    year = now.year

    q_low = query.lower()
    is_date_only = any(w in q_low for w in ("giorno", "data", "calendario", "oggi")) and not any(w in q_low for w in ("ore", "ora", "orario"))
    is_time_only = any(w in q_low for w in ("ore", "ora", "orario")) and not any(w in q_low for w in ("giorno", "data", "calendario"))

    if is_date_only:
        voice_reply = (
            f"Oggi è {day_name} {day_num} {month_name} {year} ragazzi! "
            "Il tempo corre veloce, ma la nostra musica è eterna! Magia!"
        )
    elif is_time_only:
        voice_reply = (
            f"In console sono esattamente le {time_str} ragazzi! "
            "La notte è giovane e il ritmo sale forte! Magia!"
        )
    else:
        voice_reply = (
            f"In console sono le {time_str} di {day_name} {day_num} {month_name} ragazzi! "
            "Carichi a mille per vivere una favola!"
        )

    embed = discord.Embed(
        title="🕒 Orologio di Console Franchino • Roma",
        description=f"🎙️ *\"{voice_reply}\"*",
        color=0x3498DB,
    )
    embed.add_field(name="⏰ Ora Esatta", value=f"**`{time_str}`**", inline=True)
    embed.add_field(name="📅 Data Odierna", value=f"**{day_name.capitalize()} {day_num} {month_name} {year}**", inline=True)
    embed.add_field(name="🌍 Fuso Orario", value="`Europe/Rome (CET/CEST)`", inline=True)
    embed.set_footer(text="Franchino Bot • Assistente Intelligente Vocale")

    return {
        "time_str": time_str,
        "date_str": f"{day_name} {day_num} {month_name} {year}",
        "day_name": day_name,
        "day_num": day_num,
        "month_name": month_name,
        "year": year,
        "voice_reply": voice_reply,
        "embed": embed,
        "chat_reply": f"🕒 **{time_str}** — {day_name.capitalize()} {day_num} {month_name} {year}",
    }


# ==============================================================================
# 2. METEO IN TEMPO REALE VIA OPEN-METEO
# ==============================================================================

WMO_WEATHER_MAP: Dict[int, Tuple[str, str, str]] = {
    0: ("Cielo sereno", "☀️", "Clima perfetto per scaldare la pista e ballare tutta la notte!"),
    1: ("Prevalentemente sereno", "🌤️", "Qualche velatura in cielo ma in console c'è solo luce!"),
    2: ("Parzialmente nuvoloso", "⛅", "Nuvolette passeggere ragazzi, niente che possa fermare il beat!"),
    3: ("Cielo coperto", "☁️", "Cielo coperto fuori, ma qui dentro il sound scalda l'ambiente!"),
    45: ("Nebbia", "🌫️", "Nebbia fitta come l'effetto fumo in discoteca ragazzi! Si viaggia a vista!"),
    48: ("Nebbia con brina", "🌫️", "Atmosfera da brividi ragazzi, alziamo i bassi per scaldarci!"),
    51: ("Pioggerella leggera", "🌦️", "Cade qualche goccia ragazzi, tenetevi stretti al coperto in pista!"),
    53: ("Pioggerella moderata", "🌦️", "Pioggerella fresca, ma in console l'aria è infuocata!"),
    55: ("Pioggerella intensa", "🌦️", "Gocce battenti sul tetto, a ritmo col nostro kick!"),
    61: ("Pioggia leggera", "🌧️", "Pioggia leggera fuori ragazzi, chiudete gli occhi e sentite il ritmo!"),
    63: ("Pioggia moderata", "🌧️", "Piove ragazzi, ma qui dentro non ci bagna nessuno! Si decolla!"),
    65: ("Pioggia forte", "🌧️", "Diluvio fuori ragazzi! La musica è il nostro rifugio magico!"),
    71: ("Neve leggera", "❄️", "Fiocchi di neve nell'aria ragazzi, atmosfera magica e sound glaciale!"),
    73: ("Neve moderata", "❄️", "Neve che cade, scaldiamo il cuore con la cassa che pulsa!"),
    75: ("Neve forte", "❄️", "Tempesta di neve fuori, bufera di emozioni in console!"),
    80: ("Rovesci leggeri", "🌧️", "Rovesci improvvisi fuori, ma il nostro ritmo non si ferma mai!"),
    81: ("Rovesci moderati", "🌧️", "Acquazzone in corso ragazzi! Balliamo come sotto una cascata di luce!"),
    82: ("Rovesci violenti", "🌧️", "Bombe d'acqua fuori ragazzi! Alziamo il volume al massimo!"),
    95: ("Temporale", "⛈️", "Lampi e tuoni nel cielo ragazzi! Tuona come un sub-woofer gigante! Magia!"),
    96: ("Temporale con grandine leggera", "⛈️", "Temporale e grandine, adrenalina pura nei cieli e in pista!"),
    99: ("Temporale con grandine forte", "⛈️", "Tempesta totale ragazzi! Il cielo fa la sua musica e noi facciamo la nostra!"),
}


def extract_city_from_query(query: str) -> str:
    """Estrae il nome della città richiesta da una frase naturale."""
    q = query.strip()

    # Rimuovi filler iniziali e formule di cortesia
    t = re.sub(
        r"^(?:franchino\s+|ciao\s+franchino\s+|ehi\s+franchino\s+|dimmi\s+|mi\s+dici\s+|sai\s+dirmi\s+|puoi\s+dirmi\s+|vorrei\s+sapere\s+)+",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()

    # Rimuovi prefissi meteo comuni
    t = re.sub(
        r"^(?:che\s+tempo\s+fa\b|che\s+tempo\s+far[aà]\b|com['’]?\s*[eè]\s+(?:il\s+)?(?:meteo|tempo)|previsioni\s+(?:del\s+tempo|meteo)|previsioni|(?:il\s+)?meteo|(?:il\s+)?tempo|piove|piover[aà]|fa\s+caldo|fa\s+freddo)\s*",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    # Rimuovi particelle come "a", "ad", "in", "per", "di", "stasera a", "oggi a"
    t = re.sub(
        r"^(?:stasera\s+|oggi\s+|domani\s+)?(?:a|ad|in|per|di)\s+",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    # Rimuovi suffissi temporali o formule di cortesia
    t = re.sub(
        r"(?:\s*(?:per\s+favore|per\s+piacere|grazie|stasera|oggi|domani|adesso|ora|\?|!|\.))+$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    return t if t else "Roma"


async def get_weather_info(city_query: str = "") -> Dict[str, Any]:
    """
    Interroga l'API Open-Meteo (senza chiave) per ottenere meteo in tempo reale e previsioni del giorno.
    Restituisce un dizionario completo con embed Discord e battuta da vocalist.
    """
    city = extract_city_from_query(city_query)
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1&language=it&format=json"

    timeout = aiohttp.ClientTimeout(total=3.0)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Geocoding
            async with session.get(geo_url, headers={"User-Agent": "FranchinoBot/1.0"}) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Errore geocoding HTTP {resp.status}")
                geo_data = await resp.json()

            results = geo_data.get("results")
            if not results:
                # Nessuna città trovata
                voice_err = f"Non ho trovato la città di {city} sulla mappa ragazzi! Provate con un'altra città!"
                embed_err = discord.Embed(
                    title=f"❓ Città non trovata: {city}",
                    description=f"Franchino non è riuscito a localizzare **{city}** nei sensori meteo mondiali. Prova a specificare una città nota (es. `/meteo Milano` o `/meteo Roma`).",
                    color=0xE67E22,
                )
                return {
                    "success": False,
                    "city": city,
                    "voice_reply": voice_err,
                    "chat_reply": f"⚠️ Città **{city}** non trovata.",
                    "embed": embed_err,
                }

            loc = results[0]
            city_name = loc.get("name", city)
            country = loc.get("country", "Italia")
            lat = loc["latitude"]
            lon = loc["longitude"]

            # 2. Weather forecast
            forecast_url = (
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
                "&daily=weather_code,temperature_2m_max,temperature_2m_min"
                "&timezone=Europe%2FRome"
            )

            async with session.get(forecast_url, headers={"User-Agent": "FranchinoBot/1.0"}) as w_resp:
                if w_resp.status != 200:
                    raise RuntimeError(f"Errore meteo HTTP {w_resp.status}")
                weather_data = await w_resp.json()

            curr = weather_data.get("current", {})
            temp = curr.get("temperature_2m", 0.0)
            apparent = curr.get("apparent_temperature", temp)
            humidity = curr.get("relative_humidity_2m", 0)
            precip = curr.get("precipitation", 0.0)
            wind = curr.get("wind_speed_10m", 0.0)
            code = int(curr.get("weather_code", 0))

            daily = weather_data.get("daily", {})
            t_max = daily.get("temperature_2m_max", [temp])[0] if daily.get("temperature_2m_max") else temp
            t_min = daily.get("temperature_2m_min", [temp])[0] if daily.get("temperature_2m_min") else temp

            cond_text, cond_icon, comment = WMO_WEATHER_MAP.get(
                code, ("Tempo sereno o variabile", "🌤️", "Qualsiasi sia il tempo, in console c'è sempre magia!")
            )

            voice_reply = (
                f"A {city_name} ci sono {temp:.0f} gradi con {cond_text.lower()} ragazzi! "
                f"{comment} Magia!"
            )

            embed = discord.Embed(
                title=f"{cond_icon} Meteo a {city_name} ({country})",
                description=f"🎙️ *\"{voice_reply}\"*",
                color=0x2ECC71 if "sereno" in cond_text.lower() else (0x3498DB if "pioggia" in cond_text.lower() else 0xF39C12),
            )
            embed.add_field(name="🌡️ Temperatura", value=f"**`{temp:.1f}°C`** (Percepita: `{apparent:.1f}°C`)", inline=True)
            embed.add_field(name="☁️ Condizione", value=f"**{cond_icon} {cond_text}**", inline=True)
            embed.add_field(name="💧 Umidità", value=f"**`{humidity}%`**", inline=True)
            embed.add_field(name="💨 Vento", value=f"**`{wind:.1f} km/h`**", inline=True)
            embed.add_field(name="🌧️ Precipitazioni", value=f"**`{precip:.1f} mm`**", inline=True)
            embed.add_field(name="📈 Max / Min Oggi", value=f"**`{t_max:.1f}°C`** / **`{t_min:.1f}°C`**", inline=True)
            embed.set_footer(text="Franchino Bot • Dati Meteo Live Open-Meteo")

            return {
                "success": True,
                "city": city_name,
                "country": country,
                "temperature": temp,
                "apparent_temperature": apparent,
                "humidity": humidity,
                "wind_speed": wind,
                "precipitation": precip,
                "condition": cond_text,
                "icon": cond_icon,
                "t_max": t_max,
                "t_min": t_min,
                "voice_reply": voice_reply,
                "chat_reply": f"{cond_icon} **Meteo {city_name}**: `{temp:.1f}°C`, {cond_text} (Vento: `{wind} km/h`)",
                "embed": embed,
            }

    except Exception as e:
        logger.warning(f"Errore recupero meteo per '{city}': {e}")
        fallback_voice = f"I radar meteo di Franchino sono momentaneamente offline, ma in console ragazzi splende sempre la magia!"
        fallback_embed = discord.Embed(
            title=f"🌤️ Meteo: {city.capitalize()}",
            description=(
                f"⚠️ Non è stato possibile contattare il servizio meteo in questo momento (`{e}`).\n\n"
                f"🎙️ *\"{fallback_voice}\"*"
            ),
            color=0x95A5A6,
        )
        return {
            "success": False,
            "city": city,
            "voice_reply": fallback_voice,
            "chat_reply": f"⚠️ Servizio meteo momentaneamente non disponibile per **{city}**.",
            "embed": fallback_embed,
        }


# ==============================================================================
# 3. TIMER & PROMEMORIA ASINCRONI IN BACKGROUND
# ==============================================================================

def format_duration_italian(seconds: int) -> str:
    """Formatta i secondi in una stringa colloquiale italiana (es. 5 minuti e 30 secondi)."""
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    parts = []
    if h > 0:
        parts.append(f"{h} {'ora' if h == 1 else 'ore'}")
    if m > 0:
        parts.append(f"{m} {'minuto' if m == 1 else 'minuti'}")
    if s > 0 or not parts:
        parts.append(f"{s} {'secondo' if s == 1 else 'secondi'}")

    if len(parts) == 1:
        return parts[0]
    return " e ".join([", ".join(parts[:-1]), parts[-1]])


ITALIAN_TIMER_NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11,
    "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16,
    "diciassette": 17, "diciotto": 18, "diciannove": 19, "venti": 20,
    "venticinque": 25, "trenta": 30, "trentacinque": 35, "quaranta": 40,
    "quarantacinque": 45, "cinquanta": 50, "sessanta": 60
}


def parse_timer_request(text: str) -> Tuple[int, str]:
    """
    Estrae la durata in secondi e l'eventuale etichetta/motivo da una richiesta naturale.
    Supporta cifre ('5 minuti'), parole ('due minuti'), frazioni d'ora ('mezz'ora', 'un quarto d'ora'),
    notazioni rapide ('1h30m', '5m30s') e motivi descrittivi ('per la pasta').
    """
    t = text.lower().strip().replace("’", "'")

    # Sostituisci parole numeriche italiane che precedono unità di tempo
    for w_word, w_val in sorted(ITALIAN_TIMER_NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{w_word}\s*(?=(?:ore|ora|h|minuti|minuto|min|m|secondi|secondo|sec|s)\b)", f"{w_val} ", t)

    # Estrazione durata ore
    hours = 0
    m_h = re.search(r"(\d+)\s*(?:ore|ora|h)(?=[^a-zA-Z]|$)", t)
    if m_h:
        hours = int(m_h.group(1))
    elif "un'ora" in t or "un ora" in t:
        hours = 1

    # Estrazione durata minuti
    minutes = 0
    m_m = re.search(r"(\d+)\s*(?:minuti|minuto|min|m)(?=[^a-zA-Z]|$)", t)
    if m_m:
        minutes = int(m_m.group(1))
    elif "un minuto" in t:
        minutes = 1

    if any(w in t for w in ("tre quarti d'ora", "tre quarti di ora", "tre quarti")):
        minutes += 45
    elif any(w in t for w in ("un quarto d'ora", "un quarto di ora", "quarto d'ora")):
        minutes += 15
    elif any(w in t for w in ("mezz'ora", "mezzora", "mezza ora")):
        minutes += 30
    elif re.search(r"(?:ora|ore)\s+e\s+(?:mezza|mezzo)\b", t):
        minutes += 30

    # Estrazione durata secondi
    seconds = 0
    m_s = re.search(r"(\d+)\s*(?:secondi|secondo|sec|s)(?=[^a-zA-Z]|$)", t)
    if m_s:
        seconds = int(m_s.group(1))

    total_seconds = hours * 3600 + minutes * 60 + seconds

    # Se non trovata tramite regex con unità, cerca numeri isolati es: 'timer 5' -> 5 minuti
    if total_seconds == 0:
        m_iso = re.search(r"(?:timer|promemoria|sveglia)\s+(?:di|da)?\s*(\d+)\b", t)
        if m_iso:
            total_seconds = int(m_iso.group(1)) * 60

    # Estrazione etichetta / motivo (es. 'per la pasta', 'di togliere la pizza')
    label = "Timer"
    m_label = re.search(r"(?:minuti|minuto|min|secondi|secondo|sec|ore|ora|h|m|s|mezza|mezzo)\s+(?:per|di|a|che)?\s*([a-zA-Z0-9_\s']+)", t)
    if m_label:
        cand = m_label.group(1).strip(" ,.!?:;-")
        cand = re.sub(r"^(?:e\s+mezza|e\s+mezzo)\s*", "", cand).strip()
        cand = re.sub(r"^(?:per|di|a|che|il|la|lo|i|gli|le|l['’])\s+", "", cand)
        if cand and not re.search(r"^(?:\d+|un|mezz|minut|second|or|da|di|tra)", cand):
            label = cand
    else:
        m2 = re.search(r"(?:timer|promemoria|sveglia)\s+(?:per|di|da)?\s*(?!da\b|di\b|tra\b)([a-zA-Z'\s]+?)\s+(?:di|da|tra|\d)", t)
        if m2:
            cand2 = m2.group(1).strip(" ,.!?:;-")
            cand2 = re.sub(r"^(?:per|di|a|che|il|la|lo|i|gli|le|l['’])\s+", "", cand2)
            if cand2 and not re.search(r"^(?:\d+|un|mezz|minut|second|or|da|di|tra)", cand2):
                label = cand2

    return total_seconds, label


def is_timer_status_query(text: str) -> bool:
    """Verifica se la frase è una richiesta di stato del timer ('quanto manca?', 'a che punto è il timer?')."""
    t = text.lower().replace("’", "'")
    return any(w in t for w in (
        "quanto manca", "tempo manca", "stato timer", "a che punto",
        "quanto tempo rimane", "tempo rimane", "rimane al timer", "quanto resta"
    ))


def is_timer_cancel_query(text: str) -> bool:
    """Verifica se la frase è una richiesta di cancellazione del timer o della sveglia."""
    t = text.lower().replace("’", "'")
    cancel_verbs = ("cancella", "elimina", "stop", "ferma", "annulla", "blocca", "togli", "spegni", "disattiva")
    timer_targets = ("timer", "promemoria", "sveglia")
    has_target = any(tgt in t for tgt in timer_targets)
    has_verb = any(v in t for v in cancel_verbs)
    exact_phrases = ("stop timer", "ferma il timer", "cancella il timer", "annulla il timer", "stop sveglia", "ferma la sveglia", "cancella la sveglia")
    return any(p in t for p in exact_phrases) or (has_target and has_verb)


@dataclass
class ActiveTimer:
    timer_id: str
    guild_id: int
    channel_id: int
    user_id: int
    user_name: str
    duration_seconds: int
    start_time: float
    end_time: float
    label: str
    channel: Any = None
    player: Any = None
    task: Optional[asyncio.Task] = None

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.end_time - time.monotonic())


class TimerManager:
    """Gestisce i timer asincroni in background per ciascun server Discord."""

    def __init__(self):
        # Mappa guild_id -> lista di ActiveTimer
        self._guild_timers: Dict[int, List[ActiveTimer]] = {}
        self._counter: int = 0

    def start_timer(
        self,
        guild_id: int,
        channel: Any,
        user: Any,
        duration_seconds: int,
        label: str = "Timer",
        player: Any = None,
    ) -> ActiveTimer:
        """Avvia un timer asincrono in background."""
        self._counter += 1
        timer_id = f"tmr_{self._counter}"
        now = time.monotonic()

        timer = ActiveTimer(
            timer_id=timer_id,
            guild_id=guild_id,
            channel_id=getattr(channel, "id", 0),
            user_id=getattr(user, "id", 0),
            user_name=getattr(user, "display_name", str(user)),
            duration_seconds=duration_seconds,
            start_time=now,
            end_time=now + duration_seconds,
            label=label or "Timer",
            channel=channel,
            player=player,
        )

        if guild_id not in self._guild_timers:
            self._guild_timers[guild_id] = []
        self._guild_timers[guild_id].append(timer)

        # Lancia il task in background
        task = asyncio.create_task(self._timer_worker(timer))
        timer.task = task
        logger.info(f"⏰ Avviato timer {timer_id} ({duration_seconds}s - '{label}') per {timer.user_name} nel guild {guild_id}")
        return timer

    def get_timers(self, guild_id: int, user_id: Optional[int] = None) -> List[ActiveTimer]:
        """Restituisce la lista di timer attivi per il server o utente."""
        timers = self._guild_timers.get(guild_id, [])
        if user_id:
            timers = [t for t in timers if t.user_id == user_id]
        return sorted(timers, key=lambda x: x.remaining_seconds)

    def cancel_timers(self, guild_id: int, user_id: Optional[int] = None) -> List[ActiveTimer]:
        """Cancella i timer attivi e li rimuove dal monitoraggio."""
        timers = self._guild_timers.get(guild_id, [])
        to_cancel = [t for t in timers if (user_id is None or t.user_id == user_id)]
        cancelled = []
        for t in to_cancel:
            if t.task and not t.task.done():
                t.task.cancel()
            cancelled.append(t)
            if t in timers:
                timers.remove(t)
        return cancelled

    async def _timer_worker(self, timer: ActiveTimer):
        """Worker asincrono: attende la durata del timer e avvisa vocalmente e in chat."""
        try:
            await asyncio.sleep(timer.duration_seconds)
        except asyncio.CancelledError:
            logger.info(f"⏰ Timer {timer.timer_id} per '{timer.label}' cancellato.")
            return

        # Rimuovi dai timer attivi
        timers = self._guild_timers.get(timer.guild_id, [])
        if timer in timers:
            timers.remove(timer)

        dur_str = format_duration_italian(timer.duration_seconds)
        voice_msg = (
            f"Driiiin! Ragazzi, il timer di {dur_str} per {timer.label} è scaduto! Magia!"
        )

        # 1. Notifica su canale Discord
        if timer.channel and hasattr(timer.channel, "send"):
            embed = discord.Embed(
                title="⏰ DRIIIIN! IL TIMER È SCADUTO!",
                description=(
                    f"🔔 <@{timer.user_id}>, il tuo timer per **{timer.label}** ({dur_str}) è terminato!\n\n"
                    f"🎙️ *\"{voice_msg}\"*"
                ),
                color=0xE74C3C,
            )
            embed.set_footer(text="Franchino Bot • Sveglia & Timer Assistente")
            try:
                await timer.channel.send(content=f"🔔 <@{timer.user_id}>!", embed=embed)
            except Exception as e:
                logger.warning(f"Impossibile inviare notifica completamento timer: {e}")

        # 2. Notifica Vocale TTS ed Effetto Sonoro nella stanza se connesso
        if timer.player:
            player = timer.player
            if player.voice_client and player.voice_client.is_connected():
                try:
                    # Riproduce la campanella 'alarm' se disponibile (senza riprendere subito la musica)
                    await player.play_sound_effect("alarm", resume=False)
                except Exception as e:
                    logger.debug(f"Effetto sonoro timer non riprodotto: {e}")

                try:
                    asyncio.create_task(
                        player.speak_tts(
                            voice_msg,
                            wait=False,
                            interrupt_music=True,
                            keep_paused_after=False,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Errore TTS timer: {e}")


# Singleton globale del manager timer
timer_manager = TimerManager()


# ==============================================================================
# 4. CALCOLATRICE MATEMATICA RAPIDA & SICURA (AST PARSER)
# ==============================================================================

SAFE_MATH_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_node(node: ast.AST) -> float:
    """Valuta in sicurezza un nodo AST numerico senza invocare exec o eval."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("Valore non numerico trovato nell'operazione.")

    elif isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in SAFE_MATH_OPERATORS:
            raise ValueError(f"Operatore {op_type.__name__} non consentito.")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        if op_type == ast.Pow:
            if abs(right) > 100 or abs(left) > 10000:
                raise ValueError("Esponente o base troppo elevata.")
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ZeroDivisionError("Divisione per zero non permessa.")
        return SAFE_MATH_OPERATORS[op_type](left, right)

    elif isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in SAFE_MATH_OPERATORS:
            raise ValueError("Operatore unario non supportato.")
        return SAFE_MATH_OPERATORS[op_type](_safe_eval_node(node.operand))

    elif isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in ("sqrt", "radice"):
            if len(node.args) == 1:
                val = _safe_eval_node(node.args[0])
                if val < 0:
                    raise ValueError("Radice quadrata di numero negativo.")
                return math.sqrt(val)
        raise ValueError("Funzione matematica non riconosciuta.")

    elif isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)

    raise ValueError("Struttura sintattica matematica non supportata.")


MATH_NUM_WORDS = {
    "zero": 0, "uno": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11,
    "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16,
    "diciassette": 17, "diciotto": 18, "diciannove": 19, "venti": 20,
    "trenta": 30, "quaranta": 40, "cinquanta": 50, "sessanta": 60,
    "settanta": 70, "ottanta": 80, "novanta": 90, "cento": 100, "mille": 1000
}


def clean_math_expression(text: str) -> Tuple[str, str]:
    """
    Converte una frase colloquiale in italiano in un'espressione matematica valutabile.
    Restituisce (espressione_normalizzata, rappresentazione_umana).
    """
    t = text.lower().strip()
    t = re.sub(
        r"^(?:franchino\s+)?(?:quanto\s+fa|calcola(?:mi)?|dimmi\s+quanto\s+fa|quanto\s+[eè]|fai\s+il\s+calcolo\s+di|fai\s+il\s+calcolo|risolvi)\s*",
        "",
        t,
    ).strip(" ?.,!")

    # Rimuovi articoli determinativi residui come "la", "il", "l'"
    t = re.sub(r"^(?:la|il|l['’]|lo)\s+", "", t).strip()

    human_repr = t

    # Sostituisci virgola decimale tra cifre (es. 10,5 -> 10.5)
    t = re.sub(r"(\d+),(\d+)", r"\1.\2", t)

    # Sostituisci parole numeriche italiane isolate
    for nw, nval in sorted(MATH_NUM_WORDS.items(), key=lambda x: -len(x[0])):
        t = re.sub(rf"\b{nw}\b", str(nval), t)

    # Sostituzioni linguaggio naturale italiano
    t = re.sub(r"\b(?:la\s+)?radice\s+(?:quadrata\s+)?di\s+(\d+(?:\.\d+)?)\b", r"sqrt(\1)", t)
    t = re.sub(r"\b(?:elevato\s+a(?:lla)?|alla)\s+(\d+)\b", r"** \1", t)
    t = re.sub(r"\b(?:diviso\s+(?:per)?|fratto)\b", " / ", t)
    t = re.sub(r"\b(?:per|moltiplicato\s+per)\b", " * ", t)
    t = re.sub(r"(\d+)\s*x\s*(\d+)", r"\1 * \2", t)
    t = re.sub(r"\b(?:più|piu)\b", " + ", t)
    t = re.sub(r"\bmeno\b", " - ", t)

    return t, human_repr


def evaluate_math_expression(query: str) -> Dict[str, Any]:
    """Valuta un'operazione matematica con responso vocale ed embed."""
    raw_expr, human_repr = clean_math_expression(query)
    try:
        tree = ast.parse(raw_expr, mode="eval")
        result = _safe_eval_node(tree)

        # Formatta risultato pulito (se intero, mostra senza virgola)
        if math.isclose(result, round(result), abs_tol=1e-9):
            fmt_res = str(int(round(result)))
        else:
            fmt_res = f"{result:.4f}".rstrip("0").rstrip(".")

        voice_reply = (
            f"{human_repr} fa {fmt_res} ragazzi! "
            "Matematico e preciso come il bpm della console! Magia!"
        )

        embed = discord.Embed(
            title="🧮 Calcolatrice Smart di Franchino",
            description=f"🎙️ *\"{voice_reply}\"*",
            color=0xF1C40F,
        )
        embed.add_field(name="📥 Operazione", value=f"`{human_repr}`", inline=True)
        embed.add_field(name="📤 Risultato", value=f"**`{fmt_res}`**", inline=True)
        embed.set_footer(text="Franchino Bot • Motore Matematico di Precisione")

        return {
            "success": True,
            "operation": human_repr,
            "result": fmt_res,
            "voice_reply": voice_reply,
            "chat_reply": f"🧮 `{human_repr}` = **`{fmt_res}`**",
            "embed": embed,
        }

    except ZeroDivisionError:
        err_msg = "Non si può dividere per zero ragazzi! Neanche alla console dell'Insomnia si piega la fisica!"
        return {
            "success": False,
            "error": "Divisione per zero",
            "voice_reply": err_msg,
            "chat_reply": "⚠️ Impossibile dividere per zero.",
            "embed": discord.Embed(title="⚠️ Errore Matematico", description=err_msg, color=0xE74C3C),
        }
    except Exception as e:
        err_msg = f"Non sono riuscito a calcolare l'operazione ragazzi! Prova con numeri semplici come '25 per 4' o '150 diviso 3'!"
        return {
            "success": False,
            "error": str(e),
            "voice_reply": err_msg,
            "chat_reply": f"⚠️ Espressione non valida: `{query}`",
            "embed": discord.Embed(title="⚠️ Errore Calcolo", description=err_msg, color=0xE74C3C),
        }


# ==============================================================================
# 5. TESTA O CROCE, DADO & SCELTA TRA OPZIONI
# ==============================================================================

def flip_coin() -> Dict[str, Any]:
    """Lancia una moneta (Testa o Croce)."""
    outcome = random.choice(["Testa", "Croce"])
    voice_reply = (
        f"Ho lanciato la moneta della notte in aria... Ed è uscito: {outcome} ragazzi! "
        "Il destino ha parlato! Magia!"
    )
    embed = discord.Embed(
        title="🪙 Lancio della Moneta",
        description=f"🎙️ *\"{voice_reply}\"*",
        color=0xF1C40F,
    )
    embed.add_field(name="Risultato", value=f"✨ **{outcome}**", inline=True)
    embed.set_footer(text="Franchino Bot • Utility Sorte & Destino")
    return {
        "type": "coin",
        "result": outcome,
        "voice_reply": voice_reply,
        "chat_reply": f"🪙 Moneta: **{outcome}**!",
        "embed": embed,
    }


def roll_dice(sides: int = 6) -> Dict[str, Any]:
    """Lancia un dado a N facce (default 6, oppure 20, 100, ecc.)."""
    sides = max(2, min(sides, 1000))
    val = random.randint(1, sides)

    if val == sides:
        comment = "Il punteggio massimo ragazzi! Pura energia da fuoriclasse!"
    elif val == 1:
        comment = "Numero uno ragazzi! Da qui si può solo decollare!"
    else:
        comment = "Punteggio solido, la console approva!"

    voice_reply = f"Ho tirato il dado da {sides} facce... Ed è uscito un bel {val} ragazzi! {comment} Magia!"

    embed = discord.Embed(
        title=f"🎲 Lancio del Dado (D{sides})",
        description=f"🎙️ *\"{voice_reply}\"*",
        color=0xE67E22,
    )
    embed.add_field(name="Risultato", value=f"🎯 **`{val}`** / `{sides}`", inline=True)
    embed.set_footer(text="Franchino Bot • Utility Sorte & Destino")

    return {
        "type": "dice",
        "sides": sides,
        "result": val,
        "voice_reply": voice_reply,
        "chat_reply": f"🎲 Dado (D{sides}): **{val}**!",
        "embed": embed,
    }


def choose_option(text: str) -> Dict[str, Any]:
    """Estrae le opzioni e ne sceglie una casualmente con battuta da vocalist."""
    clean = re.sub(
        r"^(?:franchino\s+)?(?:scegli\s+tra|decidi\s+tra|cosa\s+[eè]\s+meglio\s+tra|meglio|scegli|decidi)\s+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip(" ?.,!")

    # Suddivide per ' o ', ' oppure ', o virgola
    options = [o.strip() for o in re.split(r",|\b(?:oppure|o)\b", clean, flags=re.IGNORECASE) if o.strip()]
    if len(options) < 2:
        options = ["Opzione A", "Opzione B"]

    chosen = random.choice(options)
    voice_reply = (
        f"Ho ascoltato il cuore della console ragazzi... E tra tutte scelgo senza dubbi: {chosen}! Magia!"
    )

    embed = discord.Embed(
        title="✨ Scelta del Destino di Franchino",
        description=f"🎙️ *\"{voice_reply}\"*",
        color=0x9B59B6,
    )
    embed.add_field(name="Opzioni in gara", value=", ".join([f"`{o}`" for o in options]), inline=False)
    embed.add_field(name="Opzione Vincente", value=f"🏆 **{chosen.upper()}**", inline=False)
    embed.set_footer(text="Franchino Bot • Utility Sorte & Destino")

    return {
        "type": "choose",
        "options": options,
        "chosen": chosen,
        "voice_reply": voice_reply,
        "chat_reply": f"✨ Tra le opzioni scelgo: **{chosen}**!",
        "embed": embed,
    }


# ==============================================================================
# 6. BARZELLETTE & BATTUTE DA VOCALIST DELLA NOTTE
# ==============================================================================

VOCALIST_JOKES = [
    (
        "Perché i DJ mettono sempre le scarpe da ginnastica?",
        "Per non perdere mai il passo col beat! E adesso... si decolla!"
    ),
    (
        "Sapete cosa fa un matematico in discoteca dopo le quattro del mattino?",
        "Cerca il limite che tende all'infinito! Magia ragazzi!"
    ),
    (
        "Due note musicali si incontrano in console: 'Che fai stasera?'",
        "E l'altra: 'Niente fratello, resto in pausa!' Ridere fa bene all'anima!"
    ),
    (
        "Perché i programmatori amano la notte?",
        "Perché i bug vanno a dormire... e in console si accende la magia!"
    ),
    (
        "Un giorno il basso disse alla cassa: 'Senti come vibro!'",
        "E la cassa rispose: 'Tranquillo fratello, ci penso io a darti la botta giusta!' Vivere una favola!"
    ),
    (
        "Cosa dice un vocalist quando entra in pasticceria?",
        "Chiudete gli occhi e assaggiate questa millefoglie... Magia pura!"
    ),
    (
        "Perché i fantasmi amano la techno?",
        "Perché nella notte c'è lo spirito giusto per ballare!"
    ),
    (
        "Due casse acustiche si confidano a fine serata: 'Amica mia, stasera ho dato il massimo, sono proprio esausta!'",
        "E l'altra: 'Io invece mi sento ancora amplificata!' Grandi ragazzi!"
    ),
    (
        "Cosa fa un pomodoro in discoteca?",
        "Entra e fa salsa tutta la notte!"
    ),
    (
        "Perché la luna non si perde mai nella notte?",
        "Perché segue sempre il sentiero della musica!"
    ),
]


def get_vocalist_joke() -> Dict[str, Any]:
    """Restituisce una barzelletta simpatica in stile vocalist della notte."""
    question, punchline = random.choice(VOCALIST_JOKES)
    voice_reply = (
        f"Ascoltate questa ragazzi... {question} ... {punchline} Magia!"
    )
    embed = discord.Embed(
        title="😄 La Battuta della Notte di Franchino",
        description=f"**{question}**\n\n> *\"{punchline}\"*",
        color=0xF39C12,
    )
    embed.set_footer(text="Franchino Bot • Risate & Buona Energia")
    return {
        "question": question,
        "punchline": punchline,
        "voice_reply": voice_reply,
        "chat_reply": f"😄 **{question}**\n> *{punchline}*",
        "embed": embed,
    }


# ==============================================================================
# 7. ROUTER VOCALE & CLASSIFICAZIONE INTENTI SMART ASSISTANT
# ==============================================================================

def detect_smart_assistant_intent(text: str) -> Optional[str]:
    """
    Riconosce se una frase appartiene alla suite assistente intelligente (Alexa/Google Home):
    - 'time': ora, data, che ore sono, che giorno è, dimmi l'ora
    - 'weather': meteo, tempo fa, previsioni, com'è il tempo
    - 'timer': timer, promemoria, sveglia, quanto manca, cancella timer/sveglia
    - 'math': quanto fa, calcola, radice
    - 'random': dado, moneta, testa o croce, scegli tra
    - 'joke': barzelletta, battuta, fammi ridere
    """
    t = text.lower().strip().replace("’", "'")

    # 1. Timer, Promemoria & Sveglia
    if any(w in t for w in (
        "quanto manca", "tempo rimane", "tempo manca", "stato timer",
        "cancella il timer", "ferma il timer", "stop timer", "annulla il timer",
        "cancella la sveglia", "ferma la sveglia", "stop sveglia",
        "avvertimi tra", "avvisami tra", "svegliami tra"
    )) or (re.search(r"\b(?:timer|promemoria|sveglia)\b", t) and not any(w in t for w in ("svegliati", "sveglia la console"))):
        return "timer"

    # 2. Ora e Data
    if any(w in t for w in (
        "che ore sono", "che ora è", "che ora e", "orario", "ora esatta",
        "dimmi l'ora", "dimmi l ora", "sai dirmi l'ora",
        "che giorno è", "che giorno e", "data di oggi", "giorno siamo", "data odierna",
        "in che anno siamo", "che anno siamo"
    )):
        return "time"

    # 3. Meteo
    if any(w in t for w in (
        "che tempo fa", "che tempo farà", "che tempo fara",
        "previsioni meteo", "previsioni del tempo", "previsioni tempo",
        "dimmi il meteo", "meteo a", "meteo per", "meteo di",
        "piove oggi", "piove stasera", "fa caldo a", "fa freddo a"
    )) or re.search(r"\bcom['’]?\s*[eè]\s+(?:il\s+)?(?:meteo|tempo)\b", t) or (re.search(r"\bmeteo\b", t) and not any(w in t for w in ("metempsicosi", "metallo"))):
        return "weather"

    # 4. Calcoli matematici
    if any(w in t for w in ("quanto fa", "calcola", "calcolami", "fai il calcolo", "radice di", "radice quadrata")) or re.search(r"^\d+\s*[\+\-\*\/x]\s*\d+", t):
        return "math"

    # 5. Utility casuali (dado, moneta, scelta)
    if any(w in t for w in ("testa o croce", "lancia un dado", "tira un dado", "lancia una moneta", "tira una moneta", "lancia la moneta", "tira la moneta")) or re.search(r"\b(?:dado|dadi|moneta)\b", t):
        return "random"
    if any(w in t for w in ("scegli tra", "decidi tra", "cosa è meglio tra", "cosa e meglio tra", "meglio pizza o")):
        return "random"

    # 6. Barzellette & Umorismo
    if any(w in t for w in ("barzelletta", "barzellette", "battuta", "battute", "raccontami una barzelletta", "racconta una barzelletta", "fammi ridere", "dimmi una battuta", "dimmi una barzelletta")):
        return "joke"

    return None
