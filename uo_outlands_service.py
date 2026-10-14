"""
uo_outlands_service.py - Servizi UO Outlands & UOAddicts per Franchino Bot
========================================================================
Fornisce le 4 funzionalità avanzate per Ultima Online Outlands:
1. UOAddicts News Monitor: monitora le novità su uoaddicts.com/news?server=uo-outlands
   con cache locale (data/uoaddicts_news_cache.json) e pubblicazione automatica in #info.
2. UOAddicts Builds Browser: scraper asincrono con cache TTL (1 ora) per build
   PVM, PVP, Popolari e Recenti con estrazione di popolarità, voti, skill template e link.
3. Server Status Check: controllo socket TCP asincrono su play.uooutlands.com
   (fallback 74.91.113.38) porta 2593 con ping in ms e rilevamento ONLINE / OFFLINE.
4. Daily Restart Manager: calcolo countdown e allerte automatiche (10m e 5m prima)
   per il riavvio ordinario delle 11:00 AM ET (17:00 italiane).
"""

import asyncio
import html
import json
import logging
import os
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import aiohttp
import discord

logger = logging.getLogger("Franchino.UOOutlands")

# Directory e file di cache
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
NEWS_CACHE_FILE = os.path.join(DATA_DIR, "uoaddicts_news_cache.json")
BUILDS_CACHE_FILE = os.path.join(DATA_DIR, "uoaddicts_builds_cache.json")

# Fusi orari
TZ_ET = ZoneInfo("America/New_York")
TZ_ROME = ZoneInfo("Europe/Rome")

# Parametri Server UO Outlands
UO_SERVER_HOST = "play.uooutlands.com"
UO_SERVER_IP = "74.91.113.38"
UO_SERVER_PORT = 2593
UO_SERVER_LOCATION = "Ashburn, West Virginia (USA)"
UO_SERVER_TIMEOUT = 3.0

# Colori Franchino
COLOR_FRANCHINO = 0x9B59B6
COLOR_ONLINE = 0x2ECC71
COLOR_OFFLINE = 0xE74C3C
COLOR_WARNING = 0xF39C12
COLOR_INFO = 0x3498DB

# URL UOAddicts
UOADDICTS_BASE = "https://uoaddicts.com"
UOADDICTS_NEWS_URL = "https://uoaddicts.com/news?server=uo-outlands"
UOADDICTS_BUILD_URLS = {
    "pvm": "https://uoaddicts.com/builds/tags/pvm",
    "pvp": "https://uoaddicts.com/builds/tags/pvp",
    "popular": "https://uoaddicts.com/builds/popular",
    "recent": "https://uoaddicts.com/builds/recent",
}


# ==============================================================================
# DATA CLASSES
# ==============================================================================

@dataclass
class UOAddictsNewsItem:
    id: str
    title: str
    url: str
    image: Optional[str]
    description: str
    meta: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "image": self.image,
            "description": self.description,
            "meta": self.meta,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UOAddictsNewsItem":
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            image=data.get("image"),
            description=data.get("description", ""),
            meta=data.get("meta", ""),
            tags=data.get("tags", []),
        )


@dataclass
class UOAddictsBuildItem:
    title: str
    slug: str
    server: str
    url: str
    author: str
    usage: int
    votes: int
    skills: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "slug": self.slug,
            "server": self.server,
            "url": self.url,
            "author": self.author,
            "usage": self.usage,
            "votes": self.votes,
            "skills": self.skills,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UOAddictsBuildItem":
        return cls(
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            server=data.get("server", "uo-outlands"),
            url=data.get("url", ""),
            author=data.get("author", "Sconosciuto"),
            usage=data.get("usage", 0),
            votes=data.get("votes", 0),
            skills=data.get("skills", []),
            tags=data.get("tags", []),
        )


# ==============================================================================
# FALLBACK DATA FOR OFFLINE RESILIENCE
# ==============================================================================

FALLBACK_NEWS_ITEMS = [
    UOAddictsNewsItem(
        id="uo-outlands-community-news",
        title="Ultima Online Outlands — Aggiornamenti Community UOAddicts",
        url="https://uoaddicts.com/news?server=uo-outlands",
        image=None,
        description="Rimani aggiornato su tutti gli eventi, tornei e patch notes di Ultima Online Outlands direttamente dalla community.",
        meta="UOAddicts Staff · Notizie Community",
        tags=["Outlands", "Community", "News"],
    ),
    UOAddictsNewsItem(
        id="outlands-dungeon-overhaul",
        title="Dungeon Overhaul & Patch Notes su UO Outlands",
        url="https://uoaddicts.com/news?server=uo-outlands",
        image=None,
        description="Tutte le ultime modifiche alle meccaniche di gioco, codex, boss fight e bilanciamento dungeon.",
        meta="Outlands Team · Patch Notes",
        tags=["Patch Notes", "PvM", "Dungeons"],
    ),
]

