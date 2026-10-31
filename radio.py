"""
radio.py - Radio italiane in streaming per Franchino
====================================================
Quando qualcuno dice "Hey Franchino metti m2o", Franchino non deve cercare un
brano su YouTube: deve trovare il flusso live dell'emittente e metterlo in onda.

Architettura a tre livelli, dal più veloce al più robusto:

1. CATALOGO con URL verificati (latenza ~0 ms dopo il primo probe): le radio
   italiane più note, con alias per il riconoscimento vocale.
2. PROBE: ogni URL viene verificato con una GET reale prima di essere dato in
   pasto a ffmpeg (uno stream morto non deve far fallire la riproduzione).
3. RADIO-BROWSER API (https://api.radio-browser.info, gratuita e senza chiave):
   se l'URL del catalogo è cambiato o la radio non è in catalogo ("metti radio
   xxx"), il link streaming viene cercato lì e messo in cache su disco per 24h.

Le playlist .m3u/.pls vengono espanse al primo URL http reale; gli .m3u8 (HLS)
restano intatti perché sono flussi veri e propri, non elenchi.
"""

import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("Franchino.Radio")

USER_AGENT = "FranchinoBot/2.0 (Radio Streaming; +https://github.com/davide-dari/franchino-bot)"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_PATH = os.path.join(DATA_DIR, "radio_streams.json")
CACHE_TTL_SEC = 24 * 3600
PROBE_TIMEOUT = 6.0

# Mirror dell'API: si prova in ordine finché uno risponde
API_HOSTS = (
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
    "https://fr1.api.radio-browser.info",
)

# Playlist che contengono altri URL (da espandere) — NON gli .m3u8 che sono HLS
_PLAYLIST_RE = re.compile(r"\.(?:m3u|pls|asx)(?:\?|$)", re.IGNORECASE)
_URL_IN_TEXT_RE = re.compile(r"^(?:https?://\S+)$", re.IGNORECASE)


@dataclass(frozen=True)
class RadioStation:
    """Emittente radiofonica conosciuta dal catalogo."""

    key: str
    name: str
    aliases: Tuple[str, ...]
    homepage: str
    genre: str
    emoji: str
    hints: Tuple[str, ...] = field(default_factory=tuple)
    search_name: str = ""
    accent_color: int = 0xFF5500

    @property
    def search_term(self) -> str:
        return self.search_name or self.name


