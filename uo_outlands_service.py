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
4. Official Patches Monitor & Restart Alerts: monitoraggio continuo del forum ufficiale
   delle patch (https://forums.uooutlands.com/index.php?forums/patches.14/), estrazione
   delle note di rilascio (RSS + HTML), rilevamento intelligente dei riavvii/manutenzioni
   del server e pubblicazione immediata con allerta prioritaria e annuncio vocale da vocalist.
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
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import aiohttp
import discord

logger = logging.getLogger("Franchino.UOOutlands")

# Directory e file di cache
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
NEWS_CACHE_FILE = os.path.join(DATA_DIR, "uoaddicts_news_cache.json")
BUILDS_CACHE_FILE = os.path.join(DATA_DIR, "uoaddicts_builds_cache.json")
PATCHES_CACHE_FILE = os.path.join(DATA_DIR, "uo_patches_cache.json")

# URL Forum Patches UO Outlands
UO_PATCHES_FORUM_URL = "https://forums.uooutlands.com/index.php?forums/patches.14/"
UO_PATCHES_RSS_URL = "https://forums.uooutlands.com/index.php?forums/patches.14/index.rss"

# Fusi orari
TZ_ET = ZoneInfo("America/New_York")
TZ_ROME = ZoneInfo("Europe/Rome")

# Parametri Server UO Outlands
UO_SERVER_HOST = "play.uooutlands.com"
UO_SERVER_IP = "74.91.113.38"
UO_SERVER_PORT = 2593
UO_SERVER_LOCATION = "Ashburn, West Virginia (USA)"
UO_SERVER_TIMEOUT = 3.0
UO_PORTAL_STATUS_URL = "https://portal.uooutlands.com/server/status"

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

# Vendor Search Portal Outlands
UO_PORTAL_VENDOR_SEARCH_URL = "https://portal.uooutlands.com/vendor-search"
VOICE_VENDOR_SEARCH_REPLY = (
    "Ragazzi, ho cercato sul vendor search! Trovate tutti i prezzi e i venditori in chat!"
)


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
    portal_url: str = UO_PORTAL_STATUS_URL
    portal_online: Optional[bool] = None

    def build_embed(self) -> discord.Embed:
        if self.online:
            embed = discord.Embed(
                title="🌐 UO Outlands — Server Status: ONLINE 🟢",
                url=self.portal_url,
                description="Il server ufficiale di **Ultima Online Outlands** è attivo e raggiungibile!",
                color=COLOR_ONLINE,
            )
            embed.add_field(name="🟢 Stato Game Shard", value="`ONLINE`", inline=True)
            lat_str = f"`{self.latency_ms:.1f} ms`" if self.latency_ms is not None else "`< 1 ms`"
            embed.add_field(name="⚡ Ping / Latenza", value=lat_str, inline=True)
            embed.add_field(name="🔌 Porta TCP", value=f"`{self.port}`", inline=True)
            embed.add_field(name="📡 Login / DNS", value=f"`{self.host}`", inline=True)
            embed.add_field(name="🖥️ Indirizzo IP", value=f"`{self.ip}`", inline=True)
            embed.add_field(name="📍 Posizione Server", value=f"`{self.location}`", inline=True)
            if self.portal_online is not None:
                p_text = "`ONLINE 🟢`" if self.portal_online else "`NON RAGGIUNGIBILE 🔴`"
                embed.add_field(name="🌐 Portale Web", value=p_text, inline=True)
            embed.add_field(
                name="🔗 Portale Ufficiale Status",
                value=f"[Monitora lo stato del server su portal.uooutlands.com]({self.portal_url})",
                inline=False,
            )
            embed.set_footer(text="Franchino Outlands Sentinel • portal.uooutlands.com/server/status")
        else:
            embed = discord.Embed(
                title="🌐 UO Outlands — Server Status: OFFLINE 🔴",
                url=self.portal_url,
                description="⚠️ **Attenzione!** Il server di **Ultima Online Outlands** risulta attualmente non raggiungibile o offline.",
                color=COLOR_OFFLINE,
            )
            embed.add_field(name="🔴 Stato Game Shard", value="`OFFLINE / IRRAGGIUNGIBILE`", inline=True)
            err_msg = self.error if self.error else "Timeout connessione (> 3.0s)"
            embed.add_field(name="⚡ Diagnostica", value=f"`{err_msg[:60]}`", inline=True)
            embed.add_field(name="🔌 Porta TCP", value=f"`{self.port}`", inline=True)
            embed.add_field(name="📡 Login / DNS", value=f"`{self.host}`", inline=True)
            embed.add_field(name="🖥️ Indirizzo IP", value=f"`{self.ip}`", inline=True)
            embed.add_field(name="📍 Posizione Server", value=f"`{self.location}`", inline=True)
            if self.portal_online is not None:
                p_text = "`ONLINE 🟢`" if self.portal_online else "`OFFLINE 🔴`"
                embed.add_field(name="🌐 Portale Web", value=p_text, inline=True)
            embed.add_field(
                name="🔗 Portale Ufficiale Status",
                value=f"[Controlla aggiornamenti e stato su portal.uooutlands.com]({self.portal_url})",
                inline=False,
            )
            embed.set_footer(text="Franchino Outlands Sentinel • portal.uooutlands.com/server/status")
        return embed

    def voice_text(self) -> str:
        if self.online:
            lat = int(self.latency_ms) if self.latency_ms is not None else 50
            return f"Il server di Ultima Online Outlands è online con una latenza di {lat} millisecondi! Ho mandato anche il link del portale ufficiale in chat!"
        return "Attenzione! Il server di Ultima Online Outlands risulta offline o irraggiungibile! Controlla il portale ufficiale al link in chat!"


@dataclass
class UOPatchItem:
    id: str
    title: str
    url: str
    published: str
    content: str
    mentions_restart: bool = False
    restart_details: str = ""
    author: str = "Outlands Staff"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "content": self.content,
            "mentions_restart": self.mentions_restart,
            "restart_details": self.restart_details,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UOPatchItem":
        return cls(
            id=str(data.get("id", "")),
            title=data.get("title", ""),
            url=data.get("url", ""),
            published=data.get("published", ""),
            content=data.get("content", ""),
            mentions_restart=data.get("mentions_restart", False),
            restart_details=data.get("restart_details", ""),
            author=data.get("author", "Outlands Staff"),
        )

    def voice_text(self) -> str:
        """Restituisce il testo di sintesi vocale da pronunciare tramite TTS."""
        if self.mentions_restart:
            return (
                f"Attenzione ragazzi! L'ultima patch pubblicata, {self.title}, "
                f"cita un riavvio del server! Dettagli e note in chat!"
            )
        return (
            f"L'ultima patch pubblicata per Outlands è {self.title}. "
            f"Non ci sono riavvii fissi giornalieri alle diciassette: il server si riavvia solo al rilascio delle patch!"
        )

    def build_embed(self) -> discord.Embed:
        """Restituisce l'embed discord formattato per questa patch."""
        return build_patch_embed(self)


@dataclass
class VendorSearchItem:
    item_name: str
    price: int
    amount: int = 1
    vendor_name: str = ""
    owner_name: str = ""
    location_x: Optional[int] = None
    location_y: Optional[int] = None
    location_z: Optional[int] = None
    vendor_serial: Optional[int] = None
    last_synced: Optional[str] = None
    properties: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "item_name": self.item_name,
            "price": self.price,
            "amount": self.amount,
            "vendor_name": self.vendor_name,
            "owner_name": self.owner_name,
            "location_x": self.location_x,
            "location_y": self.location_y,
            "location_z": self.location_z,
            "vendor_serial": self.vendor_serial,
            "last_synced": self.last_synced,
            "properties": self.properties,
        }