FALLBACK_POPULAR_BUILDS = [
    UOAddictsBuildItem(
        title="OP Archery Tamer",
        slug="op-archery-tamer",
        server="uo-outlands",
        url="https://uoaddicts.com/builds/popular",
        author="jaseowns",
        usage=15,
        votes=12,
        skills=["Animal Lore: 120", "Animal Taming: 120", "Archery: 80", "Tactics: 100", "Veterinary: 100", "Magery: 80", "Meditation: 100"],
        tags=["pvm", "tamer", "archery"],
    ),
    UOAddictsBuildItem(
        title="Comfy TriBard Summoner",
        slug="comfy-tribard-summoner",
        server="uo-outlands",
        url="https://uoaddicts.com/builds/popular",
        author="tremblewick",
        usage=12,
        votes=8,
        skills=["Discordance: 120", "Musicianship: 100", "Peacemaking: 100", "Provocation: 100", "Magery: 100", "Meditation: 80", "Necromancy: 100"],
        tags=["pvm", "tribard", "summoner"],
    ),
    UOAddictsBuildItem(
        title="PvP Lumberjack Dexxer",
        slug="pvp-lumberjack-dexxer",
        server="uo-outlands",
        url="https://uoaddicts.com/builds/popular",
        author="JackSparrow",
        usage=9,
        votes=7,
        skills=["Swordsmanship: 100", "Lumberjacking: 100", "Tactics: 100", "Anatomy: 100", "Healing: 100", "Resisting Spells: 100", "Tracking: 100"],
        tags=["pvp", "melee", "dexxer"],
    ),
]