# =============================================================================
# Catalogo (URL verificati sull'API Radio-Browser: se cambiano, il probe + la
# ricerca live prendono il sopravvento senza toccare il codice)
# =============================================================================
RADIO_CATALOG: Tuple[RadioStation, ...] = (
    RadioStation(
        key="m2o", name="Radio m2o",
        aliases=("m2o", "m due o", "emme due o", "m2 o", "radio m2o", "m2zero"),
        homepage="https://www.m2o.it/", genre="Dance / Electronic", emoji="🎧",
        hints=("https://4c4b867c89244861ac216426883d1ad0.msvdn.net/radiom2o/radiom2o/master_ma.m3u8",),
    ),
    RadioStation(
        key="deejay", name="Radio Deejay",
        aliases=("radio deejay", "deejay", "di jay", "dee jay", "dejay"),
        homepage="https://www.deejay.it/", genre="Pop / Dance", emoji="🎵",
        hints=(
            "http://streamcdnb1-4c4b867c89244861ac216426883d1ad0.msvdn.net/radiodeejay/radiodeejay/play1.m3u8",
            "https://4c4b867c89244861ac216426883d1ad0.msvdn.net/radiodeejay/radiodeejay/master_ma.m3u8",
        ),
    ),
    RadioStation(
        key="rtl1025", name="RTL 102.5",
        aliases=("rtl 102.5", "rtl 1025", "rtl", "erre ti elle", "rtl 102"),
        homepage="https://www.rtl.it/", genre="Pop / Hits", emoji="📻",
        hints=("https://dd782ed59e2a4e86aabf6fc508674b59.msvdn.net/live/S97044836/tbbP8T1ZRPBL/playlist_audio.m3u8",),
        search_name="RTL 102.5",
    ),
    RadioStation(
        key="radioitalia", name="Radio Italia",
        aliases=("radio italia", "radioitalia", "radio italia solo musica italiana", "italia smi"),
        homepage="https://www.radioitalia.it/", genre="Solo musica italiana", emoji="🇮🇹",
        hints=("https://radioitaliasmi.akamaized.net/hls/live/2093120/RISMI/master.m3u8",),
        accent_color=0x00A0E0,
    ),
    RadioStation(
        key="radio105", name="Radio 105",
        aliases=("radio 105", "105", "cento cinque", "radio centocinque", "one o five"),
        homepage="https://www.105.net/", genre="Pop / Urban", emoji="🔥",
        hints=("http://icecast.unitedradio.it/Radio105.mp3",),
        search_name="Radio 105 Network",
    ),
    RadioStation(
        key="virgin", name="Virgin Radio",
        aliases=("virgin radio", "virgin", "vergine radio"),
        homepage="https://www.virginradio.it/", genre="Rock", emoji="🎸",
        hints=("http://icecast.unitedradio.it/Virgin.mp3",),
        search_name="Virgin Radio Italia", accent_color=0xE4002B,
    ),
    RadioStation(
        key="capital", name="Radio Capital",
        aliases=("radio capital", "capital", "capitol"),
        homepage="https://www.capital.it/", genre="Classici / Soul", emoji="🎷",
        hints=("https://4c4b867c89244861ac216426883d1ad0.msvdn.net/radiocapital/radiocapital/master_ma.m3u8",),
    ),
    RadioStation(
        key="kisskiss", name="Radio Kiss Kiss",
        aliases=("radio kiss kiss", "kiss kiss", "kisskiss", "kiss"),
        homepage="https://www.radiokisskiss.it/", genre="Pop / Dance", emoji="💋",
        hints=("http://wma08.fluidstream.net:3612/;", "https://streamingv2.shoutcast.com/radio-kiss-kiss"),
        search_name="Radio Kiss Kiss Napoli", accent_color=0xE6007E,
    ),
    RadioStation(
        key="rds", name="RDS",
        aliases=("rds", "radio dimensione suono", "erre di esse", "dimensione suono"),
        homepage="https://www.rds.it/", genre="Pop / Hits", emoji="🎶",
        hints=("https://stream.rds.radio/audio/rds.stream_aac/chunklist.m3u8",),
        accent_color=0x0072CE,
    ),
    RadioStation(
        key="rmc", name="Radio Monte Carlo",
        aliases=("radio monte carlo", "monte carlo", "rmc", "erre emme ci"),
        homepage="https://www.radiomontecarlo.net/", genre="Chill / Lounge", emoji="🌴",
        hints=("http://icecast.unitedradio.it/RMC.mp3",),
        search_name="RMC Radio Monte Carlo",
    ),
    RadioStation(
        key="r101", name="R101",
        aliases=("r101", "r 101", "erre cento uno", "radio 101"),
        homepage="https://www.r101.it/", genre="Pop / Rock", emoji="📡",
        hints=("http://icecast.unitedradio.it/r101",),
        search_name="R101 FM",
    ),
    RadioStation(
        key="zeta", name="Radio Zeta",
        aliases=("radio zeta", "zeta", "radio dzeta"),
        homepage="https://www.radiozeta.it/", genre="Pop italiano", emoji="🌟",
        hints=("https://streamingv2.shoutcast.com/radio-zeta_48.aac",),
    ),
    RadioStation(
        key="discoradio", name="Discoradio",
        aliases=("discoradio", "disco radio", "discoradio italia"),
        homepage="https://www.discoradio.it/", genre="Dance 90 / 2000", emoji="🪩",
        hints=("http://icstream.rds.radio/disco",),
    ),
    RadioStation(
        key="norba", name="Radio Norba",
        aliases=("radio norba", "norba"),
        homepage="https://www.radionorba.it/", genre="Pop italiano", emoji="☀️",
        hints=("http://onair20.xdevel.com:8348/;",),
    ),
    RadioStation(
        key="subasio", name="Radio Subasio",
        aliases=("radio subasio", "subasio"),
        homepage="https://www.radiosubasio.it/", genre="Pop / Love", emoji="💙",
        hints=("http://icy.unitedradio.it/Subasio.mp3",),
    ),
    RadioStation(
        key="radio24", name="Radio 24",
        aliases=("radio 24", "radio ventiquattro", "il sole 24 ore", "radio24"),
        homepage="https://www.radio24.ilsole24ore.com/", genre="News / Talk", emoji="🗞️",
        hints=("http://shoutcast2.radio24.it:8000/;",),
        search_name="Radio 24 il sole 24 ore", accent_color=0xF5A623,
    ),
    RadioStation(
        key="rairadio1", name="Rai Radio 1",
        aliases=("rai radio 1", "radio 1 rai", "rai uno radio", "primo canale radio"),
        homepage="https://www.raiplaysound.it/radio1", genre="News / Talk", emoji="📰",
        search_name="Rai Radio 1", accent_color=0x0A4EA2,
    ),
    RadioStation(
        key="rairadio2", name="Rai Radio 2",
        aliases=("rai radio 2", "radio 2 rai", "rai due radio"),
        homepage="https://www.raiplaysound.it/radio2", genre="Musica / Intrattenimento", emoji="🎙️",
        search_name="Rai Radio 2", accent_color=0x0A4EA2,
    ),
    RadioStation(
        key="rairadio3", name="Rai Radio 3",
        aliases=("rai radio 3", "radio 3 rai", "rai tre radio"),
        homepage="https://www.raiplaysound.it/radio3", genre="Classica / Cultura", emoji="🎻",
        search_name="Rai Radio 3", accent_color=0x0A4EA2,
    ),
    RadioStation(
        key="radiomaria", name="Radio Maria",
        aliases=("radio maria", "maria"),
        homepage="https://www.radiomaria.it/", genre="Religioso", emoji="🙏",
        search_name="Radio Maria", accent_color=0x2E86C1,
    ),
)

