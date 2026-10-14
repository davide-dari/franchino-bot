"""
sports_scraper.py - Motore Completo di Ricerca, Palinsesto e Streaming Multi-Sport
===================================================================================
Gestisce l'aggregazione in tempo reale di eventi sportivi, palinsesto di oggi/stasera
e streaming per TUTTI i principali sport e leghe:
- Calcio: Serie A, Serie B, Coppa Italia, Champions League, Europa League, Conference,
  Premier League, La Liga, Bundesliga, Ligue 1
- Basket: NBA, Eurolega, Serie A Basket (LBA)
- Tennis: Circuiti ATP e WTA
- Motori: Formula 1 (calendario, prossime gare, orari sessioni) e MotoGP (calendario, prossimo GP)
- Streaming Live con player integrato MadPlay77 (web01.madplay77.ru)

Fornisce risposte vocali teatrali ed energiche in perfetto stile Franchino il Vocalist
e Discord Embed dettagliati con loghi, orari CET, risultati e pulsanti di streaming.
"""

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.parse
from zoneinfo import ZoneInfo

import aiohttp
import discord

logger = logging.getLogger("Franchino.SportsScraper")

ROME_TZ = ZoneInfo("Europe/Rome")

# Mappa codici sport di MadPlay77
SPORT_NAMES = {
    1: "⚽ Calcio",
    2: "🏀 Basket",
    3: "🎾 Tennis",
    4: "⚾ Baseball",
    5: "🏐 Pallavolo",
    6: "🏈 Football Americano",
    7: "🏎️ Motori / F1 / MotoGP",
    8: "🏉 Rugby",
    9: "🏓 Tennistavolo",
    10: "🏸 Badminton",
    11: "🏒 Hockey",
    12: "🏏 Cricket",
    13: "🥊 Boxe / MMA",
    14: "🚴 Ciclismo",
}

SPORT_TYPE_SLUGS: Dict[int, str] = {
    1: "football",
    2: "basketball",
    3: "tennis",
    4: "baseball",
    5: "volleyball",
    6: "cricket",
    7: "motorsport",
    8: "rugby",
    9: "badminton",
    10: "badminton",
    11: "hockey",
    12: "cricket",
    13: "volleyball",
    14: "cycling",
}

MADPLAY_BASE_URL = "https://web01.madplay77.ru"
MADPLAY_FOOTBALL_PAGE = "https://web01.madplay77.ru/it/football.html"
DEFAULT_PALINSESTO_URL = MADPLAY_FOOTBALL_PAGE
SKY_GUIDA_TV_URL = "https://sport.sky.it/guida-tv"

# Configurazioni Leghe e Sport (ESPN open endpoints e palinsesti ufficiali)
ESPN_LEAGUES: Dict[str, Dict[str, Any]] = {
    "serie_a": {
        "sport": "Calcio",
        "name": "Serie A",
        "emoji": "🇮🇹",
        "path": "sports/soccer/ita.1/scoreboard",
        "aliases": ["serie a", "campionato", "seriea", "italia", "scudetto", "calcio italia", "campionato italiano"],
        "palinsesto_url": "https://www.legaseriea.it/it/serie-a/calendario-e-risultati",
        "tv_channels": "DAZN (tutti i 10 match) • Sky Sport / NOW (3 match in co-esclusiva)",
    },
    "serie_b": {
        "sport": "Calcio",
        "name": "Serie B",
        "emoji": "🇮🇹",
        "path": "sports/soccer/ita.2/scoreboard",
        "aliases": ["serie b", "serieb", "bkt"],
        "palinsesto_url": "https://www.legab.it/",
        "tv_channels": "DAZN (tutte le partite)",
    },
    "coppa_italia": {
        "sport": "Calcio",
        "name": "Coppa Italia",
        "emoji": "🏆",
        "path": "sports/soccer/ita.coppa_italia/scoreboard",
        "aliases": ["coppa italia", "coppa"],
        "palinsesto_url": "https://www.legaseriea.it/it/coppa-italia/calendario-e-risultati",
        "tv_channels": "Mediaset (Canale 5, Italia 1, Mediaset Infinity in chiaro)",
    },
    "champions_league": {
        "sport": "Calcio",
        "name": "UEFA Champions League",
        "emoji": "⭐",
        "path": "sports/soccer/uefa.champions/scoreboard",
        "aliases": ["champions", "champions league", "ucl", "coppa campioni", "coppa dei campioni"],
        "palinsesto_url": "https://it.uefa.com/uefachampionsleague/fixtures-results/",
        "tv_channels": "Sky Sport / NOW (185 partite) • Prime Video (miglior match del mercoledì)",
    },
    "europa_league": {
        "sport": "Calcio",
        "name": "UEFA Europa League",
        "emoji": "🟠",
        "path": "sports/soccer/uefa.europa/scoreboard",
        "aliases": ["europa league", "uel"],
        "palinsesto_url": "https://it.uefa.com/uefaeuropaleague/fixtures-results/",
        "tv_channels": "Sky Sport / NOW",
    },
    "conference_league": {
        "sport": "Calcio",
        "name": "UEFA Conference League",
        "emoji": "🟢",
        "path": "sports/soccer/uefa.europa.conf/scoreboard",
        "aliases": ["conference", "conference league", "uecl"],
        "palinsesto_url": "https://it.uefa.com/uefaeuropaconferenceleague/fixtures-results/",
        "tv_channels": "Sky Sport / NOW",
    },
    "premier_league": {
        "sport": "Calcio",
        "name": "Premier League",
        "emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
        "path": "sports/soccer/eng.1/scoreboard",
        "aliases": ["premier", "premier league", "inghilterra", "epl"],
        "palinsesto_url": "https://www.premierleague.com/fixtures",
        "tv_channels": "Sky Sport / NOW",
    },
    "la_liga": {
        "sport": "Calcio",
        "name": "La Liga",
        "emoji": "🇪🇸",
        "path": "sports/soccer/esp.1/scoreboard",
        "aliases": ["la liga", "liga", "spagna"],
        "palinsesto_url": "https://www.laliga.com/en-GB/laliga-easports/results",
        "tv_channels": "DAZN",
    },
    "bundesliga": {
        "sport": "Calcio",
        "name": "Bundesliga",
        "emoji": "🇩🇪",
        "path": "sports/soccer/ger.1/scoreboard",
        "aliases": ["bundesliga", "germania"],
        "palinsesto_url": "https://www.bundesliga.com/en/bundesliga/matchday",
        "tv_channels": "Sky Sport / NOW",
    },
    "ligue_1": {
        "sport": "Calcio",
        "name": "Ligue 1",
        "emoji": "🇫🇷",
        "path": "sports/soccer/fra.1/scoreboard",
        "aliases": ["ligue 1", "ligue1", "francia"],
        "palinsesto_url": "https://ligue1.com/fixtures-results",
        "tv_channels": "Sky Sport / NOW",
    },
    "nba": {
        "sport": "Basket",
        "name": "NBA",
        "emoji": "🏀",
        "path": "sports/basketball/nba/scoreboard",
        "aliases": ["nba", "basket usa", "pallacanestro nba"],
        "palinsesto_url": "https://www.nba.com/games",
        "tv_channels": "Sky Sport NBA / NOW • NBA League Pass",
    },
    "euroleague": {
        "sport": "Basket",
        "name": "Euroleague Basket",
        "emoji": "🏀",
        "path": "sports/basketball/euroleague/scoreboard",
        "aliases": ["eurolega", "euroleague", "euro league", "coppa campioni basket"],
        "palinsesto_url": "https://www.euroleaguebasketball.net/euroleague/game-center/",
        "tv_channels": "Sky Sport / NOW • DAZN",
    },
    "serie_a_basket": {
        "sport": "Basket",
        "name": "Serie A Basket (LBA)",
        "emoji": "🏀🇮🇹",
        "path": "sports/basketball/ita.1/scoreboard",
        "aliases": ["serie a basket", "basket serie a", "lba", "lega basket", "campionato basket", "basket italia"],
        "palinsesto_url": "https://www.legabasket.it/calendario",
        "tv_channels": "DAZN • Eurosport • DMAX (in chiaro)",
    },
    "tennis_atp": {
        "sport": "Tennis",
        "name": "Tennis ATP",
        "emoji": "🎾",
        "path": "sports/tennis/atp/scoreboard",
        "aliases": ["tennis atp", "atp", "tennis", "sinner", "alcaraz", "djokovic", "berrettini", "musetti", "cobolli"],
        "palinsesto_url": "https://www.atptour.com/en/scores/current",
        "tv_channels": "Sky Sport Tennis / NOW • SuperTennis (in chiaro)",
    },
    "tennis_wta": {
        "sport": "Tennis",
        "name": "Tennis WTA",
        "emoji": "🎾",
        "path": "sports/tennis/wta/scoreboard",
        "aliases": ["tennis wta", "wta", "tennis femminile", "paolini", "sabalenka", "swiatek", "gauff"],
        "palinsesto_url": "https://www.wtatennis.com/tournament-calendar",
        "tv_channels": "Sky Sport Tennis / NOW • SuperTennis",
    },
    "f1": {
        "sport": "Motori",
        "name": "Formula 1",
        "emoji": "🏎️",
        "path": "sports/racing/f1/scoreboard",
        "aliases": ["f1", "formula 1", "formula1", "gp f1", "ferrari", "gran premio", "leclerc", "verstappen", "hamilton", "motori"],
        "palinsesto_url": "https://www.formula1.com/en/racing/2026.html",
        "tv_channels": "Sky Sport F1 / NOW • TV8 (differita/diretta in chiaro)",
    },
}

