import asyncio
import os
import re
import urllib.request
import json
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Union
import discord
import requests
import yt_dlp

from config import (
    NODE_PATH,
    SPOTIPY_CLIENT_ID,
    SPOTIPY_CLIENT_SECRET,
    COLOR_FRANCHINO,
    COLOR_SPOTIFY,
    COLOR_YOUTUBE
)

logger = logging.getLogger("Franchino.Sources")


def format_duration(seconds: Optional[int]) -> str:
    """Formatta i secondi in stringa leggibile MM:SS o HH:MM:SS."""
    if not seconds or seconds <= 0:
        return "Live / N/D"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class SongInfo:
    """Rappresenta una traccia audio nella coda di Franchino."""
    title: str
    webpage_url: str
    stream_url: Optional[str] = None
    duration: int = 0
    thumbnail: Optional[str] = None
    uploader: Optional[str] = None
    requester: Optional[Union[discord.Member, discord.User, str]] = None
    source_type: str = "youtube"  # 'youtube', 'spotify', 'search'
    spotify_url: Optional[str] = None
    search_query: Optional[str] = None
    http_headers: Optional[Dict[str, str]] = None
    is_direct_stream: bool = False

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)

    @property
    def requester_mention(self) -> str:
        if isinstance(self.requester, (discord.Member, discord.User)):
            return self.requester.mention
        return str(self.requester or "Anonimo")

    def create_embed(self, title_prefix: str = "In riproduzione 🎧", color: Optional[int] = None) -> discord.Embed:
        """Crea un embed Discord ricco e formattato per questa traccia."""
        if color is None:
            if self.source_type == "spotify":
                color = COLOR_SPOTIFY
            elif self.source_type == "youtube":
                color = COLOR_YOUTUBE
            elif self.source_type == "sports_stream":
                color = discord.Color.red().value
            else:
                color = COLOR_FRANCHINO

        embed = discord.Embed(
            title=f"{title_prefix}",
            description=f"[{self.title}]({self.webpage_url or self.spotify_url or 'https://youtube.com'})",
            color=color
        )

        embed.add_field(name="⏱️ Durata", value=f"`{self.duration_str}`", inline=True)
        if self.uploader:
            embed.add_field(name="🎤 Canale / Artista", value=f"`{self.uploader}`", inline=True)
        embed.add_field(name="👤 Richiesto da", value=self.requester_mention, inline=True)

        if self.source_type == "spotify":
            badge = "🟢 Spotify"
        elif self.source_type == "sports_stream":
            badge = "⚽ MadPlay77 Live"
        else:
            badge = "🔴 YouTube"
        embed.set_footer(text=f"Franchino DJ • {badge}", icon_url="https://cdn-icons-png.flaticon.com/512/3845/3845868.png")

        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)

        return embed