CATALOG_BY_KEY: Dict[str, RadioStation] = {r.key: r for r in RADIO_CATALOG}

# Espressioni che indicano "voglio una radio" senza dire quale
_GENERIC_RADIO_QUERIES = (
    "la radio", "una radio", "delle radio", "che radio ci sono", "quali radio",
    "lista radio", "le radio", "radio italiane", "radio italiane",
)

# "metti radio deejay" / "metti la radio rtl" → nome da cercare live
_NAMED_RADIO_RE = re.compile(
    r"(?:la\s+|una\s+)?radio\s+(?P<name>[a-z0-9][a-z0-9 .'’&-]{1,40})$",
    re.IGNORECASE,
)

# Verbi/comandi che possono precedere il nome dell'emittente
_RADIO_PREFIX_RE = re.compile(
    r"^(?:hey\s+franchino\s+|franchino\s+|per\s+favore\s+)?"
    r"(?:metti|mette|mettimi|mettimi su|metti su|riproduci|suona|suonami|play|"
    r"fammi sentire|fammi ascoltare|avvia|cerca|cercami|fai partire|voglio|"
    r"ascoltiamo|ascolta|manda|accendi)\s+",
    re.IGNORECASE,
)


@dataclass
class RadioStream:
    """Flusso audio risolto e pronto per ffmpeg."""

    url: str
    station: str
    codec: str = ""
    bitrate: int = 0
    hls: bool = False
    homepage: str = ""
    favicon: str = ""
    genre: str = ""

    @property
    def quality(self) -> str:
        parti = [p for p in (self.codec, f"{self.bitrate} kbps" if self.bitrate else "") if p]
        return " • ".join(parti) if parti else "Streaming"