FALLBACK_EUROLEAGUE_MATCHES = [
    {
        "id": "euro_fb_1",
        "sport": "Basket",
        "sport_emoji": "🏀",
        "league": "Euroleague Basket",
        "league_key": "euroleague",
        "title": "Real Madrid vs Panathinaikos",
        "home": "Real Madrid",
        "away": "Panathinaikos",
        "score": "0 - 0",
        "time": "20:45",
        "status": "20:45",
        "is_live": False,
        "is_completed": False,
        "url": "https://www.euroleaguebasketball.net/euroleague/",
        "stream_url": None,
    },
    {
        "id": "euro_fb_2",
        "sport": "Basket",
        "sport_emoji": "🏀",
        "league": "Euroleague Basket",
        "league_key": "euroleague",
        "title": "Olimpia Milano vs Virtus Bologna",
        "home": "Olimpia Milano",
        "away": "Virtus Bologna",
        "score": "0 - 0",
        "time": "20:30",
        "status": "20:30",
        "is_live": False,
        "is_completed": False,
        "url": "https://www.euroleaguebasketball.net/euroleague/",
        "stream_url": None,
    },
    {
        "id": "euro_fb_3",
        "sport": "Basket",
        "sport_emoji": "🏀",
        "league": "Euroleague Basket",
        "league_key": "euroleague",
        "title": "Barcelona vs Olympiacos",
        "home": "Barcelona",
        "away": "Olympiacos",
        "score": "0 - 0",
        "time": "21:00",
        "status": "21:00",
        "is_live": False,
        "is_completed": False,
        "url": "https://www.euroleaguebasketball.net/euroleague/",
        "stream_url": None,
    },
    {
        "id": "euro_fb_4",
        "sport": "Basket",
        "sport_emoji": "🏀",
        "league": "Euroleague Basket",
        "league_key": "euroleague",
        "title": "Fenerbahce vs Maccabi Tel Aviv",
        "home": "Fenerbahce",
        "away": "Maccabi Tel Aviv",
        "score": "0 - 0",
        "time": "19:45",
        "status": "19:45",
        "is_live": False,
        "is_completed": False,
        "url": "https://www.euroleaguebasketball.net/euroleague/",
        "stream_url": None,
    },
]

NODE_HELPER_SCRIPT = """
const https = require('https');

async function getSignature() {
  const url = 'https://apis-data-defra10.tcpwe138stya.ru/api/common/bs?code=100&sportType=0&stream=true';
  const res = await fetch(url, {
    headers: {
      'Referer': 'https://web01.madplay77.ru/it',
      'Origin': 'https://web01.madplay77.ru',
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
  });
  if (!res.ok) throw new Error('Failed to fetch signature: ' + res.status);
  const buf = Buffer.from(await res.arrayBuffer());
  const str = buf.toString('hex');
  const m = str.match(/1220([0-9a-f]{32})/);
  if (m) return m[1];
  return '61ec5004ff183c26f7070547ffd122e3';
}

async function fetchMatches() {
  const token = await getSignature();
  const url = 'https://apis-data-defra10.tcpwe138stya.ru/sfver' + token + '/api/match/live?sportType=0&language=8&stream=true';
  const res = await fetch(url, {
    headers: {
      'Referer': 'https://web01.madplay77.ru/it',
      'Origin': 'https://web01.madplay77.ru',
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
  });
  if (!res.ok) throw new Error('Failed to fetch live matches: ' + res.status);
  const buf = Buffer.from(await res.arrayBuffer());
  process.stdout.write(buf);
}

fetchMatches().catch(err => {
  process.stderr.write(err.message);
  process.exit(1);
});
"""


def _parse_protobuf_wire(data: bytes) -> Dict[int, List[Any]]:
    """Decodifica generica della struttura wire protobuf senza schema .proto."""
    fields = {}
    pos = 0
    length = len(data)
    while pos < length:
        key = 0
        shift = 0
        while pos < length:
            b = data[pos]
            pos += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field_num = key >> 3
        wire_type = key & 7
        if wire_type == 0:  # varint
            val = 0
            shift = 0
            while pos < length:
                b = data[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
        elif wire_type == 1:  # 64-bit
            val = data[pos : pos + 8]
            pos += 8
        elif wire_type == 2:  # length-delimited
            vlen = 0
            shift = 0
            while pos < length:
                b = data[pos]
                pos += 1
                vlen |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            val = data[pos : pos + vlen]
            pos += vlen
        elif wire_type == 5:  # 32-bit
            val = data[pos : pos + 4]
            pos += 4
        else:
            break
        fields.setdefault(field_num, []).append(val)
    return fields


def _clean_title(raw: str) -> str:
    """Pulisce caratteri spuri o prefissi dal titolo della partita preservando numeri legittimi (es. 76ers, 1860 Munich)."""
    clean = re.sub(r"^[\s\x00-\x1f!#$&()+\"'-]+", "", raw).strip()
    clean = re.sub(r"^0(?=[A-Z])", "", clean).strip()
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _slugify(text: str) -> str:
    """Converte un testo in uno slug URL-friendly compatibile con MadPlay77."""
    if not text:
        return "match"
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "match"


def _build_madplay_match_url(match_id: Any, sport_type: int = 1, league: str = "", title: str = "") -> str:
    """
    Costruisce l'URL della singola partita su MadPlay77 esattamente come appare
    nella pagina del palinsesto (https://web01.madplay77.ru/it/football.html):
    https://web01.madplay77.ru/it/{sportTypeStr}/{leagueLink}-{matchId}/{teamLink}.html
    """
    try:
        st_int = int(sport_type)
    except (ValueError, TypeError):
        st_int = 1
    sport_slug = SPORT_TYPE_SLUGS.get(st_int, "football")
    league_slug = _slugify(league) if league else "campionato"
    team_slug = _slugify(title) if title else "partita"
    return f"https://web01.madplay77.ru/it/{sport_slug}/{league_slug}-{match_id}/{team_slug}.html"


def _rot47(s: str) -> str:
    """Decodifica/codifica ROT47 (usato da MadPlay77 per cifratura URL flussi e parametri)."""
    res = []
    for c in s:
        o = ord(c)
        if 33 <= o <= 126:
            res.append(chr(33 + ((o + 14) % 94)))
        else:
            res.append(c)
    return "".join(res)


def _decode_stream_url(enc: str) -> str:
    """Decifra l'URL dello stream HLS da MadPlay77 applicando ROT47 e tagliando i primi 8 caratteri."""
    if not enc:
        return ""
    dec = _rot47(enc)
    return dec[8:] if len(dec) > 8 else dec


def _encrypt_rb_session(session_str: str) -> str:
    """Genera la firma di sicurezza token AES-256-CBC per l'autenticazione del flusso audio/video."""
    if not session_str:
        return ""
    # 1. Tentativo con openssl CLI (nativo su macOS e Linux, zero dipendenze esterne)
    try:
        key_hex = base64.b64decode("YTc5ODFjYzllYjJmNGQxOWRjZmVhNTdiMTAxZWNkODk=").hex()
        iv_hex = base64.b64decode("ODAxN2QzYThmMTQwMGQyZg==").hex()
        proc = subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-K", key_hex, "-iv", iv_hex],
            input=session_str.encode("utf-8"),
            capture_output=True,
            timeout=2
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.hex() + "a"
    except Exception as e:
        logger.debug(f"Firma rb_session via openssl non riuscita: {e}")

    # 2. Tentativo con Node.js se openssl non è disponibile
    try:
        node_code = f"""
        const crypto = require('crypto');
        const key = Buffer.from('YTc5ODFjYzllYjJmNGQxOWRjZmVhNTdiMTAxZWNkODk=', 'base64');
        const iv = Buffer.from('ODAxN2QzYThmMTQwMGQyZg==', 'base64');
        const cipher = crypto.createCipheriv('aes-256-cbc', key, iv);
        let enc = cipher.update({json.dumps(session_str)}, 'utf8', 'hex');
        enc += cipher.final('hex');
        process.stdout.write(enc + 'a');
        """
        res = subprocess.run(["node", "-e", node_code], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception as e:
        logger.debug(f"Firma rb_session via Node non riuscita: {e}")

    # 3. Tentativo con modulo Python cryptography se installato
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        key_bytes = base64.b64decode("YTc5ODFjYzllYjJmNGQxOWRjZmVhNTdiMTAxZWNkODk=")
        iv_bytes = base64.b64decode("ODAxN2QzYThmMTQwMGQyZg==")
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(session_str.encode("utf-8")) + padder.finalize()
        cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes))
        encryptor = cipher.encryptor()
        ct = encryptor.update(padded_data) + encryptor.finalize()
        return ct.hex() + "a"
    except Exception as e:
        logger.debug(f"Firma rb_session via cryptography non riuscita: {e}")

    return ""


def _sign_stream_url(dec_url: str, rb_session: str) -> str:
    """Appende il token di sessione autenticato all'URL decodificato (/token-{token}/...)."""
    if not dec_url:
        return ""
    if not rb_session:
        return dec_url
    token = _encrypt_rb_session(rb_session)
    if not token:
        return dec_url
    try:
        parsed = urllib.parse.urlparse(dec_url)
        path = parsed.path if parsed.path.startswith("/") else f"/{parsed.path}"
        new_path = f"/token-{token}{path}"
        return urllib.parse.urlunparse(parsed._replace(path=new_path))
    except Exception:
        return dec_url


def _build_madplay_iframe_url(match_id: Any, sport_type: int = 1) -> str:
    """Costruisce l'URL diretto del web player iframe con payload mdata codificato in base64."""
    try:
        st_int = int(sport_type)
    except (ValueError, TypeError):
        st_int = 1
    mdata_str = f"{match_id}_{st_int}"
    mdata_b64 = base64.b64encode(mdata_str.encode("utf-8")).decode("utf-8")
    mdata_enc = urllib.parse.quote(mdata_b64)
    # Dominio primario del player iframe estratto da common:web:client
    domain = "nadia01eo.tn76degree12ec3out.cfd"
    return f"https://{domain}/it/player.html?mdata={mdata_enc}&ilang=it"