def _atomic_json_dump(file_path: str, data: dict) -> None:
    """Scrive un file JSON in modo atomico tramite file temporaneo per prevenire corruzioni su crash o kill."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    temp_file = f"{file_path}.tmp.{os.getpid()}"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, file_path)
    except Exception as e:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass
        raise e


@dataclass
class ServerStatusResult:
    online: bool
    host: str = UO_SERVER_HOST
    ip: str = UO_SERVER_IP
    port: int = UO_SERVER_PORT
    location: str = UO_SERVER_LOCATION
    latency_ms: Optional[float] = None
    error: Optional[str] = None

    def build_embed(self) -> discord.Embed:
        if self.online:
            embed = discord.Embed(
                title="🌐 UO Outlands — Server Status: ONLINE 🟢",
                description="Il server ufficiale di **Ultima Online Outlands** è attivo e raggiungibile!",
                color=COLOR_ONLINE,
            )
            embed.add_field(name="🟢 Stato", value="`ONLINE`", inline=True)
            lat_str = f"`{self.latency_ms:.1f} ms`" if self.latency_ms is not None else "`< 1 ms`"
            embed.add_field(name="⚡ Ping / Latenza", value=lat_str, inline=True)
            embed.add_field(name="🔌 Porta TCP", value=f"`{self.port}`", inline=True)
            embed.add_field(name="📡 Login / DNS", value=f"`{self.host}`", inline=True)
            embed.add_field(name="🖥️ Indirizzo IP", value=f"`{self.ip}`", inline=True)
            embed.add_field(name="📍 Posizione Server", value=f"`{self.location}`", inline=True)
            embed.set_footer(text="Franchino Outlands Sentinel • play.uooutlands.com:2593")
        else:
            embed = discord.Embed(
                title="🌐 UO Outlands — Server Status: OFFLINE 🔴",
                description="⚠️ **Attenzione!** Il server di **Ultima Online Outlands** risulta attualmente non raggiungibile o offline.",
                color=COLOR_OFFLINE,
            )
            embed.add_field(name="🔴 Stato", value="`OFFLINE / IRRAVALICABILE`", inline=True)
            err_msg = self.error if self.error else "Timeout connessione (> 3.0s)"
            embed.add_field(name="⚡ Diagnostica", value=f"`{err_msg[:60]}`", inline=True)
            embed.add_field(name="🔌 Porta TCP", value=f"`{self.port}`", inline=True)
            embed.add_field(name="📡 Login / DNS", value=f"`{self.host}`", inline=True)
            embed.add_field(name="🖥️ Indirizzo IP", value=f"`{self.ip}`", inline=True)
            embed.add_field(name="📍 Posizione Server", value=f"`{self.location}`", inline=True)
            embed.set_footer(text="Franchino Outlands Sentinel • Riprova tra pochi istanti")
        return embed

    def voice_text(self) -> str:
        if self.online:
            lat = int(self.latency_ms) if self.latency_ms is not None else 50
            return f"Il server di Ultima Online Outlands è online con una latenza di {lat} millisecondi!"
        return "Attenzione! Il server di Ultima Online Outlands risulta offline o irraggiungibile!"


@dataclass
class RestartInfo:
    next_restart_ny: datetime
    next_restart_rome: datetime
    time_remaining: timedelta
    hours: int
    minutes: int
    seconds: int

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🔄 UO Outlands — Riavvio Ordinario Giornaliero",
            description=(
                "Il server di **Ultima Online Outlands** effettua un riavvio automatico giornaliero "
                "per la manutenzione del mondo, l'applicazione delle patch ed eventi stagionali."
            ),
            color=COLOR_INFO,
        )
        embed.add_field(name="⏰ Orario Finestra", value="`11:00 AM ET (17:00 Italiane)`", inline=True)
        if self.hours > 0:
            countdown_str = f"**{self.hours}h {self.minutes}m {self.seconds}s**"
        else:
            countdown_str = f"**{self.minutes}m {self.seconds}s**"
        embed.add_field(name="⏳ Conto alla Rovescia", value=countdown_str, inline=True)
        date_str = self.next_restart_rome.strftime("%d/%m/%Y alle %H:%M %Z")
        embed.add_field(name="📅 Prossimo Riavvio", value=f"`{date_str}`", inline=False)
        embed.add_field(
            name="💡 Consiglio Avventurieri",
            value="Ricordati di portare il personaggio in locanda o safe zone prima dell'inizio della manutenzione!",
            inline=False,
        )
        embed.set_footer(text="Franchino Outlands Maintenance Sentinel • uooutlands.com")
        return embed

    def voice_text(self) -> str:
        rome_hour = self.next_restart_rome.hour
        h_word = {
            16: "alle sedici",
            17: "alle diciassette",
            18: "alle diciotto",
        }.get(rome_hour, f"alle ore {rome_hour}")
        if self.hours > 0:
            h_str = f"{self.hours} or{'a' if self.hours == 1 else 'e'}"
            m_str = f"{self.minutes} minut{'o' if self.minutes == 1 else 'i'}"
            return f"Il prossimo riavvio ordinario del server è previsto tra {h_str} e {m_str}, {h_word}!"
        return f"Attenzione! Il riavvio ordinario del server è tra soli {self.minutes} minuti, {h_word}!"


# ==============================================================================
# NETWORK & HTML HELPERS
# ==============================================================================

async def fetch_html(url: str, timeout: float = 10.0) -> str:
    """Scarica il contenuto HTML con aiohttp e fallback automatico su urllib in thread separato."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FranchinoBot/2.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.text()
    except Exception as e:
        logger.debug(f"aiohttp fetch fallito per {url} ({e}), uso fallback urllib...")

    def _sync_fetch() -> str:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")

    return await asyncio.to_thread(_sync_fetch)


# ==============================================================================
# FEATURE 1: UOADDICTS NEWS AUTO-POSTER & PARSER
# ==============================================================================