@dataclass
class VendorSearchResult:
    query: str
    has_credentials: bool = True
    items: List[VendorSearchItem] = field(default_factory=list)
    total_found: int = 0
    error_message: Optional[str] = None
    portal_url: str = UO_PORTAL_VENDOR_SEARCH_URL

    def build_embed(self) -> discord.Embed:
        return build_vendor_search_embed(self)


FALLBACK_PATCH_ITEMS = [
    UOPatchItem(
        id="6370",
        title="Patch Notes for October 12, 2026",
        url="https://forums.uooutlands.com/index.php?threads/patch-notes-for-october-12-2026.6370/",
        published="Tue, 13 Oct 2026 02:14:53 +0000",
        content="Aggiornamento Echoes, bilanciamento abilità e creature. Nessun riavvio automatico delle 17:00 attivo sul server.",
        mentions_restart=False,
        restart_details="Nessun riavvio specifico programmato in questa patch.",
        author="Outlands Staff",
    )
]


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

async def check_portal_status(
    url: str = UO_PORTAL_STATUS_URL,
    timeout: float = 3.0,
) -> bool:
    """Verifica se il portale ufficiale di status (https://portal.uooutlands.com/server/status) è raggiungibile."""
    try:
        html_doc = await fetch_html(url, timeout=timeout)
        return bool(html_doc and len(html_doc.strip()) > 10)
    except Exception as e:
        logger.debug(f"Verifica portale status {url} fallita: {e}")
        return False


async def check_uo_server_status(
    host: str = UO_SERVER_HOST,
    fallback_ip: str = UO_SERVER_IP,
    port: int = UO_SERVER_PORT,
    timeout: float = UO_SERVER_TIMEOUT,
    check_portal: bool = False,
) -> ServerStatusResult:
    """
    Verifica se il server UO Outlands è UP tramite controllo socket TCP asincrono (asyncio.open_connection)
    su play.uooutlands.com (fallback IP 74.91.113.38) su porta 2593 con timeout di 3 secondi.
    Calcola la latenza ping in millisecondi.
    Se check_portal=True, verifica anche lo stato del portale ufficiale (https://portal.uooutlands.com/server/status).
    """
    portal_online = None
    if check_portal:
        portal_online = await check_portal_status(timeout=min(timeout, 2.5))
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
            portal_online=portal_online,
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
            portal_online=portal_online,
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
            portal_online=portal_online,
        )
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ==============================================================================
# FEATURE 4: UO OUTLANDS OFFICIAL PATCHES MONITOR & RESTART ALERTS
# ==============================================================================

RESTART_KEYWORDS = [
    "server restart",
    "server reboot",
    "restarting the server",
    "restart the server",
    "reboot the server",
    "taking the server down",
    "server will be down",
    "patch deploy",
    "downtime",
    "maintenance",
    "scheduled restart",
    "scheduled maintenance",
    "server maintenance",
    "restarting",
    "restart",
    "restarted",
    "restarts",
    "reboot",
    "rebooted",
    "reboots",
    "riavvio",
    "riavvii",
    "riavvia",
    "riavviare",
    "riavviato",
    "riavvierà",
    "manutenzione",
]

RESTART_PATTERNS = RESTART_KEYWORDS