def normalize(text: str) -> str:
    """Normalizza il parlato/trascrizione per il confronto con gli alias."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("&", " e ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class _RadioCache:
    """Cache su disco degli URL risolti (TTL 24h) per evitare lookup ripetuti."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            if os.path.isfile(CACHE_PATH):
                with open(CACHE_PATH, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._data = data
        except Exception as e:
            logger.debug(f"Cache radio non leggibile: {e}")

    def get(self, key: str) -> Optional[Dict]:
        self._load()
        entry = self._data.get(key)
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > CACHE_TTL_SEC:
            self._data.pop(key, None)
            return None
        return entry

    def put(self, key: str, entry: Dict) -> None:
        self._load()
        entry = dict(entry)
        entry["ts"] = time.time()
        self._data[key] = entry
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False)
            os.replace(tmp, CACHE_PATH)
        except Exception as e:
            logger.debug(f"Cache radio non scrivibile: {e}")

    def drop(self, key: str) -> None:
        self._load()
        if self._data.pop(key, None) is not None:
            try:
                with open(CACHE_PATH, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False)
            except Exception:
                pass


_cache = _RadioCache()
_session: Optional[aiohttp.ClientSession] = None
_session_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_session() -> aiohttp.ClientSession:
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=6, ttl_dns_cache=300),
            headers={"User-Agent": USER_AGENT},
        )
        _session_loop = loop
    return _session


# =============================================================================
# Riconoscimento dell'emittente
# =============================================================================
def find_radio(text: str) -> Optional[RadioStation]:
    """Riconosce una radio del catalogo dentro la frase dell'utente.

    Vince l'alias più lungo: "radio italia anni 60" non deve diventare "radio italia"
    se in futuro si aggiungerà quella variante, e "radio 105" non deve collidere con "105".
    """
    norm = normalize(text)
    if not norm:
        return None

    best: Optional[Tuple[int, RadioStation]] = None
    for station in RADIO_CATALOG:
        for alias in station.aliases:
            a = normalize(alias)
            if not a:
                continue
            # match su confini di parola (evita "kiss" dentro "kissing", ecc.)
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", norm):
                if best is None or len(a) > best[0]:
                    best = (len(a), station)
    return best[1] if best else None


def wants_generic_radio(text: str) -> bool:
    """True se l'utente chiede genericamente 'una radio' senza indicarne una."""
    norm = normalize(text)
    if not norm:
        return False
    if find_radio(norm):
        return False
    return any(g in norm for g in (normalize(x) for x in _GENERIC_RADIO_QUERIES))


def extract_radio_name(text: str) -> Optional[str]:
    """Estrae il nome di una radio non in catalogo: 'metti radio deejay' → 'deejay'."""
    norm = normalize(text)
    if not norm:
        return None
    # Toglie l'eventuale verbo iniziale ("metti", "riproduci", ...)
    norm = _RADIO_PREFIX_RE.sub("", norm).strip()
    if not norm:
        return None
    m = _NAMED_RADIO_RE.search(norm)
    if not m:
        return None
    name = m.group("name").strip()
    # Scarta rumore: "radio accesa", "radio mia", frasi troppo corte
    if len(name) < 3 or name in ("mia", "accesa", "sua", "tua", "nostra", "qui"):
        return None
    return name