def parse_news_html(html_text: str) -> List[UOAddictsNewsItem]:
    """Estrae gli articoli di news Outlands da uoaddicts.com/news?server=uo-outlands."""
    items: List[UOAddictsNewsItem] = []
    pattern = r'<a\s+[^>]*href="(/news/([^"]+))"[^>]*>(.*?)</a>'
    for m in re.finditer(pattern, html_text, re.DOTALL):
        slug = m.group(2)
        full_url = UOADDICTS_BASE + m.group(1)
        card_body = m.group(3)

        # Immagine copertina
        img_m = re.search(r'<img\s+[^>]*src="([^"]+)"', card_body)
        img_url = None
        if img_m:
            src = img_m.group(1)
            img_url = UOADDICTS_BASE + src if src.startswith("/") else src

        # Titolo
        title_m = re.search(r'<h3[^>]*>(.*?)</h3>', card_body, re.DOTALL)
        if title_m:
            title = html.unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip()
        else:
            title = slug.replace("-", " ").title()

        # Descrizione/sommario
        desc_m = re.search(r'<p\s+class="[^"]*line-clamp[^"]*"[^>]*>(.*?)</p>', card_body, re.DOTALL)
        if desc_m:
            desc = html.unescape(re.sub(r'<[^>]+>', '', desc_m.group(1))).strip()
        else:
            desc = ""

        # Metadati (autore, subreddit, data)
        meta_m = re.search(r'<p\s+class="text-xs[^"]*"[^>]*>(.*?)</p>', card_body, re.DOTALL)
        if meta_m:
            meta = html.unescape(re.sub(r'<!-- -->', '', re.sub(r'<[^>]+>', '', meta_m.group(1)))).strip()
        else:
            meta = ""

        # Badge e tags
        tags: List[str] = []
        for t_m in re.finditer(r'<span[^>]*class="[^"]*(?:rounded|bg-)[^"]*"[^>]*>(.*?)</span>', card_body):
            cleaned = html.unescape(re.sub(r'<[^>]+>', '', t_m.group(1))).strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)

        items.append(
            UOAddictsNewsItem(
                id=slug,
                title=title,
                url=full_url,
                image=img_url,
                description=desc,
                meta=meta,
                tags=tags,
            )
        )
    return items


class UOAddictsNewsService:
    """Gestisce il caricamento delle news da UOAddicts e il rilevamento di nuovi post con cache."""

    def __init__(self, cache_file: str = NEWS_CACHE_FILE):
        self.cache_file = cache_file
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        except Exception:
            pass

    def load_seen_ids(self) -> Set[str]:
        if not os.path.exists(self.cache_file):
            return set()
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("seen_ids", []))
        except Exception as e:
            logger.warning(f"Errore lettura cache news {self.cache_file}: {e}")
            return set()

    def save_seen_ids(self, seen_ids: Set[str]) -> None:
        self._ensure_cache_dir()
        try:
            _atomic_json_dump(self.cache_file, {"seen_ids": sorted(list(seen_ids)), "last_updated": time.time()})
        except Exception as e:
            logger.warning(f"Errore salvataggio cache news: {e}")

    async def fetch_news(self) -> List[UOAddictsNewsItem]:
        """Scarica e parsa le news attuali da UOAddicts."""
        try:
            html_content = await fetch_html(UOADDICTS_NEWS_URL)
            return parse_news_html(html_content)
        except Exception as e:
            logger.error(f"Errore nel download news UOAddicts: {e}")
            return []

    async def check_for_new_news(self) -> List[UOAddictsNewsItem]:
        """
        Controlla se ci sono post non ancora visti.
        Al primo avvio assoluto (cache inesistente) inizializza la cache con i post attuali
        per evitare spam di post pregressi, ritornando lista vuota.
        Nelle esecuzioni successive ritorna tutti i post nuovi trovati e aggiorna la cache.
        """
        items = await self.fetch_news()
        if not items:
            return []

        if not os.path.exists(self.cache_file):
            all_ids = {item.id for item in items}
            self.save_seen_ids(all_ids)
            logger.info(f"📰 Inizializzata cache news UOAddicts con {len(all_ids)} post pregressi.")
            return []

        seen_ids = self.load_seen_ids()
        new_items = [item for item in items if item.id not in seen_ids]

        if new_items:
            for item in new_items:
                seen_ids.add(item.id)
            self.save_seen_ids(seen_ids)

        return new_items

    async def fetch_latest_news(self, limit: int = 5) -> List[UOAddictsNewsItem]:
        """Recupera le ultime N news per visualizzazione su richiesta (con fallback offline se non disponibili)."""
        items = await self.fetch_news()
        if not items:
            return FALLBACK_NEWS_ITEMS[:limit]
        return items[:limit]


def build_news_embed(item: UOAddictsNewsItem) -> discord.Embed:
    """Genera un Discord Embed elegante per un articolo di news."""
    embed = discord.Embed(
        title=f"📰 {item.title}",
        url=item.url,
        description=item.description if item.description else "Nuovo aggiornamento pubblicato per UO Outlands!",
        color=COLOR_INFO,
    )
    if item.tags:
        embed.add_field(name="🏷️ Tag / Categoria", value=" • ".join(f"`{t}`" for t in item.tags), inline=True)
    if item.meta:
        embed.add_field(name="✍️ Fonte & Autore", value=f"`{item.meta}`", inline=True)
    embed.add_field(name="🔗 Leggi su UOAddicts", value=f"[Clicca qui per l'articolo completo]({item.url})", inline=False)
    if item.image:
        embed.set_image(url=item.image)
    embed.set_footer(text="UOAddicts News • UO Outlands Updates")
    return embed