SERVER_RESTART_REGEXES = [
    r'\bserver\s+restart(?:s|ed|ing)?\b',
    r'\bserver\s+reboot(?:s|ed|ing)?\b',
    r'\b(?:restarts?|restarted|restarting)\s+(?:the\s+)?server\b',
    r'\b(?:reboots?|rebooted|rebooting)\s+(?:the\s+)?server\b',
    r'\btaking\s+(?:the\s+)?server\s+down\b',
    r'\bserver\s+(?:is\s+|will\s+be\s+)?(?:coming\s+down|down)\b',
    r'\bserver\s+maintenance\b',
    r'\bscheduled\s+(?:maintenance|restart|reboot)\b',
    r'\bmaintenance\s+window\b',
    r'\bserver\s+downtime\b',
    r'\bpatch\s+deploy(?:ment)?\b',
    r'\bshard\s+restart(?:s|ed|ing)?\b',
    r'\bworld\s+save\s+and\s+restart\b',
    r'\brestart\s+scheduled\b',
    r'\b(?:restarts?|restarted|restarting)\s+at\s+\d+',
    r'\b(?:reboots?|rebooted|rebooting)\s+at\s+\d+',
    r'\b(?:downtime|maintenance)\b',
    r'\b(?:riavvi[oia]|riavviare|riavviato|riavvierà|riavvii)\s+(?:del\s+)?server\b',
    r'\bserver\s+si\s+riavvi(?:a|erà)\b',
    r'\bmanutenzion[ei]\b',
]

CLIENT_ONLY_RESTART_PATTERN = (
    r'\b(?:restart(?:s|ed|ing)?\s+(?:your\s+|the\s+)?(?:client|game|launcher|razor|classicuo)|'
    r'(?:client|game|launcher|razor|classicuo)\s+restart(?:s|ed|ing)?)\b'
)

VOICE_ALERT_PATCH_RESTART = (
    "Attenzione ragazzi! È stata annunciata una nuova patch con riavvio del server! "
    "Date un'occhiata alla chat per i dettagli!"
)


def clean_html(raw_html: str) -> str:
    """Rimuove tag HTML, converte break in newline e decodifica le entità HTML."""
    if not raw_html:
        return ""
    text = re.sub(r'<(?:br|br\s*/|/p|/li|/div|/tr)>', '\n', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.split('\n')]
    return '\n'.join([line for line in lines if line]).strip()


def detect_restart_mention(title: str, text: str) -> Tuple[bool, str]:
    """
    Analizza il titolo e il contenuto del post/thread per verificare se viene citato un riavvio,
    reboot, manutenzione o downtime del server.
    Filtra i falsi positivi relativi ai soli riavvii del client di gioco / launcher.
    Ritorna (True, estratto_dettagli) se trovato, altrimenti (False, "").
    """
    combined = f"{title}\n{text}".strip()
    if not combined:
        return False, ""

    matched_pattern: Optional[str] = None

    # 1. Controlla i pattern specifici di riavvio / manutenzione del server
    for pat in SERVER_RESTART_REGEXES:
        if re.search(pat, combined, re.IGNORECASE):
            matched_pattern = pat
            break

    # 2. Se non ha fatto match sui pattern specifici, controlla termini generici escludendo client restart
    if not matched_pattern:
        general_restart = r'\b(?:restarts?|restarted|restarting|reboots?|rebooted|rebooting|riavvi[oia]|riavviare|riavviato|riavvierà|riavvii)\b'
        if re.search(general_restart, combined, re.IGNORECASE):
            stripped = re.sub(CLIENT_ONLY_RESTART_PATTERN, '', combined, flags=re.IGNORECASE)
            if re.search(general_restart, stripped, re.IGNORECASE):
                matched_pattern = general_restart

    # 3. Controlla anche la lista RESTART_KEYWORDS per retrocompatibilità
    if not matched_pattern:
        for kw in RESTART_KEYWORDS:
            pat = r'\b' + re.escape(kw) + r'\b'
            if re.search(pat, combined, re.IGNORECASE):
                if kw in ("restart", "restarting", "reboot", "rebooting"):
                    stripped = re.sub(CLIENT_ONLY_RESTART_PATTERN, '', combined, flags=re.IGNORECASE)
                    if not re.search(pat, stripped, re.IGNORECASE):
                        continue
                matched_pattern = pat
                break

    if not matched_pattern:
        return False, ""

    # Cerca la riga o frase più informativa che contiene il riferimento al riavvio
    lines = text.split('\n')
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        if re.search(CLIENT_ONLY_RESTART_PATTERN, line_clean, re.IGNORECASE):
            stripped_line = re.sub(CLIENT_ONLY_RESTART_PATTERN, '', line_clean, flags=re.IGNORECASE)
            if not re.search(matched_pattern, stripped_line, re.IGNORECASE):
                continue
        if re.search(matched_pattern, line_clean, re.IGNORECASE):
            snippet = line_clean[:280] + ("..." if len(line_clean) > 280 else "")
            return True, snippet

    # Se menzionato nel titolo
    if re.search(matched_pattern, title, re.IGNORECASE):
        return True, f"Riavvio o manutenzione indicati direttamente nel titolo dell'annuncio: '{title}'."

    snippet = text[:250].strip() + ("..." if len(text) > 250 else "")
    return True, snippet or "Riavvio del server annunciato per il deploy della patch."