# =============================================================================
# Probe e risoluzione del flusso
# =============================================================================
async def _probe(url: str) -> Optional[str]:
    """Verifica che l'URL risponda audio. Ritorna l'URL effettivo da usare con ffmpeg.

    - .m3u8 (HLS): si tiene l'URL così com'è (ffmpeg segue il manifest).
    - .m3u/.pls (playlist): viene espanso al primo URL http contenuto.
    """
    if not url:
        return None
    try:
        session = _get_session()
        timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT, sock_connect=4.0)
        async with session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"Icy-MetaData": "1", "Connection": "close"},
        ) as resp:
            if resp.status not in (200, 206):
                logger.debug(f"Probe radio {url}: HTTP {resp.status}")
                return None
            final_url = str(resp.url)

            # Un .m3u8 è un flusso HLS: si passa direttamente a ffmpeg
            if final_url.lower().split("?")[0].endswith(".m3u8") or url.lower().split("?")[0].endswith(".m3u8"):
                await resp.content.read(512)
                return final_url

            chunk = await resp.content.read(4096)
            if not chunk:
                return None

            text = chunk.decode("utf-8", errors="ignore")
            if _PLAYLIST_RE.search(url) or "listen.pls" in url.lower() or text.lstrip().startswith(("[playlist]", "#EXTM3U")):
                candidate = None
                for line in text.splitlines():
                    line = line.strip()
                    if _URL_IN_TEXT_RE.match(line):
                        candidate = line
                        break
                    m = re.search(r"File\d*=(https?://\S+)", line, re.IGNORECASE)
                    if m:
                        candidate = m.group(1)
                        break
                if candidate:
                    logger.info(f"🔗 Playlist radio espansa: {url} → {candidate}")
                    return candidate
                return None

            return final_url
    except asyncio.TimeoutError:
        logger.debug(f"Probe radio timeout per {url}")
        return None
    except Exception as e:
        logger.debug(f"Probe radio fallito per {url}: {e}")
        return None


