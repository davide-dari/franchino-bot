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
from typing import Any, Dict, List, Optional, Tuple
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

# Configurazioni Leghe e Sport (ESPN open endpoints)
ESPN_LEAGUES: Dict[str, Dict[str, Any]] = {
    "serie_a": {
        "sport": "Calcio",
        "name": "Serie A",
        "emoji": "🇮🇹",
        "path": "sports/soccer/ita.1/scoreboard",
        "aliases": ["serie a", "campionato", "seriea", "italia", "scudetto", "calcio italia", "campionato italiano"],
    },
    "serie_b": {
        "sport": "Calcio",
        "name": "Serie B",
        "emoji": "🇮🇹",
        "path": "sports/soccer/ita.2/scoreboard",
        "aliases": ["serie b", "serieb", "bkt"],
    },
    "coppa_italia": {
        "sport": "Calcio",
        "name": "Coppa Italia",
        "emoji": "🏆",
        "path": "sports/soccer/ita.coppa_italia/scoreboard",
        "aliases": ["coppa italia", "coppa"],
    },
    "champions_league": {
        "sport": "Calcio",
        "name": "UEFA Champions League",
        "emoji": "⭐",
        "path": "sports/soccer/uefa.champions/scoreboard",
        "aliases": ["champions", "champions league", "ucl", "coppa campioni", "coppa dei campioni"],
    },
    "europa_league": {
        "sport": "Calcio",
        "name": "UEFA Europa League",
        "emoji": "🟠",
        "path": "sports/soccer/uefa.europa/scoreboard",
        "aliases": ["europa league", "uel"],
    },
    "conference_league": {
        "sport": "Calcio",
        "name": "UEFA Conference League",
        "emoji": "🟢",
        "path": "sports/soccer/uefa.europa.conf/scoreboard",
        "aliases": ["conference", "conference league", "uecl"],
    },
    "premier_league": {
        "sport": "Calcio",
        "name": "Premier League",
        "emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
        "path": "sports/soccer/eng.1/scoreboard",
        "aliases": ["premier", "premier league", "inghilterra", "epl"],
    },
    "la_liga": {
        "sport": "Calcio",
        "name": "La Liga",
        "emoji": "🇪🇸",
        "path": "sports/soccer/esp.1/scoreboard",
        "aliases": ["la liga", "liga", "spagna"],
    },
    "bundesliga": {
        "sport": "Calcio",
        "name": "Bundesliga",
        "emoji": "🇩🇪",
        "path": "sports/soccer/ger.1/scoreboard",
        "aliases": ["bundesliga", "germania"],
    },
    "ligue_1": {
        "sport": "Calcio",
        "name": "Ligue 1",
        "emoji": "🇫🇷",
        "path": "sports/soccer/fra.1/scoreboard",
        "aliases": ["ligue 1", "ligue1", "francia"],
    },
    "nba": {
        "sport": "Basket",
        "name": "NBA",
        "emoji": "🏀",
        "path": "sports/basketball/nba/scoreboard",
        "aliases": ["nba", "basket usa", "pallacanestro nba"],
    },
    "euroleague": {
        "sport": "Basket",
        "name": "Euroleague Basket",
        "emoji": "🏀",
        "path": "sports/basketball/euroleague/scoreboard",
        "aliases": ["eurolega", "euroleague", "euro league", "coppa campioni basket"],
    },
    "serie_a_basket": {
        "sport": "Basket",
        "name": "Serie A Basket (LBA)",
        "emoji": "🏀🇮🇹",
        "path": "sports/basketball/ita.1/scoreboard",
        "aliases": ["serie a basket", "basket serie a", "lba", "lega basket", "campionato basket", "basket italia"],
    },
    "tennis_atp": {
        "sport": "Tennis",
        "name": "Tennis ATP",
        "emoji": "🎾",
        "path": "sports/tennis/atp/scoreboard",
        "aliases": ["tennis atp", "atp", "tennis", "sinner", "alcaraz", "djokovic", "berrettini", "musetti", "cobolli"],
    },
    "tennis_wta": {
        "sport": "Tennis",
        "name": "Tennis WTA",
        "emoji": "🎾",
        "path": "sports/tennis/wta/scoreboard",
        "aliases": ["tennis wta", "wta", "tennis femminile", "paolini", "sabalenka", "swiatek", "gauff"],
    },
    "f1": {
        "sport": "Motori",
        "name": "Formula 1",
        "emoji": "🏎️",
        "path": "sports/racing/f1/scoreboard",
        "aliases": ["f1", "formula 1", "formula1", "gp f1", "ferrari", "gran premio", "leclerc", "verstappen", "hamilton", "motori"],
    },
}

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

                # Generazione parametro mdata in Base64 (player web01)
                mdata_str = f"{match_id}_{sport_type}"
                mdata_b64 = base64.b64encode(mdata_str.encode("utf-8")).decode("utf-8")
                stream_url = f"https://web01.madplay77.ru/it/seth/livestream?mdata={mdata_b64}"

                sport_label = SPORT_NAMES.get(sport_type, "🏆 Sport")

                matches.append({
                    "id": match_id,
                    "sport_type": sport_type,
                    "sport_name": sport_label,
                    "title": title,
                    "league": league or sport_label,
                    "league_logo": league_logo,
                    "time": time_str,
                    "datetime": match_dt,
                    "url": stream_url,
                    "portal_url": "https://web01.madplay77.ru/it",
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
                            upcoming_dates = [c[:10].replace("-", "") for c in cal if c[:10] >= today_iso][:3]
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

                        self._cache_league_events[cache_key] = (now, events)
        except Exception as e:
            logger.warning(f"Errore query scoreboard ESPN per {league_key}: {e}")

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
                    "url": "https://www.espn.com/tennis",
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
                "stream_url": None,  # Sarà arricchito da MadPlay77 se disponibile
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
                        }
                        self._cache_motogp = (now, res)
                        return res
        except Exception as e:
            logger.warning(f"Errore recupero MotoGP info: {e}")

        return {
            "name": "Campionato del Mondo MotoGP 2026",
            "circuit": "Circuito Internazionale",
            "portal_url": "https://www.motogp.com",
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
                already_present = any(
                    (m["home"].lower() in mp_title and m["away"].lower() in mp_title)
                    for m in all_matches if m.get("home") and m.get("away")
                )
                if not already_present:
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
                        "stream_url": mp["url"],
                    })
                else:
                    # Arricchisci il match ESPN col link diretto dello streaming
                    for m in all_matches:
                        if m.get("home") and m.get("away") and m["home"].lower() in mp_title and m["away"].lower() in mp_title:
                            m["stream_url"] = mp["url"]

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

        return {
            "found": len(all_matches) > 0,
            "matches": all_matches,
            "count": len(all_matches),
            "voice_summary": voice_summary,
            "alternative_note": alternative_note,
            "is_upcoming_round": is_upcoming_round,
            "league_name": matched_league_cfg.get("name") if matched_league_cfg else league_filter,
            "sport_name": sport_filter.title() if sport_filter else None,
            "portal_url": "https://web01.madplay77.ru/it",
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
            first_dt = matches[0].get("datetime")
            dt_s = first_dt.strftime("%d/%m ore %H:%M") if first_dt else "presto"
            league_n = filter_name or "quella lega"
            return (
                f"Oggi la {league_n} riposa ragazzi! Ma non ci fermiamo mai: lo spettacolo torna il {dt_s} "
                f"con {top_t}! Vi ho preparato tutto il prossimo turno in chat, andiamo a volare!"
            )

        if not matches:
            return (
                "Al momento non ci sono incontri in diretta ragazzi, ma la notte è giovane! "
                "Consultate il palinsesto in chat per i prossimi eventi!"
            )

        live_count = sum(1 for m in matches if m.get("is_live"))
        top_match = matches[0]["title"].replace(" vs ", " contro ")
        second_match = matches[1]["title"].replace(" vs ", " contro ") if len(matches) > 1 else ""

        if filter_name and any(f in filter_name.lower() for f in ("f1", "formula", "motogp", "motori", "ferrari")):
            return (
                f"Magia sui motori ragazzi! Pista infuocata e rombo che fa tremare la terra! "
                f"Tutti i dettagli del Gran Premio sono in chat, scaldate i motori!"
            )

        if filter_name and any(f in filter_name.lower() for f in ("eurolega", "euroleague")):
            return (
                f"Notte infuocata sul parquet europeo ragazzi! Grandi match di Eurolega tra cui {top_match}! "
                f"Canestri che fanno tremare il ferro, ecco tutti i link in chat! Si decolla!"
            )

        if filter_name and any(f in filter_name.lower() for f in ("nba", "basket usa")):
            return (
                f"Sul parquet americano stanotte si vola altissimo ragazzi! "
                f"Ecco i big match NBA pronti a regalarci canestri da favola! Chiudi gli occhi e goditi la notte!"
            )

        if filter_name and "champions" in filter_name.lower():
            return (
                f"La notte delle stelle ragazzi! La Champions League accende il cielo con match da brividi tra cui {top_match}! "
                f"In campo c'è il fuoco, ecco il palinsesto completo in chat! Magia!"
            )

        if live_count > 0:
            extra = f" e {second_match}" if second_match else ""
            return (
                f"Stasera in campo c'è il fuoco ragazzi! Abbiamo {live_count} match caldissimi in diretta "
                f"tra cui {top_match}{extra}! Vi ho lasciato tutti i link e gli orari in chat! Si decolla!"
            )

        extra = f" e {second_match}" if second_match else ""
        return (
            f"Stasera in campo c'è il fuoco ragazzi! Grandi sfide in programma, a partire da {top_match}{extra}! "
            f"Vi ho messo tutti gli orari e i link in chat! Vivere una favola!"
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
                voice = (
                    f"Gas a martello ragazzi! La MotoGP vola a {mgp['circuit']} per il {mgp['name']}! "
                    f"Spettacolo puro sui due ruoli, vi ho lasciato tutti i dettagli in chat!"
                )
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
                voice = (
                    f"Magia sui motori ragazzi! La Formula 1 torna ad accendere la notte con il {f1['name']} a {f1['circuit']}! "
                    f"Tenetevi forte, vi ho messo il programma completo in chat!"
                )
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
                    "portal_url": "https://web01.madplay77.ru/it",
                }

        # C. Ricerca puntuale per Squadra o Atleta (es. "Milan", "Juventus", "Sinner", "Lakers")
        clean_q = re.sub(
            r"\b(la|il|lo|gli|le|partita|del|della|di|contro|vs|streaming|live|cerca|trova|dove\s+fanno\s+vedere|quando\s+gioca|orario)\b",
            "",
            q
        ).strip()
        tokens = [t for t in clean_q.split() if len(t) >= 2]

        madplay_events = await self.get_live_matches()

        # 1. Corrispondenza nei live streaming MadPlay77 con scoring su confini di parola
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
            best = scored[0][1]
            top_t = best['title'].replace(" vs ", " contro ")
            voice = f"Magia ragazzi! Ho trovato {top_t} in diretta streaming su MadPlay77! Vi ho lasciato il link in chat!"
            return {
                "found": True,
                "type": "single_match",
                "query": query,
                "best_match": best,
                "all_matches": [m for _, m in scored],
                "voice_summary": voice,
            }

        # 2. Corrispondenza negli scoreboard ufficiali ESPN (anche prossimo turno se oggi riposano)
        for l_key in ("serie_a", "champions_league", "premier_league", "la_liga", "bundesliga", "nba", "euroleague", "tennis_atp", "tennis_wta"):
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
                    v_top = ev["title"].replace(" vs ", " contro ")
                    voice = f"Magia ragazzi! Ho trovato {v_top} in programma alle {ev['time']}! Vi ho messo tutti i dettagli in chat!"
                    return {
                        "found": True,
                        "type": "single_match",
                        "query": query,
                        "best_match": ev,
                        "all_matches": [ev],
                        "voice_summary": voice,
                    }

        # 3. Nessuna corrispondenza: fallback elegante con i match in evidenza stasera
        tonight_fallback = await self.get_tonight_matches()
        return {
            "found": False,
            "type": "not_found",
            "query": query,
            "message": f"Non ho trovato nessuna partita per *\"{clean_q or query}\"* in questo momento.",
            "featured": tonight_fallback.get("matches", [])[:5],
            "voice_summary": f"Non ho trovato partite per {clean_q or query} al momento ragazzi, vi ho mostrato gli eventi attivi stasera in chat!",
            "portal_url": "https://web01.madplay77.ru/it",
        }

    # =========================================================================
    # 6. COSTRUZIONE DISCORD EMBED & VIEW INTERATTIVA
    # =========================================================================
    def build_sports_embed(self, result: Dict[str, Any]) -> Tuple[discord.Embed, discord.ui.View, str]:
        """Costruisce un Discord Embed completo, una View con pulsanti e la frase vocale da recitare."""
        res_type = result.get("type", "schedule")
        voice_text = result.get("voice_summary") or "Magia ragazzi! Ecco gli eventi sportivi pronti per voi!"

        # A. Motorsport (F1 / MotoGP)
        if res_type == "motorsport":
            sub = result.get("subtype", "f1")
            data = result.get("data", {})
            title_prefix = "🏎️ Formula 1" if sub == "f1" else "🏍️ MotoGP"
            embed = discord.Embed(
                title=f"{title_prefix} — {data.get('name')}",
                description=f"📍 **Circuito**: `{data.get('circuit')}`\n🏁 **Stato**: `In Programma`",
                color=discord.Color.red(),
            )
            if data.get("sessions"):
                embed.add_field(name="⏰ Orari e Sessioni Ufficiali (CET)", value="\n".join(data["sessions"]), inline=False)
            embed.set_footer(text="Franchino Sports • Motorsport World Series")

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Sito Ufficiale", url=data.get("portal_url", "https://www.formula1.com"), style=discord.ButtonStyle.link, emoji="🌐"))
            view.add_item(discord.ui.Button(label="Tutti gli Eventi Live", url="https://web01.madplay77.ru/it", style=discord.ButtonStyle.link, emoji="📺"))
            return embed, view, voice_text

        # B. Match singolo trovato
        if res_type == "single_match" or (result.get("found") and "best_match" in result and res_type != "schedule"):
            m = result["best_match"]
            embed = discord.Embed(
                title=f"🏆 {m['title']}",
                description="Diretta e programma ufficiale su **MadPlay77** e network sportivi!",
                color=discord.Color.green() if m.get("is_live") else discord.Color.blue(),
            )
            embed.add_field(name="⚽ Disciplina", value=m.get("sport_name") or m.get("sport", "Sport"), inline=True)
            embed.add_field(name="⏰ Orario / Stato", value=f"`{m['time']}`", inline=True)
            if m.get("score"):
                embed.add_field(name="📊 Risultato Live", value=f"**{m['score']}**", inline=True)

            target_url = m.get("stream_url") or m.get("url") or "https://web01.madplay77.ru/it"
            embed.add_field(name="🔗 Link Streaming", value=f"[Guarda la Partita in Diretta]({target_url})", inline=False)
            embed.set_footer(text="Franchino Sports • Live Arena")

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Guarda lo Streaming", url=target_url, style=discord.ButtonStyle.link, emoji="📺"))
            view.add_item(discord.ui.Button(label="Palinsesto Live", url="https://web01.madplay77.ru/it", style=discord.ButtonStyle.link, emoji="🌐"))
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

            embed_desc = "Ecco il quadro completo delle sfide e gli orari del prossimo turno!" if is_upcoming else "Ecco le partite calde, gli incontri live e il grande spettacolo dello sport di oggi!"

            embed = discord.Embed(
                title=embed_title,
                description=embed_desc,
                color=discord.Color.gold(),
            )

            alt_note = result.get("alternative_note")
            if alt_note:
                embed.add_field(name="ℹ️ Info Lega", value=alt_note, inline=False)

            # Raggruppa i match per disciplina / competizione
            grouped: Dict[str, List[str]] = {}
            for m in matches[:14]:
                grp_name = f"{m.get('sport_emoji', '🏆')} {m.get('league', m.get('sport', 'Sport'))}"
                time_disp = f"`{m['time']}`"
                if m.get("score"):
                    time_disp += f" (**{m['score']}**)"

                target_link = m.get("stream_url") or m.get("url") or "https://web01.madplay77.ru/it"
                link_md = f"[{m['title']}]({target_link})"
                if m.get("stream_url"):
                    link_md += " 🔴 *[Streaming]*"

                line = f"• {time_disp} — {link_md}"
                grouped.setdefault(grp_name, []).append(line)

            for grp, lines in list(grouped.items())[:6]:
                embed.add_field(name=grp, value="\n".join(lines[:4]), inline=False)

            footer_txt = f"Franchino Sports • {len(matches)} incontri tracciati"
            if is_upcoming:
                footer_txt += " (prossimo turno)"
            embed.set_footer(text=footer_txt)

            view = discord.ui.View()
            # Aggiungi fino a 2 pulsanti diretti per le partite che hanno streaming attivo
            stream_count = 0
            for m in matches:
                if m.get("stream_url") and stream_count < 2:
                    short_title = m["title"]
                    if len(short_title) > 28:
                        short_title = short_title[:25] + "..."
                    view.add_item(discord.ui.Button(label=f"Stream: {short_title}", url=m["stream_url"], style=discord.ButtonStyle.link, emoji="📺"))
                    stream_count += 1

            view.add_item(discord.ui.Button(label="Apri Palinsesto Completo", url="https://web01.madplay77.ru/it", style=discord.ButtonStyle.link, emoji="🌐"))
            return embed, view, voice_text

        # D. Not Found / Fallback
        embed = discord.Embed(
            title="⚠️ Nessuna Partita Trovata",
            description=result.get("message", "Nessun evento sportivo trovato per questa ricerca."),
            color=discord.Color.orange(),
        )
        featured = result.get("featured", [])
        if featured:
            lines = [f"• **{f['title']}** ({f.get('sport_name') or f.get('league', 'Sport')}) - `{f['time']}` → [Guarda]({f.get('url', 'https://web01.madplay77.ru/it')})" for f in featured[:5]]
            embed.add_field(name="🔥 Eventi Principali Attivi Adesso:", value="\n".join(lines), inline=False)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Apri Palinsesto MadPlay77", url="https://web01.madplay77.ru/it", style=discord.ButtonStyle.link, emoji="🌐"))
        return embed, view, voice_text


# Istanza singleton globale
sports_scraper = SportsScraper()