def build_news_list_embed(items: List[UOAddictsNewsItem], title: str = "📰 Ultime Novità UO Outlands da UOAddicts") -> discord.Embed:
    """Genera un Discord Embed che riepiloga le ultime news disponibili."""
    embed = discord.Embed(
        title=title,
        url=UOADDICTS_NEWS_URL,
        description="Ecco le novità e discussioni più recenti della community di Ultima Online Outlands:",
        color=COLOR_INFO,
    )
    for i, it in enumerate(items[:6], 1):
        tags_str = f" `{' / '.join(it.tags[:3])}`" if it.tags else ""
        meta_str = f" • *{it.meta}*" if it.meta else ""
        desc_snippet = f"\n> {it.description[:110]}..." if it.description else ""
        embed.add_field(
            name=f"{i}. {it.title}{tags_str}",
            value=f"[Apri articolo su UOAddicts]({it.url}){meta_str}{desc_snippet}",
            inline=False,
        )
    embed.set_footer(text="Franchino News Sentinel • uoaddicts.com/news")
    return embed


# ==============================================================================
# FEATURE 2: UOADDICTS BUILDS SCRAPER & BROWSER
# ==============================================================================

def parse_builds_html(html_text: str, category: str = "popular") -> List[UOAddictsBuildItem]:
    """Parsa le schede build Outlands dal markup di UOAddicts."""
    builds: List[UOAddictsBuildItem] = []
    pattern = r'<a\s+[^>]*href="(/builds/([^"/]+)/([^"/]+))"[^>]*>(.*?)</a>'
    matches = list(re.finditer(pattern, html_text, re.DOTALL))

    for m in matches:
        full_url = UOADDICTS_BASE + m.group(1)
        server = m.group(2)
        slug = m.group(3)
        body = m.group(4)

        # Filtra solo i server Outlands (o compatibili)
        if "outlands" not in server.lower() and server != "uo-outlands":
            continue

        # Titolo
        title_m = re.search(r'<div\s+class="not-input[^"]*"[^>]*>([^<]+)</div>', body)
        if title_m:
            title = html.unescape(title_m.group(1).strip())
        else:
            title = slug.replace("-", " ").title()

        # Autore
        author_m = re.search(r'by\s*(?:<!-- -->)?\s*([a-zA-Z0-9_\-]+)', body)
        author = author_m.group(1) if author_m else "Sconosciuto"

        # Conteggio giocatori che usano la build
        usage_m = re.search(r'(\d+)\s*(?:<!-- -->)?\s*players use this build', body)
        usage = int(usage_m.group(1)) if usage_m else 0

        # Voti (Love/Like)
        votes = 0
        votes_m = re.search(r'❤️\s*(?:<!-- -->)?\s*(\d+)', body)
        if votes_m:
            votes += int(votes_m.group(1))
        likes_m = re.search(r'👍\s*(?:<!-- -->)?\s*(\d+)', body)
        if likes_m:
            votes += int(likes_m.group(1))

        # Skills list
        skills: List[str] = []
        for s_m in re.finditer(
            r'<div\s+class="skill-row[^"]*">\s*<span\s+class="name">([^<]+)</span>\s*<span\s+class="value">([^<]+)</span>',
            body,
        ):
            s_name = s_m.group(1).strip()
            s_val = s_m.group(2).strip()
            if s_name.lower() != "total":
                skills.append(f"{s_name}: {s_val}")

        # Tags trovati subito dopo il tag </a> nel blocco
        end_pos = m.end()
        chunk = html_text[end_pos: end_pos + 600]
        tags = [html.unescape(t.strip()) for t in re.findall(r'href="/builds/tags/([^"/]+)"', chunk)]

        builds.append(
            UOAddictsBuildItem(
                title=title,
                slug=slug,
                server=server,
                url=full_url,
                author=author,
                usage=usage,
                votes=votes,
                skills=skills,
                tags=tags,
            )
        )

    if category != "recent":
        builds.sort(key=lambda b: (b.usage, b.votes), reverse=True)

    return builds