async def _api_search(name: str, country: str = "IT", limit: int = 5) -> List[Dict]:
    """Cerca emittenti sull'API pubblica Radio-Browser (senza chiave)."""
    from urllib.parse import quote_plus

    params = (
        f"/json/stations/search?name={quote_plus(name)}&countrycode={country}"
        f"&limit={limit}&order=votes&reverse=true&hidebroken=true"
    )
    session = _get_session()
    timeout = aiohttp.ClientTimeout(total=8.0, sock_connect=4.0)
    for host in API_HOSTS:
        try:
            async with session.get(host + params, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
                if isinstance(data, list) and data:
                    return data
                return []
        except Exception as e:
            logger.debug(f"Radio-Browser {host} non raggiungibile: {e}")
            continue
    return []


def _best_station_match(stations: List[Dict], wanted: str) -> Optional[Dict]:
    """Sceglie la stazione più affidabile: prima il match sul nome, poi i voti."""
    if not stations:
        return None
    wanted_norm = normalize(wanted)
    tokens = [t for t in wanted_norm.split() if len(t) > 2]

    def score(st: Dict) -> Tuple[int, int, int]:
        name = normalize(st.get("name", ""))
        match = 1 if wanted_norm and wanted_norm in name else 0
        if not match and tokens:
            match = 1 if all(t in name for t in tokens) else 0
        return (match, int(st.get("votes") or 0), 1 if st.get("lastcheckok") else 0)

    ordered = sorted(stations, key=score, reverse=True)
    for st in ordered:
        if st.get("url_resolved") or st.get("url"):
            return st
    return None


async def _resolve_from_api(key: str, name: str) -> Optional[Dict]:
    """Trova il flusso di un'emittente sull'API e verifica che sia riproducibile."""
    stations = await _api_search(name)
    best = _best_station_match(stations, name)
    if not best:
        return None

    candidates = [best.get("url_resolved"), best.get("url")]
    for cand in candidates:
        if not cand:
            continue
        working = await _probe(cand)
        if working:
            return {
                "url": working,
                "name": best.get("name") or name,
                "codec": best.get("codec") or "",
                "bitrate": int(best.get("bitrate") or 0),
                "hls": bool(best.get("hls")),
                "homepage": best.get("homepage") or "",
                "favicon": best.get("favicon") or "",
                "tags": (best.get("tags") or "").split(",")[:3],
            }
    return None


async def resolve_stream(
    station: Optional[RadioStation] = None,
    name: Optional[str] = None,
    force_refresh: bool = False,
) -> Optional[RadioStream]:
    """Risolve il flusso live di una radio (catalogo, poi API, poi cache)."""
    if station is not None:
        key = f"cat:{station.key}"
    elif name:
        key = f"name:{normalize(name)}"
    else:
        return None

    if not force_refresh:
        cached = _cache.get(key)
        if cached and cached.get("url"):
            return RadioStream(
                url=cached["url"],
                station=cached.get("name") or (station.name if station else name or "Radio"),
                codec=cached.get("codec", ""),
                bitrate=int(cached.get("bitrate") or 0),
                hls=bool(cached.get("hls")),
                homepage=cached.get("homepage") or (station.homepage if station else ""),
                favicon=cached.get("favicon", ""),
                genre=station.genre if station else "",
            )

    display_name = station.name if station else (name or "Radio")

    # 1. URL del catalogo (verificati) — i più veloci
    if station is not None:
        for hint in station.hints:
            working = await _probe(hint)
            if working:
                entry = {
                    "url": working, "name": station.name, "codec": "", "bitrate": 0,
                    "hls": working.lower().split("?")[0].endswith(".m3u8"),
                    "homepage": station.homepage, "favicon": "",
                }
                _cache.put(key, entry)
                logger.info(f"📻 Radio risolta dal catalogo: {station.name} → {working}")
                return RadioStream(
                    url=working, station=station.name, codec=entry["codec"],
                    bitrate=0, hls=entry["hls"], homepage=station.homepage, genre=station.genre,
                )

    # 2. Ricerca live sull'API Radio-Browser
    search_name = station.search_term if station else (name or "")
    found = await _resolve_from_api(key, search_name)
    if found:
        _cache.put(key, found)
        logger.info(f"📻 Radio risolta via API: {found['name']} → {found['url']}")
        return RadioStream(
            url=found["url"],
            station=found.get("name") or display_name,
            codec=found.get("codec", ""),
            bitrate=int(found.get("bitrate") or 0),
            hls=bool(found.get("hls")),
            homepage=found.get("homepage") or (station.homepage if station else ""),
            favicon=found.get("favicon", ""),
            genre=station.genre if station else ", ".join(found.get("tags") or []),
        )

    # 3. Un'ultima chance con il nome "pulito"
    if station is not None and search_name != station.name:
        found = await _resolve_from_api(key, station.name)
        if found:
            _cache.put(key, found)
            return RadioStream(
                url=found["url"], station=found.get("name") or display_name,
                codec=found.get("codec", ""), bitrate=int(found.get("bitrate") or 0),
                hls=bool(found.get("hls")),
                homepage=found.get("homepage") or station.homepage,
                favicon=found.get("favicon", ""), genre=station.genre,
            )

    logger.warning(f"📻 Nessun flusso riproducibile trovato per '{display_name}'")
    return None


def catalog_lines(max_items: int = 20) -> List[str]:
    """Elenco leggibile del catalogo (per embed/help)."""
    righe = []
    for st in RADIO_CATALOG[:max_items]:
        righe.append(f"{st.emoji} **{st.name}** — *{st.genre}*")
    return righe


def catalog_keys() -> List[str]:
    return [st.key for st in RADIO_CATALOG]


def invalidate(station_key: Optional[str] = None, name: Optional[str] = None) -> None:
    """Butta via la cache di una radio: usato quando lo stream smette di funzionare."""
    if station_key:
        _cache.drop(f"cat:{station_key}")
        logger.info(f"🧹 Cache radio invalidata per la chiave '{station_key}'")
    if name:
        _cache.drop(f"name:{normalize(name)}")
        logger.info(f"🧹 Cache radio invalidata per il nome '{name}'")
