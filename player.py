import asyncio
import collections
import json
import logging
import os
import random
import time
from typing import Optional, List, Dict, Tuple, Union, Any
import discord
from discord.ext import commands

from config import (
    COMMAND_PREFIX,
    DEFAULT_VOLUME,
    INACTIVITY_TIMEOUT,
    COLOR_FRANCHINO,
    COLOR_INFO,
    FRANCHINO_QUOTES,
    FRANCHINO_ENTRANCE_QUOTES
)
from sources import SongInfo, SourceManager, format_duration
from views import FranchinoControlsView, QueuePaginationView

logger = logging.getLogger("Franchino.Player")

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

# ==============================================================================
# 12 Effetti Audio Musicali DJ in Tempo Reale (FFmpeg Audio Filters)
# ==============================================================================
DJ_EFFECTS: Dict[str, Dict[str, str]] = {
    "off": {
        "label": "Normale (Originale)",
        "emoji": "🎧",
        "filter": "",
    },
    "bassboost": {
        "label": "Bass Boost (Club)",
        "emoji": "🔊",
        "filter": "bass=g=12:f=110:w=0.6",
    },
    "megabass": {
        "label": "Mega Sub-Bass (Estremo)",
        "emoji": "💣",
        "filter": "bass=g=18:f=95:w=0.7,dynaudnorm",
    },
    "nightcore": {
        "label": "Nightcore (Dance/Rave)",
        "emoji": "⚡",
        "filter": "asetrate=48000*1.20,aresample=48000,bass=g=4",
    },
    "slowed": {
        "label": "Slowed + Reverb",
        "emoji": "🌙",
        "filter": "asetrate=48000*0.86,aresample=48000,aecho=0.8:0.88:60:0.4",
    },
    "8d": {
        "label": "8D Audio Surround",
        "emoji": "🌀",
        "filter": "apulsator=hz=0.125",
    },
    "reverb": {
        "label": "Eco / Riverbero Arena",
        "emoji": "🏛️",
        "filter": "aecho=0.8:0.88:90|180:0.35|0.25",
    },
    "karaoke": {
        "label": "Karaoke (Attenua Voce)",
        "emoji": "🎤",
        "filter": "stereotools=mlev=0.03",
    },
    "vaporwave": {
        "label": "Vaporwave Lo-Fi",
        "emoji": "🌴",
        "filter": "asetrate=48000*0.80,aresample=48000,lowpass=f=3800",
    },
    "chipmunk": {
        "label": "Voce Scoiattolo (Chipmunk)",
        "emoji": "🐿️",
        "filter": "asetrate=48000*1.38,aresample=48000,atempo=0.78",
    },
    "demon": {
        "label": "Voce Profonda (Dark/Demon)",
        "emoji": "👹",
        "filter": "asetrate=48000*0.72,aresample=48000,atempo=1.25",
    },
    "vintage": {
        "label": "Radio Vintage / Vinile",
        "emoji": "📻",
        "filter": "highpass=f=420,lowpass=f=3200,aecho=0.7:0.7:25:0.25",
    },
}

FAVORITES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "favorites.json")


def _load_all_favorites() -> Dict[str, List[dict]]:
    try:
        if os.path.isfile(FAVORITES_FILE):
            with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"Errore lettura favorites.json: {e}")
    return {}


def _save_all_favorites(data: Dict[str, List[dict]]) -> None:
    try:
        os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
        with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Errore salvataggio favorites.json: {e}")


def resolve_preferred_text_channel(guild: discord.Guild, fallback_channel=None):
    """
    Cerca prima un canale testuale chiamato 'musica' (es. #musica, #🎵-musica) nel server Discord.
    Se non esiste, usa la chat da cui è partito il comando o la chat del canale vocale.
    """
    if guild and guild.me:
        # 1. Cerca corrispondenza esatta 'musica'
        for ch in guild.text_channels:
            clean_name = ch.name.lower().strip()
            if clean_name == "musica" and ch.permissions_for(guild.me).send_messages:
                return ch

        # 2. Cerca un canale testuale che contenga la parola 'musica' (es. 🎵-musica, chat-musica)
        for ch in guild.text_channels:
            clean_name = ch.name.lower()
            if "musica" in clean_name and ch.permissions_for(guild.me).send_messages:
                return ch

    # 3. Altrimenti usa il canale dove è partito (chat testuale o chat del canale vocale)
    if fallback_channel is not None:
        if not guild or not guild.me or fallback_channel.permissions_for(guild.me).send_messages:
            return fallback_channel

    # 4. Ultimo fallback: system_channel o primo canale testuale scrivibile
    if guild and guild.me:
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            return guild.system_channel
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                return ch

    return fallback_channel