class UOAddictsBuildsService:
    """Gestisce il recupero e la cache delle build da UOAddicts con TTL di 1 ora."""

    def __init__(self, cache_file: str = BUILDS_CACHE_FILE, ttl_seconds: float = 3600.0):
        self.cache_file = cache_file
        self.ttl_seconds = ttl_seconds
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        except Exception:
            pass

    def _load_cache(self) -> dict:
        if not os.path.exists(self.cache_file):
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Errore lettura cache build: {e}")
            return {}

    def _save_cache(self, cache_data: dict) -> None:
        self._ensure_cache_dir()
        try:
            _atomic_json_dump(self.cache_file, cache_data)
        except Exception as e:
            logger.warning(f"Errore salvataggio cache build: {e}")

    def normalize_category(self, cat: Optional[str]) -> str:
        if not cat:
            return "popular"
        c = cat.lower().strip()
        if c in ("pvm", "mostri", "farm", "farming", "dungeon"):
            return "pvm"
        if c in ("pvp", "duello", "duelli", "pk", "anti-pk"):
            return "pvp"
        if c in ("recent", "recenti", "nuove", "ultime"):
            return "recent"
        return "popular"

    async def fetch_builds(self, category: str = "popular", force_refresh: bool = False) -> List[UOAddictsBuildItem]:
        """Scarica le build per una categoria (pvm, pvp, popular, recent) sfruttando la cache TTL."""
        norm_cat = self.normalize_category(category)
        cache = self._load_cache()
        now = time.time()

        if not force_refresh and norm_cat in cache:
            entry = cache[norm_cat]
            if now - entry.get("timestamp", 0) < self.ttl_seconds:
                return [UOAddictsBuildItem.from_dict(d) for d in entry.get("builds", [])]

        target_url = UOADDICTS_BUILD_URLS.get(norm_cat, UOADDICTS_BUILD_URLS["popular"])
        try:
            html_text = await fetch_html(target_url)
            parsed = parse_builds_html(html_text, category=norm_cat)
            if parsed:
                cache[norm_cat] = {
                    "timestamp": now,
                    "builds": [b.to_dict() for b in parsed],
                }
                self._save_cache(cache)
                return parsed
        except Exception as e:
            logger.error(f"Errore download build per {norm_cat}: {e}")

        if norm_cat in cache:
            return [UOAddictsBuildItem.from_dict(d) for d in cache[norm_cat].get("builds", [])]
        return [b for b in FALLBACK_POPULAR_BUILDS if norm_cat in b.tags or norm_cat == "popular"] or FALLBACK_POPULAR_BUILDS

    async def search_builds(self, query: str, category: Optional[str] = None) -> List[UOAddictsBuildItem]:
        """Cerca build per parola chiave (es. 'tamer', 'bardo', 'arciere', 'necro') tra le build scaricate."""
        all_builds: List[UOAddictsBuildItem] = []
        if category:
            all_builds = await self.fetch_builds(category)
        else:
            pop = await self.fetch_builds("popular")
            pvm = await self.fetch_builds("pvm")
            seen_slugs = set()
            for b in pop + pvm:
                if b.slug not in seen_slugs:
                    seen_slugs.add(b.slug)
                    all_builds.append(b)

        q = query.lower().strip()
        matched = []
        for b in all_builds:
            full_searchable = f"{b.title} {' '.join(b.tags)} {' '.join(b.skills)} {b.author}".lower()
            if q in full_searchable:
                matched.append(b)

        return matched if matched else all_builds


def build_builds_embed(
    builds: List[UOAddictsBuildItem],
    category: str = "popular",
    query: Optional[str] = None,
    limit: int = 6,
) -> discord.Embed:
    """Costruisce un Discord Embed formattato per la lista di build UOAddicts."""
    cat_titles = {
        "popular": "Più Popolari & Utilizzate",
        "pvm": "PvM (Player vs Monster)",
        "pvp": "PvP (Player vs Player)",
        "recent": "Più Recenti",
    }
    cat_label = cat_titles.get(category, category.title())
    title_str = f"⚔️ Build UO Outlands — {cat_label}"
    if query:
        title_str += f" (Filtro: '{query}')"

    source_url = UOADDICTS_BUILD_URLS.get(category, UOADDICTS_BUILD_URLS["popular"])
    embed = discord.Embed(
        title=title_str,
        url=source_url,
        description=(
            f"Ecco le build più utilizzate dalla community di **UO Outlands** su [UOAddicts.com]({source_url}):\n"
            "*(Usa i link per visualizzare l'albero completo delle abilità e gli aspetti)*"
        ),
        color=COLOR_FRANCHINO,
    )

    if not builds:
        embed.description = "⚠️ Nessuna build trovata per i criteri specificati."
        return embed

    for i, b in enumerate(builds[:limit], 1):
        author_str = f"👤 Autore: `{b.author}`"
        stats_str = f"👥 **{b.usage}** usano questa build" + (f" • ❤️ **{b.votes}**" if b.votes > 0 else "")
        skills_str = ", ".join(b.skills[:6]) if b.skills else "Non specificate"
        if len(b.skills) > 6:
            skills_str += "..."
        tags_str = f"🏷️ `{' / '.join(b.tags[:4])}`" if b.tags else ""

        value_lines = [
            f"{stats_str} • {author_str}",
            f"🎯 **Skill**: `{skills_str}`",
        ]
        if tags_str:
            value_lines.append(tags_str)
        value_lines.append(f"🔗 [Apri su UOAddicts]({b.url})")

        embed.add_field(
            name=f"{i}. {b.title}",
            value="\n".join(value_lines),
            inline=False,
        )

    embed.set_footer(text="Franchino UOAddicts Build Advisor • uoaddicts.com/builds")
    return embed