def parse_patches_rss(xml_content: str) -> List[UOPatchItem]:
    """Parsa il feed RSS ufficiale XenForo da https://forums.uooutlands.com/index.php?forums/patches.14/index.rss."""
    items: List[UOPatchItem] = []
    if not xml_content or not xml_content.strip():
        return items

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_content)
        channel = root.find("channel")
        if channel is not None:
            for it in channel.findall("item"):
                title_elem = it.find("title")
                title = html.unescape(title_elem.text.strip()) if title_elem is not None and title_elem.text else "Nuova Patch UO Outlands"

                link_elem = it.find("link")
                link = link_elem.text.strip() if link_elem is not None and link_elem.text else UO_PATCHES_FORUM_URL

                guid_elem = it.find("guid")
                guid = guid_elem.text.strip() if guid_elem is not None and guid_elem.text else ""
                if not guid:
                    m_id = re.search(r'\.(\d+)/?$', link)
                    guid = m_id.group(1) if m_id else link

                pub_elem = it.find("pubDate")
                pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""

                creator_elem = it.find("{http://purl.org/dc/elements/1.1/}creator")
                author = creator_elem.text.strip() if creator_elem is not None and creator_elem.text else "Outlands Staff"

                content_elem = it.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                desc_elem = it.find("description")
                raw_content = ""
                if content_elem is not None and content_elem.text:
                    raw_content = content_elem.text
                elif desc_elem is not None and desc_elem.text:
                    raw_content = desc_elem.text

                clean_desc = clean_html(raw_content)
                mentions_restart, restart_details = detect_restart_mention(title, clean_desc)

                items.append(
                    UOPatchItem(
                        id=str(guid),
                        title=title,
                        url=link,
                        published=pub_date,
                        content=clean_desc,
                        mentions_restart=mentions_restart,
                        restart_details=restart_details,
                        author=author,
                    )
                )
    except Exception as e:
        logger.debug(f"Parsing XML standard fallito ({e}), tento fallback regex per RSS...")
        item_blocks = re.findall(r'<item>(.*?)</item>', xml_content, re.DOTALL)
        for block in item_blocks:
            t_m = re.search(r'<title>(.*?)</title>', block, re.DOTALL)
            title = html.unescape(re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', t_m.group(1))).strip() if t_m else "Nuova Patch"
            l_m = re.search(r'<link>(.*?)</link>', block)
            link = l_m.group(1).strip() if l_m else UO_PATCHES_FORUM_URL
            g_m = re.search(r'<guid[^>]*>(.*?)</guid>', block)
            guid = g_m.group(1).strip() if g_m else link
            p_m = re.search(r'<pubDate>(.*?)</pubDate>', block)
            pub_date = p_m.group(1).strip() if p_m else ""
            c_m = re.search(r'<content:encoded>(.*?)</content:encoded>', block, re.DOTALL) or re.search(r'<description>(.*?)</description>', block, re.DOTALL)
            raw_c = c_m.group(1) if c_m else ""
            raw_c = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', raw_c)
            clean_desc = clean_html(raw_c)
            mentions_restart, restart_details = detect_restart_mention(title, clean_desc)
            items.append(
                UOPatchItem(
                    id=str(guid),
                    title=title,
                    url=link,
                    published=pub_date,
                    content=clean_desc,
                    mentions_restart=mentions_restart,
                    restart_details=restart_details,
                    author="Outlands Staff",
                )
            )

    return items


def extract_thread_first_post(html_text: str) -> str:
    """Estrae il corpo del messaggio principale (primo post) di un thread XenForo."""
    if not html_text:
        return ""
    m = re.search(r'<article\s+class="message-body[^>]*>(.*?)</article>', html_text, re.DOTALL)
    if m:
        raw_article = m.group(1)
        raw_article = re.sub(r'<script[^>]*>.*?</script>', '', raw_article, flags=re.DOTALL)
        return clean_html(raw_article)
    m_bb = re.search(r'<div\s+class="bbWrapper">(.*?)</div>', html_text, re.DOTALL)
    if m_bb:
        return clean_html(m_bb.group(1))
    return ""


def parse_patches_html(html_text: str) -> List[UOPatchItem]:
    """Parsa i thread della sezione Patch Notes da https://forums.uooutlands.com/index.php?forums/patches.14/."""
    items: List[UOPatchItem] = []
    if not html_text:
        return items

    # Cerca i blocchi structItem o structItem-cell--main
    cell_pattern = r'<div\s+class="[^"]*structItem-cell--main[^"]*"[^>]*>(.*?)(?=<div\s+class="[^"]*structItem-cell|$)'
    cells = re.findall(cell_pattern, html_text, re.DOTALL)
    if not cells:
        # Fallback se non ci sono structItem-cell--main: split su structItem-title
        cells = [m.group(0) for m in re.finditer(r'<div\s+class="structItem-title"[^>]*>.*?(?=<div\s+class="structItem-title"|$)', html_text, re.DOTALL)]

    seen_urls = set()
    for block in cells:
        t_match = re.search(r'<a\s+href="(/index\.php\?threads/[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not t_match:
            continue
        rel_url = t_match.group(1)
        raw_title = t_match.group(2)
        clean_title = html.unescape(re.sub(r'<[^>]+>', '', raw_title)).strip()
        if not clean_title or "prefix_id=" in rel_url:
            continue

        full_url = "https://forums.uooutlands.com" + rel_url
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        m_id = re.search(r'\.(\d+)/?$', rel_url)
        thread_id = m_id.group(1) if m_id else rel_url

        a_m = re.search(r'<a\s+href="[^"]*members/[^"]*"[^>]*>(?:<span[^>]*>)?([^<]+)', block)
        author = a_m.group(1).strip() if a_m else "Outlands Staff"

        d_m = re.search(r'<time[^>]*datetime="([^"]+)"', block) or re.search(r'<time[^>]*title="([^"]+)"', block)
        pub_date = d_m.group(1).strip() if d_m else ""

        mentions_restart, restart_details = detect_restart_mention(clean_title, "")
        items.append(
            UOPatchItem(
                id=str(thread_id),
                title=clean_title,
                url=full_url,
                published=pub_date,
                content=clean_title,
                mentions_restart=mentions_restart,
                restart_details=restart_details,
                author=author,
            )
        )
    return items


class UOPatchMonitorService:
    """Monitora il forum ufficiale delle patch di UO Outlands con cache persistente per rilevare nuovi annunci e riavvii."""

    def __init__(self, cache_file: str = PATCHES_CACHE_FILE):
        self.cache_file = cache_file
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        except Exception:
            pass

    def load_cache(self) -> dict:
        if not os.path.exists(self.cache_file):
            return {"seen_ids": [], "latest_patch": None, "latest_restart_patch": None, "last_updated": 0}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"seen_ids": [], "latest_patch": None, "latest_restart_patch": None, "last_updated": 0}
        except Exception as e:
            logger.warning(f"Errore lettura cache patch {self.cache_file}: {e}")
            return {"seen_ids": [], "latest_patch": None, "latest_restart_patch": None, "last_updated": 0}

    def save_cache(self, data: dict) -> None:
        self._ensure_cache_dir()
        try:
            _atomic_json_dump(self.cache_file, data)
        except Exception as e:
            logger.warning(f"Errore salvataggio cache patch: {e}")

    async def fetch_patches(self) -> List[UOPatchItem]:
        """Scarica e parsa la lista delle patch ufficiali provando prima RSS e poi HTML."""
        # Tentativo 1: Feed RSS ufficiale XenForo
        try:
            content = await fetch_html(UO_PATCHES_RSS_URL)
            items = parse_patches_rss(content)
            if items:
                return items
        except Exception as e:
            logger.debug(f"Download RSS patches fallito ({e}), provo scraping HTML...")

        # Tentativo 2: Scraping HTML
        try:
            html_text = await fetch_html(UO_PATCHES_FORUM_URL)
            items = parse_patches_html(html_text)
            if items:
                return items
        except Exception as e:
            logger.error(f"Download forum patches fallito: {e}")

        return []

    async def enrich_patch_content_if_needed(self, patch: UOPatchItem) -> UOPatchItem:
        """
        Se la patch non menziona il riavvio ma ha contenuto troncato da feed RSS ('Read more' / breve)
        oppure ottenuto da HTML con solo titolo, scarica il thread completo per analizzare l'intero post.
        """
        if patch.mentions_restart:
            return patch

        is_truncated = (
            "Read more" in patch.content
            or len(patch.content) <= 500
            or patch.content == patch.title
            or not patch.content
        )
        if is_truncated and patch.url and "uooutlands.com/index.php?threads/" in patch.url:
            try:
                thread_html = await fetch_html(patch.url)
                full_body = extract_thread_first_post(thread_html)
                if full_body:
                    patch.content = full_body
                    m_restart, r_details = detect_restart_mention(patch.title, full_body)
                    if m_restart:
                        patch.mentions_restart = True
                        patch.restart_details = r_details
            except Exception as e:
                logger.debug(f"Impossibile scaricare dettagli thread patch {patch.url}: {e}")
        return patch

    async def check_for_new_patches(self) -> List[UOPatchItem]:
        """
        Controlla se ci sono post non ancora visti.
        Al primo avvio assoluto (cache inesistente) inizializza la cache con le patch attuali
        per evitare spam di post storici, ritornando lista vuota.
        Nelle esecuzioni successive ritorna tutti i post nuovi trovati e aggiorna la cache.
        """
        items = await self.fetch_patches()
        if not items:
            return []

        if not os.path.exists(self.cache_file):
            all_ids = [it.id for it in items]
            cache_data = {
                "seen_ids": all_ids,
                "latest_patch": items[0].to_dict() if items else None,
                "latest_restart_patch": next((it.to_dict() for it in items if it.mentions_restart), None),
                "last_updated": time.time(),
            }
            self.save_cache(cache_data)
            logger.info(f"🛠️ Inizializzata cache patch Outlands con {len(all_ids)} thread pregressi.")
            return []

        cache_data = self.load_cache()
        seen_ids = set(cache_data.get("seen_ids", []))
        new_items = [it for it in items if it.id not in seen_ids]

        if new_items:
            for it in new_items:
                seen_ids.add(it.id)
                if not it.mentions_restart:
                    await self.enrich_patch_content_if_needed(it)

            cache_data["seen_ids"] = sorted(list(seen_ids))
            cache_data["latest_patch"] = items[0].to_dict()
            for it in items:
                if it.mentions_restart:
                    cache_data["latest_restart_patch"] = it.to_dict()
                    break
            cache_data["last_updated"] = time.time()
            self.save_cache(cache_data)

        return new_items

    async def get_latest_patch(self) -> UOPatchItem:
        """Recupera l'ultima patch (dalla rete o fallback/cache)."""
        items = await self.fetch_patches()
        if items:
            latest = items[0]
            if not latest.mentions_restart:
                await self.enrich_patch_content_if_needed(latest)
            try:
                cache_data = self.load_cache()
                cache_data["latest_patch"] = latest.to_dict()
                if latest.mentions_restart:
                    cache_data["latest_restart_patch"] = latest.to_dict()
                if not cache_data.get("seen_ids"):
                    cache_data["seen_ids"] = [it.id for it in items]
                cache_data["last_updated"] = time.time()
                self.save_cache(cache_data)
            except Exception:
                pass
            return latest

        cache_data = self.load_cache()
        latest_dict = cache_data.get("latest_patch")
        if latest_dict:
            return UOPatchItem.from_dict(latest_dict)
        return FALLBACK_PATCH_ITEMS[0]

    async def get_latest_restart_patch(self) -> Optional[UOPatchItem]:
        """Recupera la patch più recente che cita espressamente un riavvio del server."""
        items = await self.fetch_patches()
        for idx, item in enumerate(items):
            if not item.mentions_restart and idx < 3:
                await self.enrich_patch_content_if_needed(item)
            if item.mentions_restart:
                return item
        cache_data = self.load_cache()
        restart_dict = cache_data.get("latest_restart_patch")
        if restart_dict:
            return UOPatchItem.from_dict(restart_dict)
        latest_dict = cache_data.get("latest_patch")
        if latest_dict and latest_dict.get("mentions_restart"):
            return UOPatchItem.from_dict(latest_dict)
        return None


def build_patch_restart_alert_embed(patch: UOPatchItem) -> discord.Embed:
    """Discord Embed prioritario ed elegante quando viene pubblicata una nuova patch che cita un riavvio."""
    embed = discord.Embed(
        title=f"⚠️ Riavvio Server per Nuova Patch — {patch.title}",
        url=patch.url,
        description=(
            f"È stata annunciata una nuova patch ufficiale su **UO Outlands** che richiede il **riavvio del server**!\n\n"
            f"**Estratto & Dettagli Manutenzione**:\n"
            f"> {patch.restart_details or patch.content[:300]}\n"
        ),
        color=COLOR_WARNING,
    )
    embed.add_field(name="📢 Stato Riavvio", value="⚠️ `RIAVVIO / MANUTENZIONE PREVISTA`", inline=True)
    if patch.published:
        embed.add_field(name="📅 Data Annuncio", value=f"`{patch.published[:25]}`", inline=True)
    embed.add_field(
        name="🔗 Note di Rilascio Complete",
        value=f"[Leggi il thread completo sul Forum UO Outlands]({patch.url})",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Raccomandazione Avventurieri",
        value="Portate al sicuro il vostro personaggio in locanda o safe zone prima dell'inizio del downtime!",
        inline=False,
    )
    embed.set_footer(text="Franchino Patch Sentinel • forums.uooutlands.com/patches")
    return embed


def build_patch_embed(patch: UOPatchItem) -> discord.Embed:
    """Discord Embed per la visualizzazione dell'ultima patch annunciata (usato da /riavvio e /patch)."""
    if patch.mentions_restart:
        embed = discord.Embed(
            title=f"🛠️ UO Outlands — Ultima Patch: {patch.title}",
            url=patch.url,
            description="Informazioni sull'ultima patch pubblicata sul forum ufficiale di Ultima Online Outlands:",
            color=COLOR_WARNING,
        )
        embed.add_field(
            name="⚠️ Riavvio Server",
            value=f"⚠️ `RIAVVIO CITATO NELL'ANNUNCIO`\n> {patch.restart_details or 'Riavvio programmato per applicazione della patch.'}",
            inline=False,
        )
    else:
        embed = discord.Embed(
            title=f"🛠️ UO Outlands — Ultima Patch: {patch.title}",
            url=patch.url,
            description="Informazioni sull'ultima patch pubblicata sul forum ufficiale di Ultima Online Outlands:",
            color=COLOR_INFO,
        )
        embed.add_field(
            name="🔄 Riavvio Server",
            value="ℹ️ *Nessun riavvio automatico delle 17:00 attivo.* Il server si riavvia solo al deploy di nuove patch che lo richiedono espressamente.",
            inline=False,
        )

    if patch.content:
        snippet = patch.content[:350].strip()
        if len(patch.content) > 350:
            snippet += "..."
        embed.add_field(name="📝 Note & Novità", value=f"> {snippet}", inline=False)

    if patch.published:
        embed.add_field(name="📅 Pubblicazione", value=f"`{patch.published[:25]}`", inline=True)
    if patch.author:
        embed.add_field(name="✍️ Autore", value=f"`{patch.author}`", inline=True)
    embed.add_field(name="🔗 Discussione Ufficiale", value=f"[Apri thread sul Forum Outlands]({patch.url})", inline=False)
    embed.set_footer(text="Franchino Outlands Patch Sentinel • forums.uooutlands.com/patches")
    return embed


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
patch_service = UOPatchMonitorService()

# Aliases per retrocompatibilità e flessibilità di naming
PatchesService = UOPatchMonitorService
PatchItem = UOPatchItem
patches_service = patch_service


async def get_latest_patch() -> UOPatchItem:
    """Recupera l'ultima patch tramite il servizio patch."""
    return await patch_service.get_latest_patch()


async def get_latest_restart_patch() -> Optional[UOPatchItem]:
    """Recupera l'ultima patch che cita un riavvio tramite il servizio patch."""
    return await patch_service.get_latest_restart_patch()


# ==============================================================================
# FEATURE 5: UO OUTLANDS PORTAL VENDOR SEARCH SERVICE
# ==============================================================================

class VendorSearchService:
    """Gestisce la ricerca prezzi e articoli sui Player Vendor di UO Outlands tramite portal.uooutlands.com."""

    def __init__(self):
        self._jwt_token: Optional[str] = None
        self._jwt_expiry: float = 0.0
        self._auth_lock = asyncio.Lock()
        self._cache: Dict[str, Tuple[VendorSearchResult, float]] = {}
        self._cache_ttl = 300.0  # 5 minuti di cache per evitare spam e rate limit 429
        self._rate_limited_until: float = 0.0

    @property
    def email(self) -> str:
        return (os.getenv("OUTLANDS_PORTAL_EMAIL") or "").strip()

    @property
    def password(self) -> str:
        return (os.getenv("OUTLANDS_PORTAL_PASSWORD") or "").strip()

    @property
    def has_credentials(self) -> bool:
        return bool(self.email and self.password)

    async def authenticate(self, force: bool = False) -> Optional[str]:
        """Esegue il login su portal.uooutlands.com/api/user/login e memorizza il token JWT."""
        if not self.has_credentials:
            return None

        async with self._auth_lock:
            now = time.monotonic()
            if not force and self._jwt_token and now < self._jwt_expiry:
                return self._jwt_token

            if now < self._rate_limited_until:
                logger.warning("Vendor search in cooldown per rate-limit.")
                return None

            login_url = "https://portal.uooutlands.com/api/user/login"
            payload = {"email": self.email, "password": self.password}
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://portal.uooutlands.com",
                "Referer": "https://portal.uooutlands.com/login",
            }

            try:
                timeout = aiohttp.ClientTimeout(total=10.0)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(login_url, json=payload, headers=headers) as resp:
                        if resp.status == 429:
                            self._rate_limited_until = time.monotonic() + 60.0
                            logger.warning("Login portal.uooutlands.com ha risposto 429 Too Many Requests.")
                            return None
                        if resp.status == 200:
                            data = await resp.json()
                            token = data.get("jwt") or data.get("token") or data.get("temporaryToken")
                            if token:
                                self._jwt_token = token
                                self._jwt_expiry = time.monotonic() + (3600.0 * 12)
                                logger.info("Autenticazione riuscita su portal.uooutlands.com!")
                                return self._jwt_token
                            else:
                                logger.warning(f"Login riuscito ma nessun token trovato nella risposta: {data}")
                        else:
                            text = await resp.text()
                            logger.warning(f"Login portal fallito ({resp.status}): {text[:150]}")
            except Exception as e:
                logger.warning(f"Errore connessione login portal.uooutlands.com: {e}")

            return None

    async def search_vendor(self, item_name: str, max_results: int = 5) -> VendorSearchResult:
        """Cerca un oggetto sui player vendor di UO Outlands."""
        clean_query = item_name.strip()
        cache_key = clean_query.lower()
        now = time.monotonic()

        # Controllo cache in memoria (5 minuti TTL)
        if cache_key in self._cache:
            res, ts = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return res

        portal_direct_url = f"https://portal.uooutlands.com/vendor-search"

        # Se le credenziali non sono configurate nel file .env
        if not self.has_credentials:
            res = VendorSearchResult(
                query=clean_query,
                has_credentials=False,
                items=[],
                total_found=0,
                portal_url=portal_direct_url
            )
            self._cache[cache_key] = (res, now)
            return res

        token = await self.authenticate()
        if not token:
            res = VendorSearchResult(
                query=clean_query,
                has_credentials=False,
                items=[],
                total_found=0,
                error_message="Impossibile autenticarsi sul portale con le credenziali fornite.",
                portal_url=portal_direct_url
            )
            return res

        search_api = "https://portal.uooutlands.com/api/VendorSearch/Search"
        payload = {
            "page": 0,
            "pageSize": max_results,
            "sortName": "Price",
            "sortAscending": True,
            "filterParams": {
                "name": clean_query,
                "category": "All",
                "propertyFilters": []
            }
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://portal.uooutlands.com",
            "Referer": "https://portal.uooutlands.com/vendor-search",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=10.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(search_api, json=payload, headers=headers) as resp:
                    if resp.status == 401:
                        # Token scaduto, ritenta forzando il login
                        token = await self.authenticate(force=True)
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                            async with session.post(search_api, json=payload, headers=headers) as retry_resp:
                                if retry_resp.status == 200:
                                    data = await retry_resp.json()
                                    result = self._parse_search_data(clean_query, data, portal_direct_url, max_results)
                                    self._cache[cache_key] = (result, time.monotonic())
                                    return result
                        return VendorSearchResult(
                            query=clean_query,
                            has_credentials=True,
                            error_message="Sessione scaduta o non autorizzata sul portale.",
                            portal_url=portal_direct_url
                        )

                    if resp.status == 429:
                        self._rate_limited_until = time.monotonic() + 60.0
                        return VendorSearchResult(
                            query=clean_query,
                            has_credentials=True,
                            error_message="Rate limit raggiunto sul portale di Outlands (HTTP 429). Riprova tra poco.",
                            portal_url=portal_direct_url
                        )

                    if resp.status == 200:
                        data = await resp.json()
                        result = self._parse_search_data(clean_query, data, portal_direct_url, max_results)
                        self._cache[cache_key] = (result, time.monotonic())
                        return result
                    else:
                        text = await resp.text()
                        return VendorSearchResult(
                            query=clean_query,
                            has_credentials=True,
                            error_message=f"Errore dal server del portale ({resp.status}): {text[:100]}",
                            portal_url=portal_direct_url
                        )
        except Exception as e:
            logger.error(f"Errore chiamata Vendor Search per '{clean_query}': {e}")
            return VendorSearchResult(
                query=clean_query,
                has_credentials=True,
                error_message=f"Errore di rete durante la ricerca: {e}",
                portal_url=portal_direct_url
            )

    def _parse_search_data(self, query: str, data: Any, portal_url: str, max_results: int) -> VendorSearchResult:
        """Estrae gli oggetti VendorSearchItem dalla risposta JSON del portale."""
        raw_items = []
        total = 0
        if isinstance(data, dict):
            raw_items = data.get("items") or data.get("data") or []
            total = data.get("total") or data.get("totalCount") or len(raw_items)
        elif isinstance(data, list):
            raw_items = data
            total = len(raw_items)

        items: List[VendorSearchItem] = []
        for ri in raw_items[:max_results]:
            if not isinstance(ri, dict):
                continue
            vendor_obj = ri.get("vendor") or {}
            item_name = ri.get("name") or ri.get("itemName") or ri.get("title") or query
            price = int(ri.get("price") or ri.get("lowestPrice") or ri.get("totalPrice") or 0)
            amount = int(ri.get("totalAmount") or ri.get("amount") or ri.get("count") or 1)
            v_name = vendor_obj.get("vendorName") or vendor_obj.get("name") or ri.get("vendorName") or "Vendor sconosciuto"
            owner = vendor_obj.get("ownerCharName") or vendor_obj.get("ownerName") or ri.get("ownerCharName") or "Sconosciuto"
            lx = vendor_obj.get("locationX") or ri.get("vendorLocationX")
            ly = vendor_obj.get("locationY") or ri.get("vendorLocationY")
            lz = vendor_obj.get("locationZ") or ri.get("vendorLocationZ")
            v_serial = ri.get("vendorSerial") or vendor_obj.get("serial")
            last_sync = vendor_obj.get("lastSyncedAt") or ri.get("lastSyncedAt")

            items.append(VendorSearchItem(
                item_name=item_name,
                price=price,
                amount=amount,
                vendor_name=v_name,
                owner_name=owner,
                location_x=int(lx) if lx is not None else None,
                location_y=int(ly) if ly is not None else None,
                location_z=int(lz) if lz is not None else None,
                vendor_serial=int(v_serial) if v_serial is not None else None,
                last_synced=str(last_sync) if last_sync else None,
            ))

        return VendorSearchResult(
            query=query,
            has_credentials=True,
            items=items,
            total_found=total,
            portal_url=portal_url
        )


def build_vendor_search_embed(result: VendorSearchResult) -> discord.Embed:
    """Costruisce un Discord Embed elegante per i risultati di ricerca sui vendor del portale."""
    if not result.has_credentials:
        embed = discord.Embed(
            title=f"🛒 UO Outlands Vendor Search — {result.query}",
            url=result.portal_url,
            description=(
                f"Franchino può cercare in tempo reale prezzi, coordinate e venditori sul server ufficiale di Outlands!\n\n"
                f"⚠️ **Autenticazione Richiesta**: Il portale Outlands richiede un account per visualizzare i vendor.\n"
                f"Per abilitare le ricerche live automatiche nel bot, configura nel file `.env`:\n"
                f"```env\n"
                f"OUTLANDS_PORTAL_EMAIL=tua_email@dominio.com\n"
                f"OUTLANDS_PORTAL_PASSWORD=tua_password\n"
                f"```\n"
                f"🔗 Puoi consultare i vendor per **{result.query}** direttamente sul portale web:\n"
                f"**[Apri Vendor Search su portal.uooutlands.com]({result.portal_url})**"
            ),
            color=COLOR_WARNING,
        )
        embed.set_footer(text="Franchino Outlands • portal.uooutlands.com/vendor-search")
        return embed

    if result.error_message:
        embed = discord.Embed(
            title=f"🛒 UO Outlands Vendor Search — {result.query}",
            url=result.portal_url,
            description=(
                f"⚠️ **Attenzione**: {result.error_message}\n\n"
                f"🔗 Puoi consultare manualmente i prezzi su: **[portal.uooutlands.com]({result.portal_url})**"
            ),
            color=COLOR_OFFLINE,
        )
        embed.set_footer(text="Franchino Outlands • portal.uooutlands.com/vendor-search")
        return embed

    if not result.items:
        embed = discord.Embed(
            title=f"🛒 UO Outlands Vendor Search — {result.query}",
            url=result.portal_url,
            description=(
                f"❌ Nessun player vendor sta attualmente vendendo **\"{result.query}\"**, "
                f"oppure l'oggetto è temporaneamente esaurito.\n\n"
                f"💡 *Suggerimento: prova a cercare con un nome parziale o in inglese (es. 'aspect core', 'scroll', 'ingot').*\n\n"
                f"🔗 **[Verifica sul portale portal.uooutlands.com]({result.portal_url})**"
            ),
            color=COLOR_INFO,
        )
        embed.set_footer(text="Franchino Outlands • portal.uooutlands.com/vendor-search")
        return embed

    embed = discord.Embed(
        title=f"🛒 Prezzi Vendor Outlands: {result.query}",
        url=result.portal_url,
        description=f"Trovati venditori con i migliori prezzi (ordinati dal più basso in **GP**):",
        color=COLOR_ONLINE,
    )

    for i, item in enumerate(result.items, 1):
        coord_str = f" • 📍 `(X: {item.location_x}, Y: {item.location_y})`" if (item.location_x is not None and item.location_y is not None) else ""
        qty_str = f" `x{item.amount}`" if item.amount > 1 else ""
        vendor_link = f"https://portal.uooutlands.com/vendor-search/vendor/{item.vendor_serial}" if item.vendor_serial else result.portal_url

        field_title = f"#{i} 🏷️ {item.price:,} GP{qty_str} — {item.item_name}"
        field_value = (
            f"🏪 Vendor: **[{item.vendor_name}]({vendor_link})**{coord_str}\n"
            f"👤 Proprietario: *{item.owner_name}*"
        )
        embed.add_field(name=field_title, value=field_value, inline=False)

    embed.set_footer(text=f"Franchino Outlands Sentinel • portal.uooutlands.com/vendor-search • Totale trovati: {result.total_found}")
    return embed


# Istanza singleton del servizio Vendor Search
vendor_search_service = VendorSearchService()


async def search_vendor(item_name: str, max_results: int = 5) -> VendorSearchResult:
    """Cerca un oggetto sui player vendor di Outlands tramite il servizio dedicato."""
    return await vendor_search_service.search_vendor(item_name, max_results=max_results)