class SourceManager:
    """Gestisce la ricerca ed estrazione dei flussi audio da YouTube e Spotify con architettura ultra-rapida a bassa latenza."""

    def __init__(self):
        self.sp = None
        if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET:
            try:
                import spotipy
                from spotipy.oauth2 import SpotifyClientCredentials
                auth_manager = SpotifyClientCredentials(
                    client_id=SPOTIPY_CLIENT_ID,
                    client_secret=SPOTIPY_CLIENT_SECRET
                )
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                logger.info("Autenticazione Spotify API inizializzata con successo.")
            except Exception as e:
                logger.warning(f"Errore inizializzazione Spotipy: {e}. Verrà usato il parser integrato.")

        # Cache in memoria per risoluzione query ultra-veloce (TTL 1 ora)
        self._resolution_cache: Dict[str, Tuple[SongInfo, float]] = {}
        # Cache metadati Spotify (TTL 24 ore)
        self._spotify_cache: Dict[str, Tuple[str, str, float]] = {}
        # Cache directory persistente yt-dlp (evita ricaricamento JavaScript decipher player)
        self.ytdlp_cache_dir = "/tmp/franchino_ytdlp_cache"
        try:
            os.makedirs(self.ytdlp_cache_dir, exist_ok=True)
            os.makedirs("/tmp/franchino_cache", exist_ok=True)
        except Exception:
            pass

        # Client YouTube: 'default' usa automaticamente (web_embedded, tv_downgraded, web) quando ci sono i cookie,
        # mentre 'android' e 'visionos' funzionano senza cookie.
        yt_extractor_args = {
            "youtube": {
                "player_client": ["android", "default", "web_embedded"],
            }
        }

        # Opzioni yt-dlp per estrazione ultra-rapida flussi audio (prioritizza Opus 251 e AAC 140 nativi Discord)
        self.ydl_opts_stream = {
            "format": "ba[abr<=160]/251/140/bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "extract_flat": False,
            "nocheckcertificate": True,
            "cachedir": self.ytdlp_cache_dir,
            "concurrent_fragment_downloads": 4,
            "buffersize": 64 * 1024,
            "http_chunk_size": 10485760,
            "remote_components": ["ejs:github"],
            "extractor_args": yt_extractor_args,
            "socket_timeout": 12.0,
        }
        if NODE_PATH:
            self.ydl_opts_stream["js_runtimes"] = {"node": {}}

        # Opzioni yt-dlp per estrazione rapida metadati (senza scaricare flussi audio)
        self.ydl_opts_flat = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "nocheckcertificate": True,
            "cachedir": self.ytdlp_cache_dir,
            "remote_components": ["ejs:github"],
            "extractor_args": yt_extractor_args,
            "socket_timeout": 12.0,
        }
        if NODE_PATH:
            self.ydl_opts_flat["js_runtimes"] = {"node": {}}

        self._configure_cookies()

    def _get_cached_song(self, key: str, requester: Optional[Union[discord.Member, discord.User, str]] = None) -> Optional[SongInfo]:
        """Restituisce un SongInfo dalla cache in memoria se ancora valido e non scaduto."""
        import time as _t
        now = _t.time()
        if key in self._resolution_cache:
            cached_song, ts = self._resolution_cache[key]
            if now - ts < 3600:  # 1 ora TTL
                # Clona l'oggetto per aggiornare il requester corrente
                return SongInfo(
                    title=cached_song.title,
                    webpage_url=cached_song.webpage_url,
                    stream_url=cached_song.stream_url,
                    duration=cached_song.duration,
                    thumbnail=cached_song.thumbnail,
                    uploader=cached_song.uploader,
                    requester=requester or cached_song.requester,
                    source_type=cached_song.source_type,
                    spotify_url=cached_song.spotify_url,
                    search_query=cached_song.search_query,
                    http_headers=cached_song.http_headers,
                    is_direct_stream=cached_song.is_direct_stream,
                )
            else:
                del self._resolution_cache[key]
        return None

    def _cache_song(self, key: str, song: SongInfo):
        """Salva una risoluzione nella cache in memoria (LRU limit a 256 voci)."""
        import time as _t
        if len(self._resolution_cache) > 256:
            # Rimuove le prime 32 voci più vecchie
            oldest_keys = sorted(self._resolution_cache.keys(), key=lambda k: self._resolution_cache[k][1])[:32]
            for ok in oldest_keys:
                self._resolution_cache.pop(ok, None)
        self._resolution_cache[key] = (song, _t.time())
        if song.webpage_url:
            self._resolution_cache[song.webpage_url.strip().lower()] = (song, _t.time())

    def _configure_cookies(self):
        """Rileva cookies.txt e ne crea una copia scrivibile in /tmp per evitare errori di permessi."""
        import shutil
        for cookie_path in ["cookies.txt", "../cookies.txt", "/app/cookies.txt"]:
            if os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 0:
                writable_cookie = "/tmp/franchino_cookies.txt"
                try:
                    shutil.copyfile(cookie_path, writable_cookie)
                    self.ydl_opts_stream["cookiefile"] = writable_cookie
                    self.ydl_opts_flat["cookiefile"] = writable_cookie
                except Exception:
                    self.ydl_opts_stream["cookiefile"] = cookie_path
                    self.ydl_opts_flat["cookiefile"] = cookie_path
                logger.info(f"File cookies rilevato e configurato da: {cookie_path}")
                return

    async def resolve_url_or_query(self, query: str, requester: Optional[Union[discord.Member, discord.User]] = None) -> List[SongInfo]:
        """
        Risolve un URL o termine di ricerca (YouTube o Spotify) e restituisce una lista di SongInfo.
        Può restituire un brano singolo o un'intera playlist/album con latenza minima grazie alla cache e single-pass.
        """
        query = query.strip()
        cache_key = query.lower()

        # Controllo immediato cache in memoria per ricerche frequenti o link già risolti (0 ms)
        cached = self._get_cached_song(cache_key, requester)
        if cached:
            logger.info(f"⚡ Cache HIT in memoria per '{query}' - restituzione istantanea (0 ms)!")
            return [cached]

        # Risoluzione redirect per shortlink (es. spotify.link)
        if "spotify.link/" in query:
            try:
                query = await asyncio.to_thread(self._resolve_redirect, query)
            except Exception as e:
                logger.warning(f"Errore risoluzione shortlink: {e}")

        # Controllo se è un URL di Spotify
        if "spotify.com" in query:
            songs = await self._process_spotify(query, requester)
        else:
            # Altrimenti trattalo come YouTube (URL diretto, playlist o ricerca testuale)
            songs = await self._process_youtube(query, requester)

        if songs and len(songs) == 1:
            self._cache_song(cache_key, songs[0])

        return songs

    def _resolve_redirect(self, url: str) -> str:
        resp = requests.head(url, allow_redirects=True, timeout=5)
        return resp.url

    async def _process_spotify(self, url: str, requester: Optional[Union[discord.Member, discord.User]]) -> List[SongInfo]:
        """Estrae le tracce da Spotify e per singoli brani pre-risolve immediatamente lo stream YouTube."""
        pattern = re.compile(r"spotify\.com/(?:[a-zA-Z0-9\-_]+/)?(track|album|playlist)/([a-zA-Z0-9]+)")
        match = pattern.search(url)
        if not match:
            # Se l'URL non corrisponde a un pattern noto, tenta come ricerca
            return await self._process_youtube(url, requester)

        item_type = match.group(1)
        item_id = match.group(2)

        raw_tracks = []
        if self.sp:
            try:
                raw_tracks = await asyncio.to_thread(self._fetch_spotify_api, item_type, item_id)
            except Exception as e:
                logger.warning(f"Spotipy API fallita ({e}), uso fallback parser.")
                raw_tracks = await asyncio.to_thread(self._fetch_spotify_no_auth, item_type, item_id)
        else:
            raw_tracks = await asyncio.to_thread(self._fetch_spotify_no_auth, item_type, item_id)

        if not raw_tracks:
            raise ValueError("Impossibile estrarre tracce da questo link Spotify.")

        # Ottimizzazione ultra-veloce per traccia singola Spotify: risoluzione immediata dello stream YouTube in single-pass!
        if item_type == "track" and len(raw_tracks) == 1:
            t = raw_tracks[0]
            artist_part = t.get("artist", "")
            title_part = t.get("title", "")
            search_query = f"{title_part} {artist_part}".strip()
            display_title = f"{artist_part} - {title_part}" if artist_part else title_part

            try:
                yt_song = await asyncio.to_thread(self._extract_youtube_single, f"ytsearch1:{search_query}", requester)
                song = SongInfo(
                    title=display_title,
                    webpage_url=yt_song.webpage_url or url,
                    stream_url=yt_song.stream_url,
                    duration=t.get("duration") or yt_song.duration,
                    thumbnail=t.get("thumbnail") or yt_song.thumbnail,
                    uploader=artist_part or yt_song.uploader,
                    requester=requester,
                    source_type="spotify",
                    spotify_url=url,
                    search_query=search_query,
                    http_headers=yt_song.http_headers,
                    is_direct_stream=yt_song.is_direct_stream,
                )
                self._cache_song(url.strip().lower(), song)
                self._cache_song(search_query.lower(), song)
                return [song]
            except Exception as e:
                logger.warning(f"Pre-risoluzione stream YouTube per Spotify non riuscita ({e}), userò fallback on-demand.")

        songs = []
        for t in raw_tracks:
            search_query = f"{t['title']} {t['artist']}".strip()
            song = SongInfo(
                title=f"{t['artist']} - {t['title']}" if t.get('artist') else t['title'],
                webpage_url=url if item_type == "track" else "",
                stream_url=None,  # Verrà estratto on-demand per gli elementi di playlist/album
                duration=t.get("duration", 0),
                thumbnail=t.get("thumbnail"),
                uploader=t.get("artist"),
                requester=requester,
                source_type="spotify",
                spotify_url=url,
                search_query=search_query
            )
            songs.append(song)

        return songs

    def _fetch_spotify_api(self, item_type: str, item_id: str) -> List[Dict[str, Any]]:
        """Estrae tracce tramite l'API ufficiale Spotipy."""
        tracks = []
        if item_type == "track":
            t = self.sp.track(item_id)
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            thumb = t.get("album", {}).get("images", [{}])[0].get("url") if t.get("album") else None
            tracks.append({
                "title": t["name"],
                "artist": artists,
                "duration": t.get("duration_ms", 0) // 1000,
                "thumbnail": thumb
            })
        elif item_type == "playlist":
            res = self.sp.playlist_tracks(item_id)
            while res:
                for item in res.get("items", []):
                    t = item.get("track")
                    if t and t.get("name"):
                        artists = ", ".join(a["name"] for a in t.get("artists", []))
                        thumb = t.get("album", {}).get("images", [{}])[0].get("url") if t.get("album") else None
                        tracks.append({
                            "title": t["name"],
                            "artist": artists,
                            "duration": t.get("duration_ms", 0) // 1000,
                            "thumbnail": thumb
                        })
                if res.get("next"):
                    res = self.sp.next(res)
                else:
                    break
        elif item_type == "album":
            album = self.sp.album(item_id)
            thumb = album.get("images", [{}])[0].get("url") if album.get("images") else None
            res = self.sp.album_tracks(item_id)
            while res:
                for t in res.get("items", []):
                    if t.get("name"):
                        artists = ", ".join(a["name"] for a in t.get("artists", []))
                        tracks.append({
                            "title": t["name"],
                            "artist": artists,
                            "duration": t.get("duration_ms", 0) // 1000,
                            "thumbnail": thumb
                        })
                if res.get("next"):
                    res = self.sp.next(res)
                else:
                    break
        return tracks

    def _fetch_spotify_no_auth(self, item_type: str, item_id: str) -> List[Dict[str, Any]]:
        """Parser di fallback che estrae metadati da Spotify tramite embed senza credenziali."""
        embed_url = f"https://open.spotify.com/embed/{item_type}/{item_id}"
        req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")

        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
        if not match:
            # Fallback estremo oEmbed per singole tracce
            if item_type == "track":
                oembed_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{item_id}"
                req_oe = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req_oe, timeout=10) as oe_resp:
                    data = json.loads(oe_resp.read().decode("utf-8"))
                    return [{
                        "title": data.get("title", "Brano Spotify"),
                        "artist": "",
                        "duration": 0,
                        "thumbnail": data.get("thumbnail_url")
                    }]
            return []

        data = json.loads(match.group(1))
        entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})

        if item_type == "track":
            title = entity.get("title") or entity.get("name") or "Brano Spotify"
            artists = entity.get("subtitle") or ", ".join(a.get("name", "") for a in entity.get("artists", []))
            images = entity.get("visualIdentity", {}).get("image", [{}])
            thumb = images[0].get("url") if images else None
            return [{
                "title": title,
                "artist": artists,
                "duration": entity.get("duration", 0) // 1000,
                "thumbnail": thumb
            }]
        else:
            # Playlist o Album
            tracks = []
            for t in entity.get("trackList", []):
                tracks.append({
                    "title": t.get("title", ""),
                    "artist": t.get("subtitle", ""),
                    "duration": t.get("duration", 0) // 1000,
                    "thumbnail": None
                })
            return tracks

    async def _process_youtube(self, query: str, requester: Optional[Union[discord.Member, discord.User]]) -> List[SongInfo]:
        """Estrae tracce da YouTube (URL singolo, playlist o ricerca testuale)."""
        is_url = query.startswith("http://") or query.startswith("https://")
        is_playlist = is_url and ("list=" in query or "playlist?list=" in query)

        if is_playlist:
            return await asyncio.to_thread(self._extract_youtube_playlist, query, requester)
        elif is_url:
            song = await asyncio.to_thread(self._extract_youtube_single, query, requester)
            return [song]
        else:
            # Ricerca testuale con prefisso ytsearch1:
            search_target = f"ytsearch1:{query}"
            song = await asyncio.to_thread(self._extract_youtube_single, search_target, requester)
            return [song]

    def _extract_youtube_playlist(self, url: str, requester: Optional[Union[discord.Member, discord.User]]) -> List[SongInfo]:
        """Estrae tutti i brani di una playlist YouTube rapidamente in modalità flat."""
        with yt_dlp.YoutubeDL(self.ydl_opts_flat) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Nessuna informazione trovata per questa playlist YouTube.")

            entries = info.get("entries", [])
            songs = []
            for entry in entries:
                if not entry:
                    continue
                v_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                song = SongInfo(
                    title=entry.get("title", "Traccia sconosciuta"),
                    webpage_url=v_url,
                    stream_url=None,  # Estratto on demand quando sarà in cima alla coda
                    duration=int(entry.get("duration") or 0),
                    thumbnail=entry.get("thumbnail"),
                    uploader=entry.get("uploader"),
                    requester=requester,
                    source_type="youtube"
                )
                songs.append(song)
            return songs

    def _find_cached_file(self, video_id: str) -> Optional[str]:
        """Cerca se il file audio per il video_id è già presente nella cache locale su disco."""
        cache_dir = "/tmp/franchino_cache"
        if not video_id or not os.path.isdir(cache_dir):
            return None
        try:
            for fname in os.listdir(cache_dir):
                if fname.startswith(f"{video_id}."):
                    candidate = os.path.join(cache_dir, fname)
                    if os.path.isfile(candidate) and os.path.getsize(candidate) > 1024:
                        return candidate
        except Exception:
            pass
        return None

    def _extract_youtube_single(self, target: str, requester: Optional[Union[discord.Member, discord.User]]) -> SongInfo:
        """Estrae direttamente il flusso audio e i metadati in un unico passaggio ad altissima velocità."""
        # Se il target contiene già un video ID, controlla se è già in cache locale su disco (avvio istantaneo 0 ms)
        vid_match = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", target)
        if vid_match:
            vid_id = vid_match.group(1)
            cached_local = self._find_cached_file(vid_id)
            if cached_local:
                logger.info(f"⚡ Traccia locale già pronta in cache per '{vid_id}': {cached_local} (0 ms)!")
                return SongInfo(
                    title=f"YouTube {vid_id}",
                    webpage_url=f"https://www.youtube.com/watch?v={vid_id}",
                    stream_url=cached_local,
                    duration=0,
                    thumbnail=None,
                    uploader=None,
                    requester=requester,
                    source_type="youtube",
                    is_direct_stream=False
                )

        if "cookiefile" not in self.ydl_opts_stream:
            self._configure_cookies()

        try:
            song = self._do_extract_single(target, requester, self.ydl_opts_stream, download=False)
            if song:
                self._cache_song(target.strip().lower(), song)
            return song
        except Exception as first_err:
            logger.warning(f"Primo tentativo estrazione streaming per '{target}': {first_err}. Riprovo con client alternativi...")
            fallback_opts = dict(self.ydl_opts_stream)
            fallback_opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["android", "web_safari", "visionos"],
                }
            }
            if "cookiefile" in fallback_opts:
                del fallback_opts["cookiefile"]
            song = self._do_extract_single(target, requester, fallback_opts, download=False)
            if song:
                self._cache_song(target.strip().lower(), song)
            return song

    def _do_extract_single(self, target: str, requester: Optional[Union[discord.Member, discord.User]], opts: Dict[str, Any], download: bool = False) -> SongInfo:
        cache_dir = "/tmp/franchino_cache"
        os.makedirs(cache_dir, exist_ok=True)
        self._cleanup_cache(cache_dir, max_files=32)

        run_opts = dict(opts)
        if download:
            run_opts["outtmpl"] = os.path.join(cache_dir, "%(id)s.%(ext)s")

        with yt_dlp.YoutubeDL(run_opts) as ydl:
            info = ydl.extract_info(target, download=download)
            if not info:
                raise ValueError("Nessun risultato trovato per la ricerca.")

            if "entries" in info:
                entries = info.get("entries", [])
                if not entries:
                    raise ValueError("Nessun brano trovato per la ricerca.")
                info = entries[0]

            vid_id = info.get("id")
            webpage_url = info.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else target)

            local_file = None
            if download:
                prepared = ydl.prepare_filename(info)
                if os.path.isfile(prepared) and os.path.getsize(prepared) > 0:
                    local_file = prepared
                elif vid_id:
                    local_file = self._find_cached_file(vid_id)
            elif vid_id:
                local_file = self._find_cached_file(vid_id)

            stream_url = local_file or info.get("url")
            http_headers = info.get("http_headers")
            is_direct_stream = (local_file is None and stream_url is not None and (stream_url.startswith("http://") or stream_url.startswith("https://")))

            return SongInfo(
                title=info.get("title", "Traccia sconosciuta"),
                webpage_url=webpage_url,
                stream_url=stream_url,
                duration=int(info.get("duration") or 0),
                thumbnail=info.get("thumbnail"),
                uploader=info.get("uploader"),
                requester=requester,
                source_type="youtube",
                http_headers=http_headers,
                is_direct_stream=is_direct_stream,
            )

    def _cleanup_cache(self, cache_dir: str, max_files: int = 32):
        """Mantiene gli ultimi max_files brani nella cache temporanea per riproduzioni istantanee."""
        try:
            files = [
                os.path.join(cache_dir, f)
                for f in os.listdir(cache_dir)
                if os.path.isfile(os.path.join(cache_dir, f))
            ]
            if len(files) > max_files:
                files.sort(key=lambda x: os.path.getmtime(x))
                for old_f in files[:-max_files]:
                    try:
                        os.remove(old_f)
                    except Exception:
                        pass
        except Exception:
            pass

    def _download_youtube_audio(self, target: str, requester: Optional[Union[discord.Member, discord.User]]) -> SongInfo:
        """Scarica il flusso audio in /tmp/franchino_cache (fallback se lo streaming diretto incontra blocchi di rete)."""
        if "cookiefile" not in self.ydl_opts_stream:
            self._configure_cookies()

        try:
            return self._do_extract_single(target, requester, self.ydl_opts_stream, download=True)
        except Exception as first_err:
            logger.warning(f"Primo tentativo download fallito per '{target}': {first_err}. Riprovo con client web_safari/android...")
            fallback_opts = {
                "format": "ba[abr<=160]/251/140/bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "source_address": "0.0.0.0",
                "extract_flat": False,
                "cachedir": self.ytdlp_cache_dir,
                "remote_components": ["ejs:github"],
                "extractor_args": {
                    "youtube": {
                        "player_client": ["web_safari", "android", "visionos"],
                    }
                },
                "socket_timeout": 8.0,
            }
            if NODE_PATH:
                fallback_opts["js_runtimes"] = {"node": {}}
            if "cookiefile" in self.ydl_opts_stream:
                fallback_opts["cookiefile"] = self.ydl_opts_stream["cookiefile"]
            return self._do_extract_single(target, requester, fallback_opts, download=True)

    async def ensure_stream_url(self, song: SongInfo) -> str:
        """
        Assicura che la traccia abbia uno stream_url pronto (flusso HTTP diretto ad alta velocità o file locale).
        Se stream_url è già disponibile, ritorna istantaneamente in 0 ms senza blocchi o ri-estrazioni.
        """
        # 1. Se c'è già un file locale pronto su disco, usalo subito (0 ms)!
        if song.stream_url and os.path.isfile(song.stream_url) and os.path.getsize(song.stream_url) > 0:
            return song.stream_url

        # 2. Se c'è già un flusso HTTP diretto estratto in precedenza, usalo subito (0 ms)!
        if song.stream_url and (song.stream_url.startswith("http://") or song.stream_url.startswith("https://")):
            if not (song.source_type == "sports_stream" and song.stream_url.endswith(".html")):
                return song.stream_url

        # 3. Flusso sportivo in streaming da MadPlay77
        if song.source_type == "sports_stream":
            if song.stream_url and not song.stream_url.endswith(".html"):
                return song.stream_url
            try:
                from sports_scraper import sports_scraper
                meta = await sports_scraper.resolve_match_for_streaming(song.search_query or song.title)
                if meta and meta.get("direct_hls_available") and meta.get("stream_url") and not meta["stream_url"].endswith(".html"):
                    song.stream_url = meta["stream_url"]
                    return song.stream_url
            except Exception as e:
                logger.debug(f"Errore risoluzione stream sportivo in ensure_stream_url: {e}")
            raise RuntimeError(f"Flusso audio live non disponibile per '{song.title}'")

        # 4. Se non ancora estratto (es. brano da playlist o Spotify in coda), estrai in singolo passaggio ultra-rapido
        if song.source_type == "spotify":
            target = f"ytsearch1:{song.search_query or song.title}"
        elif song.webpage_url and (song.webpage_url.startswith("http://") or song.webpage_url.startswith("https://")):
            target = song.webpage_url
        elif song.search_query:
            target = f"ytsearch1:{song.search_query}"
        else:
            target = f"ytsearch1:{song.title}"

        # Controllo se è presente nella cache in memoria
        cached = self._get_cached_song(target.strip().lower(), song.requester)
        if cached and cached.stream_url:
            song.stream_url = cached.stream_url
            song.http_headers = cached.http_headers
            song.is_direct_stream = cached.is_direct_stream
            return song.stream_url

        fresh_info = await asyncio.to_thread(self._extract_youtube_single, target, song.requester)

        song.stream_url = fresh_info.stream_url
        song.http_headers = fresh_info.http_headers
        song.is_direct_stream = fresh_info.is_direct_stream
        if not song.webpage_url:
            song.webpage_url = fresh_info.webpage_url
        if not song.duration:
            song.duration = fresh_info.duration
        if not song.thumbnail:
            song.thumbnail = fresh_info.thumbnail
        if not song.uploader:
            song.uploader = fresh_info.uploader

        return song.stream_url

    async def resolve_related_song(self, seed_song: SongInfo, history_urls: set[str]) -> Optional[SongInfo]:
        """Cerca su YouTube un brano correlato a seed_song non ancora ascoltato (per la modalità AutoPlay Radio)."""
        def _search_related() -> Optional[SongInfo]:
            artist_hint = seed_song.uploader or ""
            clean_title = re.sub(r"\(.*?\)|\[.*?\]", "", seed_song.title).strip()
            search_q = f"ytsearch8:{artist_hint} {clean_title} musica".strip()
            with yt_dlp.YoutubeDL(self.ydl_opts_flat) as ydl:
                info = ydl.extract_info(search_q, download=False)
                if not info or "entries" not in info:
                    return None
                candidates = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    vid_id = entry.get("id")
                    url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else None)
                    if url and not url.startswith("http"):
                        url = f"https://www.youtube.com/watch?v={url}"
                    title = entry.get("title") or ""
                    if not url or url in history_urls or url == seed_song.webpage_url:
                        continue
                    if title.lower().strip() == seed_song.title.lower().strip():
                        continue
                    dur = int(entry.get("duration") or 0)
                    if dur > 900:  # Evita compilation di ore in modalità radio
                        continue
                    candidates.append(
                        SongInfo(
                            title=title,
                            webpage_url=url,
                            duration=dur,
                            thumbnail=entry.get("thumbnail") or (entry.get("thumbnails", [{}])[-1].get("url") if entry.get("thumbnails") else None),
                            uploader=entry.get("uploader") or entry.get("channel"),
                            requester=seed_song.requester,
                            source_type="youtube",
                        )
                    )
                if candidates:
                    import random
                    return random.choice(candidates[:4])
                return None

        try:
            return await asyncio.to_thread(_search_related)
        except Exception as e:
            logger.debug(f"Errore ricerca AutoPlay correlata: {e}")
            return None