def build_single_build_embed(build: UOAddictsBuildItem) -> discord.Embed:
    """Costruisce un Discord Embed di dettaglio per una singola build."""
    embed = discord.Embed(
        title=f"⚔️ Build UO Outlands: {build.title}",
        url=build.url,
        description=f"Build condivisa su **UOAddicts** per il server **UO Outlands** da `{build.author}`.",
        color=COLOR_FRANCHINO,
    )
    embed.add_field(name="👥 Popolarità", value=f"**{build.usage}** giocatori usano questa build", inline=True)
    embed.add_field(name="❤️ Apprezzamenti", value=f"**{build.votes}** voti", inline=True)
    if build.tags:
        embed.add_field(name="🏷️ Tag", value=" • ".join(f"`{t}`" for t in build.tags), inline=False)
    if build.skills:
        skill_lines = "\n".join(f"• **{s.split(':')[0]}**: `{s.split(':')[1].strip() if ':' in s else ''}`" for s in build.skills)
        embed.add_field(name="🎯 Skill Template", value=skill_lines[:1024], inline=False)
    embed.add_field(name="🔗 Guida Completa", value=f"[Visualizza dettagli completi su UOAddicts]({build.url})", inline=False)
    embed.set_footer(text="Franchino UOAddicts Build Advisor • uoaddicts.com")
    return embed


# ==============================================================================
# FEATURE 3: UO OUTLANDS SERVER STATUS CHECK
# ==============================================================================

async def check_uo_server_status(
    host: str = UO_SERVER_HOST,
    fallback_ip: str = UO_SERVER_IP,
    port: int = UO_SERVER_PORT,
    timeout: float = UO_SERVER_TIMEOUT,
) -> ServerStatusResult:
    """
    Verifica se il server UO Outlands è UP tramite controllo socket TCP asincrono (asyncio.open_connection)
    su play.uooutlands.com (fallback IP 74.91.113.38) su porta 2593 con timeout di 3 secondi.
    Calcola la latenza ping in millisecondi.
    """
    writer = None
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        lat = (time.perf_counter() - t0) * 1000.0
        return ServerStatusResult(
            online=True,
            host=host,
            ip=fallback_ip,
            port=port,
            location=UO_SERVER_LOCATION,
            latency_ms=round(lat, 1),
            error=None,
        )
    except Exception as err_host:
        logger.debug(f"Connessione a {host}:{port} fallita ({err_host}), provo fallback IP {fallback_ip}...")
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    writer = None
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(fallback_ip, port),
            timeout=timeout,
        )
        lat = (time.perf_counter() - t0) * 1000.0
        return ServerStatusResult(
            online=True,
            host=host,
            ip=fallback_ip,
            port=port,
            location=UO_SERVER_LOCATION,
            latency_ms=round(lat, 1),
            error=None,
        )
    except Exception as err_ip:
        logger.debug(f"Connessione a {fallback_ip}:{port} fallita: {err_ip}")
        err_text = str(err_ip).strip()
        if not err_text:
            err_text = f"Timeout connessione (> {timeout:.1f}s)" if isinstance(err_ip, asyncio.TimeoutError) else f"Errore connessione: {type(err_ip).__name__}"
        return ServerStatusResult(
            online=False,
            host=host,
            ip=fallback_ip,
            port=port,
            location=UO_SERVER_LOCATION,
            latency_ms=None,
            error=err_text,
        )
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ==============================================================================
# FEATURE 4: DAILY SERVER RESTART MANAGER & ALERTS
# ==============================================================================

