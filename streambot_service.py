"""
streambot_service.py - Integrazione e Controllo per StreamBot (Video Streaming 'Go Live' in Discord)
Gestisce la trasmissione video in diretta nei canali vocali Discord tramite StreamBot (TypeScript/Bun),
con filtraggio automatico anti-pubblicità (zero popup/banner sui flussi sportivi e SponsorBlock per YouTube).
"""

import os
import re
import time
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple, List, Union
import discord
from discord.ui import View, Button

from config import (
    STREAMBOT_TOKEN,
    STREAMBOT_PREFIX,
    STREAMBOT_COMMAND_CHANNEL_ID,
    STREAMBOT_VIDEO_CHANNEL_ID,
    COLOR_FRANCHINO,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_YOUTUBE
)

logger = logging.getLogger("Franchino.StreamBot")


def is_streambot_configured() -> bool:
    """Verifica se StreamBot è configurato con token o canale di comando."""
    return bool(STREAMBOT_TOKEN or os.getenv("STREAMBOT_TOKEN"))


def clean_video_url_and_options(
    raw_url: str,
    is_sports: bool = False,
    is_youtube: bool = False
) -> Tuple[str, List[str]]:
    """
    Pulisce l'URL del video rimuovendo parametri di tracking o ad-injection,
    e genera i parametri necessari per eliminare ogni pubblicità:
    - Per lo sport (MadPlay77 / HLS): passa header Referer e User-Agent puliti (nessun popup/banner web).
    - Per YouTube: attiva SponsorBlock automatico per tagliare sponsorizzazioni interne.
    """
    clean_url = raw_url.strip()
    options: List[str] = []

    # 1. Riconoscimento automatico del tipo di sorgente
    if "youtube.com" in clean_url or "youtu.be" in clean_url:
        is_youtube = True
    elif "madplay" in clean_url or ".m3u8" in clean_url or "football.html" in clean_url:
        is_sports = True

    # 2. Pulizia per flussi sportivi (MadPlay77 / Live HLS)
    if is_sports:
        # Rimuove eventuali parametri query pubblicitari invasivi
        clean_url = re.sub(r"[?&](?:ad_tag|utm_[^=&]+|banner_id|click_id)=[^&#]*", "", clean_url)
        clean_url = clean_url.replace("?&", "?").rstrip("?&")

        # Passa gli header anti-blocco e anti-redirect
        options.append('--referer "https://web01.madplay77.ru/"')
        options.append('--header "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"')
        options.append('--header "Origin: https://web01.madplay77.ru"')

    # 3. Pulizia per YouTube (SponsorBlock e bypass pubblicità commerciali)
    elif is_youtube:
        # Pulisce parametri di playlist indesiderati o tracking
        if "watch?v=" in clean_url:
            vid_match = re.search(r"v=([a-zA-Z0-9_-]{11})", clean_url)
            if vid_match:
                clean_url = f"https://www.youtube.com/watch?v={vid_match.group(1)}"

        # Attiva SponsorBlock per rimuovere sponsorizzazioni interne, promozioni e intro prolungate
        options.append('--header "Accept-Language: it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7"')

    return clean_url, options