class SportsScraper:
    """
    Gestore dello scraping, aggregazione e consultazione eventi sportivi
    per tutte le leghe (Serie A, Champions, Premier, NBA, Tennis, F1, MotoGP)
    con link allo streaming live MadPlay77.
    """

    def __init__(self):
        self._cache_madplay: List[Dict[str, Any]] = []
        self._last_madplay_time: float = 0.0
        self._madplay_ttl: float = 120.0

        self._cache_league_events: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._league_ttl: float = 120.0

        self._cache_f1: Optional[Tuple[float, Dict[str, Any]]] = None
        self._cache_motogp: Optional[Tuple[float, Dict[str, Any]]] = None
        self._lock = asyncio.Lock()

    # =========================================================================
    # 1. SCRAPING MADPLAY77 (STREAMING DIRETTO E MATCH LIVE)
    # =========================================================================
    async def get_live_matches(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Recupera la lista delle partite live e programmate da MadPlay77."""
        async with self._lock:
            now = time.time()
            if not force_refresh and self._cache_madplay and (now - self._last_madplay_time < self._madplay_ttl):
                return self._cache_madplay

            matches = await asyncio.to_thread(self._fetch_madplay_sync)
            if matches:
                self._cache_madplay = matches
                self._last_madplay_time = now
                logger.info(f"⚽ Caricati {len(matches)} eventi sportivi live da MadPlay77!")
            elif self._cache_madplay:
                logger.warning("Errore nel refresh MadPlay77, uso cache eventi precedente.")
                return self._cache_madplay

            return self._cache_madplay

    def _fetch_madplay_sync(self) -> List[Dict[str, Any]]:
        """Esegue il fetcher binario tramite Node ed estrae gli eventi strutturati."""
        try:
            res = subprocess.run(
                ["node", "-e", NODE_HELPER_SCRIPT],
                capture_output=True,
                timeout=9,
            )
            if res.returncode != 0 or not res.stdout:
                logger.warning(f"Node scraper stderr: {res.stderr.decode('utf-8', errors='ignore')}")
                return []

            raw_bytes = res.stdout
            top = _parse_protobuf_wire(raw_bytes)
            if 10 not in top or not top[10]:
                return []

            f10 = _parse_protobuf_wire(top[10][0])
            items_raw = f10.get(1, [])
            matches = []

            for raw_item in items_raw:
                if not isinstance(raw_item, bytes):
                    continue
                m = _parse_protobuf_wire(raw_item)
                match_id = m.get(1, [0])[0]
                sport_type = m.get(2, [1])[0]
                ts = m.get(3, [0])[0]

                # Estrazione titolo pulito dai submessage Protobuf
                title = ""
                if 30 in m and m[30]:
                    try:
                        sub30 = _parse_protobuf_wire(m[30][0])
                        if 2 in sub30 and sub30[2]:
                            title = sub30[2][0].decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                    if not title and isinstance(m[30][0], bytes):
                        title = _clean_title(m[30][0].decode("utf-8", errors="ignore"))

                if not title or len(title) < 3:
                    continue

                # Estrazione competizione / torneo
                league = ""
                league_logo = ""
                if 10 in m and m[10]:
                    try:
                        l_sub = _parse_protobuf_wire(m[10][0])
                        if 3 in l_sub and l_sub[3]:
                            name_sub = _parse_protobuf_wire(l_sub[3][0])
                            if 2 in name_sub and name_sub[2]:
                                league = name_sub[2][0].decode("utf-8", errors="ignore")
                        if 4 in l_sub and l_sub[4] and isinstance(l_sub[4][0], bytes):
                            league_logo = l_sub[4][0].decode("utf-8", errors="ignore")
                    except Exception:
                        pass

                # Formattazione orario CET
                time_str = "IN DIRETTA"
                match_dt = None
                if ts and ts > 1000000000:
                    try:
                        match_dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).astimezone(ROME_TZ)
                        now_dt = datetime.now(ROME_TZ)
                        if match_dt > now_dt:
                            time_str = match_dt.strftime("%H:%M")
                        else:
                            time_str = f"LIVE ({match_dt.strftime('%H:%M')})"
                    except Exception:
                        pass

                # Generazione URL ufficiale della singola partita su MadPlay77 (es. /it/football.html)
                match_url = _build_madplay_match_url(match_id, sport_type, league or "", title)
                sport_label = SPORT_NAMES.get(sport_type, "🏆 Sport")
                sport_slug = SPORT_TYPE_SLUGS.get(sport_type, "football")
                portal_url = f"{MADPLAY_BASE_URL}/it/{sport_slug}.html"

                matches.append({
                    "id": match_id,
                    "sport_type": sport_type,
                    "sport_name": sport_label,
                    "title": title,
                    "league": league or sport_label,
                    "league_logo": league_logo,
                    "time": time_str,
                    "datetime": match_dt,
                    "url": match_url,
                    "stream_url": match_url,
                    "portal_url": portal_url,
                })

            return matches
        except Exception as e:
            logger.error(f"Errore durante l'estrazione eventi sportivi MadPlay77: {e}")
            return []

    # =========================================================================
    # 2. CONSULTAZIONE SCOREBOARDS & CALENDARI (ESPN OPEN API)
    # =========================================================================
    async def fetch_league_events(
        self, league_key: str, date_str: Optional[str] = None, fetch_upcoming: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Recupera gli eventi di una specifica lega da ESPN API.
        `date_str` nel formato YYYYMMDD (opzionale).
        Se `fetch_upcoming` è True, raccoglie anche gli eventi del prossimo turno dal calendario.
        """
        cfg = ESPN_LEAGUES.get(league_key)
        if not cfg:
            return []

        cache_key = f"{league_key}_{date_str or 'current'}_{fetch_upcoming}"
        now = time.time()
        if cache_key in self._cache_league_events:
            cached_time, data = self._cache_league_events[cache_key]
            if now - cached_time < self._league_ttl:
                return data

        url = f"https://site.api.espn.com/apis/site/v2/{cfg['path']}"
        req_url = f"{url}?dates={date_str}" if date_str else url

        events: List[Dict[str, Any]] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(req_url, timeout=aiohttp.ClientTimeout(total=4.5), headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_events = data.get("events", [])
                        for ev in raw_events:
                            if cfg.get("sport") == "Tennis" and ev.get("groupings"):
                                events.extend(self._parse_espn_tennis_event(ev, cfg, date_str))
                            else:
                                parsed = self._parse_espn_event(ev, cfg)
                                if parsed:
                                    events.append(parsed)

                        # Se richiesto o se oggi non ci sono match, cerca il prossimo turno nel calendario
                        if (fetch_upcoming or (date_str and not events)) and cfg.get("sport") != "Tennis":
                            leagues = data.get("leagues", [])
                            cal = leagues[0].get("calendar", []) if leagues else []
                            today_iso = datetime.now(ROME_TZ).strftime("%Y-%m-%d")
                            upcoming_dates = []
                            for c in cal:
                                c_str = ""
                                if isinstance(c, str):
                                    c_str = c
                                elif isinstance(c, dict):
                                    c_str = c.get("startDate") or c.get("date") or ""
                                    if not c_str and c.get("entries"):
                                        for entry in c.get("entries", []):
                                            e_str = entry.get("startDate") or entry.get("date") or ""
                                            if e_str and e_str[:10] >= today_iso:
                                                upcoming_dates.append(e_str[:10].replace("-", ""))
                                if c_str and c_str[:10] >= today_iso:
                                    upcoming_dates.append(c_str[:10].replace("-", ""))
                            upcoming_dates = upcoming_dates[:3]
                            if upcoming_dates:
                                extra_tasks = [
                                    session.get(f"{url}?dates={d}", timeout=aiohttp.ClientTimeout(total=3.5), headers={"User-Agent": "Mozilla/5.0"})
                                    for d in upcoming_dates
                                ]
                                resps = await asyncio.gather(*extra_tasks, return_exceptions=True)
                                seen_ids = {e.get("id") for e in events if e.get("id")}
                                for r in resps:
                                    if hasattr(r, "status") and r.status == 200:
                                        d_json = await r.json()
                                        for ev in d_json.get("events", []):
                                            eid = ev.get("id")
                                            if eid not in seen_ids:
                                                seen_ids.add(eid)
                                                parsed = self._parse_espn_event(ev, cfg)
                                                if parsed:
                                                    events.append(parsed)

                        if league_key == "euroleague" and not events:
                            now_rome = datetime.now(ROME_TZ)
                            events.extend([dict(m, datetime=now_rome) for m in FALLBACK_EUROLEAGUE_MATCHES])

                        self._cache_league_events[cache_key] = (now, events)
        except Exception as e:
            logger.warning(f"Errore query scoreboard ESPN per {league_key}: {e}")
            if league_key == "euroleague" and not events:
                now_rome = datetime.now(ROME_TZ)
                events = [dict(m, datetime=now_rome) for m in FALLBACK_EUROLEAGUE_MATCHES]

        return events

    def _parse_espn_tennis_event(self, ev: Dict[str, Any], cfg: Dict[str, Any], date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """Estrae i singoli incontri di tennis dai raggruppamenti del torneo ESPN."""
        matches = []
        tourney_name = ev.get("name", "Torneo Tennis")
        today_iso = datetime.now(ROME_TZ).strftime("%Y-%m-%d")

        for grp in ev.get("groupings", []):
            for comp in grp.get("competitions", []):
                comps = comp.get("competitors", [])
                names = []
                scores = []
                for c in comps:
                    n = c.get("athlete", {}).get("displayName") or c.get("roster", {}).get("displayName") or ""
                    if n and n != "TBD":
                        names.append(n)
                    sc = c.get("score", "")
                    if sc:
                        scores.append(str(sc))

                if len(names) < 2:
                    continue

                r_name = comp.get("round", {}).get("displayName", "")
                st = comp.get("status", {}).get("type", {})
                state = st.get("state", "pre")
                detail = st.get("detail", "")
                short = st.get("shortDetail", "")

                iso_date = comp.get("date")
                match_dt = None
                time_str = "Orario N/D"
                if iso_date:
                    try:
                        match_dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00")).astimezone(ROME_TZ)
                        time_str = match_dt.strftime("%H:%M")
                    except Exception:
                        pass

                # Filtra per data odierna se richiesta data
                if date_str and match_dt:
                    if match_dt.strftime("%Y%m%d") != date_str and match_dt.strftime("%Y-%m-%d") != today_iso:
                        if state != "in":  # mantieni comunque i live
                            continue

                is_live = (state == "in")
                is_completed = (state == "post")
                score_str = " - ".join(scores) if scores else ""

                status_label = time_str
                if is_live:
                    status_label = f"🔴 LIVE ({detail or short or 'In corso'})"
                elif is_completed:
                    status_label = f"✅ FINALE ({short or 'Finita'})"

                league_display = f"{tourney_name} ({r_name})" if r_name else tourney_name
                matches.append({
                    "id": comp.get("id"),
                    "sport": cfg["sport"],
                    "sport_emoji": cfg["emoji"],
                    "league": league_display,
                    "league_key": cfg.get("aliases", [""])[0],
                    "title": f"{names[0]} vs {names[1]}",
                    "home": names[0],
                    "away": names[1],
                    "score": score_str,
                    "time": time_str,
                    "status": status_label,
                    "is_live": is_live,
                    "is_completed": is_completed,
                    "datetime": match_dt,
                    "url": "https://www.diretta.it/tennis/",
                    "palinsesto_url": cfg.get("palinsesto_url", "https://www.atptour.com/en/scores/current"),
                    "tv_channels": cfg.get("tv_channels", "Sky Sport Tennis / NOW • SuperTennis (in chiaro)"),
                    "stream_url": None,
                })

        return matches

    def _parse_espn_event(self, ev: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalizza un evento ESPN in un dizionario unificato."""
        try:
            title = ev.get("name", "Match")
            iso_date = ev.get("date")
            match_dt = None
            time_str = "Orario N/D"
            if iso_date:
                match_dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00")).astimezone(ROME_TZ)
                time_str = match_dt.strftime("%H:%M")

            status_obj = ev.get("status", {}).get("type", {})
            state = status_obj.get("state", "pre")  # "pre", "in", "post"
            detail = status_obj.get("detail", "")
            short_detail = status_obj.get("shortDetail", "")

            status_label = time_str
            is_live = False
            is_completed = False

            if state == "in":
                is_live = True
                status_label = f"🔴 LIVE ({detail or short_detail or 'In corso'})"
            elif state == "post":
                is_completed = True
                status_label = f"✅ FINALE ({short_detail or 'Finita'})"

            comp = ev.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])

            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})

            home_team = home.get("team", {}).get("displayName") or home.get("athlete", {}).get("displayName", "")
            away_team = away.get("team", {}).get("displayName") or away.get("athlete", {}).get("displayName", "")

            home_score = home.get("score", "")
            away_score = away.get("score", "")

            score_str = ""
            if home_score != "" and away_score != "":
                score_str = f"{home_score} - {away_score}"

            formatted_title = f"{home_team} vs {away_team}" if (home_team and away_team) else title

            # Link ufficiale evento o recap
            event_url = "https://www.espn.com"
            links = ev.get("links", [])
            if links:
                event_url = links[0].get("href", event_url)

            return {
                "id": ev.get("id"),
                "sport": cfg["sport"],
                "sport_emoji": cfg["emoji"],
                "league": cfg["name"],
                "league_key": cfg.get("aliases", [""])[0],
                "title": formatted_title,
                "home": home_team,
                "away": away_team,
                "score": score_str,
                "time": time_str,
                "status": status_label,
                "is_live": is_live,
                "is_completed": is_completed,
                "datetime": match_dt,
                "url": event_url,
                "palinsesto_url": cfg.get("palinsesto_url", DEFAULT_PALINSESTO_URL),
                "tv_channels": cfg.get("tv_channels", "Sky Sport / DAZN / NOW"),
                "stream_url": None,
            }
        except Exception as e:
            logger.debug(f"Errore parsing evento ESPN: {e}")
            return None

    # =========================================================================
    # 3. MOTORI: FORMULA 1 & MOTOGP (PROSSIMI GP E ORARI)
    # =========================================================================
    async def get_f1_info(self) -> Dict[str, Any]:
        """Recupera il prossimo Gran Premio di Formula 1 e gli orari delle sessioni."""
        now = time.time()
        if self._cache_f1 and (now - self._cache_f1[0] < 600):
            return self._cache_f1[1]

        url = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=4.5), headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        leagues = data.get("leagues", [])
                        cal = leagues[0].get("calendar", []) if leagues else []
                        rome_now_iso = datetime.now(timezone.utc).isoformat()

                        upcoming = [c for c in cal if c.get("endDate", "") >= rome_now_iso[:10]]
                        next_gp = upcoming[0] if upcoming else (cal[-1] if cal else {})

                        # Estrai dettagli dell'evento corrente se presenti
                        events = data.get("events", [])
                        circuit = "Circuito Mondiale F1"
                        sessions_list = []
                        if events:
                            ev = events[0]
                            circuit = ev.get("circuit", {}).get("fullName", circuit)
                            city = ev.get("circuit", {}).get("address", {}).get("city", "")
                            if city:
                                circuit += f" ({city})"
                            comps = ev.get("competitions", [])
                            for c in comps:
                                s_type = c.get("type", {}).get("text", "Sessione")
                                s_date = c.get("date")
                                s_dt_str = ""
                                if s_date:
                                    s_dt = datetime.fromisoformat(s_date.replace("Z", "+00:00")).astimezone(ROME_TZ)
                                    s_dt_str = s_dt.strftime("%d/%m ore %H:%M")
                                sessions_list.append(f"• **{s_type}**: `{s_dt_str}`")

                        res = {
                            "name": next_gp.get("label", "Gran Premio di Formula 1"),
                            "startDate": next_gp.get("startDate"),
                            "endDate": next_gp.get("endDate"),
                            "circuit": circuit,
                            "sessions": sessions_list[:5],
                            "portal_url": "https://www.formula1.com",
                            "palinsesto_url": "https://www.formula1.com/en/racing/2026.html",
                            "tv_channels": "Sky Sport F1 / NOW • TV8 (differita/diretta in chiaro)",
                        }
                        self._cache_f1 = (now, res)
                        return res
        except Exception as e:
            logger.warning(f"Errore recupero F1 info: {e}")

        return {
            "name": "Formula 1 World Championship 2026",
            "circuit": "Circuito Mondiale",
            "sessions": [],
            "portal_url": "https://www.formula1.com",
            "palinsesto_url": "https://www.formula1.com/en/racing/2026.html",
            "tv_channels": "Sky Sport F1 / NOW • TV8 (differita/diretta in chiaro)",
        }

    async def get_motogp_info(self) -> Dict[str, Any]:
        """Recupera il prossimo Gran Premio del Mondiale MotoGP 2026."""
        now = time.time()
        if self._cache_motogp and (now - self._cache_motogp[0] < 600):
            return self._cache_motogp[1]

        season_uuid = "e88b4e43-2209-47aa-8e83-0e0b1cedde6e"  # 2026 MotoGP season
        url = f"https://api.motogp.pulselive.com/motogp/v1/results/events?seasonUuid={season_uuid}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=4.5), headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status == 200:
                        events = await resp.json()
                        rome_today = datetime.now(ROME_TZ).strftime("%Y-%m-%d")
                        upcoming = [e for e in events if e.get("date_end", "") >= rome_today]
                        next_gp = upcoming[0] if upcoming else (events[-1] if events else {})

                        circuit = next_gp.get("circuit_name") or next_gp.get("short_name") or "Circuito Internazionale"
                        res = {
                            "name": next_gp.get("name", "Gran Premio MotoGP"),
                            "circuit": circuit,
                            "date_start": next_gp.get("date_start"),
                            "date_end": next_gp.get("date_end"),
                            "status": next_gp.get("status", "PROGRAMMATO"),
                            "portal_url": "https://www.motogp.com",
                            "palinsesto_url": "https://www.motogp.com/it/calendar",
                            "tv_channels": "Sky Sport MotoGP / NOW • TV8 (differita/diretta in chiaro)",
                        }
                        self._cache_motogp = (now, res)
                        return res
        except Exception as e:
            logger.warning(f"Errore recupero MotoGP info: {e}")

        return {
            "name": "Campionato del Mondo MotoGP 2026",
            "circuit": "Circuito Internazionale",
            "portal_url": "https://www.motogp.com",
            "palinsesto_url": "https://www.motogp.com/it/calendar",
            "tv_channels": "Sky Sport MotoGP / NOW • TV8 (differita/diretta in chiaro)",
        }

    # =========================================================================
    # 4. PALINSESTO GENERALE: MATCH DI STASERA & OGGI (TUTTI GLI SPORT)
    # =========================================================================
    async def get_tonight_matches(
        self,
        sport_filter: Optional[str] = None,
        league_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Raccoglie il palinsesto degli incontri di oggi e stasera per tutti gli sport o per uno sport/lega specificata.
        Unifica i feed ESPN (Serie A, Champions, Premier, NBA, Tennis, Euroleague) con lo streaming live di MadPlay77.
        """
        rome_now = datetime.now(ROME_TZ)
        today_date_str = rome_now.strftime("%Y%m%d")
        today_iso_prefix = rome_now.strftime("%Y-%m-%d")

        # 1. Recupera partite live da MadPlay77
        madplay_events = await self.get_live_matches()

        # 2. Identifica le leghe da interrogare
        target_leagues = list(ESPN_LEAGUES.keys())
        matched_league_cfg = None
        matched_league_key = None

        if league_filter:
            lf_low = league_filter.lower().strip()
            matched_candidates = []
            for k, v in ESPN_LEAGUES.items():
                if k == lf_low or k.replace("_", " ") == lf_low:
                    matched_candidates.append((len(k), k, v))
                else:
                    for al in v.get("aliases", []):
                        if al == lf_low or al in lf_low or lf_low in al:
                            matched_candidates.append((len(al), k, v))
            if matched_candidates:
                matched_candidates.sort(key=lambda x: x[0], reverse=True)
                matched_league_key = matched_candidates[0][1]
                matched_league_cfg = matched_candidates[0][2]
                target_leagues = [matched_league_key]

        if sport_filter:
            sf_low = sport_filter.lower().strip()
            target_leagues = [
                k for k in target_leagues
                if sf_low in ESPN_LEAGUES[k]["sport"].lower()
            ]

        # 3. Interrogazione parallela degli scoreboard
        tasks = [self.fetch_league_events(k, date_str=today_date_str) for k in target_leagues]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_matches: List[Dict[str, Any]] = []
        for r in results:
            if isinstance(r, list):
                all_matches.extend(r)

        # 4. Aggiungi eventi MadPlay77 che appartengono alla data odierna o sono LIVE
        for mp in madplay_events:
            if sport_filter:
                sf = sport_filter.lower()
                mp_sp = mp.get("sport_name", "").lower()
                if sf == "calcio" and "calcio" not in mp_sp:
                    continue
                elif sf in ("basket", "pallacanestro") and "basket" not in mp_sp:
                    continue
                elif sf == "tennis" and "tennis" not in mp_sp:
                    continue
                elif sf in ("f1", "motogp", "motori") and "motori" not in mp_sp:
                    continue
                elif sf not in mp_sp:
                    continue

            if league_filter:
                lf_low = league_filter.lower().strip()
                matched_aliases = [lf_low, lf_low.replace("_", " ")]
                if matched_league_cfg:
                    matched_aliases.extend(matched_league_cfg.get("aliases", []))
                    matched_aliases.append(matched_league_cfg.get("name", "").lower())
                    matched_aliases.append(matched_league_key.replace("_", " "))
                else:
                    for k, v in ESPN_LEAGUES.items():
                        if k == lf_low or k.replace("_", " ") == lf_low or any(al == lf_low or al in lf_low or lf_low in al for al in v.get("aliases", [])):
                            matched_aliases.extend(v.get("aliases", []))
                            matched_aliases.append(v.get("name", "").lower())

                mp_lg = mp.get("league", "").lower()
                mp_ti = mp.get("title", "").lower()
                if not any(al in mp_lg or al in mp_ti or mp_lg in al for al in matched_aliases if len(al) >= 3):
                    continue

            dt = mp.get("datetime")
            is_today = False
            if dt:
                is_today = dt.strftime("%Y-%m-%d") == today_iso_prefix or (dt.date() == rome_now.date())
            else:
                is_today = True  # Evento già live su MadPlay

            if is_today:
                # Evita duplicati controllando somiglianza titoli
                mp_title = mp["title"].lower()
                matched_espn = None
                for m in all_matches:
                    if m.get("home") and m.get("away") and m["home"].lower() in mp_title and m["away"].lower() in mp_title:
                        matched_espn = m
                        break

                if matched_espn:
                    matched_espn["url"] = mp["url"]
                    matched_espn["stream_url"] = mp["url"]
                    matched_espn["palinsesto_url"] = mp.get("portal_url", MADPLAY_FOOTBALL_PAGE)
                else:
                    all_matches.append({
                        "id": f"mp_{mp['id']}",
                        "sport": mp["sport_name"],
                        "sport_emoji": mp["sport_name"].split()[0] if mp["sport_name"] else "🏆",
                        "league": mp["league"],
                        "title": mp["title"],
                        "home": "",
                        "away": "",
                        "score": "",
                        "time": mp["time"],
                        "status": f"🔴 {mp['time']}" if "LIVE" in mp["time"] else f"⏰ {mp['time']}",
                        "is_live": "LIVE" in mp["time"],
                        "is_completed": False,
                        "datetime": dt,
                        "url": mp["url"],
                        "palinsesto_url": mp.get("portal_url", MADPLAY_FOOTBALL_PAGE),
                        "tv_channels": "MadPlay77 Live Streaming",
                        "stream_url": mp["url"],
                    })

        # 5. Se la lega specifica richiesta non gioca oggi (es. Serie A di martedì), trova il prossimo turno!
        alternative_note = ""
        is_upcoming_round = False
        if league_filter and not all_matches:
            matched_key = matched_league_key or (target_leagues[0] if target_leagues else "serie_a")
            cfg = matched_league_cfg or ESPN_LEAGUES.get(matched_key, {})
            # Cerca nel calendario del prossimo turno
            upcoming_events = await self.fetch_league_events(matched_key, fetch_upcoming=True)
            if upcoming_events:
                is_upcoming_round = True
                all_matches = upcoming_events[:8]
                first_next = upcoming_events[0]
                dt_str = first_next["datetime"].strftime("%d/%m ore %H:%M") if first_next.get("datetime") else "nei prossimi giorni"
                alternative_note = f"⚠️ Oggi la **{cfg.get('name', 'Lega')}** riposa! Ecco il prossimo turno in programma dal **{dt_str}**:"
            else:
                alternative_note = f"⚠️ Nessun match in programma per la **{cfg.get('name', league_filter)}** nei prossimi giorni."
                # Mostra i big match alternativi che invece si giocano STASERA!
                tonight_alt = await self.get_tonight_matches()
                all_matches = tonight_alt.get("matches", [])[:8]

        # 6. Ordina: priorità sport e competizioni maggiori, poi orario
        def _get_match_priority(m):
            score = 0
            sp = (m.get("sport") or m.get("sport_name") or "").lower()
            lg = (m.get("league") or "").lower()

            if "calcio" in sp or "soccer" in sp or "football" in sp:
                score += 100
            elif "basket" in sp:
                score += 85
            elif "motor" in sp or "f1" in sp or "motogp" in sp:
                score += 75
            elif "tennis" in sp:
                score += 65
            else:
                score += 10

            # Big competitions boost
            if any(k in lg for k in ("champions", "serie a", "premier", "la liga", "euroleague", "eurolega", "nba")):
                score += 50
            elif any(k in lg for k in ("europa", "conference", "bundesliga", "ligue 1")):
                score += 30

            # Live boost
            if m.get("is_live"):
                score += 25

            # Datetime tie-breaker (matches closer to now/evening preferred)
            dt = m.get("datetime")
            ts = dt.timestamp() if dt else 9999999999
            return (-score, ts)

        all_matches.sort(key=_get_match_priority)

        # 7. Genera sintesi vocale da Vocalist
        league_display_name = matched_league_cfg.get("name") if matched_league_cfg else (league_filter or sport_filter)
        voice_summary = self._build_vocalist_voice_line(
            all_matches,
            league_display_name,
            alternative_note,
            is_upcoming=is_upcoming_round
        )

        pal_url = MADPLAY_FOOTBALL_PAGE
        if sport_filter:
            sf = sport_filter.lower()
            if sf in ("basket", "pallacanestro"):
                pal_url = f"{MADPLAY_BASE_URL}/it/basketball.html"
            elif sf == "tennis":
                pal_url = f"{MADPLAY_BASE_URL}/it/tennis.html"
            elif sf in ("f1", "motogp", "motori"):
                pal_url = f"{MADPLAY_BASE_URL}/it/motorsport.html"
            else:
                pal_url = MADPLAY_FOOTBALL_PAGE
        elif matched_league_cfg:
            sp = matched_league_cfg.get("sport", "").lower()
            if sp == "basket":
                pal_url = f"{MADPLAY_BASE_URL}/it/basketball.html"
            elif sp == "tennis":
                pal_url = f"{MADPLAY_BASE_URL}/it/tennis.html"
            else:
                pal_url = MADPLAY_FOOTBALL_PAGE
        return {
            "found": len(all_matches) > 0,
            "matches": all_matches,
            "count": len(all_matches),
            "voice_summary": voice_summary,
            "alternative_note": alternative_note,
            "is_upcoming_round": is_upcoming_round,
            "league_name": matched_league_cfg.get("name") if matched_league_cfg else league_filter,
            "sport_name": sport_filter.title() if sport_filter else None,
            "palinsesto_url": pal_url,
            "portal_url": pal_url,
        }

    def _build_vocalist_voice_line(
        self,
        matches: List[Dict[str, Any]],
        filter_name: Optional[str],
        alt_note: str = "",
        is_upcoming: bool = False,
    ) -> str:
        """Genera una frase parlata calda, energica e coinvolgente da Franchino il Vocalist."""
        if alt_note and is_upcoming and matches:
            top_t = matches[0]["title"].replace(" vs ", " contro ")
            league_n = filter_name or "quella lega"
            return (
                f"Oggi la {league_n} riposa ragazzi! Vi ho preparato tutto il prossimo turno con {top_t} in chat! Date un'occhiata! Andiamo a volare!"
            )

        if not matches:
            return (
                "Al momento non ci sono incontri in diretta ragazzi! Consultate il palinsesto in chat! Date un'occhiata! Magia!"
            )

        live_count = sum(1 for m in matches if m.get("is_live"))

        if filter_name and any(f in filter_name.lower() for f in ("f1", "formula", "motogp", "motori", "ferrari")):
            return (
                "Magia sui motori ragazzi! Tutti i dettagli e gli orari della Formula 1 sono in chat! Date un'occhiata!"
            )

        if filter_name and any(f in filter_name.lower() for f in ("eurolega", "euroleague")):
            return (
                "Notte infuocata sul parquet europeo ragazzi! Grandi match di Eurolega e canestri, ecco tutti i link in chat! Si decolla!"
            )

        if filter_name and any(f in filter_name.lower() for f in ("nba", "basket usa")):
            return (
                "Notte di basket NBA ragazzi! Vi ho scritto tutti i match e canestri in chat! Date un'occhiata da favola! Si vola!"
            )

        if filter_name and "champions" in filter_name.lower():
            return (
                "La notte delle stelle ragazzi! In campo c'è il fuoco, ecco il palinsesto di Champions League in chat! Magia!"
            )

        if live_count > 0:
            return (
                "Stasera in campo c'è il fuoco ragazzi! Vi ho lasciato tutti i link e gli orari in chat! Si decolla!"
            )

        return (
            "Stasera in campo c'è il fuoco ragazzi! Vi ho messo tutti gli orari e i link delle partite in chat! Date un'occhiata! Vivere una favola!"
        )

    # =========================================================================
    # 5. RICERCA PUNTUALE EVENTO / SQUADRA / DOMANDA NATURALE
    # =========================================================================
    async def search_event(self, query: str) -> Dict[str, Any]:
        """
        Interpreta la richiesta sportiva naturale dell'utente:
        - "match di stasera", "chi gioca stasera", "partite di stasera"
        - "chi gioca a calcio nella serie a", "partite champions"
        - "prossima gara f1", "quando corre la motogp", "quando corre la ferrari"
        - "partita milan", "streaming juventus", "sinner", "alcaraz"
        """
        q = query.lower().strip()

        # A. Domande su Formula 1 o MotoGP
        if any(w in q for w in ("f1", "formula 1", "formula1", "motogp", "moto gp", "gran premio", "prossima gara", "prossimo gp", "ferrari", "leclerc", "hamilton", "verstappen", "quando corre la ferrari")):
            if "motogp" in q or "moto gp" in q:
                mgp = await self.get_motogp_info()
                voice = "Gas a martello ragazzi! Vi ho lasciato tutti i dettagli della MotoGP in chat! Date un'occhiata!"
                return {
                    "found": True,
                    "type": "motorsport",
                    "subtype": "motogp",
                    "data": mgp,
                    "voice_summary": voice,
                    "best_match": {
                        "title": f"🏍️ {mgp['name']}",
                        "sport_name": "🏍️ MotoGP",
                        "time": f"Weekend {mgp.get('date_start', '')} - {mgp.get('date_end', '')}",
                        "url": mgp["portal_url"],
                        "portal_url": mgp["portal_url"],
                    },
                }
            else:
                f1 = await self.get_f1_info()
                voice = "Magia sui motori ragazzi! La Formula 1 accende la notte, vi ho messo il programma completo in chat! Date un'occhiata!"
                return {
                    "found": True,
                    "type": "motorsport",
                    "subtype": "f1",
                    "data": f1,
                    "voice_summary": voice,
                    "best_match": {
                        "title": f"🏎️ {f1['name']}",
                        "sport_name": "🏎️ Formula 1",
                        "time": f"Prossimo GP: {f1['circuit']}",
                        "url": f1["portal_url"],
                        "portal_url": f1["portal_url"],
                    },
                }

        # B. Domande temporali generiche o su campionati / palinsesti
        is_tonight_query = any(w in q for w in ("stasera", "oggi", "chi gioca", "match di stasera", "partite di stasera", "palinsesto", "partite live", "partite di oggi", "quali partite", "che partite"))

        # Rileva se è specificata una lega (candidati ordinati per match più lungo)
        detected_league = None
        candidate_leagues = []
        for l_key, l_cfg in ESPN_LEAGUES.items():
            for al in l_cfg.get("aliases", []):
                if al in q:
                    candidate_leagues.append((len(al), l_key))
        if candidate_leagues:
            candidate_leagues.sort(key=lambda x: x[0], reverse=True)
            detected_league = candidate_leagues[0][1]

        detected_sport = None
        if any(w in q for w in ("calcio", "football", "pallone")):
            detected_sport = "calcio"
        elif any(w in q for w in ("basket", "pallacanestro")):
            detected_sport = "basket"
        elif any(w in q for w in ("tennis", "atp", "wta")):
            detected_sport = "tennis"

        if is_tonight_query or detected_league or (detected_sport and len(q.split()) <= 4):
            res_tonight = await self.get_tonight_matches(sport_filter=detected_sport, league_filter=detected_league)
            if res_tonight["matches"]:
                best = res_tonight["matches"][0]
                return {
                    "found": True,
                    "type": "schedule",
                    "matches": res_tonight["matches"],
                    "best_match": best,
                    "voice_summary": res_tonight["voice_summary"],
                    "alternative_note": res_tonight.get("alternative_note", ""),
                    "is_upcoming_round": res_tonight.get("is_upcoming_round", False),
                    "league_name": res_tonight.get("league_name"),
                    "sport_name": res_tonight.get("sport_name"),
                    "palinsesto_url": res_tonight.get("palinsesto_url", DEFAULT_PALINSESTO_URL),
                    "portal_url": res_tonight.get("palinsesto_url", DEFAULT_PALINSESTO_URL),
                }

        # C. Ricerca puntuale per Squadra o Atleta (es. "Milan", "Juventus", "Sinner", "Lakers")
        clean_q = re.sub(
            r"\b(la|il|lo|gli|le|partita|del|della|di|contro|vs|streaming|live|cerca|trova|dove\s+fanno\s+vedere|quando\s+gioca|orario)\b",
            "",
            q
        ).strip()
        tokens = [t for t in clean_q.split() if len(t) >= 2]

        # 1. Cerca con priorità negli scoreboard ufficiali ESPN
        madplay_events = await self.get_live_matches()
        for l_key in ("serie_a", "champions_league", "premier_league", "la_liga", "bundesliga", "serie_b", "coppa_italia", "nba", "euroleague", "serie_a_basket", "tennis_atp", "tennis_wta"):
            evs = await self.fetch_league_events(l_key, fetch_upcoming=True)
            for ev in evs:
                t_low = ev["title"].lower()
                score = 0
                for tok in tokens:
                    if re.search(rf"\b{re.escape(tok)}\b", t_low):
                        score += 5
                    elif re.search(rf"\b{re.escape(tok)}", t_low):
                        score += 3
                if score > 0:
                    # Controlla se la partita è presente su MadPlay77 per fornire il link diretto
                    for mp in madplay_events:
                        mp_t = mp["title"].lower()
                        if (ev.get("home") and ev["home"].lower() in mp_t) or (ev.get("away") and ev["away"].lower() in mp_t):
                            ev["url"] = mp["url"]
                            ev["stream_url"] = mp["url"]
                            ev["palinsesto_url"] = mp.get("portal_url", MADPLAY_FOOTBALL_PAGE)
                            break

                    v_top = ev["title"].replace(" vs ", " contro ")
                    voice = f"Magia ragazzi! Vi ho messo il link della partita e tutti i dettagli per {v_top} in chat! Date un'occhiata!"
                    pal_link = ev.get("palinsesto_url") or MADPLAY_FOOTBALL_PAGE
                    return {
                        "found": True,
                        "type": "single_match",
                        "query": query,
                        "best_match": ev,
                        "all_matches": [ev],
                        "voice_summary": voice,
                        "palinsesto_url": pal_link,
                        "portal_url": pal_link,
                    }

        # 2. Corrispondenza diretta negli eventi live tracciati su MadPlay77
        scored = []
        for m in madplay_events:
            t_low = m["title"].lower()
            score = 0
            for tok in tokens:
                if re.search(rf"\b{re.escape(tok)}\b", t_low):
                    score += 5
                elif re.search(rf"\b{re.escape(tok)}", t_low):
                    score += 3
            if score > 0:
                if " vs " in t_low or " v " in t_low or " contro " in t_low:
                    score += 4
                sp_name = (m.get("sport_name") or "").lower()
                if any(s in sp_name for s in ("calcio", "basket", "football", "tennis")):
                    score += 2
                scored.append((score, m))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best = dict(scored[0][1])
            top_t = best['title'].replace(" vs ", " contro ")
            voice = f"Magia ragazzi! Vi ho lasciato il link della partita in chat per {top_t}! Date un'occhiata!"
            pal_u = best.get("portal_url") or MADPLAY_FOOTBALL_PAGE
            return {
                "found": True,
                "type": "single_match",
                "query": query,
                "best_match": best,
                "all_matches": [m for _, m in scored],
                "voice_summary": voice,
                "palinsesto_url": pal_u,
                "portal_url": pal_u,
            }

        # 3. Nessuna corrispondenza: fallback elegante con i match in evidenza stasera
        tonight_fallback = await self.get_tonight_matches()
        return {
            "found": False,
            "type": "not_found",
            "query": query,
            "message": f"Non ho trovato nessuna partita per *\"{clean_q or query}\"* in questo momento.",
            "featured": tonight_fallback.get("matches", [])[:5],
            "voice_summary": f"Non ho trovato partite per {clean_q or query} al momento ragazzi, vi ho mostrato il palinsesto degli eventi attivi stasera in chat! Date un'occhiata!",
            "palinsesto_url": DEFAULT_PALINSESTO_URL,
            "portal_url": DEFAULT_PALINSESTO_URL,
        }

    # =========================================================================
    # 6. COSTRUZIONE DISCORD EMBED & VIEW INTERATTIVA CON LINK PALINSESTO
    # =========================================================================
    def build_sports_embed(self, result: Dict[str, Any]) -> Tuple[discord.Embed, discord.ui.View, str]:
        """Costruisce un Discord Embed completo, una View con pulsanti e la frase vocale da recitare."""
        res_type = result.get("type", "schedule")
        voice_text = result.get("voice_summary") or "Ragazzi, vi ho scritto gli eventi sportivi in chat! Date un'occhiata! Magia!"

        # A. Motorsport (F1 / MotoGP)
        if res_type == "motorsport":
            sub = result.get("subtype", "f1")
            data = result.get("data", {})
            title_prefix = "🏎️ Formula 1" if sub == "f1" else "🏍️ MotoGP"
            embed = discord.Embed(
                title=f"{title_prefix} — {data.get('name')}",
                description=f"📍 **Circuito**: `{data.get('circuit')}`\n📅 **Palinsesto & Programmazione Ufficiale**",
                color=discord.Color.red(),
            )
            if data.get("sessions"):
                embed.add_field(name="⏰ Orari e Sessioni Ufficiali (CET)", value="\n".join(data["sessions"]), inline=False)

            tv_info = data.get("tv_channels", "Sky Sport F1 / NOW • TV8 (differita/diretta in chiaro)" if sub == "f1" else "Sky Sport MotoGP / NOW • TV8 (in chiaro)")
            embed.add_field(name="📺 Dove Vederlo in TV & Streaming", value=f"• **{tv_info}**\n• [Guida TV Sky Sport]({SKY_GUIDA_TV_URL})", inline=False)
            embed.set_footer(text="Franchino Sports • Motorsport World Series")

            view = discord.ui.View()
            portal_link = data.get("portal_url", "https://www.formula1.com" if sub == "f1" else "https://www.motogp.com")
            pal_link = data.get("palinsesto_url", portal_link)
            view.add_item(discord.ui.Button(label="Palinsesto Ufficiale", url=pal_link, style=discord.ButtonStyle.link, emoji="📅"))
            view.add_item(discord.ui.Button(label="Guida TV Sky & Orari", url=SKY_GUIDA_TV_URL, style=discord.ButtonStyle.link, emoji="📺"))
            motori_diretta = "https://www.diretta.it/motori/formula-1/" if sub == "f1" else "https://www.diretta.it/motori/motogp/"
            view.add_item(discord.ui.Button(label="Risultati Live Diretta.it", url=motori_diretta, style=discord.ButtonStyle.link, emoji="⚡"))
            return embed, view, voice_text

        # B. Match singolo trovato
        if res_type == "single_match" or (result.get("found") and "best_match" in result and res_type != "schedule"):
            m = result["best_match"]
            embed = discord.Embed(
                title=f"🏆 {m['title']}",
                description="Palinsesto ufficiale, orari e programmazione TV / streaming.",
                color=discord.Color.green() if m.get("is_live") else discord.Color.blue(),
            )
            embed.add_field(name="⚽ Disciplina / Torneo", value=m.get("league") or m.get("sport_name") or m.get("sport", "Sport"), inline=True)
            embed.add_field(name="⏰ Orario / Stato", value=f"`{m['time']}`", inline=True)
            if m.get("score"):
                embed.add_field(name="📊 Risultato Live", value=f"**{m['score']}**", inline=True)

            tv_info = m.get("tv_channels") or "Sky Sport / DAZN / NOW"
            embed.add_field(name="📺 Trasmissione TV Ufficiale", value=f"**{tv_info}**", inline=False)

            match_url = m.get("url") or m.get("stream_url") or MADPLAY_FOOTBALL_PAGE
            palinsesto_url = m.get("palinsesto_url") or MADPLAY_FOOTBALL_PAGE

            links_desc = (
                f"• [📺 **Guarda la Partita su MadPlay77**]({match_url})\n"
                f"• [🌐 Palinsesto Completo MadPlay77]({palinsesto_url})\n"
                f"• [📺 Guida TV Sky Sport & Canali]({SKY_GUIDA_TV_URL})\n"
                f"• [⚡ Diretta.it - Risultati in Tempo Reale]({DEFAULT_PALINSESTO_URL})"
            )
            embed.add_field(name="🔗 Link Partita & Palinsesto", value=links_desc, inline=False)
            embed.set_footer(text="Franchino Sports • Palinsesto MadPlay77")

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Guarda la Partita", url=match_url, style=discord.ButtonStyle.link, emoji="📺"))
            view.add_item(discord.ui.Button(label="Palinsesto MadPlay77", url=palinsesto_url, style=discord.ButtonStyle.link, emoji="🌐"))
            view.add_item(discord.ui.Button(label="Guida TV Sky Sport", url=SKY_GUIDA_TV_URL, style=discord.ButtonStyle.link, emoji="📺"))
            return embed, view, voice_text

        # C. Palinsesto Incontri di Oggi / Stasera
        if res_type == "schedule" and result.get("matches"):
            matches = result["matches"]
            req_league = result.get("league_name")
            req_sport = result.get("sport_name")
            is_upcoming = result.get("is_upcoming_round", False)

            if req_league:
                embed_title = f"📅 {req_league} — Prossimo Turno in Programma" if is_upcoming else f"🏆 {req_league} — Partite di Oggi & Stasera"
            elif req_sport:
                embed_title = f"🏆 {req_sport} — Palinsesto & Incontri di Stasera"
            else:
                embed_title = "🏆 Palinsesto Sportivo di Stasera & Grandi Match"

            embed_desc = "Ecco il quadro completo delle sfide, gli orari e i link delle partite per seguirle su MadPlay77!"

            embed = discord.Embed(
                title=embed_title,
                description=embed_desc,
                color=discord.Color.gold(),
            )

            alt_note = result.get("alternative_note")
            if alt_note:
                embed.add_field(name="ℹ️ Info Palinsesto", value=alt_note, inline=False)

            # Raggruppa i match per disciplina / competizione
            grouped: Dict[str, List[str]] = {}
            for m in matches[:14]:
                grp_name = f"{m.get('sport_emoji', '🏆')} {m.get('league', m.get('sport', 'Sport'))}"
                time_disp = f"`{m['time']}`"
                if m.get("score"):
                    time_disp += f" (**{m['score']}**)"

                target_link = m.get("url") or m.get("stream_url") or MADPLAY_FOOTBALL_PAGE
                link_md = f"[{m['title']}]({target_link})"

                line = f"• {time_disp} — {link_md}"
                grouped.setdefault(grp_name, []).append(line)

            for grp, lines in list(grouped.items())[:6]:
                embed.add_field(name=grp, value="\n".join(lines[:4]), inline=False)

            footer_txt = f"Franchino Sports • {len(matches)} incontri tracciati nel palinsesto"
            if is_upcoming:
                footer_txt += " (prossimo turno)"
            embed.set_footer(text=footer_txt)

            view = discord.ui.View()
            # Pulsanti diretti per le prime 2 partite presenti su MadPlay77
            stream_count = 0
            for m in matches:
                m_url = m.get("url") or m.get("stream_url")
                if m_url and "madplay77.ru" in m_url and stream_count < 2:
                    short_title = m["title"]
                    if len(short_title) > 28:
                        short_title = short_title[:25] + "..."
                    view.add_item(discord.ui.Button(label=f"Partita: {short_title}", url=m_url, style=discord.ButtonStyle.link, emoji="📺"))
                    stream_count += 1

            palinsesto_target = result.get("palinsesto_url") or MADPLAY_FOOTBALL_PAGE
            view.add_item(discord.ui.Button(label="Palinsesto MadPlay77", url=palinsesto_target, style=discord.ButtonStyle.link, emoji="🌐"))
            view.add_item(discord.ui.Button(label="Guida TV Sky Sport", url=SKY_GUIDA_TV_URL, style=discord.ButtonStyle.link, emoji="📺"))
            return embed, view, voice_text

        # D. Not Found / Fallback
        embed = discord.Embed(
            title="⚠️ Nessuna Partita Trovata",
            description=result.get("message", "Nessun evento sportivo trovato per questa ricerca."),
            color=discord.Color.orange(),
        )
        featured = result.get("featured", [])
        if featured:
            lines = [f"• **{f['title']}** ({f.get('sport_name') or f.get('league', 'Sport')}) - `{f['time']}` → [Guarda la Partita]({f.get('url', MADPLAY_FOOTBALL_PAGE)})" for f in featured[:5]]
            embed.add_field(name="🔥 Eventi Principali nel Palinsesto di Oggi:", value="\n".join(lines), inline=False)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Palinsesto MadPlay77", url=MADPLAY_FOOTBALL_PAGE, style=discord.ButtonStyle.link, emoji="🌐"))
        view.add_item(discord.ui.Button(label="Guida TV Sky Sport", url=SKY_GUIDA_TV_URL, style=discord.ButtonStyle.link, emoji="📺"))
        return embed, view, voice_text

    # =========================================================================
    # 7. ESTRAZIONE STREAMING LIVE HLS & TRASMISSIONE VOCALE AUDIO
    # =========================================================================
    async def get_match_stream_info(self, match_id: Any, sport_type: int = 1) -> Dict[str, Any]:
        """
        Interroga le API di MadPlay77 (/api/common/bs, /api/match/detail, /api/stream/detail)
        per estrarre il flusso live HLS (.m3u8), i canali TV, il token di sessione autenticato
        e l'URL diretto del player web.
        """
        try:
            st_int = int(sport_type)
        except (ValueError, TypeError):
            st_int = 1

        headers = {
            "Referer": "https://web01.madplay77.ru/it",
            "Origin": "https://web01.madplay77.ru",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

        token = "7011c2a84af83d5d939f3b5d8a8768eb"
        channel_name = "MadPlay77 Live"
        stream_id = None
        site_type = None
        stream_url = None
        rb_session = None

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.5)) as session:
                # 1. Recupero firma bs per autorizzare /api/match/detail (codice 102)
                bs_url = f"https://apis-data-defra10.tcpwe138stya.ru/api/common/bs?code=102&matchId={match_id}&sportType={st_int}&stream=true"
                async with session.get(bs_url, headers=headers) as r:
                    if r.status == 200:
                        buf = await r.read()
                        proto = _parse_protobuf_wire(buf)
                        if 10 in proto and proto[10]:
                            f10 = _parse_protobuf_wire(proto[10][0])
                            for item in f10.get(1, []):
                                sub = _parse_protobuf_wire(item)
                                if 2 in sub and sub[2]:
                                    token = sub[2][0].decode("utf-8", errors="ignore")

                # 2. Richiesta dettaglio match e lista canali di trasmissione
                det_url = f"https://apis-data-defra10.tcpwe138stya.ru/sfver{token}/api/match/detail?language=8&matchId={match_id}&sportType={st_int}&stream=true"
                async with session.get(det_url, headers=headers) as r:
                    if r.status == 200:
                        buf = await r.read()
                        proto = _parse_protobuf_wire(buf)
                        f10 = _parse_protobuf_wire(proto.get(10, [b""])[0]) if 10 in proto else {}
                        stream_items = f10.get(2, [])
                        if stream_items:
                            sp = _parse_protobuf_wire(stream_items[0])
                            stream_id = sp.get(1, [None])[0]
                            ch_bytes = sp.get(3, [b""])[0]
                            if ch_bytes:
                                channel_name = ch_bytes.decode("utf-8", errors="ignore")
                            site_type = sp.get(8, [None])[0]

                # 3. Richiesta URL del flusso HLS se stream_id è presente
                if stream_id is not None:
                    surl = f"https://apis-data-defra10.tcpwe138stya.ru/api/stream/detail?streamId={stream_id}&matchId={match_id}&sportType={st_int}&continent=EU&country=IT&digit=seth"
                    if site_type:
                        surl += f"&siteType={site_type}"
                    async with session.get(surl, headers=headers) as sr:
                        rb_session = sr.headers.get("rb-session")
                        if sr.status == 200:
                            sbuf = await sr.read()
                            sproto = _parse_protobuf_wire(sbuf)
                            if 10 in sproto and sproto[10]:
                                sf10 = _parse_protobuf_wire(sproto[10][0])
                                raw_stream_url = sf10.get(1, [b""])[0].decode("utf-8", errors="ignore")
                                if raw_stream_url:
                                    dec_url = _decode_stream_url(raw_stream_url)
                                    stream_url = _sign_stream_url(dec_url, rb_session)
        except Exception as e:
            logger.debug(f"Errore recupero dettagli stream match {match_id}: {e}")

        iframe_url = _build_madplay_iframe_url(match_id, st_int)
        portal_url = _build_madplay_match_url(match_id, st_int)

        return {
            "match_id": match_id,
            "sport_type": st_int,
            "channel_name": channel_name,
            "stream_id": stream_id,
            "stream_url": stream_url,
            "direct_hls_available": bool(stream_url),
            "rb_session": rb_session,
            "iframe_player_url": iframe_url,
            "match_page_url": portal_url,
        }

    async def resolve_match_for_streaming(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Cerca una partita in base alla query dell'utente (squadra, evento, sport)
        e ne estrae i metadati completi con flusso streaming audio/video.
        """
        clean_q = re.sub(
            r"^(?:(?:(?:hey+|ehi+|hei+|ey+|ei+)\s+)?(?:franchino\s+)?)?"
            r"(?:puoi\s+)?(?:per\s+favore\s+)?"
            r"(?:trasmetti|streamma|metti|fai\s+partire|ascolta|fammi\s+ascoltare|riproduci|avvia|collega|stream|streaming)?\s*"
            r"(?:(?:la|il|lo|l['’]|i|gli|le)\s+)?"
            r"(?:partita|telecronaca|streaming|diretta|audio|match|palinsesto)?\s*"
            r"(?:(?:di|del|della|dei|degli|dello|d['’])\s+)?",
            "",
            query,
            flags=re.IGNORECASE
        )
        clean_q = re.sub(r"\b(stream|streamma|telecronaca|trasmetti|audio|ascolta)\b", "", clean_q, flags=re.IGNORECASE).strip(" ,.!?:;-")

        target_q = clean_q or query or "live"
        search_res = await self.search_event(target_q)
        match = search_res.get("best_match")

        if not match:
            # Prova prima dai match live
            live_matches = await self.get_live_matches()
            if live_matches:
                if target_q.lower() in ("live", "stasera", "oggi", "partita", "tutte"):
                    match = live_matches[0]
                else:
                    q_tokens = [t for t in target_q.lower().split() if len(t) >= 2]
                    for lm in live_matches:
                        lm_title = lm.get("title", "").lower()
                        lm_league = lm.get("league", "").lower()
                        if target_q.lower() in lm_title or target_q.lower() in lm_league:
                            match = lm
                            break
                        if q_tokens and all(tok in lm_title or tok in lm_league for tok in q_tokens):
                            match = lm
                            break

        if not match:
            return None

        # Arricchisci con le informazioni di streaming avanzate
        stream_meta = await self.get_match_stream_info(match["id"], match.get("sport_type", 1))
        match_copy = dict(match)
        s_url = stream_meta.get("stream_url")
        if not s_url or s_url.endswith(".html"):
            cand_url = match_copy.get("stream_url")
            s_url = cand_url if (cand_url and not cand_url.endswith(".html")) else None

        has_direct = bool(s_url) and not s_url.endswith(".html")
        match_copy.update({
            "channel_name": stream_meta.get("channel_name") or match_copy.get("tv_channels", "MadPlay77 Live"),
            "stream_url": s_url,
            "direct_hls_available": has_direct,
            "iframe_player_url": stream_meta.get("iframe_player_url"),
            "web_url": match_copy.get("url") or stream_meta.get("match_page_url"),
            "match_page_url": stream_meta.get("match_page_url") or match_copy.get("url"),
        })
        return match_copy

    def build_match_streaming_embed(
        self,
        match: Dict[str, Any],
        player: Optional[Any] = None,
        requester: Optional[Union[discord.Member, discord.User, str]] = None,
    ) -> Tuple[discord.Embed, discord.ui.View, str]:
        """
        Costruisce l'embed Discord ricco con statistiche live, canale telecronaca,
        comandi del player vocale e link web per aprire il video su MadPlay77.
        """
        from views import MatchStreamingView

        title = match.get("title", "Partita")
        league = match.get("league") or match.get("sport_name") or "🏆 Live Sport"
        time_str = match.get("time", "LIVE")
        score = match.get("score") or "0 - 0"
        channel = match.get("channel_name") or match.get("tv_channels") or "MadPlay77 Live"
        web_link = match.get("match_page_url") or match.get("url") or match.get("web_url") or MADPLAY_FOOTBALL_PAGE
        iframe_link = match.get("iframe_player_url")
        s_url = match.get("stream_url")
        direct_hls = match.get("direct_hls_available", False) or (bool(s_url) and not str(s_url).endswith(".html"))

        clean_title = title.replace(" vs ", " contro ")
        if direct_hls:
            embed_title = f"🔴 TELECRONACA LIVE: {title}"
            embed_desc = (
                f"🎙️ **Audio della partita collegato in diretta nel canale vocale!**\n"
                f"Ascolta la telecronaca con tutti gli amici nella stanza o apri la diretta video dal link qui sotto!"
            )
            embed_color = discord.Color.red()
            voice_text = f"Si va in scena ragazzi! Vi ho collegato la telecronaca di {clean_title} direttamente in console! Magia!"
        else:
            embed_title = f"⚽ PARTITA / STREAMING: {title}"
            embed_desc = (
                f"📺 **Flusso video e dettagli match disponibili su MadPlay77!**\n"
                f"Il flusso audio diretto non è ancora attivo sul server (partita non iniziata o segnale in attesa). "
                f"Puoi aprire la diretta video tramite il player web cliccando sui link qui sotto!"
            )
            embed_color = discord.Color.gold()
            voice_text = f"Ragazzi, vi ho trovato {clean_title}! Il flusso video è pronto sul player web di MadPlay77, cliccate sul link per seguirla!"

        embed = discord.Embed(
            title=embed_title,
            description=embed_desc,
            color=embed_color,
        )

        embed.add_field(name="🏆 Competizione", value=f"`{league}`", inline=True)
        embed.add_field(name="⏰ Stato / Orario", value=f"`{time_str}`", inline=True)
        embed.add_field(name="📊 Risultato Live", value=f"**{score}**", inline=True)
        embed.add_field(name="📺 Canale Telecronaca", value=f"**{channel}**", inline=True)

        req_name = "Anonimo"
        if requester:
            if hasattr(requester, "display_name"):
                req_name = requester.display_name
            elif hasattr(requester, "mention"):
                req_name = requester.mention
            else:
                req_name = str(requester)
        embed.add_field(name="👤 Richiesto da", value=f"`{req_name}`", inline=True)

        if direct_hls:
            voice_tips = (
                "• *\"Franchino pausa\"* / *\"riprendi\"* — Metti in pausa o riprendi\n"
                "• *\"Franchino alza\"* / *\"abbassa\"* — Regola il volume telecronaca\n"
                "• *\"Franchino stop\"* — Interrompi la trasmissione"
            )
            embed.add_field(name="🎧 Controlli Vocali Console", value=voice_tips, inline=False)

        video_links = f"• [📺 **Guarda il Video in Diretta su MadPlay77**]({web_link})"
        if iframe_link:
            video_links += f"\n• [🌐 Player Web Fullscreen]({iframe_link})"
        embed.add_field(name="🔗 Guarda il Video sul Sito", value=video_links, inline=False)

        if match.get("league_logo"):
            embed.set_thumbnail(url=match["league_logo"])

        embed.set_footer(
            text="Franchino DJ Sports • MadPlay77 Live Audio Stream",
            icon_url="https://cdn-icons-png.flaticon.com/512/3845/3845868.png",
        )

        view = MatchStreamingView(player=player, match=match)
        return embed, view, voice_text


# Istanza singleton globale
sports_scraper = SportsScraper()
