"""
sports_scraper.py - Modulo di ricerca e streaming eventi sportivi (MadPlay77)
=============================================================================
Effettua lo scraping e l'interrogazione delle API di MadPlay77 (web01.madplay77.ru)
per trovare partite ed eventi in diretta (Calcio, Basket NBA, F1, MotoGP, Tennis, ecc.)
e genera i link diretti allo streaming live con player integrato.
"""

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Franchino.SportsScraper")

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
    """Pulisce caratteri spuri o prefissi dal titolo della partita."""
    clean = re.sub(r"^[\s\x00-\x1f!#$&()1-9]+", "", raw).strip()
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


class SportsScraper:
    """Gestore dello scraping e della ricerca eventi sportivi su MadPlay77."""

    def __init__(self):
        self._cache_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._cache_ttl: float = 180.0  # 3 minuti di cache
        self._lock = asyncio.Lock()

    async def get_live_matches(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Recupera la lista delle partite live e programmate con link diretti."""
        async with self._lock:
            now = time.time()
            if not force_refresh and self._cache_matches and (now - self._last_fetch_time < self._cache_ttl):
                return self._cache_matches

            matches = await asyncio.to_thread(self._fetch_from_api)
            if matches:
                self._cache_matches = matches
                self._last_fetch_time = now
                logger.info(f"⚽ Caricati {len(matches)} eventi sportivi live da MadPlay77!")
            elif self._cache_matches:
                logger.warning("Errore nel refresh, uso cache eventi precedente.")
                return self._cache_matches

            return self._cache_matches

    def _fetch_from_api(self) -> List[Dict[str, Any]]:
        """Esegue il fetcher binario tramite Node ed estrae gli eventi strutturati."""
        try:
            res = subprocess.run(
                ["node", "-e", NODE_HELPER_SCRIPT],
                capture_output=True,
                timeout=8,
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

                raw_title = ""
                if 30 in m and isinstance(m[30][0], bytes):
                    raw_title = m[30][0].decode("utf-8", errors="ignore")

                title = _clean_title(raw_title)
                if not title or len(title) < 3:
                    continue

                # Formattazione orario
                time_str = "IN DIRETTA"
                if ts and ts > 1000000000:
                    try:
                        match_dt = datetime.fromtimestamp(ts / 1000.0)
                        now_dt = datetime.now()
                        if match_dt > now_dt:
                            time_str = match_dt.strftime("%H:%M")
                        else:
                            time_str = f"LIVE ({match_dt.strftime('%H:%M')})"
                    except Exception:
                        pass

                # Generazione parametro mdata in Base64 (usato dal player web01)
                mdata_str = f"{match_id}_{sport_type}"
                mdata_b64 = base64.b64encode(mdata_str.encode("utf-8")).decode("utf-8")
                stream_url = f"https://web01.madplay77.ru/it/seth/livestream?mdata={mdata_b64}"

                sport_label = SPORT_NAMES.get(sport_type, "🏆 Sport")

                matches.append({
                    "id": match_id,
                    "sport_type": sport_type,
                    "sport_name": sport_label,
                    "title": title,
                    "time": time_str,
                    "url": stream_url,
                    "portal_url": "https://web01.madplay77.ru/it",
                })

            return matches
        except Exception as e:
            logger.error(f"Errore durante l'estrazione eventi sportivi: {e}")
            return []

    async def search_event(self, query: str) -> Dict[str, Any]:
        """
        Cerca una squadra, competizione o evento sportivo.
        Restituisce un dizionario con l'evento trovato o gli eventi live alternativi.
        """
        matches = await self.get_live_matches()
        if not matches:
            return {
                "found": False,
                "query": query,
                "matches": [],
                "message": "Nessun evento sportivo attualmente raggiungibile su MadPlay77.",
            }

        q = query.lower().strip()
        # Rimuovi parole comuni di stop
        q_clean = re.sub(r"\b(la|il|lo|gli|le|partita|del|della|di|contro|vs|streaming|live)\b", "", q).strip()
        tokens = [t for t in q_clean.split() if len(t) >= 2]

        # 1. Ricerca con corrispondenza esatta
        exact = [m for m in matches if q_clean in m["title"].lower()]
        if exact:
            return {"found": True, "query": query, "best_match": exact[0], "all_matches": exact}

        # 2. Ricerca per token (es. "milan", "juventus", "ferrari", "nets")
        scored = []
        for m in matches:
            t_low = m["title"].lower()
            score = sum(1 for tok in tokens if tok in t_low)
            if score > 0:
                scored.append((score, m))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0][1]
            return {
                "found": True,
                "query": query,
                "best_match": best,
                "all_matches": [m for _, m in scored],
            }

        # 3. Nessuna corrispondenza: restituisci i primi eventi in evidenza
        return {
            "found": False,
            "query": query,
            "message": f"Non ho trovato nessuna partita per *\"{query}\"* in questo momento.",
            "featured": matches[:5],
            "portal_url": "https://web01.madplay77.ru/it",
        }


# Istanza singleton globale
sports_scraper = SportsScraper()