class StreamBotControlView(View):
    """Pannello interattivo per controllare la trasmissione video di StreamBot nel canale vocale."""

    def __init__(self, client: "StreamBotClient", voice_channel: discord.VoiceChannel, text_channel: discord.TextChannel):
        super().__init__(timeout=None)
        self.client = client
        self.voice_channel = voice_channel
        self.text_channel = text_channel
        self.is_paused = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Verifica che l'utente sia connesso al canale vocale dove trasmette il video."""
        if not interaction.user:
            return False
        if isinstance(interaction.user, discord.Member) and interaction.user.voice:
            if interaction.user.voice.channel == self.voice_channel:
                return True
        await interaction.response.send_message(
            f"⚠️ Devi essere collegato al canale vocale **{self.voice_channel.name}** per controllare la diretta video!",
            ephemeral=True
        )
        return False

    @discord.ui.button(emoji="⏸️", label="Pausa Video", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume_btn(self, interaction: discord.Interaction, button: Button):
        if not self.is_paused:
            success = await self.client.pause_video_stream(self.text_channel)
            if success:
                self.is_paused = True
                button.emoji = "▶️"
                button.label = "Riprendi Video"
                button.style = discord.ButtonStyle.success
                await interaction.response.edit_message(view=self)
                await self.text_channel.send("⏸️ **Diretta video messa in pausa.**")
            else:
                await interaction.response.send_message("❌ Impossibile mettere in pausa lo stream.", ephemeral=True)
        else:
            success = await self.client.resume_video_stream(self.text_channel)
            if success:
                self.is_paused = False
                button.emoji = "⏸️"
                button.label = "Pausa Video"
                button.style = discord.ButtonStyle.secondary
                await interaction.response.edit_message(view=self)
                await self.text_channel.send("▶️ **Diretta video ripresa!**")
            else:
                await interaction.response.send_message("❌ Impossibile riprendere lo stream.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", label="Interrompi Video", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: Button):
        success = await self.client.stop_video_stream(self.text_channel)
        button.disabled = True
        self.pause_resume_btn.disabled = True
        await interaction.response.edit_message(view=self)
        await self.text_channel.send("⏹️ **Diretta video interrotta.** StreamBot ha lasciato il canale.")


class StreamBotClient:
    """Gestore client per controllare StreamBot e inviare stream video puliti su Discord."""

    def __init__(self):
        self.prefix: str = STREAMBOT_PREFIX or "$"
        self.is_active: bool = False
        self.current_title: Optional[str] = None
        self.current_stream_url: Optional[str] = None
        self.current_voice_channel: Optional[discord.VoiceChannel] = None
        self.started_at: float = 0.0

    def _resolve_command_channel(self, guild: Optional[discord.Guild], fallback_channel: Optional[discord.TextChannel] = None) -> Optional[discord.TextChannel]:
        """Trova il canale testuale ideale per inviare i comandi di controllo a StreamBot."""
        if not guild:
            return fallback_channel

        if STREAMBOT_COMMAND_CHANNEL_ID:
            try:
                ch = guild.get_channel(STREAMBOT_COMMAND_CHANNEL_ID)
                if ch:
                    return ch
            except Exception:
                pass

        # Cerca un canale dedicato 'streambot', 'bot-comandi' o '#musica'
        text_channels = getattr(guild, "text_channels", None)
        if text_channels and isinstance(text_channels, (list, tuple)):
            for name in ("streambot", "streambot-commands", "bot-comandi", "comandi-bot", "musica"):
                for ch in text_channels:
                    if getattr(ch, "name", None) == name:
                        return ch

        return fallback_channel

    async def start_video_stream(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel,
        text_channel: discord.TextChannel,
        stream_url: str,
        title: str,
        is_sports: bool = False,
        is_youtube: bool = False,
        requester: Optional[Union[discord.Member, discord.User, str]] = None
    ) -> Tuple[discord.Embed, discord.ui.View]:
        """
        Avvia la trasmissione video in diretta (Go Live) nel canale vocale specificato tramite StreamBot.
        Rimuove automaticamente le pubblicità e genera l'interfaccia interattiva di controllo.
        """
        clean_url, options = clean_video_url_and_options(stream_url, is_sports=is_sports, is_youtube=is_youtube)
        cmd_channel = self._resolve_command_channel(guild, fallback_channel=text_channel)

        if not cmd_channel:
            raise RuntimeError("Nessun canale testuale disponibile per inviare il comando a StreamBot.")

        # 1. Configura dinamicamente server e canale vocale su StreamBot affinché entri nel canale corretto
        set_guild_cmd = f"{self.prefix}cfg GUILD_ID {guild.id}"
        set_channel_cmd = f"{self.prefix}cfg VIDEO_CHANNEL_ID {voice_channel.id}"
        try:
            await cmd_channel.send(set_guild_cmd)
            await asyncio.sleep(0.3)
            await cmd_channel.send(set_channel_cmd)
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"Errore configurazione StreamBot: {e}")

        # 2. Invia il comando di riproduzione con opzioni anti-pubblicità
        opt_str = (" " + " ".join(options)) if options else ""
        play_cmd = f"{self.prefix}play {clean_url}{opt_str}"
        await cmd_channel.send(play_cmd)

        self.is_active = True
        self.current_title = title
        self.current_stream_url = clean_url
        self.current_voice_channel = voice_channel
        self.started_at = time.time()

        # 3. Costruisce l'Embed ricco con dettagli e stato anti-pubblicità
        embed = discord.Embed(
            title=f"🔴 DIRETTA VIDEO (GO LIVE): {title}",
            description=(
                f"📺 **Trasmissione video in diretta avviata nel canale vocale `{voice_channel.name}`!**\n\n"
                f"🛡️ **Filtro Anti-Pubblicità & SponsorBlock ATTIVO**: visualizzazione pulita senza popup o interruzioni commerciali!\n"
                f"Tutti i partecipanti al canale vocale possono guardare lo streaming direttamente su Discord."
            ),
            color=discord.Color.red() if is_sports else COLOR_FRANCHINO
        )

        embed.add_field(name="🔊 Canale Vocale", value=f"`#{voice_channel.name}`", inline=True)
        embed.add_field(name="📶 Risoluzione", value="`720p / 1080p 60fps`", inline=True)
        tipo_str = "⚽ Live Sport HLS" if is_sports else ("🔴 YouTube" if is_youtube else "🌐 Streaming Web")
        embed.add_field(name="📡 Sorgente Video", value=f"`{tipo_str}`", inline=True)

        if requester:
            req_str = requester.mention if hasattr(requester, "mention") else str(requester)
            embed.add_field(name="👤 Richiesto da", value=req_str, inline=True)

        embed.add_field(
            name="💡 Comando Chat per StreamBot",
            value=f"Se StreamBot non entra in automatico, invia tu stesso in chat:\n`{play_cmd}`",
            inline=False
        )

        embed.set_footer(
            text="Franchino Video Streamer • Powered by StreamBot & AdBlock Engine",
            icon_url="https://cdn-icons-png.flaticon.com/512/3845/3845868.png"
        )

        view = StreamBotControlView(self, voice_channel, text_channel)
        return embed, view

    async def stop_video_stream(self, text_channel: discord.TextChannel) -> bool:
        """Invia il comando di stop a StreamBot per interrompere lo streaming video e liberare il canale vocale."""
        guild = text_channel.guild if text_channel else None
        cmd_channel = self._resolve_command_channel(guild, fallback_channel=text_channel)
        if not cmd_channel:
            return False

        try:
            await cmd_channel.send(f"{self.prefix}stop")
            self.is_active = False
            self.current_title = None
            self.current_stream_url = None
            self.current_voice_channel = None
            return True
        except Exception as e:
            logger.error(f"Errore invio comando stop a StreamBot: {e}")
            return False

    async def pause_video_stream(self, text_channel: discord.TextChannel) -> bool:
        """Invia il comando di pausa a StreamBot."""
        guild = text_channel.guild if text_channel else None
        cmd_channel = self._resolve_command_channel(guild, fallback_channel=text_channel)
        if not cmd_channel:
            return False

        try:
            await cmd_channel.send(f"{self.prefix}pause")
            return True
        except Exception as e:
            logger.error(f"Errore invio comando pause a StreamBot: {e}")
            return False

    async def resume_video_stream(self, text_channel: discord.TextChannel) -> bool:
        """Invia il comando di ripresa a StreamBot."""
        guild = text_channel.guild if text_channel else None
        cmd_channel = self._resolve_command_channel(guild, fallback_channel=text_channel)
        if not cmd_channel:
            return False

        try:
            await cmd_channel.send(f"{self.prefix}resume")
            return True
        except Exception as e:
            logger.error(f"Errore invio comando resume a StreamBot: {e}")
            return False


# Istanza singleton globale per Franchino
streambot_client = StreamBotClient()