def build_commands_embed() -> discord.Embed:
    """Costruisce l'embed completo con tutti i comandi vocali, testuali, effetti DJ, Soundboard e pulsanti di Franchino."""
    embed = discord.Embed(
        title="🎧 Franchino DJ — Guida a tutti i Comandi & Effetti Audio",
        description=(
            "Puoi controllare Franchino **con la voce** (parlando nel canale vocale), "
            "**con i pulsanti e menu interattivi** o **scrivendo in chat**!\n"
            "*(Tutte le notifiche vengono inviate automaticamente nel canale `#musica` se presente)*"
        ),
        color=COLOR_FRANCHINO
    )

    embed.add_field(
        name="🎙️ Comandi Vocali Principali (dì nel microfono...)",
        value=(
            "• **\"Franchino ci senti?\"** / **\"ci sei?\"** / **\"sei vivo?\"** — Verifica l'ascolto e la presenza in console!\n"
            "• **\"Franchino metti `<titolo e artista>`\"** — Cerca e avvia subito la canzone\n"
            "• **\"Franchino aggiungi `<titolo>`\"** — Aggiunge il brano in coda\n"
            "• **\"Franchino manda messaggio a `<nome>`\"** — Ti chiede *\"Cosa scrivo nel messaggio?\"* e manda il DM all'amico nella stanza!\n"
            "• **\"Franchino salta\"** / **\"pausa\"** / **\"riprendi\"** / **\"stop\"** — Controlla la riproduzione\n"
            "• **\"Franchino alza\"** / **\"abbassa\"** / **\"volume a 50\"** / **\"a palla\"** — Regola il volume\n"
            "• **\"Franchino salva\"** / **\"metti i preferiti\"** — Salva o avvia i ❤️ Preferiti del server\n"
            "• **\"Franchino attiva radio\"** / **\"riavviati\"** / **\"comandi\"** / **\"esci\"**"
        ),
        inline=False
    )

    embed.add_field(
        name="🎛️ 12 Effetti Audio DJ Live sulla Musica (Vocali, Menu o `/effetto`)",
        value=(
            "Dì **\"Franchino `<effetto>`\"** mentre suona una canzone per trasformarla in tempo reale:\n"
            "🔊 `bass boost` • 💣 `mega bass` • ⚡ `nightcore` • 🌙 `slowed` • 🌀 `8d` • 🏛️ `eco` (`reverb`)\n"
            "🎤 `karaoke` • 🌴 `vaporwave` • 🐿️ `voce scoiattolo` (`chipmunk`) • 👹 `voce profonda` (`demon`) • 📻 `radio vintage` • 🎧 `normale`"
        ),
        inline=False
    )

    embed.add_field(
        name="📯 12 Effetti Sonori Soundboard Istantanei (Vocali, Menu o `/suono`)",
        value=(
            "Dì **\"Franchino `<suono>`\"** per sparare un effetto sonoro in stanza:\n"
            "📯 `trombetta` (`airhorn`) • 🚨 `sirena` • 💣 `drop` • 🥁 `rullo di tamburi` (`battuta`) • 👏 `applausi` • 🎺 `fail` (`figura di merda`)\n"
            "🏆 `vittoria` • 🔫 `laser` • 🤬 `censura` (`beep`) • ⚽ `fischio` (`arbitro`) • 😂 `risata` • 👎 `buuu` (`errore`)"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Comandi da Admin & Moderatore Discord (Vocali & Slash)",
        value=(
            "• **\"Franchino sposta `<nome>` in `<stanza>`\"** (o *\"porta qui `<nome>`\"*, *\"sposta tutti in `<stanza>`\"*)\n"
            "• **\"Franchino caccia `<nome>` dalla stanza\"** (disconnette dalla vocale)\n"
            "• **\"Franchino muta `<nome>`\"** / **\"smuta `<nome>`\"** / **\"muta tutti\"** / **\"smuta tutti\"**\n"
            "• **\"Franchino togli l'audio a `<nome>`\"** / **\"ridai l'audio a `<nome>`\"** (Deafen cuffie)\n"
            "• **\"Franchino dai il ruolo `<ruolo>` a `<nome>`\"** / **\"togli il ruolo `<ruolo>` a `<nome>`\"** / **\"crea il ruolo `<nome>`\"**\n"
            "• **\"Franchino cambia soprannome a `<nome>` in `<nuovo nome>`\"** / **\"resetta soprannome a `<nome>`\"**\n"
            "• **\"Franchino metti in timeout `<nome>` per 5 minuti\"** / **\"libera `<nome>`\"**\n"
            "• **\"Franchino pulisci la chat\"** (o *\"cancella 20 messaggi\"*) • **\"crea stanza vocale `<nome>`\"** • **\"chiudi/apri la stanza\"**\n"
            "• **\"Franchino espelli `<nome>` dal server\"** / **\"banna `<nome>`\"** / **\"sbanna `<nome>`\"**"
        ),
        inline=False
    )

    embed.add_field(
        name="📖 Enciclopedia Vocale UO Outlands (wiki.uooutlands.com in Italiano!)",
        value=(
            "Fai qualsiasi domanda a voce su **Ultima Online Outlands** e Franchino cercherà sulla Wiki ufficiale, "
            "tradurrà tutto in italiano e **ti risponderà a voce** (+ scheda completa in chat):\n"
            "• **\"Hey Franchino cos'è il Mastery Chain?\"** • **\"Hey Franchino spiegami gli Aspect\"**\n"
            "• **\"Hey Franchino come si allena Animal Taming?\"** • **\"Hey Franchino dove si trova Aegis Keep?\"**\n"
            "• **\"Hey Franchino cerca sul vendor aspect core\"** • `/vendor <oggetto>` (`/prezzi`, `/status`, `/patch`, `/builds`)\n"
            "• Oppure usa il comando `/wiki <domanda>` (`/outlands`)"
        ),
        inline=False
    )

    embed.add_field(
        name="💖 Interazioni Sociali con gli Utenti (Vocali & Slash)",
        value=(
            "Dì a Franchino di interagire con chi è nella stanza vocale con te o con tutta la chat:\n"
            "• **\"Franchino saluta `<nome>`\"** / **\"saluta tutti\"** — 🎙️ Saluto da vocalist con trombetta DJ\n"
            "• **\"Franchino fai un brindisi per `<nome>`\"** / **\"brindiamo a tutti\"** — 🥂 Cin cin ed energia Insomnia!\n"
            "• **\"Franchino abbraccia `<nome>`\"** — 🫂 Caloroso abbraccio dalla console\n"
            "• **\"Franchino carica `<nome>`\"** / **\"carica la stanza\"** — 🔥 Hype a mille con sirena DJ!\n"
            "• **\"Franchino fai gli auguri a `<nome>`\"** — 🎂 Tanti auguri di compleanno con fanfare di vittoria!\n"
            "• **\"Franchino dai la buonanotte a `<nome>`\"** — 🌙 Sogni d'oro e atmosfera notturna\n"
            "• **\"Franchino batti il cinque a `<nome>`\"** — ✋ High five al volo dalla console!\n"
            "• **\"Franchino prendi in giro `<nome>`\"** — 😜 Sfottò amichevole con trombone fail!\n"
            "*(Disponibili anche via Slash:* `/saluta`, `/brindisi`, `/abbraccio`, `/carica`, `/auguri`, `/buonanotte`, `/cinque`, `/troll`*)*"
        ),
        inline=False
    )

    embed.add_field(
        name="🤖 Suite Assistente Intelligente Vocale (stile Alexa / Google Home)",
        value=(
            "Parla a voce con Franchino o usa i comandi slash dedicati:\n"
            "• **\"Franchino che ore sono?\"** / `/ora` — Ora e data esatta italiana (`Europe/Rome`)\n"
            "• **\"Franchino che tempo fa a Milano?\"** / `/meteo [citta]` — Previsioni live Open-Meteo\n"
            "• **\"Franchino metti un timer di 5 minuti per la pasta\"** / `/timer <durata> [motivo]` — Timer asincrono con sveglia 'Driiiin!' e TTS\n"
            "• **\"Franchino quanto fa 25 per 4?\"** / `/calcola <espressione>` — Calcolatrice vocale e AST di precisione\n"
            "• **\"Franchino lancia un dado\"** / **\"testa o croce\"** / `/lancia [tipo]` — Sorti, dadi e monete\n"
            "• **\"Franchino raccontami una barzelletta\"** / `/barzelletta` — Freddure e battute da vocalist della notte"
        ),
        inline=False
    )

    embed.add_field(
        name="📺 Streaming Video Go Live (Screen Share) & Sport Live",
        value=(
            "Guarda le partite o i video in diretta streaming nel canale vocale con StreamBot (zero pubblicità & SponsorBlock):\n"
            "• **`/partita_video <squadra>`** (o **`/partita video <squadra>`**) — Avvia la diretta video 'Go Live' della partita!\n"
            "• **`/stream <squadra>`** (o **`/partita stream <squadra>`**) — Ascolta la telecronaca audio in vocale da MadPlay77\n"
            "• **`/video [play|stop|pausa|riprendi] [url]`** — Condivisione schermo di video YouTube o streaming web\n"
            "• **`/partite [lega]`** — Palinsesto sportivo completo (Calcio, Serie A, Champions, NBA, Tennis, F1, MotoGP)\n"
            "• A voce: **\"Hey Franchino trasmetti la partita del Milan in video\"** • **\"Hey Franchino ferma il video\"**"
        ),
        inline=False
    )

    embed.add_field(
        name="💬 Comandi Slash & Chat",
        value=(
            f"`/partita_video <squadra>` • `/partita video <squadra>` • `/video [play|stop|pausa] [url]` • `/stream <squadra>` • `/partite`\n"
            f"`/ora` • `/meteo [citta]` • `/timer <tempo> [motivo]` • `/calcola <operazione>` • `/lancia [dado|moneta]` • `/barzelletta`\n"
            f"`/cisei` (`/ping`, `/cisenti`, `/seivivo`) • `/play <brano>` • `/pause` • `/resume` • `/skip` • `/stop` • `/queue` • `/volume <1-100>`\n"
            f"`/saluta` • `/brindisi` • `/abbraccio` • `/carica` • `/auguri` • `/buonanotte` • `/cinque` • `/troll`\n"
            f"`/partita <squadra>` • `/wiki <domanda>` (`/outlands`, `/vendor`, `/prezzi`, `/patch`, `/status`) • `/radio` • `/comandi` • `!sync`\n"
            f"**Admin:** `/sposta` • `/spostatutti` • `/scollega` • `/muta` • `/smuta` • `/ruolo` • `/soprannome` • `/timeout` • `/pulisci` • `/kick` • `/ban`"
        ),
        inline=False
    )

    embed.set_footer(
        text="Franchino DJ, UO Outlands Wiki & Server Admin • Sempre in ascolto 24/7! ✨",
        icon_url="https://cdn-icons-png.flaticon.com/512/3845/3845868.png"
    )
    return embed


class GuildPlayer:
    """Gestisce la riproduzione musicale, la coda, gli effetti DJ, la radio e i preferiti per un server Discord."""

    def __init__(self, bot: commands.Bot, guild: discord.Guild, source_manager: SourceManager):
        self.bot = bot
        self.guild = guild
        self.source_manager = source_manager

        self.queue: collections.deque[SongInfo] = collections.deque()
        self.current: Optional[SongInfo] = None
        self.last_played_song: Optional[SongInfo] = None
        self.history_urls: set[str] = set()

        self.loop_mode: str = "off"       # "off", "song", "queue"
        self.autoplay: bool = False       # Modalità Radio Infinita
        self.audio_effect: str = "off"    # "off", "bassboost", "nightcore", "slowed", "8d"
        self.vocalist_mode: bool = True   # Voce maschile profonda con eco da Console Vocalist (Franchino)
        self.volume: float = DEFAULT_VOLUME

        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self.now_playing_message: Optional[discord.Message] = None

        self.play_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self.inactivity_task: Optional[asyncio.Task] = None
        self._manual_stop = False
        self._restarting = False
        self._playing_tts = False
        self._is_tts_source: bool = False
        self._current_tts_id: int = 0
        self._tts_start_time: float = 0.0
        self._tts_max_duration: float = 25.0
        self._tts_lock = asyncio.Lock()
        self._track_start_monotonic: float = 0.0
        self._interrupted_song: Optional[SongInfo] = None
        self._interrupted_elapsed: int = 0
        self._tts_done_event: Optional[asyncio.Event] = None

        # Stato Auto-Ducking (abbassamento automatico volume musica mentre qualcuno parla)
        self._is_ducked: bool = False
        self._duck_restore_task: Optional[asyncio.Task] = None
        self._last_entrance_greeting_time: float = 0.0
        self._consecutive_play_errors: int = 0
        self._last_activity_time: float = time.monotonic()
        self._manual_leave_until: float = 0.0
        self._last_connect_time: float = 0.0
        self._connect_cooldown_seconds: float = 5.0
        self._is_disconnecting: bool = False

    def is_connected(self) -> bool:
        """Verifica se il voice client è attivo e connesso a Discord."""
        return bool(self.voice_client and self.voice_client.is_connected())

    def set_manual_leave_cooldown(self, seconds: float = 60.0):
        """Imposta un periodo di cooldown dopo una disconnessione manuale per impedire l'auto-join immediato."""
        self._manual_leave_until = time.monotonic() + seconds

    def has_manual_leave_cooldown(self) -> bool:
        """Controlla se è ancora attivo il cooldown di disconnessione manuale."""
        leave_until = getattr(self, "_manual_leave_until", 0.0)
        try:
            return float(leave_until) > time.monotonic()
        except (TypeError, ValueError):
            return False

    def clear_manual_leave_cooldown(self):
        """Azzera il cooldown di disconnessione manuale."""
        self._manual_leave_until = 0.0

    def record_activity(self):
        """Registra attività (musica, comandi vocali, interazioni) e aggiorna il timer di inattività."""
        self._last_activity_time = time.monotonic()
        if not self.is_playing() and not self.queue:
            self._reset_inactivity_timer()

    @property
    def is_speaking_tts(self) -> bool:
        """Verifica se Franchino sta effettivamente parlando in vocale, auto-resettando flag bloccati."""
        if not self._playing_tts:
            return False
        if not (self.voice_client and self.voice_client.is_connected() and self.voice_client.is_playing()):
            self._playing_tts = False
            self._is_tts_source = False
            return False
        # Se la musica è in corso e non è una sorgente TTS dedicata, allora sta suonando una canzone, non TTS!
        if self.current and self._interrupted_song is None and not self._is_tts_source:
            self._playing_tts = False
            return False
        # Timeout di sicurezza: nessun messaggio TTS dura più di _tts_max_duration (default 30s)
        if time.monotonic() - getattr(self, "_tts_start_time", 0) > getattr(self, "_tts_max_duration", 30.0):
            self._playing_tts = False
            self._is_tts_source = False
            return False
        return True

    @property
    def is_active(self) -> bool:
        return (self.is_playing() or self.is_paused()) and not self.is_speaking_tts

    @property
    def is_busy(self) -> bool:
        """Indica se Franchino sta suonando, parlando in TTS, riproducendo un effetto o ha elementi in coda."""
        return (
            self.is_playing()
            or self.is_paused()
            or self.is_speaking_tts
            or bool(self.queue)
            or bool(self.current)
            or bool(self._interrupted_song)
        )

    def is_playing(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_playing())

    def is_paused(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_paused())

    # ==========================================================================
    # Auto-Ducking (Abbassa il volume della musica quando qualcuno parla)
    # ==========================================================================
    def trigger_auto_duck(self):
        """Abbassa temporaneamente il volume della musica quando un utente inizia a parlare nel canale."""
        if not self.is_playing() or self._playing_tts:
            return
        if self.voice_client and self.voice_client.source and hasattr(self.voice_client.source, "volume"):
            duck_vol = min(self.volume, 0.16)
            if not self._is_ducked and self.volume > 0.20:
                self._is_ducked = True
                self.voice_client.source.volume = duck_vol
            # Pianifica il ripristino automatico dopo 2.6s se non arrivano altri pacchetti voce
            if self._duck_restore_task and not self._duck_restore_task.done():
                self._duck_restore_task.cancel()
            self._duck_restore_task = self.bot.loop.create_task(self._auto_restore_duck_after(2.6))

    async def _auto_restore_duck_after(self, delay: float):
        try:
            await asyncio.sleep(delay)
            self.restore_duck_volume()
        except asyncio.CancelledError:
            pass

    def restore_duck_volume(self):
        """Ripristina il volume originale della musica appena l'utente finisce di parlare."""
        if self._duck_restore_task and not self._duck_restore_task.done():
            self._duck_restore_task.cancel()
        self._duck_restore_task = None
        if self._is_ducked:
            self._is_ducked = False
            if self.voice_client and self.voice_client.source and hasattr(self.voice_client.source, "volume"):
                self.voice_client.source.volume = self.volume

    # ==========================================================================
    # Voce del Vero Franchino il Vocalist (100% Gratuita: Neural Male TTS + Console Club DSP)
    # ==========================================================================
    async def speak_tts(
        self,
        text: str,
        wait: bool = False,
        interrupt_music: bool = True,
        keep_paused_after: bool = False,
        dramatic_fable: bool = False,
    ):
        """
        Fa parlare Franchino nel canale vocale emulando gratuitamente la voce di Franchino il Vocalist:
        - Usa gratuitamente la voce neurale maschile italiana profonda (`it-IT-DiegoNeural` via `edge-tts`, zero API key)
          con pitch ribassato e cadenza ipnotica da vocalist notturno.
        - Applica in FFmpeg la catena DSP da Console Club (calore sui medio-bassi + eco/delay da discoteca).
        """
        if (not self.voice_client or not self.voice_client.is_connected()) and self.guild.voice_client:
            if self.guild.voice_client.is_connected():
                self.voice_client = self.guild.voice_client

        if not self.voice_client or not self.voice_client.is_connected():
            logger.warning(f"⚠️ Impossibile eseguire speak_tts: Franchino non è connesso a una stanza vocale in '{self.guild.name}'")
            return
        if (self.is_playing() or self.is_paused()) and not interrupt_music and not self._playing_tts:
            return

        cache_dir = "/tmp/franchino_cache"
        os.makedirs(cache_dir, exist_ok=True)
        import hashlib
        cache_hash = hashlib.md5(f"vocalist_v2_{text.strip().lower()}_{dramatic_fable}".encode("utf-8")).hexdigest()
        tts_file = os.path.join(cache_dir, f"tts_{cache_hash}.mp3")
        used_gtts_fallback = False
        generated_ok = os.path.isfile(tts_file) and os.path.getsize(tts_file) > 100

        # 1. Se non è già in cache, genera la voce tramite Microsoft Edge Neural TTS
        # Utilizza la voce neurale espressiva italiana (GiuseppeMultilingualNeural o DiegoNeural)
        if not generated_ok:
            try:
                import edge_tts
                pitch_val = "-10Hz" if dramatic_fable else "-6Hz"
                rate_val = "-4%" if dramatic_fable else "+0%"
                try:
                    communicate = edge_tts.Communicate(
                        text=text,
                        voice="it-IT-GiuseppeMultilingualNeural",
                        pitch=pitch_val,
                        rate=rate_val,
                    )
                    await asyncio.wait_for(communicate.save(tts_file), timeout=12.0)
                    generated_ok = os.path.isfile(tts_file) and os.path.getsize(tts_file) > 100
                except Exception as ve:
                    logger.warning(f"⚠️ GiuseppeMultilingualNeural non disponibile ({ve}), fallback su DiegoNeural...")
                    communicate = edge_tts.Communicate(
                        text=text,
                        voice="it-IT-DiegoNeural",
                        pitch="-12Hz" if dramatic_fable else "-8Hz",
                        rate=rate_val,
                    )
                    await asyncio.wait_for(communicate.save(tts_file), timeout=12.0)
                    generated_ok = os.path.isfile(tts_file) and os.path.getsize(tts_file) > 100

                if generated_ok:
                    logger.info(f"🔊 Audio vocalist generato con successo tramite edge-tts per: '{text[:45]}'")
            except Exception as e:
                logger.warning(f"⚠️ edge-tts non riuscito ({e}), fallback su gTTS in corso...")

        # 2. Fallback d'emergenza a gTTS (con pitch shift maschile in FFmpeg)
        if not generated_ok:
            def _generate_gtts(phrase: str) -> bool:
                try:
                    from gtts import gTTS
                    tts = gTTS(text=phrase, lang="it", slow=False)
                    tts.save(tts_file)
                    return True
                except Exception as ge:
                    logger.error(f"❌ Errore generazione anche con gTTS: {ge}")
                    return False

            try:
                generated_ok = await asyncio.wait_for(asyncio.to_thread(_generate_gtts, text), timeout=10.0)
            except Exception as ge:
                logger.warning(f"⚠️ Timeout o errore fallback gTTS: {ge}")
                generated_ok = False
            used_gtts_fallback = True

        if not generated_ok or not os.path.isfile(tts_file):
            logger.error(f"❌ Impossibile generare l'audio vocale per la frase: '{text}'")
            return

        async with self._tts_lock:
            if not self.voice_client or not self.voice_client.is_connected():
                return

            self._current_tts_id += 1
            tts_id = self._current_tts_id

            # Se c'è musica o un TTS/audio precedente in corso, ferma la riproduzione in sicurezza
            if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                if not interrupt_music:
                    return
                # Salva il brano musicale solo se è effettivamente musica e non un TTS precedente
                if self.current and self.current.stream_url and self._interrupted_song is None and not self._is_tts_source:
                    self._interrupted_song = self.current
                    self._interrupted_elapsed = max(0, int(time.monotonic() - self._track_start_monotonic))
                self._playing_tts = True
                self._is_tts_source = True
                try:
                    if hasattr(self.voice_client, "stop_playing"):
                        self.voice_client.stop_playing()
                    else:
                        self.voice_client.stop()
                except Exception:
                    pass
                # Attende fino a 350ms che il player si fermi completamente per evitare ClientException("Already playing audio")
                for _ in range(7):
                    if not self.voice_client.is_playing() and not self.voice_client.is_paused():
                        break
                    await asyncio.sleep(0.05)

            done_ev = asyncio.Event()
            self._tts_done_event = done_ev
            self._playing_tts = True
            self._is_tts_source = True
            self._tts_start_time = time.monotonic()
            self._tts_max_duration = min(35.0, max(6.0, len(text) / 12.0 + 3.0))

            def _after_tts(err):
                if err:
                    logger.error(f"❌ Errore callback after_tts: {err}")
                if self._current_tts_id == tts_id:
                    self._playing_tts = False
                    self._is_tts_source = False
                self.bot.loop.call_soon_threadsafe(done_ev.set)

            try:
                # Catena DSP "Franchino il Vocalist":
                # - Calore ed effetto prossimità da microfono club a condensatore (140Hz a +5dB)
                # - Taglio inscatolamento e risonanza medio-bassa (450Hz a -2.5dB)
                # - Presenza e graffio microfonico da console DJ (3200Hz a +4dB)
                # - Brillantezza ed aria sulle alte frequenze (9500Hz a +2dB)
                # - Compressore/Limiter analogico broadcast (voce solida, presente e mai saturata)
                # - Eco/Delay ritmico da console live (Franchino signature club delay)
                filters = []
                if used_gtts_fallback:
                    filters.append("asetrate=24000*0.78,aresample=48000,atempo=1.22")

                # Equalizzatore da console vocalist
                filters.append("equalizer=f=140:t=q:w=1.2:g=5")
                filters.append("equalizer=f=450:t=q:w=1.5:g=-2.5")
                filters.append("equalizer=f=3200:t=q:w=1.3:g=4")
                filters.append("equalizer=f=9500:t=q:w=1.0:g=2")

                # Compressore analogico broadcast live
                filters.append("acompressor=threshold=-16dB:ratio=4:attack=5:release=50:makeup=3")

                if getattr(self, "vocalist_mode", True):
                    if dramatic_fable:
                        filters.append("aecho=0.8:0.75:165|330:0.36|0.18")
                    else:
                        filters.append("aecho=0.8:0.75:140|280:0.28|0.14")
                filters.append("volume=1.4")
                af_chain = ",".join(filters)

                source = discord.FFmpegPCMAudio(tts_file, options=f'-vn -af "{af_chain}"')
                transformed = discord.PCMVolumeTransformer(source, volume=min(1.0, self.volume + 0.35))
                logger.info(f"🗣️ Avvio riproduzione audio TTS nella stanza vocale: '{text[:50]}'")
                self.voice_client.play(transformed, after=_after_tts)

                async def _tts_completion_monitor():
                    try:
                        timeout_dur = getattr(self, "_tts_max_duration", 25.0) + 4.0
                        await asyncio.wait_for(done_ev.wait(), timeout=timeout_dur)
                    except Exception as te:
                        logger.debug(f"Timeout o interruzione monitoraggio TTS: {te}")
                    finally:
                        if self._current_tts_id == tts_id:
                            self._playing_tts = False
                            self._is_tts_source = False
                            if self._interrupted_song and not keep_paused_after and not self._manual_stop:
                                await self.resume_interrupted_song()

                if wait:
                    await _tts_completion_monitor()
                else:
                    asyncio.create_task(_tts_completion_monitor())
            except asyncio.CancelledError:
                if self._current_tts_id == tts_id:
                    self._playing_tts = False
                    self._is_tts_source = False
                raise
            except Exception as e:
                if self._current_tts_id == tts_id:
                    self._playing_tts = False
                    self._is_tts_source = False
                logger.error(f"❌ Errore durante la riproduzione audio TTS: {e}", exc_info=True)
            finally:
                if wait and self._current_tts_id == tts_id:
                    self._playing_tts = False
                    self._is_tts_source = False

    async def resume_interrupted_song(self):
        """Riprende il brano musicale esattamente dal secondo in cui Franchino lo aveva sospeso per parlare."""
        song = self._interrupted_song
        elapsed = self._interrupted_elapsed
        self._interrupted_song = None
        self._interrupted_elapsed = 0

        if not song or not song.stream_url or not self.voice_client or not self.voice_client.is_connected():
            return
        if self.is_playing():
            return

        try:
            self.current = song
            self._playing_tts = False
            self._is_tts_source = False
            transformed_source = self._build_ffmpeg_audio_source(
                song.stream_url,
                seek_seconds=elapsed,
                http_headers=getattr(song, "http_headers", None)
            )

            def after_playback(error):
                if error:
                    if self._manual_stop:
                        return
                    try:
                        if self.bot.loop and self.bot.loop.is_running():
                            self.bot.loop.call_soon_threadsafe(
                                lambda: asyncio.create_task(self._handle_after_playback_error(song, error))
                            )
                    except Exception as e:
                        logger.error(f"Errore nella schedulazione after_playback: {e}")
                    return

                self._consecutive_play_errors = 0
                if self._manual_stop or self._playing_tts:
                    return
                try:
                    if self.bot.loop and self.bot.loop.is_running():
                        self.bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.play_next()))
                except Exception as e:
                    logger.error(f"Errore nella schedulazione after_playback: {e}")

            self._track_start_monotonic = time.monotonic() - elapsed
            self.voice_client.play(transformed_source, after=after_playback)
        except Exception as e:
            logger.debug(f"Errore ripresa brano interrotto: {e}")

    async def play_entrance_greeting(self, member: Optional[discord.Member] = None):
        """Disabilitato: Franchino non saluta più all'ingresso per non disturbare la conversazione vocale."""
        return

    # ==========================================================================
    # Effetti DJ in Tempo Reale (FFmpeg Filter Hot-Swap)
    # ==========================================================================
    def _build_ffmpeg_audio_source(
        self, stream_url: str, seek_seconds: int = 0, http_headers: Optional[Dict[str, str]] = None
    ) -> discord.PCMVolumeTransformer:
        """Crea la sorgente FFmpeg applicando probe ultra-veloce, intestazioni HTTP e filtri DJ."""
        effect_info = DJ_EFFECTS.get(self.audio_effect, DJ_EFFECTS["off"])
        af_filter = effect_info["filter"]

        before_parts = []
        if stream_url.startswith("http://") or stream_url.startswith("https://"):
            # Probe ultra-veloce: abbatte il tempo di analisi da 5.0s a ~0.1s
            before_parts.append("-probesize 65536 -analyzeduration 100000 -fflags +nobuffer+fastseek")
            before_parts.append("-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
            # Passa le intestazioni HTTP (User-Agent, etc.) se presenti per evitare 403 Forbidden su YouTube
            if http_headers:
                header_lines = "".join(f"{k}: {v}\r\n" for k, v in http_headers.items())
                safe_headers = header_lines.replace('"', '\\"')
                before_parts.append(f'-headers "{safe_headers}"')
        if seek_seconds > 0:
            before_parts.append(f"-ss {seek_seconds}")

        before_opts = " ".join(before_parts) if before_parts else None
        opts = f'-vn -af "{af_filter}"' if af_filter else "-vn"

        if before_opts:
            audio_source = discord.FFmpegPCMAudio(stream_url, before_options=before_opts, options=opts)
        else:
            audio_source = discord.FFmpegPCMAudio(stream_url, options=opts)

        return discord.PCMVolumeTransformer(audio_source, volume=self.volume)

    async def set_effect(self, effect_key: str) -> str:
        """Imposta un filtro audio DJ e lo applica in tempo reale al brano in corso senza interromperlo."""
        if effect_key not in DJ_EFFECTS:
            effect_key = "off"
        self.audio_effect = effect_key
        effect_meta = DJ_EFFECTS[effect_key]

        # Se c'è una canzone in riproduzione, applica subito il filtro al secondo corrente!
        if self.is_playing() and self.current and self.current.stream_url and self.voice_client:
            try:
                elapsed = max(0, int(time.monotonic() - self._track_start_monotonic))
                old_source = self.voice_client.source
                new_source = self._build_ffmpeg_audio_source(
                    self.current.stream_url,
                    seek_seconds=elapsed,
                    http_headers=getattr(self.current, "http_headers", None),
                )
                self.voice_client.source = new_source
                self._track_start_monotonic = time.monotonic() - elapsed
                if old_source:
                    try:
                        old_source.cleanup()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Errore applicazione effetto DJ live: {e}")

        return f"{effect_meta['emoji']} {effect_meta['label']}"

    async def play_sound_effect(self, sfx_key: str, resume: bool = True) -> str:
        """
        Riproduce un effetto sonoro della Soundboard DJ (Trombetta, Sirena, Drop, Rullo di Tamburi, Applausi, ecc.).
        Se una canzone sta suonando, la mette un istante in pausa al secondo esatto, spara l'effetto sonoro
        e riprende subito la canzone da dove si era fermata (se resume=True)!
        """
        from sound_effects import SOUNDBOARD_EFFECTS, ensure_sfx_file

        if sfx_key not in SOUNDBOARD_EFFECTS:
            sfx_key = "airhorn"
        meta = SOUNDBOARD_EFFECTS[sfx_key]
        label = f"{meta['emoji']} {meta['label']}"

        if (not self.voice_client or not self.voice_client.is_connected()) and self.guild.voice_client:
            if self.guild.voice_client.is_connected():
                self.voice_client = self.guild.voice_client

        if not self.voice_client or not self.voice_client.is_connected():
            return label

        try:
            wav_path = await asyncio.to_thread(ensure_sfx_file, sfx_key)
        except Exception as e:
            logger.error(f"Errore generazione effetto sonoro {sfx_key}: {e}")
            return label

        # Se c'è una canzone in riproduzione, salva il punto esatto e interrompila temporaneamente
        if self.is_playing() and not self._playing_tts:
            if self.current and self.current.stream_url and self._interrupted_song is None:
                self._interrupted_song = self.current
                self._interrupted_elapsed = max(0, int(time.monotonic() - self._track_start_monotonic))
            self._playing_tts = True
            if hasattr(self.voice_client, "stop_playing"):
                self.voice_client.stop_playing()
            else:
                self.voice_client.stop()
            await asyncio.sleep(0.06)

        done_ev = asyncio.Event()
        self._tts_done_event = done_ev
        self._playing_tts = True

        def _after_sfx(err):
            self._playing_tts = False
            self.bot.loop.call_soon_threadsafe(done_ev.set)

        try:
            source = discord.FFmpegPCMAudio(wav_path, options='-vn -af "volume=1.5"')
            transformed = discord.PCMVolumeTransformer(source, volume=min(1.0, self.volume + 0.30))
            self.voice_client.play(transformed, after=_after_sfx)
            await asyncio.wait_for(done_ev.wait(), timeout=5.0)
        except asyncio.CancelledError:
            self._playing_tts = False
            raise
        except Exception as e:
            self._playing_tts = False
            logger.debug(f"Errore riproduzione SFX {sfx_key}: {e}")
        finally:
            self._playing_tts = False

        if resume and self._interrupted_song and not self._manual_stop:
            await self.resume_interrupted_song()

        return label

    # ==========================================================================
    # Playlist "Preferiti del Server" (Salvataggio & Riproduzione)
    # ==========================================================================
    def get_favorites(self) -> List[dict]:
        all_favs = _load_all_favorites()
        return all_favs.get(str(self.guild.id), [])

    def save_current_to_favorites(self, user=None) -> Tuple[bool, Optional[SongInfo], int]:
        """Salva il brano attualmente in riproduzione nei Preferiti del server."""
        song = self.current or self.last_played_song
        if not song:
            return False, None, len(self.get_favorites())

        all_favs = _load_all_favorites()
        guild_key = str(self.guild.id)
        fav_list = all_favs.get(guild_key, [])

        url = song.webpage_url or song.spotify_url or ""
        for item in fav_list:
            if (url and item.get("webpage_url") == url) or item.get("title", "").lower() == song.title.lower():
                return False, song, len(fav_list)

        fav_list.append({
            "title": song.title,
            "webpage_url": url,
            "duration": song.duration,
            "thumbnail": song.thumbnail,
            "uploader": song.uploader,
            "saved_by": getattr(user, "display_name", "Amico"),
        })
        all_favs[guild_key] = fav_list
        _save_all_favorites(all_favs)
        return True, song, len(fav_list)

    def load_favorites_as_songs(self, requester=None) -> List[SongInfo]:
        """Converte i Preferiti del server in oggetti SongInfo pronti da riprodurre."""
        fav_list = self.get_favorites()
        songs: List[SongInfo] = []
        for item in fav_list:
            songs.append(
                SongInfo(
                    title=item.get("title", "Brano Preferito"),
                    webpage_url=item.get("webpage_url", ""),
                    duration=int(item.get("duration") or 0),
                    thumbnail=item.get("thumbnail"),
                    uploader=item.get("uploader"),
                    requester=requester,
                    source_type="youtube",
                )
            )
        return songs

    # ==========================================================================
    # Connessione Vocale & Watchdog Multi-Utente
    # ==========================================================================
    async def _cleanup_stale_voice_client(self):
        """Pulisce eventuali sessioni vocali zombie rimaste registrate in discord.py."""
        for vc in (self.voice_client, self.guild.voice_client):
            if vc is not None:
                try:
                    if hasattr(vc, "stop_listening"):
                        vc.stop_listening()
                except Exception:
                    pass
                try:
                    vc.stop()
                except Exception:
                    pass
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
        try:
            if hasattr(self.guild, "_state") and hasattr(self.guild._state, "_remove_voice_client"):
                self.guild._state._remove_voice_client(self.guild.id)
        except Exception:
            pass
        self.voice_client = None

    async def connect(
        self,
        channel: discord.VoiceChannel,
        text_channel: Optional[discord.TextChannel] = None,
        is_auto: bool = False,
    ) -> Optional[discord.VoiceClient]:
        """Connette Franchino al canale vocale specificato e attiva l'ascolto dei comandi vocali."""
        async with self._connect_lock:
            # Se è una connessione automatica e il cooldown di disconnessione manuale è attivo, non connetterti
            if is_auto and self.has_manual_leave_cooldown():
                logger.debug(f"🛑 Auto-connessione annullata per '{self.guild.name}': cooldown disconnessione manuale attivo.")
                return None

            # Backoff / cooldown sulle riconnessioni per evitare rate limit Gateway Voice OP 4 (Close Code 4021)
            now = time.monotonic()
            time_since_last_connect = now - getattr(self, "_last_connect_time", 0.0)
            connect_cooldown = getattr(self, "_connect_cooldown_seconds", 5.0)
            if time_since_last_connect < connect_cooldown:
                if self.is_connected() and self.voice_client and self.voice_client.channel == channel:
                    return self.voice_client
                wait_time = connect_cooldown - time_since_last_connect
                logger.debug(f"⏳ Cooldown connessione vocale per '{self.guild.name}': attesa {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)

                if is_auto and self.has_manual_leave_cooldown():
                    logger.debug(f"🛑 Auto-connessione annullata dopo attesa per '{self.guild.name}': cooldown attivo.")
                    return None

            self._last_connect_time = time.monotonic()
            if not is_auto:
                self.clear_manual_leave_cooldown()

            self.text_channel = resolve_preferred_text_channel(self.guild, text_channel or self.text_channel or channel)

            # Sincronizza eventuale voice_client già attivo sul server
            if (not self.voice_client or not self.voice_client.is_connected()) and self.guild.voice_client:
                if self.guild.voice_client.is_connected():
                    self.voice_client = self.guild.voice_client
                else:
                    await self._cleanup_stale_voice_client()

            if not self.voice_client or not self.voice_client.is_connected():
                if self.guild.voice_client is not None:
                    await self._cleanup_stale_voice_client()
                try:
                    import voice_commands  # noqa: F401
                    from discord.ext import voice_recv
                    self.voice_client = await channel.connect(
                        cls=voice_recv.VoiceRecvClient,
                        reconnect=True,
                        timeout=20.0,
                        self_deaf=False,
                    )
                except Exception as e:
                    logger.warning(f"Errore primo tentativo connessione vocale ({e}), pulizia e riprova...")
                    await self._cleanup_stale_voice_client()
                    await asyncio.sleep(0.5)
                    try:
                        from discord.ext import voice_recv
                        self.voice_client = await channel.connect(
                            cls=voice_recv.VoiceRecvClient,
                            reconnect=True,
                            timeout=20.0,
                            self_deaf=False,
                        )
                    except Exception as e2:
                        logger.warning(f"Fallback a VoiceClient standard ({e2})")
                        await self._cleanup_stale_voice_client()
                        self.voice_client = await channel.connect(reconnect=True, timeout=20.0, self_deaf=False)
            elif self.voice_client.channel != channel:
                await self.voice_client.move_to(channel)

            self._ensure_voice_listening()
            self._reset_inactivity_timer()
            return self.voice_client

    def _ensure_voice_listening(self):
        """Avvia il sink di riconoscimento vocale se non è già in ascolto e mantiene attivo il watchdog."""
        if not self.voice_client or not self.voice_client.is_connected():
            if self.guild.voice_client and self.guild.voice_client.is_connected():
                self.voice_client = self.guild.voice_client
            else:
                return
        try:
            if hasattr(self.voice_client, "is_listening") and not self.voice_client.is_listening():
                from voice_commands import FranchinoVoiceSink
                self.voice_client.listen(FranchinoVoiceSink(self))
                logger.info(f"🎙️ Ascolto vocale 'Franchino...' attivo nel server {self.guild.name}")
        except Exception as e:
            logger.warning(f"Impossibile avviare l'ascolto vocale: {e}")

        if not hasattr(self, "_watchdog_task") or self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = self.bot.loop.create_task(self._voice_listener_watchdog())

    async def _voice_listener_watchdog(self):
        """
        Watchdog audio sink: controlla periodicamente che, SE Franchino è connesso,
        il sink di ricezione vocale rimanga attivo.
        NON chiama MAI self.connect() per evitare loop di riconnessione e rate limit 4021.
        """
        try:
            consecutive_disconnected_checks = 0
            while True:
                await asyncio.sleep(2.0)
                if self._restarting:
                    continue

                if (not self.voice_client or not self.voice_client.is_connected()) and self.guild.voice_client:
                    if self.guild.voice_client.is_connected():
                        self.voice_client = self.guild.voice_client
                        consecutive_disconnected_checks = 0

                # Se non è connesso, non tentare di riconnetterti:
                # la riconnessione è gestita esclusivamente da on_voice_state_update o comandi utente.
                if not self.voice_client or not self.voice_client.is_connected():
                    consecutive_disconnected_checks += 1
                    if consecutive_disconnected_checks >= 5:
                        logger.debug(f"🛑 Watchdog vocale per '{self.guild.name}' terminato per disconnessione prolungata.")
                        break
                    continue

                consecutive_disconnected_checks = 0

                reader = getattr(self.voice_client, "_reader", None)
                router_dead = bool(
                    reader
                    and hasattr(reader, "packet_router")
                    and not reader.packet_router.is_alive()
                )
                sink = getattr(reader, "sink", None) if reader else None
                sink_dead = bool(
                    reader
                    and (
                        sink is None
                        or not getattr(sink, "_running", False)
                        or (hasattr(sink, "_monitor_task") and sink._monitor_task and sink._monitor_task.done())
                    )
                )
                not_listening = hasattr(self.voice_client, "is_listening") and not self.voice_client.is_listening()
                if not_listening or router_dead or sink_dead:
                    logger.info("🔄 Riattivazione automatica del listener vocale di Franchino...")
                    from voice_commands import FranchinoVoiceSink
                    try:
                        if hasattr(self.voice_client, "stop_listening"):
                            self.voice_client.stop_listening()
                    except Exception:
                        pass
                    try:
                        self.voice_client.listen(FranchinoVoiceSink(self))
                    except Exception as e:
                        logger.debug(f"Errore riattivazione listener: {e}")
        except asyncio.CancelledError:
            pass

    async def restart_console(self, target_channel: Optional[discord.VoiceChannel] = None) -> Optional[discord.VoiceClient]:
        """Riavvia i lock, ferma l'audio bloccato e riavvia il ricevitore microfono senza uscire dalla stanza."""
        self._restarting = True
        try:
            channel = target_channel
            if not channel and self.voice_client and self.voice_client.channel:
                channel = self.voice_client.channel
            if not channel and self.guild.voice_client and self.guild.voice_client.channel:
                channel = self.guild.voice_client.channel
            if not channel:
                bot_user = getattr(self.bot, "user", None)
                bot_id = bot_user.id if bot_user else 0
                if not bot_id and getattr(self.guild, "me", None):
                    bot_id = getattr(self.guild.me, "id", 0)
                afk_channel = getattr(self.guild, "afk_channel", None)
                for vc_ch in self.guild.voice_channels:
                    if afk_channel and (vc_ch == afk_channel or getattr(vc_ch, "id", None) == getattr(afk_channel, "id", None)):
                        continue
                    human_ids = []
                    for uid in getattr(vc_ch, "voice_states", {}).keys():
                        if bot_id and uid == bot_id:
                            continue
                        m = self.guild.get_member(uid) if hasattr(self.guild, "get_member") else None
                        if m and getattr(m, "bot", False):
                            continue
                        u = getattr(self.bot, "get_user", lambda _: None)(uid)
                        if u and getattr(u, "bot", False):
                            continue
                        human_ids.append(uid)
                    if human_ids:
                        channel = vc_ch
                        break

            self.play_lock = asyncio.Lock()
            self._tts_lock = asyncio.Lock()
            self.queue.clear()
            self.current = None
            self.loop_mode = "off"
            self.audio_effect = "off"
            self._is_ducked = False
            self._playing_tts = False
            self._is_tts_source = False
            self._interrupted_song = None
            self._interrupted_elapsed = 0

            vc = self.voice_client or self.guild.voice_client
            if vc and vc.is_connected():
                self.voice_client = vc
                try:
                    if hasattr(vc, "stop_playing"):
                        vc.stop_playing()
                    else:
                        vc.stop()
                except Exception:
                    pass
                try:
                    if hasattr(vc, "stop_listening"):
                        vc.stop_listening()
                except Exception:
                    pass
                await asyncio.sleep(0.15)
                if channel and vc.channel != channel:
                    try:
                        await vc.move_to(channel)
                    except Exception:
                        pass
                try:
                    from voice_commands import FranchinoVoiceSink
                    vc.listen(FranchinoVoiceSink(self))
                except Exception:
                    pass
                self._ensure_voice_listening()
                return vc

            if hasattr(self, "_watchdog_task") and self._watchdog_task:
                self._watchdog_task.cancel()
                self._watchdog_task = None

            await self._cleanup_stale_voice_client()
            await asyncio.sleep(0.5)

            if channel:
                return await self.connect(channel, self.text_channel or channel)
            return None
        finally:
            self._restarting = False

    async def disconnect(self):
        """Disconnette Franchino dal canale vocale e pulisce lo stato."""
        self._is_disconnecting = True
        try:
            async with self._connect_lock:
                self._cancel_inactivity_timer()
                if hasattr(self, "_watchdog_task") and self._watchdog_task:
                    self._watchdog_task.cancel()
                    self._watchdog_task = None
                self.queue.clear()
                self.current = None

                if self.now_playing_message:
                    try:
                        await self.now_playing_message.edit(view=None)
                    except Exception:
                        pass
                    self.now_playing_message = None

                await self._cleanup_stale_voice_client()
        finally:
            self._is_disconnecting = False

    def pause(self):
        if self.is_playing():
            self.voice_client.pause()

    def resume(self):
        if self.is_paused():
            self.voice_client.resume()

    def skip(self):
        """Salta il brano corrente forzando l'interruzione senza fermare il microfono."""
        self._consecutive_play_errors = 0
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            if self.loop_mode == "song":
                self.loop_mode = "off"
            if hasattr(self.voice_client, "stop_playing"):
                self.voice_client.stop_playing()
            else:
                self.voice_client.stop()

    def stop(self):
        """
        Ferma IMMEDIATAMENTE qualsiasi attività audio di Franchino:
        - Interrompe la musica o brani in corso (e svuota la coda)
        - Interrompe all'istante la voce di Franchino se sta parlando (TTS, favole, spiegazioni wiki, annunci)
        - Interrompe gli effetti sonori della soundboard (trombetta, sirene, ecc.)
        - Azzera qualsiasi brano sospeso che avrebbe dovuto riprendere dopo il parlato
        - Spegne autoplay e radio
        - Mantiene il microfono aperto e pronto in ascolto per i prossimi comandi vocali.
        """
        self._manual_stop = True
        self._consecutive_play_errors = 0
        self.queue.clear()
        self.current = None
        self.autoplay = False
        self.loop_mode = "off"
        self._interrupted_song = None
        self._interrupted_elapsed = 0
        self._playing_tts = False

        # Risveglia ed esce istantaneamente da qualsiasi await su speak_tts o play_sound_effect
        if getattr(self, "_tts_done_event", None) is not None:
            self._tts_done_event.set()

        # Ferma l'output audio reale su Discord (musica, voce o effetti)
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            try:
                if hasattr(self.voice_client, "stop_playing"):
                    self.voice_client.stop_playing()
                else:
                    self.voice_client.stop()
            except Exception as e:
                logger.debug(f"Errore stop voice_client: {e}")

        self.restore_duck_volume()
        self._ensure_voice_listening()
        self._reset_inactivity_timer()

    def shuffle(self):
        """Mescola casualmente i brani presenti in coda."""
        temp = list(self.queue)
        random.shuffle(temp)
        self.queue = collections.deque(temp)

    def set_volume(self, volume: float):
        """Imposta il volume (0.01 - 1.0)."""
        self.volume = max(0.01, min(1.0, volume))
        self._is_ducked = False
        if self.voice_client and self.voice_client.source:
            if hasattr(self.voice_client.source, "volume"):
                self.voice_client.source.volume = self.volume

    def remove(self, index: int) -> Optional[SongInfo]:
        """Rimuove un brano dalla coda per indice (1-based)."""
        if 1 <= index <= len(self.queue):
            temp = list(self.queue)
            removed = temp.pop(index - 1)
            self.queue = collections.deque(temp)
            return removed
        return None

    def clear_queue(self) -> int:
        """Svuota i brani in attesa mantenendo la riproduzione del brano corrente."""
        count = len(self.queue)
        self.queue.clear()
        return count

    async def add_songs(self, songs: List[SongInfo], text_channel: Optional[discord.TextChannel] = None):
        """Aggiunge una o più tracce alla coda e avvia la riproduzione se non già attiva."""
        self._manual_stop = False
        self._consecutive_play_errors = 0
        self.text_channel = resolve_preferred_text_channel(self.guild, text_channel or self.text_channel)

        self._cancel_inactivity_timer()

        for song in songs:
            self.queue.append(song)

        if not self.is_active:
            await self.play_next()
        else:
            asyncio.create_task(self._prefetch_next_song())

    async def play_now(
        self,
        songs: List[SongInfo],
        text_channel: Optional[discord.TextChannel] = None,
        voice_announce: Optional[str] = None,
    ):
        """Avvia immediatamente il brano richiesto (usato dai comandi vocali 'Franchino metti...')."""
        self._manual_stop = False
        self._consecutive_play_errors = 0
        self.text_channel = resolve_preferred_text_channel(self.guild, text_channel or self.text_channel)

        self._cancel_inactivity_timer()

        for song in reversed(songs):
            self.queue.appendleft(song)

        if self.is_active:
            self.skip()
        else:
            await self.play_next()

        if voice_announce and songs:
            asyncio.create_task(self.speak_tts(voice_announce, wait=False))

    async def stream_sports_match(
        self,
        match: Dict[str, Any],
        voice_channel: Optional[discord.VoiceChannel] = None,
        text_channel: Optional[discord.TextChannel] = None,
        requester: Optional[Union[discord.Member, discord.User, str]] = None,
        send_embed: bool = True,
    ) -> Tuple[Optional[SongInfo], discord.Embed, discord.ui.View, str]:
        """
        Avvia lo streaming audio della telecronaca della partita scelta nel canale vocale di Discord,
        e pubblica un Discord Embed ricco con statistiche live, comandi del player e link video web.
        """
        from sports_scraper import sports_scraper

        if (not self.voice_client or not self.voice_client.is_connected()) and voice_channel:
            await self.connect(voice_channel, text_channel or self.text_channel)

        # Se il match non ha ancora i dettagli completi dello stream, risolvili
        if not match.get("direct_hls_available") and not match.get("stream_url"):
            stream_meta = await sports_scraper.get_match_stream_info(
                match.get("id") or match.get("match_id"), match.get("sport_type", 1)
            )
            match.update(stream_meta)

        title = match.get("title", "Partita Live")
        league = match.get("league") or match.get("sport_name") or "🏆 Live Sport"
        channel = match.get("channel_name") or match.get("tv_channels") or "MadPlay77 Live"
        match_web_url = match.get("match_page_url") or match.get("url") or match.get("web_url") or "https://web01.madplay77.ru/it/football.html"
        stream_url = match.get("stream_url")
        direct_hls = match.get("direct_hls_available", False) and bool(stream_url) and not str(stream_url).endswith(".html")

        stream_headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://web01.madplay77.ru/",
            "Origin": "https://web01.madplay77.ru",
        }

        song = None
        if direct_hls:
            song = SongInfo(
                title=f"⚽ {title} - Telecronaca Live ({league})",
                webpage_url=match_web_url,
                stream_url=stream_url,
                duration=0,
                thumbnail=match.get("league_logo"),
                uploader=f"MadPlay77 ({channel})",
                requester=requester,
                source_type="sports_stream",
                http_headers=stream_headers,
                is_direct_stream=True,
            )

        embed, view, voice_msg = sports_scraper.build_match_streaming_embed(match, player=self, requester=requester)

        target_ch = resolve_preferred_text_channel(self.guild, text_channel or self.text_channel)

        if direct_hls and song:
            await self.play_now([song], text_channel=target_ch, voice_announce=voice_msg)
        else:
            # Flusso audio non ancora attivo su CDN: annuncia via TTS vocale se connesso
            if self.voice_client and self.voice_client.is_connected() and voice_msg:
                asyncio.create_task(self.speak_tts(voice_msg, wait=False, interrupt_music=True))

        if send_embed and target_ch:
            try:
                await target_ch.send(
                    content=f"🎙️ **Franchino**: *\"{voice_msg}\"*",
                    embed=embed,
                    view=view,
                )
            except Exception as e:
                logger.debug(f"Errore invio embed streaming sportivo: {e}")

        return song, embed, view, voice_msg

    async def _prefetch_next_song(self):
        """Pre-scarica in background il prossimo brano in coda per evitare attese al cambio traccia."""
        if not self.queue:
            return
        next_in_queue = self.queue[0]
        if next_in_queue.stream_url:
            return
        try:
            await self.source_manager.ensure_stream_url(next_in_queue)
            logger.info(f"Pre-caricato in cache il prossimo brano: {next_in_queue.title}")
        except Exception as e:
            logger.debug(f"Pre-caricamento non riuscito per {next_in_queue.title}: {e}")

    async def _send_circuit_breaker_warning(self):
        """Invia un avviso in chat quando scatta il circuit breaker anti-crash loop."""
        if self.text_channel:
            try:
                await self.text_channel.send(
                    "🛑 Franchino ha riscontrato troppi errori consecutivi durante la riproduzione. Riproduzione interrotta per sicurezza!"
                )
            except Exception:
                pass

    async def _handle_after_playback_error(self, song: Optional[SongInfo], error: Exception):
        """Gestisce in modo difensivo gli errori di riproduzione asincroni nel thread audio."""
        self._consecutive_play_errors += 1
        title = getattr(song, "title", "Sconosciuto")
        logger.error(f"Errore durante la riproduzione del brano '{title}': {error}")
        self.current = None
        if self.loop_mode == "song":
            self.loop_mode = "off"

        if self._consecutive_play_errors > 3:
            logger.warning(
                f"🛑 Raggiunto limite errori consecutivi ({self._consecutive_play_errors}) per guild {self.guild.id}. Interruzione riproduzione."
            )
            await self._send_circuit_breaker_warning()
            self._reset_inactivity_timer()
            return

        if self.text_channel:
            try:
                await self.text_channel.send(
                    f"⚠️ Franchino non è riuscito a riprodurre **{title}**: `{error}`. Salto al prossimo brano!"
                )
            except Exception:
                pass

        await asyncio.sleep(1.0)
        if not self._manual_stop:
            asyncio.create_task(self.play_next())

    async def play_next(self):
        """Prepara e riproduce la traccia successiva nella coda (o un brano correlato se AutoPlay Radio è attivo)."""
        async with self.play_lock:
            if (not self.voice_client or not self.voice_client.is_connected()) and self.guild.voice_client:
                self.voice_client = self.guild.voice_client

            if not self.voice_client or not self.voice_client.is_connected():
                logger.warning("Voice client non connesso in play_next.")
                self._reset_inactivity_timer()
                return

            self._ensure_voice_listening()

            next_song: Optional[SongInfo] = None

            if self.loop_mode == "song" and self.current:
                next_song = self.current
            elif self.loop_mode == "queue" and self.current:
                self.queue.append(self.current)
                if self.queue:
                    next_song = self.queue.popleft()
            elif self.queue:
                next_song = self.queue.popleft()

            # Modalità AutoPlay / Radio Infinita quando la coda termina
            if not next_song and self.autoplay and self.last_played_song:
                related = await self.source_manager.resolve_related_song(self.last_played_song, self.history_urls)
                if related:
                    next_song = related
                    if self.text_channel:
                        try:
                            await self.text_channel.send(
                                f"📻 **AutoPlay Radio di Franchino**: proseguo la serata con **{related.title}**! ✨"
                            )
                        except Exception:
                            pass

            if not next_song:
                self.current = None
                self._consecutive_play_errors = 0
                self._reset_inactivity_timer()
                if self.now_playing_message:
                    try:
                        await self.now_playing_message.edit(view=None)
                    except Exception:
                        pass
                return

            self.current = next_song
            self.last_played_song = next_song
            if next_song.webpage_url:
                self.history_urls.add(next_song.webpage_url)
                if len(self.history_urls) > 50:
                    self.history_urls.clear()

            self._cancel_inactivity_timer()

            try:
                stream_url = await self.source_manager.ensure_stream_url(next_song)
                transformed_source = self._build_ffmpeg_audio_source(
                    stream_url, seek_seconds=0, http_headers=getattr(next_song, "http_headers", None)
                )

                def after_playback(error):
                    if error:
                        if self._manual_stop:
                            return
                        try:
                            if self.bot.loop and self.bot.loop.is_running():
                                self.bot.loop.call_soon_threadsafe(
                                    lambda: asyncio.create_task(self._handle_after_playback_error(next_song, error))
                                )
                        except Exception as e:
                            logger.error(f"Errore nella schedulazione after_playback: {e}")
                        return

                    self._consecutive_play_errors = 0
                    if self._manual_stop or self._playing_tts:
                        return
                    try:
                        if self.bot.loop and self.bot.loop.is_running():
                            self.bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.play_next()))
                    except Exception as e:
                        logger.error(f"Errore nella schedulazione after_playback: {e}")

                self._track_start_monotonic = time.monotonic()
                self.voice_client.play(transformed_source, after=after_playback)
                self._consecutive_play_errors = 0

                await self._send_now_playing_message(next_song)

                if self.queue:
                    asyncio.create_task(self._prefetch_next_song())

            except Exception as e:
                # Se lo streaming diretto fallisce, tenta un download audio locale come fallback prima di arrendersi (solo brani multimediali, non telecronache live)
                if getattr(next_song, "is_direct_stream", False) and getattr(next_song, "source_type", "") != "sports_stream":
                    try:
                        logger.warning(f"Flusso diretto non riuscito per '{getattr(next_song, 'title', '')}', tento fallback download locale...")
                        target = next_song.webpage_url or next_song.title
                        dl_song = await asyncio.to_thread(self.source_manager._download_youtube_audio, target, next_song.requester)
                        next_song.stream_url = dl_song.stream_url
                        next_song.is_direct_stream = False
                        transformed_source = self._build_ffmpeg_audio_source(dl_song.stream_url, seek_seconds=0)
                        self._track_start_monotonic = time.monotonic()
                        self.voice_client.play(transformed_source, after=after_playback)
                        self._consecutive_play_errors = 0
                        await self._send_now_playing_message(next_song)
                        if self.queue:
                            asyncio.create_task(self._prefetch_next_song())
                        return
                    except Exception as fb_err:
                        logger.error(f"Fallback download fallito per '{getattr(next_song, 'title', '')}': {fb_err}")

                self._consecutive_play_errors += 1
                title = getattr(next_song, "title", "Sconosciuto")
                logger.error(f"Errore riproduzione brano '{title}': {e}", exc_info=True)
                self.current = None
                if self.loop_mode == "song":
                    self.loop_mode = "off"

                if self._consecutive_play_errors > 3:
                    logger.warning(
                        f"🛑 Raggiunto limite errori consecutivi ({self._consecutive_play_errors}) per guild {self.guild.id}. Interruzione riproduzione."
                    )
                    await self._send_circuit_breaker_warning()
                    self._reset_inactivity_timer()
                    return

                if self.text_channel:
                    try:
                        await self.text_channel.send(
                            f"⚠️ Franchino non è riuscito a riprodurre **{title}**: `{e}`. Salto al prossimo brano!"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(1.0)
                if not self._manual_stop:
                    asyncio.create_task(self.play_next())

    async def _send_now_playing_message(self, song: SongInfo):
        """Invia un messaggio ricco con i controlli chat interattivi nel canale #musica (o nella chat di avvio)."""
        if getattr(song, "source_type", "") == "sports_stream":
            return  # Lo streaming sportivo ha il proprio MatchStreamingView dedicato

        self.text_channel = resolve_preferred_text_channel(
            self.guild,
            self.text_channel or (self.voice_client.channel if self.voice_client else None)
        )
        if not self.text_channel:
            return

        if self.now_playing_message:
            try:
                await self.now_playing_message.edit(view=None)
            except Exception:
                pass

        quote = random.choice(FRANCHINO_QUOTES)
        embed = song.create_embed(title_prefix=f"🎧 Franchino in console: {quote}")

        queue_info = f"{len(self.queue)} brani in attesa" if self.queue else ("AutoPlay Radio 📻" if self.autoplay else "Nessun altro brano")
        loop_status = "Traccia 🔂" if self.loop_mode == "song" else ("Coda 🔁" if self.loop_mode == "queue" else "Disattivo ➡️")
        effect_meta = DJ_EFFECTS.get(self.audio_effect, DJ_EFFECTS["off"])

        embed.add_field(name="📜 Coda", value=f"`{queue_info}`", inline=True)
        embed.add_field(name="🔁 Loop", value=f"`{loop_status}`", inline=True)
        embed.add_field(name="🔊 Volume", value=f"`{int(self.volume * 100)}%`", inline=True)
        embed.add_field(name="🎛️ Effetto DJ", value=f"`{effect_meta['emoji']} {effect_meta['label']}`", inline=True)
        embed.add_field(name="📻 Radio AutoPlay", value=f"`{'Attiva ✅' if self.autoplay else 'Spenta ❌'}`", inline=True)
        embed.add_field(name="❤️ Preferiti Server", value=f"`{len(self.get_favorites())} salvati`", inline=True)

        view = FranchinoControlsView(self)
        try:
            self.now_playing_message = await self.text_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.warning(f"Impossibile inviare messaggio Now Playing: {e}")

    def _reset_inactivity_timer(self):
        """Avvia o resetta il timer di inattività: se la stanza resta inattiva per INACTIVITY_TIMEOUT, disconnette il bot."""
        self._cancel_inactivity_timer()
        self._ensure_voice_listening()
        self._last_activity_time = time.monotonic()
        if self.is_connected() and not self.is_playing() and not self.queue:
            self.inactivity_task = self.bot.loop.create_task(self._inactivity_countdown())

    def _cancel_inactivity_timer(self):
        """Cancella eventuali timer di inattività attivi."""
        if self.inactivity_task and not self.inactivity_task.done():
            self.inactivity_task.cancel()
        self.inactivity_task = None

    async def _inactivity_countdown(self):
        """Disconnette Franchino in modo pulito se rimane inattivo senza brani o comandi vocali per INACTIVITY_TIMEOUT secondi."""
        timeout = float(INACTIVITY_TIMEOUT)
        try:
            while True:
                now = time.monotonic()
                elapsed = now - getattr(self, "_last_activity_time", now)
                remaining = timeout - elapsed
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 5.0))
                # Se è iniziata la musica, TTS o ci sono canzoni in coda, fermiamo il conto alla rovescia
                if self.is_playing() or self.is_speaking_tts or (self.queue and len(self.queue) > 0):
                    return

            if not self.is_playing() and not self.is_speaking_tts and not self.queue and self.is_connected():
                # Verifichiamo se ci sono utenti umani nel canale vocale
                channel = getattr(self.voice_client, "channel", None)
                if channel:
                    try:
                        from main import _get_human_ids_in_channel
                        human_ids = _get_human_ids_in_channel(channel)
                        if human_ids:
                            # Ci sono utenti umani nella stanza: Franchino NON abbandona la console
                            return
                    except Exception:
                        pass

                logger.info(f"⏳ Inattività di {int(timeout)}s trascorsa su '{self.guild.name}': Franchino lascia la console.")
                if self.text_channel:
                    try:
                        await self.text_channel.send("👋 **Franchino lascia la console per inattività!** A presto ragazzi! 🎧")
                    except Exception:
                        pass
                await self.disconnect()
        except asyncio.CancelledError:
            pass

    def create_queue_embed(self, page: int = 1) -> discord.Embed:
        """Genera un embed formattato con l'elenco dei brani in coda e paginazione."""
        items_per_page = 10
        total_items = len(self.queue)
        total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
        page = max(1, min(page, total_pages))

        embed = discord.Embed(
            title="📜 Coda musicale di Franchino",
            color=COLOR_INFO
        )

        if self.current:
            embed.add_field(
                name="▶️ Attualmente in riproduzione",
                value=f"**[{self.current.title}]({self.current.webpage_url or self.current.spotify_url})**\n⏱️ `{self.current.duration_str}` | Richiesto da: {self.current.requester_mention}",
                inline=False
            )

        if not self.queue:
            embed.description = "La coda è attualmente vuota! Usa `/play` o dì *\"Franchino metti...\"* per aggiungere nuova musica."
        else:
            start_idx = (page - 1) * items_per_page
            end_idx = min(start_idx + items_per_page, total_items)
            queue_list = list(self.queue)

            lines = []
            for i in range(start_idx, end_idx):
                song = queue_list[i]
                lines.append(f"`{i + 1}.` **[{song.title}]({song.webpage_url or song.spotify_url})** (`{song.duration_str}`) - {song.requester_mention}")

            embed.description = "\n".join(lines)

            total_seconds = sum(s.duration for s in self.queue)
            embed.set_footer(
                text=f"Pagina {page}/{total_pages} • {total_items} brani in attesa • Durata totale: {format_duration(total_seconds)}"
            )

        return embed


class PlayerManager:
    """Mantiene e gestisce i player musicali attivi per ciascun server (Guild)."""

    def __init__(self, bot: commands.Bot, source_manager: SourceManager):
        self.bot = bot
        self.source_manager = source_manager
        self.players: Dict[int, GuildPlayer] = {}

    def get_player(self, guild: discord.Guild) -> GuildPlayer:
        """Ottiene o crea il player dedicato per un server."""
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(self.bot, guild, self.source_manager)
        return self.players[guild.id]

    async def remove_player(self, guild_id: int):
        """Rimuove e disconnette il player di un server."""
        if guild_id in self.players:
            await self.players[guild_id].disconnect()
            del self.players[guild_id]