def get_next_restart_info(
    target_hour_et: int = 11,
    target_minute_et: int = 0,
    current_time_ny: Optional[datetime] = None,
) -> RestartInfo:
    """
    Calcola il countdown esatto al prossimo riavvio ordinario delle 11:00 AM ET (17:00 italiane).
    Tiene conto accuratamente del fuso orario New York (ET) e conversione in ora italiana (Europe/Rome).
    """
    now_ny = current_time_ny or datetime.now(TZ_ET)
    restart_today_ny = datetime.combine(
        now_ny.date(),
        dtime(target_hour_et, target_minute_et, 0),
        tzinfo=TZ_ET,
    )

    if now_ny >= restart_today_ny:
        next_restart_ny = restart_today_ny + timedelta(days=1)
    else:
        next_restart_ny = restart_today_ny

    next_restart_rome = next_restart_ny.astimezone(TZ_ROME)
    diff = next_restart_ny - now_ny
    total_sec = max(0, int(diff.total_seconds()))
    hours, rem = divmod(total_sec, 3600)
    minutes, seconds = divmod(rem, 60)

    return RestartInfo(
        next_restart_ny=next_restart_ny,
        next_restart_rome=next_restart_rome,
        time_remaining=diff,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )


def build_restart_alert_embed(minutes_left: int, target_time_str: Optional[str] = None) -> discord.Embed:
    """Genera l'Embed di allerta per il riavvio del server a 10m o 5m."""
    time_label = target_time_str or "alle 17:00 italiane / 11:00 AM ET"
    if minutes_left >= 10:
        embed = discord.Embed(
            title="⚠️ UO Outlands — Riavvio Ordinario tra 10 Minuti!",
            description=(
                "Attenzione avventurieri! Il server di **Ultima Online Outlands** effettuerà il consueto "
                f"**riavvio ordinario giornaliero** per manutenzione e applicazione patch tra **10 minuti** "
                f"({time_label}).\n\n"
                "🛡️ **Raccomandazione**: portatevi al sicuro in una locanda o safe zone per prevenire rollback o perdite!"
            ),
            color=COLOR_WARNING,
        )
        embed.set_footer(text="Franchino Sentinel • Riavvio ordinario UO Outlands")
    else:
        embed = discord.Embed(
            title="🚨 UO Outlands — Ultimo Avviso: Riavvio tra 5 Minuti!",
            description=(
                "🚨 **ULTIMO AVVISO!** Il server di **Ultima Online Outlands** si riavvierà tra soli **5 minuti esatti**!\n\n"
                "⚠️ **Sloggate immediatamente** e assicuratevi di non essere nel mezzo di dungeon o combattimenti!"
            ),
            color=COLOR_OFFLINE,
        )
        embed.set_footer(text="Franchino Sentinel • Spegnimento imminente del server")
    return embed


def build_restart_alert_voice(minutes_left: int) -> str:
    """Testo per la notifica vocale TTS."""
    if minutes_left >= 10:
        return (
            "Attenzione ragazzi! Il server di Ultima Online Outlands si riavvia tra dieci minuti "
            "per la manutenzione ordinaria! Mettetevi al sicuro in una locanda!"
        )
    return (
        "Ultimo avviso ragazzi! Il server di Outlands si riavvia tra cinque minuti esatti! "
        "Sloggate subito per non perdere nulla!"
    )


# ==============================================================================
# DISCORD ANNOUNCEMENT CHANNEL RESOLUTION
# ==============================================================================

def resolve_announcement_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Risolve il canale di destinazione per notifiche e annunci:
    1. Cerca canale con nome esatto 'info', 'news', 'annunci'
    2. Cerca canale che contiene 'info', 'news', 'annunci'
    3. Fallback sul preferred channel (musica), system_channel o primo canale scrivibile
    """
    if not guild or not guild.me:
        return None

    target_names = ("info", "news", "annunci", "announcements", "avvisi")
    for ch in guild.text_channels:
        if ch.name.lower().strip() in target_names and ch.permissions_for(guild.me).send_messages:
            return ch

    for ch in guild.text_channels:
        cname = ch.name.lower()
        if any(t in cname for t in target_names) and ch.permissions_for(guild.me).send_messages:
            return ch

    try:
        from player import resolve_preferred_text_channel
        preferred = resolve_preferred_text_channel(guild)
        if preferred and preferred.permissions_for(guild.me).send_messages:
            return preferred
    except Exception:
        pass

    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        return guild.system_channel

    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            return ch

    return None


# Istanza singleton dei servizi
news_service = UOAddictsNewsService()
builds_service = UOAddictsBuildsService()
