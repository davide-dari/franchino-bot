import asyncio
import logging
import random
import re
import sys
from datetime import datetime, time as dtime, timedelta
from typing import Optional, Literal
import discord
from discord.ext import commands

from config import (
    DISCORD_TOKEN,
    COMMAND_PREFIX,
    COLOR_FRANCHINO,
    COLOR_SUCCESS,
    COLOR_ERROR,
    COLOR_INFO,
    DEFAULT_VOLUME,
    FRANCHINO_LIVENESS_QUOTES
)
from sources import SourceManager, SongInfo
from player import PlayerManager, resolve_preferred_text_channel, build_commands_embed
from views import FranchinoControlsView, QueuePaginationView

# ==========================================
# CONFIGURAZIONE LOGGING
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Franchino")

# ==========================================
# CARICAMENTO LIBRERIA OPUS (PER TUTTI GLI OS)
# ==========================================
def load_opus_library():
    """Tenta il caricamento della libreria libopus necessaria per l'audio vocale."""
    if not discord.opus.is_loaded():
        possible_paths = [
            "libopus.0.dylib",
            "libopus.dylib",
            "/opt/homebrew/lib/libopus.dylib",
            "/usr/local/lib/libopus.dylib",
            "libopus.so.0",
            "libopus.so",
            "/usr/lib/x86_64-linux-gnu/libopus.so.0",
            "/usr/lib/aarch64-linux-gnu/libopus.so.0"
        ]
        for path in possible_paths:
            try:
                discord.opus.load_opus(path)
                logger.info(f"Libreria Opus caricata con successo da: {path}")
                break
            except Exception:
                pass

    if discord.opus.is_loaded():
        logger.info("Modulo Opus attivo e pronto per la trasmissione vocale.")
    else:
        logger.warning("Attenzione: libopus non è stato caricato automaticamente. Su alcuni sistemi potrebbe essere necessario installare opus (es: brew install opus).")

load_opus_library()

# ==========================================
# INIZIALIZZAZIONE BOT DISCORD
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(COMMAND_PREFIX),
    intents=intents,
    help_command=None  # Utilizziamo il comando /help personalizzato
)

source_manager = SourceManager()
player_manager = PlayerManager(bot, source_manager)


_presence_loop_task: Optional[asyncio.Task] = None
_uo_news_task: Optional[asyncio.Task] = None
_uo_patch_task: Optional[asyncio.Task] = None
_tts_warm_task: Optional[asyncio.Task] = None


def _get_human_ids_in_channel(vc_channel: discord.VoiceChannel, excluding_user_id: Optional[int] = None) -> list[int]:
    """Restituisce gli ID degli utenti umani (non-bot) presenti in un canale vocale."""
    if not vc_channel:
        return []
    guild = getattr(vc_channel, "guild", None)
    if not guild:
        return []

    # Escludi esplicitamente l'AFK channel del server
    afk_channel = getattr(guild, "afk_channel", None)
    if afk_channel and (vc_channel == afk_channel or getattr(vc_channel, "id", None) == getattr(afk_channel, "id", None)):
        return []

    bot_user = getattr(bot, "user", None)
    bot_id = getattr(bot_user, "id", 0) if bot_user else 0
    guild_me = getattr(guild, "me", None)
    guild_me_id = getattr(guild_me, "id", 0) if guild_me else 0

    human_ids = set()
    members_by_id = {}

    for m in getattr(vc_channel, "members", []):
        members_by_id[m.id] = m
        if getattr(m, "bot", False):
            continue
        if (bot_id and m.id == bot_id) or (guild_me_id and m.id == guild_me_id) or m.id == excluding_user_id:
            continue
        human_ids.add(m.id)

    for uid in getattr(vc_channel, "voice_states", {}).keys():
        if (bot_id and uid == bot_id) or (guild_me_id and uid == guild_me_id) or uid == excluding_user_id:
            continue
        if uid in members_by_id:
            if getattr(members_by_id[uid], "bot", False):
                human_ids.discard(uid)
                continue
            human_ids.add(uid)
            continue

        member = None
        if hasattr(guild, "get_member"):
            member = guild.get_member(uid)
        if member is None and hasattr(vc_channel, "guild") and vc_channel.guild:
            member = vc_channel.guild.get_member(uid)

        if member is not None:
            if getattr(member, "bot", False):
                human_ids.discard(uid)
                continue
            human_ids.add(uid)
            continue

        # Fallback su bot.get_user(uid) per identificare bot non presenti nella cache della guild
        user = getattr(bot, "get_user", lambda _: None)(uid) if bot else None
        if user is not None:
            if getattr(user, "bot", False):
                human_ids.discard(uid)
                continue
            human_ids.add(uid)
            continue

        # Se né member né user sono in cache (intents.members disattivati),
        # l'ID connesso a voice_states è un utente umano reale nel canale vocale
        human_ids.add(uid)

    return list(human_ids)


def _get_all_humans_in_voice(guild: discord.Guild, excluding_user_id: Optional[int] = None) -> list[int]:
    """Restituisce tutti gli utenti umani (non-bot) attualmente presenti nei canali vocali del server."""
    if not guild:
        return []
    afk_channel = getattr(guild, "afk_channel", None)
    all_humans = set()
    for vc in getattr(guild, "voice_channels", []):
        if afk_channel and (vc == afk_channel or getattr(vc, "id", None) == getattr(afk_channel, "id", None)):
            continue
        all_humans.update(_get_human_ids_in_channel(vc, excluding_user_id=excluding_user_id))
    return list(all_humans)


async def _sync_slash_commands_bg():
    """Sincronizza i comandi slash sia per le singole guild (istantaneo) sia a livello globale."""
    # 1. Sync istantaneo per ogni server in cui si trova il bot (visibile in pochi secondi sul client Discord)
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced_guild = await bot.tree.sync(guild=guild)
            logger.info(f"⚡ Comandi slash sincronizzati all'istante per il server '{guild.name}' ({guild.id}): {len(synced_guild)} comandi registrati.")
        except Exception as e:
            logger.warning(f"Errore sincronizzazione comandi slash per guild {guild.id} ({guild.name}): {e}")

    # 2. Sync globale canonico
    try:
        synced = await bot.tree.sync()
        logger.info(f"🌍 Comandi slash globali sincronizzati con successo: {len(synced)} comandi registrati.")
    except Exception as e:
        logger.error(f"Errore durante la sincronizzazione dei comandi slash globali: {e}")


async def _check_and_sync_voice_presence():
    """
    Controlla tutti i server:
    - Se ci sono persone in una stanza vocale e Franchino non è dentro (o è in una stanza vuota), ENTRA!
    - Se non c'è più nessuna persona in nessuna stanza vocale, Franchino SI DISCONNETTE.
    """
    for guild in bot.guilds:
        try:
            player = player_manager.get_player(guild)
            if getattr(player, "_restarting", False) is True:
                continue

            active_channels = [
                vc for vc in guild.voice_channels
                if len(_get_human_ids_in_channel(vc)) > 0
            ]
            bot_vc = guild.voice_client or player.voice_client

            if not active_channels:
                if bot_vc and (bot_vc.is_connected() or bot_vc.channel):
                    logger.info(f"👋 Nessun utente nelle stanze vocali su '{guild.name}': Franchino esce subito dalla console.")
                    await player.disconnect()
                continue

            # Se ci sono canali con umani, verifichiamo il cooldown di disconnessione manuale
            has_cooldown = False
            try:
                if hasattr(player, "has_manual_leave_cooldown"):
                    res = player.has_manual_leave_cooldown()
                    if isinstance(res, bool):
                        has_cooldown = res
                elif hasattr(player, "_manual_leave_until"):
                    has_cooldown = float(player._manual_leave_until) > time.monotonic()
            except Exception:
                has_cooldown = False

            target_vc = active_channels[0]
            if not bot_vc or not bot_vc.is_connected() or not bot_vc.channel:
                if not has_cooldown:
                    text_ch = _find_default_text_channel(guild, target_vc)
                    await player.connect(target_vc, text_ch, is_auto=True)
                    logger.info(f"🎙️ Franchino è entrato automaticamente nella stanza '{target_vc.name}' ({guild.name})")
            else:
                current_humans = _get_human_ids_in_channel(bot_vc.channel)
                if len(current_humans) == 0:
                    if not has_cooldown and bot_vc.channel != target_vc:
                        text_ch = _find_default_text_channel(guild, target_vc)
                        await player.connect(target_vc, text_ch, is_auto=True)
                        logger.info(f"🎙️ Franchino si è spostato nella stanza popolata '{target_vc.name}' ({guild.name})")
                    else:
                        logger.info(f"👋 Canale vocale '{bot_vc.channel.name}' rimasto vuoto su '{guild.name}': Franchino esce.")
                        await player.disconnect()
                else:
                    player.voice_client = bot_vc
                    player._ensure_voice_listening()
        except Exception as e:
            logger.debug(f"Errore controllo presenza vocale in {guild.name}: {e}")


async def _voice_room_presence_loop():
    """Loop continuo di sicurezza che verifica ogni 60 secondi la presenza vocale nei server."""
    try:
        while True:
            await _check_and_sync_voice_presence()
            await asyncio.sleep(60.0)
    except asyncio.CancelledError:
        pass


async def _uoaddicts_news_monitor_loop(interval_sec: float = 900.0):
    """Loop di background che controlla nuove news su UOAddicts ogni 15 minuti e le pubblica in #info."""
    try:
        await asyncio.sleep(5.0)
        import uo_outlands_service
        logger.info("📰 Avviato loop di monitoraggio news UOAddicts...")
        while True:
            try:
                new_items = await uo_outlands_service.news_service.check_for_new_news()
                if new_items:
                    logger.info(f"📰 Trovate {len(new_items)} nuove news su UOAddicts!")
                    for item in new_items:
                        embed = uo_outlands_service.build_news_embed(item)
                        for guild in bot.guilds:
                            ch = uo_outlands_service.resolve_announcement_channel(guild)
                            if ch:
                                try:
                                    await ch.send(
                                        content=f"📰 **Nuovo articolo su UOAddicts per UO Outlands!**\n> **{item.title}**",
                                        embed=embed,
                                    )
                                except Exception as e:
                                    logger.warning(f"Errore invio news nel canale {ch.name} di {guild.name}: {e}")
            except Exception as e:
                logger.error(f"Errore nel check periodico news UOAddicts: {e}")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        pass


async def _uo_patch_monitor_loop(check_interval_sec: float = 120.0):
    """
    Loop di background che monitora il forum ufficiale delle patch di UO Outlands:
    https://forums.uooutlands.com/index.php?forums/patches.14/
    Quando viene pubblicata una nuova patch:
    - Se cita un riavvio/reboot/manutenzione/downtime:
      * Pubblica un Discord Embed prioritario in #info (o canale annunci)
      * Effettua annuncio vocale conciso da vocalist nei canali vocali attivi
    - Altrimenti pubblica la notizia della nuova patch nel canale annunci.
    """
    try:
        await asyncio.sleep(6.0)
        import uo_outlands_service
        logger.info("🛠️ Avviato loop di monitoraggio patch UO Outlands con allerta riavvio...")
        while True:
            try:
                new_patches = await uo_outlands_service.patch_service.check_for_new_patches()
                if new_patches:
                    logger.info(f"🛠️ Rilevate {len(new_patches)} nuove patch su UO Outlands!")
                    for patch in new_patches:
                        if patch.mentions_restart:
                            logger.info(f"⚠️ Nuova patch con RIAVVIO CITATO: {patch.title}")
                            embed = uo_outlands_service.build_patch_restart_alert_embed(patch)
                            for guild in bot.guilds:
                                ch = uo_outlands_service.resolve_announcement_channel(guild)
                                if ch:
                                    try:
                                        await ch.send(
                                            content=f"⚠️ **Riavvio Server per Nuova Patch!**\n> **{patch.title}**",
                                            embed=embed,
                                        )
                                    except Exception as e:
                                        logger.warning(f"Errore invio alert patch restart a {guild.name}: {e}")
                                player = player_manager.get_player(guild)
                                if player and player.voice_client and player.voice_client.is_connected():
                                    asyncio.create_task(
                                        player.speak_tts(
                                            uo_outlands_service.VOICE_ALERT_PATCH_RESTART,
                                            wait=False,
                                            interrupt_music=True,
                                        )
                                    )
                        else:
                            logger.info(f"🛠️ Nuova patch (senza riavvio): {patch.title}")
                            embed = uo_outlands_service.build_patch_embed(patch)
                            for guild in bot.guilds:
                                ch = uo_outlands_service.resolve_announcement_channel(guild)
                                if ch:
                                    try:
                                        await ch.send(
                                            content=f"🛠️ **Nuova Patch Annunciata per UO Outlands!**\n> **{patch.title}**",
                                            embed=embed,
                                        )
                                    except Exception as e:
                                        logger.warning(f"Errore invio patch a {guild.name}: {e}")
            except Exception as e:
                logger.error(f"Errore nel loop monitoraggio patch Outlands: {e}")
            await asyncio.sleep(check_interval_sec)
    except asyncio.CancelledError:
        pass


# ==========================================
# EVENTI DEL BOT
# ==========================================
@bot.event
async def on_ready():
    global _presence_loop_task, _uo_news_task, _uo_patch_task
    logger.info("==========================================")
    logger.info(f"Franchino è online come: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Prefisso comandi testuali: {COMMAND_PREFIX}")
    logger.info("Franchino è pronto in console! 🎧 Magia!")
    logger.info("==========================================")

    # 1. Entra SUBITO nel canale vocale se ci sono già persone dentro (prima di qualsiasi altra operazione!)
    await _check_and_sync_voice_presence()

    # 2. Avvia il guardiano continuo 24/7 delle stanze vocali (controlla ogni 2.5 secondi)
    if _presence_loop_task is None or _presence_loop_task.done():
        _presence_loop_task = asyncio.create_task(_voice_room_presence_loop())

    # 2b. Avvia i guardiani di monitoraggio UO Outlands (News UOAddicts e Official Patches Monitor)
    if _uo_news_task is None or _uo_news_task.done():
        _uo_news_task = asyncio.create_task(_uoaddicts_news_monitor_loop())

    if _uo_patch_task is None or _uo_patch_task.done():
        _uo_patch_task = asyncio.create_task(_uo_patch_monitor_loop())

    # 3. Sincronizza i comandi slash in background (così eventuali rate-limit Discord non bloccano mai la voce)
    asyncio.create_task(_sync_slash_commands_bg())

    # 4. Ottimizzazione Hardware per server ARM (ThreadPool a 16 workers & GC ARM tuning, configurato una sola volta)
    if not hasattr(bot, "_executor_configured"):
        import concurrent.futures
        from jack_math_engine import hardware_optimizer
        try:
            hardware_optimizer.configure_for_arm()
            loop = asyncio.get_running_loop()
            bounded_pool = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="FranchinoARM")
            loop.set_default_executor(bounded_pool)
            bot._executor_configured = True
            logger.info("⚡ ThreadPoolExecutor ottimizzato e configurato con max_workers=16 per evitare starvation.")
        except Exception as e:
            logger.debug(f"ThreadPool setup: {e}")

    # 5. Avvia in background l'indicizzazione completa della wiki e sincronizzazione catalogo Razor Scripts
    from outlands_wiki import preload_outlands_wiki_index
    from scripts.scrape_razor_scripts import sync_razor_scripts
    asyncio.create_task(preload_outlands_wiki_index())
    asyncio.create_task(asyncio.to_thread(sync_razor_scripts))

    # 6. Inizializza l'Hub Centrale di Conoscenza e il Cervello AI Gemma 2 2B
    from central_hub import hub
    from brain import brain
    asyncio.create_task(hub.initialize())
    asyncio.create_task(brain.initialize())

    # 7. Preriscalda la cache TTS delle frasi canoniche: le risposte fisse partono a latenza zero
    global _tts_warm_task
    if _tts_warm_task is None or _tts_warm_task.done():
        from player import warm_tts_cache
        _tts_warm_task = asyncio.create_task(warm_tts_cache())

    # 5. Imposta l'attività di stato
    try:
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name=f"la musica | /help o {COMMAND_PREFIX}help"
        )
        await bot.change_presence(activity=activity)
    except Exception:
        pass


def _find_default_text_channel(guild: discord.Guild, voice_channel: discord.VoiceChannel):
    """Cerca prima il canale #musica nel server Discord; se non esiste usa la chat del canale vocale o di avvio."""
    return resolve_preferred_text_channel(guild, voice_channel)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Gestisce l'ingresso automatico istantaneo quando un utente entra in vocale e l'uscita quando si svuota."""
    bot_id = bot.user.id if bot.user else (getattr(member.guild.me, "id", None) if getattr(member.guild, "me", None) else None)
    if bot_id and member.id == bot_id:
        player = player_manager.get_player(member.guild)
        if getattr(player, "_restarting", False) is True:
            return
        if before.channel and not after.channel:
            is_self_disconnect = getattr(player, "_is_disconnecting", False)
            if is_self_disconnect is True:
                return
            if hasattr(player, "set_manual_leave_cooldown"):
                player.set_manual_leave_cooldown(60.0)
            await player.disconnect()
        elif after.channel:
            player.text_channel = _find_default_text_channel(member.guild, after.channel)
            player._ensure_voice_listening()
        return

    if member.bot:
        return

    # Se lo stato vocale non riguarda un cambio di canale (es. mute/deafen/webcam), ignoriamo
    if before.channel == after.channel:
        return

    guild = member.guild
    player = player_manager.get_player(guild)
    bot_vc = guild.voice_client or player.voice_client

    # Controllo cooldown di disconnessione manuale
    has_cooldown = False
    try:
        if hasattr(player, "has_manual_leave_cooldown"):
            res = player.has_manual_leave_cooldown()
            if isinstance(res, bool):
                has_cooldown = res
        elif hasattr(player, "_manual_leave_until"):
            has_cooldown = float(player._manual_leave_until) > time.monotonic()
    except Exception:
        has_cooldown = False

    # 1. Se un utente entra o si sposta in una stanza vocale:
    if after.channel:
        target_vc = after.channel
        text_ch = _find_default_text_channel(guild, target_vc)

        # Caso A: Franchino non è connesso a nessun canale vocale
        if not bot_vc or not bot_vc.is_connected() or not bot_vc.channel:
            if len(_get_human_ids_in_channel(target_vc)) > 0 and not has_cooldown:
                try:
                    await player.connect(target_vc, text_ch, is_auto=True)
                    logger.info(f"🎙️ Auto-ingresso immediato nel canale '{target_vc.name}' per {member.display_name}")
                except Exception as e:
                    logger.warning(f"Errore durante auto-ingresso vocale: {e}")
            return

        # Caso B: Franchino è già nello stesso canale in cui è appena entrato l'utente -> rimane silenzioso
        elif bot_vc.channel == target_vc:
            logger.info(f"🎙️ Utente {member.display_name} entrato nel canale di Franchino '{target_vc.name}'")
            return

        # Caso C: Franchino era in un'altra stanza rimasta vuota (o tutti si sono spostati) -> lo segue se non in cooldown
        elif bot_vc.channel != target_vc:
            humans_in_current = _get_human_ids_in_channel(bot_vc.channel, excluding_user_id=member.id)
            if len(humans_in_current) == 0:
                if len(_get_human_ids_in_channel(target_vc)) > 0 and not has_cooldown:
                    try:
                        await player.connect(target_vc, text_ch, is_auto=True)
                        logger.info(f"🎙️ Franchino ha seguito {member.display_name} nel canale '{target_vc.name}'")
                    except Exception as e:
                        logger.warning(f"Errore spostamento automatico vocale: {e}")
                else:
                    await player.disconnect()
                return

    # 2. Se un utente è uscito o si è spostato, verifichiamo la presenza umana nel server:
    # Se in tutto il server Discord non c'è più nessun umano nei canali vocali, Franchino esce subito!
    all_humans = _get_all_humans_in_voice(guild, excluding_user_id=member.id if not after.channel else None)
    if len(all_humans) == 0:
        if bot_vc and (bot_vc.is_connected() or bot_vc.channel):
            logger.info(f"👋 Nessun utente rimasto nei canali vocali di '{guild.name}'. Franchino esce subito dalla console.")
            await player.disconnect()
        return

    # Se Franchino è connesso ma la sua stanza attuale è rimasta senza nessun umano:
    if bot_vc and bot_vc.channel:
        humans_in_bot_room = _get_human_ids_in_channel(bot_vc.channel, excluding_user_id=member.id if after.channel != bot_vc.channel else None)
        if len(humans_in_bot_room) == 0:
            target_vc = None
            if not has_cooldown:
                for vc_ch in guild.voice_channels:
                    if vc_ch != bot_vc.channel and len(_get_human_ids_in_channel(vc_ch, excluding_user_id=member.id if not after.channel else None)) > 0:
                        target_vc = vc_ch
                        break
            if target_vc:
                text_ch = _find_default_text_channel(guild, target_vc)
                await player.connect(target_vc, text_ch, is_auto=True)
                logger.info(f"🎙️ Franchino si è spostato nella stanza popolata '{target_vc.name}'")
            else:
                logger.info(f"👋 Canale vocale '{bot_vc.channel.name}' rimasto vuoto: Franchino si disconnette subito.")
                await player.disconnect()


@bot.event
async def on_message(message: discord.Message):
    """Intercetta messaggi diretti a Franchino in chat e gestisce comandi con prefisso o menzione."""
    if message.author.bot:
        return

    ctx = await bot.get_context(message)
    if ctx.command is not None:
        await bot.invoke(ctx)
        return

    content_lower = message.content.lower().strip()
    clean_lower = message.clean_content.lower().strip() if hasattr(message, "clean_content") else content_lower

    is_bot_mentioned = (bot.user and bot.user in message.mentions) or any(
        w in content_lower or w in clean_lower
        for w in (
            "franchino", "frankino", "francino", "franchì",
            "franchi no", "fra chi no", "frankie no", "franki no",
            "fran chino", "franco ino", "frank ino", "franc ino", "franck ino"
        )
    )

    # Se l'utente scrive in chat una domanda di presenza diretta a Franchino o lo menziona/saluta:
    # es: "franchino ci sei?", "franchino ci senti?", "franchino sei vivo?", "ci sei franchino?", "@Franchino"
    if is_bot_mentioned:
        liveness_triggers = (
            "ci sei", "ci senti", "sei vivo", "mi senti", "ci siete", "rispondi",
            "batti un colpo", "sei sveglio", "funzioni", "funziona", "tutto bene",
            "come va", "sei online", "online", "ping"
        )
        is_liveness = any(q in content_lower or q in clean_lower for q in liveness_triggers)

        stripped = re.sub(
            r'<@!?\d+>|@?\b(?:franchin[oaie]|frankin[oaie]|francin[oaie]|franchì|franchi\s*,?\s*n[oaie]|fra\s+chi\s*,?\s*n[oaie]|frankie\s*,?\s*n[oaie]|franki\s*,?\s*n[oaie]|fran\s+chin[oaie]|franco\s+in[oaie]|frank\s+in[oaie]|franc\s+in[oaie]|franck\s+in[oaie])\b',
            '',
            clean_lower,
            flags=re.IGNORECASE
        )
        stripped = re.sub(r'<@!?\d+>', '', stripped).strip(" @,.!?:;-")

        if is_liveness or not stripped or stripped in ("ciao", "salve", "buongiorno", "buonasera", "ehi", "hey"):
            await cisei_cmd(ctx)
            return

        # MEMORIA: se qualcuno insulta Franchino in chat, se lo ricorda e risponde a tono
        try:
            from memoria import detect_offense, memoria

            offense = detect_offense(clean_lower)
            if offense is not None:
                record = memoria.record(message.author.id, message.author.display_name, offense)
                if record.level >= 1:
                    chat_reply, voice_reply = memoria.reply(record)
                    await message.reply(chat_reply)
                    guild_player = player_manager.get_player(message.guild) if message.guild else None
                    if guild_player and guild_player.voice_client and guild_player.voice_client.is_connected():
                        asyncio.create_task(
                            guild_player.speak_tts(voice_reply, wait=False, interrupt_music=True)
                        )
                    return
        except Exception as mem_err:
            logger.debug(f"Memoria in chat non applicata: {mem_err}")

    await bot.process_commands(message)


# ==========================================
# FUNZIONI HELPER
# ==========================================
async def ensure_voice_connection(ctx: commands.Context) -> Optional[discord.VoiceClient]:
    """Assicura che l'utente sia in un canale vocale e connette Franchino se necessario."""
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("❌ Devi prima entrare in un canale vocale per chiamare Franchino in console!")
        return None

    user_channel = ctx.author.voice.channel
    player = player_manager.get_player(ctx.guild)

    if not player.voice_client or not player.voice_client.is_connected():
        try:
            vc = await player.connect(user_channel, ctx.channel)
            return vc
        except Exception as e:
            await ctx.send(f"❌ Impossibile collegarsi al canale vocale: `{e}`")
            return None
    elif player.voice_client.channel != user_channel:
        current_humans = _get_human_ids_in_channel(player.voice_client.channel)
        if current_humans:
            await ctx.send(f"⚠️ Franchino sta già suonando nel canale vocale **{player.voice_client.channel.name}**!")
            return None
        else:
            await player.voice_client.move_to(user_channel)
            player.text_channel = ctx.channel

    return player.voice_client


# ==========================================
# COMANDI HYBRID (SLASH + PREFISSATI)
# ==========================================

@bot.hybrid_command(
    name="cisei",
    aliases=["cisenti", "ping", "testvoce", "test_voce", "seivivo", "sei_vivo", "franchinocisei"],
    description="Verifica se Franchino è online, in ascolto e se il microfono e l'audio funzionano"
)
async def cisei_cmd(ctx: commands.Context):
    """
    Verifica se Franchino risponde, mostra la latenza ping e riproduce un test vocale immediato in console.
    """
    await ctx.defer()
    player = player_manager.get_player(ctx.guild)
    bot_vc = ctx.guild.voice_client or player.voice_client

    # Se l'utente è in un canale vocale e Franchino non è ancora connesso, collegalo subito!
    if (not bot_vc or not bot_vc.is_connected()) and ctx.author.voice and ctx.author.voice.channel:
        try:
            bot_vc = await player.connect(ctx.author.voice.channel, ctx.channel)
        except Exception as e:
            logger.debug(f"Auto-connect su /cisei fallito: {e}")

    ping_ms = round(bot.latency * 1000)
    chosen_quote = random.choice(FRANCHINO_LIVENESS_QUOTES)

    embed = discord.Embed(
        title="🎙️✨ Franchino è in Console e vi sente!",
        description=f"> *\"{chosen_quote}\"*",
        color=COLOR_FRANCHINO
    )
    embed.add_field(name="📶 Latenza Ping", value=f"`{ping_ms} ms`", inline=True)
    embed.add_field(name="🔊 Volume Console", value=f"`{int(player.volume * 100)}%`", inline=True)

    if bot_vc and bot_vc.is_connected() and bot_vc.channel:
        voice_status = f"✅ Connesso a **#{bot_vc.channel.name}**\n🎧 Ascolto comandi vocali: **Attivo 24/7**"
        embed.add_field(name="🎙️ Canale Vocale", value=voice_status, inline=False)
        
        # Riproduce subito la voce parlata di Franchino nel canale vocale per testare l'audio!
        asyncio.create_task(
            player.speak_tts(chosen_quote, wait=False, interrupt_music=True, dramatic_fable=True)
        )
        embed.add_field(name="🗣️ Test Audio Vocale", value="🔊 Franchino sta rispondendo a voce adesso nel tuo canale!", inline=False)
    else:
        embed.add_field(
            name="🎙️ Canale Vocale",
            value="⚠️ Non sono connesso a una stanza vocale.\nEntra in un canale vocale e scrivi `/cisei` o `/play` per farmi entrare!",
            inline=False
        )

    embed.set_footer(text="Franchino Vocalist • Sempre in ascolto 24/7! ✨")
    await ctx.send(embed=embed)

    from central_hub import hub
    asyncio.create_task(
        hub.archive_interaction(
            user_id=str(ctx.author.id),
            user_name=ctx.author.display_name,
            raw_query="cisei",
            intent="liveness_check",
            ai_response=chosen_quote,
            voice_summary=chosen_quote,
            channel_name=ctx.channel.name if hasattr(ctx.channel, "name") else None,
        )
    )


@bot.hybrid_command(
    name="play",
    aliases=["p", "metti", "suona"],
    description="Riproduce un brano o playlist da YouTube o Spotify"
)
async def play(ctx: commands.Context, *, brano: str):
    """
    Riproduce musica da YouTube (video, playlist, ricerca) o Spotify (tracce, playlist, album).
    Esempio: /play Gigi D'Agostino oppure /play https://open.spotify.com/track/...
    """
    vc = await ensure_voice_connection(ctx)
    if not vc:
        return

    await ctx.defer()
    player = player_manager.get_player(ctx.guild)

    # RADIO IN DIRETTA: /play m2o, !play radio deejay, /play radio gamma
    try:
        from radio import find_radio, extract_radio_name, resolve_stream

        station = find_radio(brano)
        radio_name = None if station else extract_radio_name(brano)
        if station is not None or radio_name:
            stream = await resolve_stream(station=station, name=radio_name)
            if stream:
                radio_song = SongInfo(
                    title=f"📻 {stream.station} — Live",
                    webpage_url=stream.homepage or "",
                    stream_url=stream.url,
                    duration=0,
                    thumbnail=stream.favicon or None,
                    uploader=f"Radio Live • {stream.quality}",
                    requester=ctx.author,
                    source_type="radio",
                    search_query=station.key if station else "",
                    is_direct_stream=True,
                )
                player._manual_stop = False
                await player.play_now([radio_song], text_channel=ctx.channel)
                return
            if station is not None:
                await ctx.send(f"⚠️ Non trovo un flusso attivo per **{station.name}** in questo momento. Riprova tra poco!")
                return
            # Non era una radio: si prosegue con la normale ricerca musicale
    except Exception as radio_err:
        logger.debug(f"Gestione radio nel comando play fallita: {radio_err}")

    try:
        songs = await source_manager.resolve_url_or_query(brano, ctx.author)
    except Exception as e:
        logger.error(f"Errore nella risoluzione del brano '{brano}': {e}", exc_info=True)
        await ctx.send(f"❌ Non ho trovato nulla per: `{brano}`. Errore: {e}")
        return

    if not songs:
        await ctx.send(f"❌ Nessun brano trovato per la ricerca.")
        return

    # Se è una traccia singola
    if len(songs) == 1:
        song = songs[0]
        is_currently_active = player.is_active

        await player.add_songs([song], ctx.channel)

        if is_currently_active:
            embed = discord.Embed(
                title="🎶 Aggiunto alla coda",
                description=f"**[{song.title}]({song.webpage_url or song.spotify_url})**",
                color=COLOR_SUCCESS
            )
            embed.add_field(name="⏱️ Durata", value=f"`{song.duration_str}`", inline=True)
            embed.add_field(name="📍 Posizione in coda", value=f"`#{len(player.queue)}`", inline=True)
            embed.add_field(name="👤 Richiesto da", value=song.requester_mention, inline=True)
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"🎧 Franchino accende la console! Avvio riproduzione...", ephemeral=True)

    else:
        # È una playlist o un album
        await player.add_songs(songs, ctx.channel)
        first_song = songs[0]
        badge = "🟢 Spotify" if first_song.source_type == "spotify" else "🔴 YouTube"

        embed = discord.Embed(
            title=f"📚 Playlist aggiunta alla coda! ({badge})",
            description=f"Aggiunti **{len(songs)} brani** alla coda musicale di Franchino.",
            color=COLOR_SUCCESS
        )
        embed.add_field(name="🎶 Primo brano", value=f"[{first_song.title}]({first_song.webpage_url or first_song.spotify_url})", inline=False)
        embed.add_field(name="👤 Richiesto da", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Franchino DJ • Pronti per il viaggio musicale! 🚀")
        await ctx.send(embed=embed)


@bot.hybrid_command(
    name="pause",
    description="Mette in pausa il brano attualmente in riproduzione"
)
async def pause(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    if not player.is_playing():
        await ctx.send("❌ Nessun brano in riproduzione da mettere in pausa.")
        return

    player.pause()
    await ctx.send("⏸️ **Franchino ha messo in pausa la musica!** Usa `/resume` o `!resume` per ripartire.")


@bot.hybrid_command(
    name="resume",
    description="Riprende la riproduzione musicale dopo una pausa"
)
async def resume(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    if not player.is_paused():
        await ctx.send("❌ La musica non è in pausa.")
        return

    player.resume()
    await ctx.send("▶️ **Si riparte! Franchino riaccende la cassa!** 🎶")


@bot.hybrid_command(
    name="skip",
    aliases=["s", "next"],
    description="Salta al brano successivo nella coda"
)
async def skip(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    if not player.is_active or not player.current:
        if player.queue:
            player._consecutive_play_errors = 0
            await player.play_next()
            await ctx.send("⏭️ Riavvio riproduzione dal brano successivo in coda!")
            return
        await ctx.send("❌ Nessun brano da saltare.")
        return

    skipped_title = player.current.title
    player.skip()
    await ctx.send(f"⏭️ Brano saltato: **{skipped_title}**")


@bot.hybrid_command(
    name="stop",
    aliases=["ferma", "fermati", "basta", "zitto", "silenzio", "stoppa", "spegni"],
    description="Ferma immediatamente musica, voce di Franchino, effetti e svuota la coda"
)
async def stop(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    player.stop()
    if ctx.interaction and not ctx.interaction.response.is_done():
        await ctx.interaction.response.defer()
        try:
            await ctx.interaction.delete_original_response()
        except Exception:
            pass


@bot.hybrid_command(
    name="queue",
    aliases=["q"],
    description="Mostra la lista dei brani in coda con paginazione"
)
async def queue_cmd(ctx: commands.Context, pagina: Optional[int] = 1):
    player = player_manager.get_player(ctx.guild)
    page_num = pagina if pagina and pagina > 0 else 1
    total_pages = max(1, (len(player.queue) + 9) // 10)

    embed = player.create_queue_embed(page=page_num)
    view = QueuePaginationView(player, current_page=page_num, total_pages=total_pages) if total_pages > 1 else None

    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="nowplaying",
    aliases=["np"],
    description="Mostra il brano attualmente in riproduzione con i controlli chat interattivi"
)
async def nowplaying(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    if not player.current or not player.is_active:
        await ctx.send("❌ Nessun brano attualmente in riproduzione.")
        return

    embed = player.current.create_embed(title_prefix="🎧 Ora in riproduzione:")
    embed.add_field(name="📜 Coda", value=f"`{len(player.queue)} brani`", inline=True)
    embed.add_field(name="🔁 Loop", value=f"`{player.loop_mode.capitalize()}`", inline=True)
    embed.add_field(name="🔊 Volume", value=f"`{int(player.volume * 100)}%`", inline=True)

    view = FranchinoControlsView(player)
    await ctx.send(embed=embed, view=view)


@bot.hybrid_command(
    name="volume",
    aliases=["vol", "v"],
    description="Regola il volume di ascolto (da 1 a 100)"
)
async def volume(ctx: commands.Context, livello: int):
    if livello < 1 or livello > 100:
        await ctx.send("⚠️ Il volume deve essere compreso tra 1 e 100.")
        return

    player = player_manager.get_player(ctx.guild)
    new_vol = livello / 100.0
    player.set_volume(new_vol)

    await ctx.send(f"🔊 **Volume impostato al {livello}%**")


@bot.hybrid_command(
    name="loop",
    description="Imposta la modalità di ripetizione (off, song, queue)"
)
async def loop(ctx: commands.Context, modalita: Optional[Literal["off", "song", "queue"]] = None):
    player = player_manager.get_player(ctx.guild)

    if modalita is None:
        # Cicla tra le modalità
        if player.loop_mode == "off":
            player.loop_mode = "song"
        elif player.loop_mode == "song":
            player.loop_mode = "queue"
        else:
            player.loop_mode = "off"
    else:
        player.loop_mode = modalita

    nomi = {
        "off": "Disattivato ➡️",
        "song": "Traccia corrente 🔂",
        "queue": "Tutta la coda 🔁"
    }
    await ctx.send(f"🔁 **Modalità Loop aggiornata:** {nomi.get(player.loop_mode, player.loop_mode)}")


@bot.hybrid_command(
    name="shuffle",
    description="Mischia casualmente l'ordine dei brani in coda"
)
async def shuffle(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    if len(player.queue) < 2:
        await ctx.send("⚠️ Ci devono essere almeno 2 brani in coda per mescolare.")
        return

    player.shuffle()
    await ctx.send(f"🔀 **Coda mescolata con successo!** ({len(player.queue)} brani riordinati)")


@bot.hybrid_command(
    name="remove",
    description="Rimuove un brano dalla coda specificandone il numero"
)
async def remove(ctx: commands.Context, numero: int):
    player = player_manager.get_player(ctx.guild)
    if numero < 1 or numero > len(player.queue):
        await ctx.send(f"⚠️ Numero non valido. La coda ha {len(player.queue)} brani. Usa `/queue` per controllare i numeri.")
        return

    removed = player.remove(numero)
    if removed:
        await ctx.send(f"🗑️ Brano rimosso dalla posizione #{numero}: **{removed.title}**")
    else:
        await ctx.send("❌ Impossibile rimuovere il brano.")


@bot.hybrid_command(
    name="clear",
    description="Svuota tutti i brani in attesa nella coda"
)
async def clear(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    count = player.clear_queue()
    await ctx.send(f"🧹 Coda svuotata! Rimossi **{count}** brani in attesa.")


@bot.hybrid_command(
    name="join",
    aliases=["connect"],
    description="Fa entrare Franchino nel tuo canale vocale"
)
async def join(ctx: commands.Context):
    vc = await ensure_voice_connection(ctx)
    if vc:
        await ctx.send(f"🎧 **Franchino è entrato in console nel canale:** `{vc.channel.name}`! Pronti per la magia! ✨")


@bot.hybrid_command(
    name="leave",
    aliases=["disconnect", "dc"],
    description="Fa uscire Franchino dal canale vocale"
)
async def leave(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    if not player.voice_client or not player.voice_client.is_connected():
        await ctx.send("❌ Franchino non è connesso a nessun canale vocale.")
        return

    if hasattr(player, "set_manual_leave_cooldown"):
        player.set_manual_leave_cooldown(60.0)
    await player.disconnect()
    await ctx.send("👋 **Franchino lascia la console!** Alla prossima serata ragazzi! 🎧")


@bot.hybrid_command(
    name="riavvia",
    aliases=["restart", "reset", "sblocca"],
    description="Riavvia e sblocca Franchino riconnettendolo al canale vocale"
)
async def riavvia(ctx: commands.Context):
    await ctx.defer()
    player = player_manager.get_player(ctx.guild)
    target_ch = ctx.author.voice.channel if (ctx.author.voice and ctx.author.voice.channel) else None
    player.text_channel = ctx.channel
    vc = await player.restart_console(target_channel=target_ch)
    if vc and vc.channel:
        await ctx.send(f"🔄 **Franchino si è riavviato ed è di nuovo in ascolto nel canale `{vc.channel.name}`!** ✨")
    else:
        await ctx.send("🔄 **Console di Franchino resettata!** Entra in un canale vocale per farlo partire.")


@bot.hybrid_command(
    name="radio",
    aliases=["autoplay"],
    description="Attiva o disattiva la modalità Radio Infinita (AutoPlay a fine coda)"
)
async def radio_cmd(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    player.autoplay = not player.autoplay
    stato = "ATTIVATA ✅ (Franchino metterà brani correlati a fine coda!)" if player.autoplay else "DISATTIVATA ❌"
    await ctx.send(f"📻 **Modalità Radio AutoPlay {stato}**")


@bot.hybrid_command(
    name="effetto",
    aliases=["effect", "fx"],
    description="Applica uno dei 12 effetti audio DJ dal vivo sulla musica in corso"
)
async def effetto_cmd(
    ctx: commands.Context,
    effetto: Literal[
        "off", "bassboost", "megabass", "nightcore", "slowed", "8d",
        "reverb", "karaoke", "vaporwave", "chipmunk", "demon", "vintage"
    ] = "off"
):
    await ctx.defer()
    player = player_manager.get_player(ctx.guild)
    label = await player.set_effect(effetto)
    await ctx.send(f"🎛️ Effetto DJ impostato su: **{label}**!")


@bot.hybrid_command(
    name="boost",
    aliases=["bassboost"],
    description="Attiva o regola il Bass Boost DJ (off, low, medium, high)"
)
async def boost_cmd(ctx: commands.Context, modo: Optional[str] = None):
    await ctx.defer()
    clean_mode = (modo or "").strip().lower()
    if clean_mode in ("jack", "math", "formula", "status", "info", "hardware"):
        from jack_math_engine import jack_engine, hardware_optimizer
        telem = hardware_optimizer.get_hardware_telemetry()
        embed = discord.Embed(
            title="🧠 Formula Matematica Jack & Ottimizzazione Hardware ARM",
            description=(
                "Franchino opera con il motore di intelligenza analitica **Super Matematico Jack**:\n\n"
                "$$\\mathcal{S}(\\text{Intent}, q) = w_{\\text{wake}} \\cdot \\phi_{\\text{wake}} + "
                "w_{\\text{phonetic}} \\cdot \\sigma_{\\text{jaro}} + w_{\\text{domain}} \\cdot \\text{BM25}(q, D) + "
                "w_{\\text{action}} \\cdot \\alpha_{\\text{syntax}} - \\lambda_{\\text{noise}}$$\n\n"
                "**Distribuzione Probabilistica:**\n"
                "$$\\mathcal{P}(\\text{Intent} \\mid q) = \\frac{e^{\\beta \\cdot \\mathcal{S}(\\text{Intent}, q)}}{\\sum_k e^{\\beta \\cdot \\mathcal{S}(k, q)}}$$"
            ),
            color=0xF1C40F,
        )
        embed.add_field(
            name="⚖️ Pesi & Parametri della Formula",
            value=(
                "• $w_{\\text{wake}}$: `0.15` (Riconoscimento vocale diretto)\n"
                "• $w_{\\text{phonetic}}$: `0.25` (Jaro-Winkler tolleranza STT)\n"
                "• $w_{\\text{domain}}$: `0.35` (Okapi BM25 pertinenza ontologica)\n"
                "• $w_{\\text{action}}$: `0.25` (Sintassi imperativa grammaticale)\n"
                "• $\\beta$ Softmax: `3.0` (Contrasto netto decisionale)"
            ),
            inline=False,
        )
        embed.add_field(
            name="☁️ Ottimizzazione Hardware Server Oracle ARM",
            value=(
                f"• **Architettura:** `{telem['server_arch']}`\n"
                f"• **vCPU:** Target `2 vCPU ARM` (Disponibili: `{telem['cpu_cores_available']}`)\n"
                f"• **RAM Sistema:** Target `12 GB` (Processo Python: `{telem['process_ram_mb']} MB`)\n"
                f"• **Disco:** `{telem['disk_used_gb']} GB` usati su `{telem['disk_total_gb']} GB` max ({telem['disk_free_gb']} GB liberi)\n"
                f"• **Efficienza Risorse:** `{telem['target_efficiency']}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Franchino Bot • Super Matematico Jack Intelligence")
        await ctx.send(embed=embed)
        return

    player = player_manager.get_player(ctx.guild)
    target = "off" if player.audio_effect == "bassboost" else "bassboost"
    label = await player.set_effect(target)
    await ctx.send(f"🎛️ Effetto Bass Boost impostato su: **{label}**!")


@bot.hybrid_command(
    name="suono",
    aliases=["sfx", "soundboard"],
    description="Riproduce subito uno dei 13 effetti sonori della Soundboard DJ nel canale vocale"
)
async def suono_cmd(
    ctx: commands.Context,
    suono: Literal[
        "airhorn", "siren", "drop", "drumroll", "applause", "fail",
        "victory", "laser", "censor", "whistle", "laugh", "booo", "alarm"
    ] = "airhorn"
):
    vc = await ensure_voice_connection(ctx)
    if not vc:
        return
    await ctx.defer()
    player = player_manager.get_player(ctx.guild)
    from sound_effects import SOUNDBOARD_EFFECTS
    meta = SOUNDBOARD_EFFECTS.get(suono, SOUNDBOARD_EFFECTS["airhorn"])
    await ctx.send(f"{meta['emoji']} **{ctx.author.display_name}** ha suonato l'effetto **{meta['label']}**!")
    await player.play_sound_effect(suono)


@bot.hybrid_command(
    name="salva",
    aliases=["save", "like"],
    description="Salva la canzone in riproduzione nei Preferiti del Server"
)
async def salva_cmd(ctx: commands.Context):
    player = player_manager.get_player(ctx.guild)
    added, song, total = player.save_current_to_favorites(ctx.author)
    if not song:
        await ctx.send("⚠️ Nessun brano in riproduzione da salvare!")
    elif added:
        await ctx.send(f"❤️ **{song.title}** salvato nei Preferiti del Server! *(Totale: {total} brani)*")
    else:
        await ctx.send(f"❤️ **{song.title}** è già presente nei Preferiti del Server! *({total} brani)*")


@bot.hybrid_command(
    name="preferiti",
    aliases=["favorites", "fav"],
    description="Riproduce in modalità casuale la playlist dei Preferiti del Server"
)
async def preferiti_cmd(ctx: commands.Context):
    vc = await ensure_voice_connection(ctx)
    if not vc:
        return
    await ctx.defer()
    player = player_manager.get_player(ctx.guild)
    fav_songs = player.load_favorites_as_songs(requester=ctx.author)
    if not fav_songs:
        await ctx.send("⚠️ Nessun brano salvato nei **Preferiti del Server**! Usa `/salva`, il pulsante ❤️ o dì *\"Franchino salva\"* mentre suona una canzone.")
        return
    random.shuffle(fav_songs)
    await player.play_now(fav_songs, text_channel=ctx.channel)
    await ctx.send(f"❤️ Avviati i **Preferiti del Server** ({len(fav_songs)} brani in rotazione)!")


@bot.hybrid_command(
    name="comandi",
    aliases=["help", "aiuto"],
    description="Ti invia in messaggio privato tutti i comandi vocali, testuali e i pulsanti di Franchino"
)
async def help_cmd(ctx: commands.Context):
    embed = build_commands_embed()
    try:
        await ctx.author.send(
            content=f"🎧 Ciao **{ctx.author.display_name}**! Ecco la guida completa ai comandi di **Franchino DJ**:",
            embed=embed,
        )
        await ctx.send(f"📬 {ctx.author.mention}, ti ho inviato tutti i comandi di Franchino in **messaggio privato**!", ephemeral=True)
    except discord.Forbidden:
        await ctx.send(
            content=f"⚠️ {ctx.author.mention} hai i messaggi privati chiusi, ecco qui i comandi:",
            embed=embed,
        )


async def _handle_outlands_query(ctx: commands.Context, domanda: str):
    await ctx.defer()
    from outlands_wiki import query_outlands_wiki, is_help_question, get_outlands_help_embed
    from central_hub import hub

    # Riconosce "aiuto", "help", "guida", "cosa puoi fare su outlands", ecc.
    domanda_low = domanda.lower().strip()
    if domanda_low in ("aiuto", "help", "guida", "argomenti", "comandi", "cosa puoi fare", "cosa sai") \
            or is_help_question(domanda_low):
        help_embed, voice_answer = get_outlands_help_embed()
        await ctx.send(
            content=f"🗺️ **{ctx.author.display_name}**, ecco tutto quello che puoi chiedermi su **UO Outlands**!",
            embed=help_embed,
        )
        player = player_manager.get_player(ctx.guild)
        if player.voice_client and player.voice_client.is_connected():
            asyncio.create_task(
                player.speak_tts(voice_answer, wait=False, interrupt_music=True, keep_paused_after=False)
            )
        return

    # Riconosce scorciatoie per status, riavvio, build e news
    if domanda_low in ("status", "server", "server status", "online", "stato", "ping", "portal", "portale", "up", "down", "portal status"):
        import uo_outlands_service
        s_res = await uo_outlands_service.check_uo_server_status()
        await ctx.send(embed=s_res.build_embed())
        player = player_manager.get_player(ctx.guild)
        if player and player.voice_client and player.voice_client.is_connected():
            asyncio.create_task(player.speak_tts(s_res.voice_text(), wait=False, interrupt_music=True))
        return

    if domanda_low in ("riavvio", "restart", "manutenzione", "quando riavvia", "orario riavvio", "patch", "patches", "ultima patch"):
        import uo_outlands_service
        is_patch = domanda_low in ("patch", "patches", "ultima patch")
        if is_patch:
            patch = await uo_outlands_service.patch_service.get_latest_patch()
        else:
            patch = await uo_outlands_service.patch_service.get_latest_restart_patch()
            if not patch:
                patch = await uo_outlands_service.patch_service.get_latest_patch()
        p_embed = uo_outlands_service.build_patch_embed(patch)
        await ctx.send(embed=p_embed)
        player = player_manager.get_player(ctx.guild)
        if player and player.voice_client and player.voice_client.is_connected():
            v_text = patch.voice_text()
            asyncio.create_task(player.speak_tts(v_text, wait=False, interrupt_music=True))
        return

    if domanda_low in ("build", "builds", "builds popolari", "build popolari") or any(k in domanda_low for k in ("build pvm", "build pvp", "build popolari", "build recenti", "build uoaddicts", "build più usate", "build piu usate")):
        import uo_outlands_service
        cat = "pvm" if "pvm" in domanda_low else ("pvp" if "pvp" in domanda_low else ("recent" if "recent" in domanda_low else "popular"))
        builds = await uo_outlands_service.builds_service.fetch_builds(cat)
        b_embed = uo_outlands_service.build_builds_embed(builds, category=cat)
        await ctx.send(embed=b_embed)
        player = player_manager.get_player(ctx.guild)
        if player and player.voice_client and player.voice_client.is_connected():
            asyncio.create_task(player.speak_tts("Ecco le build più utilizzate su UO Addicts, te le ho mandate in chat!", wait=False, interrupt_music=True))
        return

    if domanda_low in ("news", "notizie", "novità", "novita", "aggiornamenti"):
        import uo_outlands_service
        items = await uo_outlands_service.news_service.fetch_latest_news(limit=5)
        n_embed = uo_outlands_service.build_news_list_embed(items)
        await ctx.send(embed=n_embed)
        player = player_manager.get_player(ctx.guild)
        if player and player.voice_client and player.voice_client.is_connected():
            asyncio.create_task(player.speak_tts("Ho trovato le ultime novità da UO Addicts e te le ho mandate in chat!", wait=False, interrupt_music=True))
        return

    try:
        res = await asyncio.wait_for(query_outlands_wiki(domanda), timeout=18.0)
    except asyncio.TimeoutError:
        await ctx.send(f"⚠️ La ricerca su `wiki.uooutlands.com` per **\"{domanda}\"** ha impiegato troppo tempo. Riprova tra poco!")
        return
    except Exception as e:
        await ctx.send(f"⚠️ Errore durante la ricerca su Outlands Wiki: {e}")
        return

    if not res:
        await ctx.send(f"⚠️ Non ho trovato nulla su `wiki.uooutlands.com` per: **{domanda}**")
        return

    title = res["page_title"]
    sec_title = res["section_title"]
    sec_url = res["section_url"]
    voice_answer = res["voice_answer"]
    wiki_embed = res["embed"]

    # Archiviazione nell'Hub Centrale
    asyncio.create_task(
        hub.archive_interaction(
            user_id=str(ctx.author.id),
            user_name=ctx.author.display_name,
            raw_query=f"outlands {domanda}",
            intent="wiki_guide",
            ai_response=f"Sezione: {title} - {sec_title}",
            voice_summary=voice_answer,
            channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else None,
            metadata={"url": sec_url}
        )
    )

    sent_dm = False
    try:
        await ctx.author.send(
            content=(
                f"📖 Ciao **{ctx.author.display_name}**! Ecco il link diretto alla sezione di riferimento per *\"{domanda}\"*:\n"
                f"🔗 **{title} → {sec_title}**: {sec_url}"
            ),
            embed=wiki_embed,
        )
        sent_dm = True
    except Exception:
        pass

    dm_note = " *(ti ho mandato il link diretto anche in messaggio privato! 📬)*" if sent_dm else ""
    await ctx.send(
        content=f"📖 **{ctx.author.display_name}**, ecco la guida da [**wiki.uooutlands.com** ({title} → {sec_title})]({sec_url}) tradotta in italiano{dm_note}:",
        embed=wiki_embed,
    )
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(
            player.speak_tts(
                voice_answer,
                wait=False,
                interrupt_music=True,
                keep_paused_after=False,
            )
        )


@bot.hybrid_command(
    name="outlands",
    aliases=["uo", "outland"],
    description="Cerca guide, build, dungeon o boss su UO Outlands in italiano"
)
async def outlands_cmd(ctx: commands.Context, *, domanda: str = "aiuto"):
    await _handle_outlands_query(ctx, domanda)


@bot.hybrid_command(
    name="outlands_aiuto",
    aliases=["uo_aiuto", "outlands_help", "uo_help"],
    description="Mostra tutti gli argomenti e guide disponibili per UO Outlands"
)
async def outlands_aiuto_cmd(ctx: commands.Context):
    """Mostra l'elenco completo degli argomenti Outlands che Franchino conosce."""
    await ctx.defer()
    from outlands_wiki import get_outlands_help_embed
    help_embed, voice_answer = get_outlands_help_embed()
    await ctx.send(
        content=f"🗺️ **{ctx.author.display_name}**, ecco tutto quello che puoi chiedermi su **UO Outlands**!",
        embed=help_embed,
    )
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(
            player.speak_tts(voice_answer, wait=False, interrupt_music=True, keep_paused_after=False)
        )




@bot.hybrid_command(
    name="wiki",
    description="Cerca informazioni e guide sulla wiki di Ultima Online Outlands"
)
async def wiki_cmd(ctx: commands.Context, *, domanda: str = "aiuto"):
    await _handle_outlands_query(ctx, domanda)


@bot.hybrid_command(
    name="script",
    aliases=["scripts", "razor", "razorscript", "razor_script"],
    description="Cerca e scarica script Razor per UO Outlands da UORazorScripts"
)
async def script_cmd(ctx: commands.Context, skill: Optional[str] = "lista", numero: Optional[int] = None):
    """Mostra tutti gli script Razor per una skill e permette di scaricare il file .razor pronto da usare."""
    await ctx.defer()
    from razor_scripts import build_razor_scripts_response, build_razor_skills_list_response

    target_skill = (skill or "lista").strip()
    embed, view, script_file, voice_answer, code = build_razor_scripts_response(target_skill, requested_idx=numero)
    if not embed:
        embed, view, script_file, voice_answer, code = build_razor_skills_list_response()

    code_block = ""
    if code:
        clean_code = code.replace("```", "'''")
        if len(clean_code) <= 1400:
            code_block = f"\n```razor\n{clean_code}\n```"
        else:
            code_block = f"\n```razor\n{clean_code[:1300]}\n# ... [Codice completo nel file allegato sotto!] ...\n```"

    if target_skill.lower() in ("lista", "all", "skills", "aiuto", "catalogo", "list"):
        content_text = f"📜 **{ctx.author.display_name}**, ecco il catalogo completo degli script Razor di **UO Outlands**!"
    elif not script_file and not code:
        content_text = f"📜 **{ctx.author.display_name}**, ecco il catalogo per la tua ricerca su **{target_skill.title()}**:"
    else:
        content_text = f"📜 **{ctx.author.display_name}**, ecco gli script Razor ufficiali per **{target_skill.title()}**!{code_block}"

    if len(content_text) > 1950:
        content_text = content_text[:1900] + "\n```"

    kwargs = {
        "content": content_text,
        "embed": embed,
    }
    if view:
        kwargs["view"] = view
    if script_file:
        kwargs["file"] = script_file

    await ctx.send(**kwargs)

    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected() and voice_answer:
        asyncio.create_task(
            player.speak_tts(voice_answer, wait=False, interrupt_music=True, keep_paused_after=False)
        )


@bot.hybrid_command(
    name="status",
    aliases=["uo_status", "server_status", "portal", "portale", "portal_status"],
    description="Verifica se il server di Ultima Online Outlands è online"
)
async def status_cmd(ctx: commands.Context):
    """Verifica lo stato di connessione TCP e la latenza del server UO Outlands, e mostra il link al portale ufficiale."""
    await ctx.defer()
    import uo_outlands_service
    res = await uo_outlands_service.check_uo_server_status()
    embed = res.build_embed()
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(res.voice_text(), wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="server",
    description="Verifica se il server di UO Outlands è online"
)
async def server_cmd(ctx: commands.Context):
    """Verifica lo stato del server UO Outlands (alias per /status)."""
    await ctx.defer()
    import uo_outlands_service
    res = await uo_outlands_service.check_uo_server_status()
    embed = res.build_embed()
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(res.voice_text(), wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="riavvio",
    aliases=["server_riavvio", "server_restart", "uo_restart", "outlands_restart"],
    description="Mostra le informazioni sul riavvio del server e l'ultima patch di UO Outlands"
)
async def riavvio_cmd(ctx: commands.Context):
    """Mostra l'ultima patch annunciata, l'eventuale riavvio citato e il link diretto al forum."""
    await ctx.defer()
    import uo_outlands_service
    patch = await uo_outlands_service.patch_service.get_latest_restart_patch()
    if not patch:
        patch = await uo_outlands_service.patch_service.get_latest_patch()
    embed = uo_outlands_service.build_patch_embed(patch)
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        v_text = patch.voice_text()
        asyncio.create_task(player.speak_tts(v_text, wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="patch",
    aliases=["patches", "patchnotes", "patch_notes", "ultimapatch", "outlands_patch"],
    description="Mostra l'ultima patch ufficiale di UO Outlands con note e link"
)
async def patch_cmd(ctx: commands.Context):
    """Mostra l'ultima patch annunciata, l'eventuale riavvio citato e il link diretto al forum."""
    await ctx.defer()
    import uo_outlands_service
    patch = await uo_outlands_service.patch_service.get_latest_patch()
    embed = uo_outlands_service.build_patch_embed(patch)
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        if patch.mentions_restart:
            v_text = uo_outlands_service.VOICE_ALERT_PATCH_RESTART
        else:
            v_text = f"L'ultima patch pubblicata per Outlands è {patch.title}. Ho inviato i dettagli e il link in chat!"
        asyncio.create_task(player.speak_tts(v_text, wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="build",
    aliases=["cerca_build", "find_build"],
    description="Cerca una build specifica di UO Outlands per nome o abilità su UOAddicts"
)
async def build_cmd(
    ctx: commands.Context,
    *,
    nome: str
):
    """Cerca e visualizza una build specifica di Ultima Online Outlands da UOAddicts."""
    await ctx.defer()
    import uo_outlands_service
    builds = await uo_outlands_service.builds_service.search_builds(nome)
    if builds:
        if len(builds) == 1:
            embed = uo_outlands_service.build_single_build_embed(builds[0])
        else:
            embed = uo_outlands_service.build_builds_embed(builds, query=nome)
        voice_msg = f"Ho cercato la build per {nome} su UO Addicts e te l'ho mandata in chat!"
    else:
        popular = await uo_outlands_service.builds_service.fetch_builds("popular")
        embed = uo_outlands_service.build_builds_embed(popular, query=f"{nome} (nessun risultato)")
        embed.description = f"⚠️ Nessuna build trovata per **\"{nome}\"**. Ecco le build più popolari su UOAddicts:"
        voice_msg = f"Non ho trovato build per {nome}, ma ti ho mandato in chat le più popolari!"

    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(voice_msg, wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="builds",
    aliases=["builds_list", "uoaddicts_builds", "outlands_builds"],
    description="Mostra le build più usate di UO Outlands da UOAddicts (popolari, pvm, pvp)"
)
async def builds_cmd(
    ctx: commands.Context,
    categoria: Literal["popolari", "pvm", "pvp", "recenti"] = "popolari",
    *,
    ricerca: Optional[str] = None
):
    """Sfoglia o cerca le build più utilizzate di Ultima Online Outlands da UOAddicts."""
    await ctx.defer()
    import uo_outlands_service
    norm_cat = uo_outlands_service.builds_service.normalize_category(categoria)
    if ricerca:
        builds = await uo_outlands_service.builds_service.search_builds(ricerca, category=norm_cat)
    else:
        builds = await uo_outlands_service.builds_service.fetch_builds(category=norm_cat)
    embed = uo_outlands_service.build_builds_embed(builds, category=norm_cat, query=ricerca)
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        voice_msg = f"Ho recuperato le build {categoria} da UO Addicts e te le ho mandate in chat!"
        asyncio.create_task(player.speak_tts(voice_msg, wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="news",
    aliases=["notizie", "uoaddicts_news", "outlands_news"],
    description="Mostra le ultime novità e discussioni di UO Outlands da UOAddicts"
)
async def news_cmd(ctx: commands.Context):
    """Mostra le ultime notizie di Ultima Online Outlands pubblicate su UOAddicts."""
    await ctx.defer()
    import uo_outlands_service
    items = await uo_outlands_service.news_service.fetch_latest_news(limit=5)
    if not items:
        await ctx.send("⚠️ Impossibile recuperare le notizie da UOAddicts in questo momento. Riprova tra poco!")
        return
    embed = uo_outlands_service.build_news_list_embed(items)
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        voice_msg = "Ho trovato le ultime novità su Outlands da UO Addicts e le ho mandate in chat!"
        asyncio.create_task(player.speak_tts(voice_msg, wait=False, interrupt_music=True))


@bot.hybrid_command(
    name="vendor",
    aliases=["vendorsearch"],
    description="Cerca oggetti e prezzi sui Player Vendor di UO Outlands"
)
async def vendor_cmd(ctx: commands.Context, *, oggetto: str):
    """Cerca i migliori prezzi per un oggetto sui Player Vendor di Ultima Online Outlands."""
    await ctx.defer()
    import uo_outlands_service
    res = await uo_outlands_service.vendor_search_service.search_vendor(oggetto)
    embed = uo_outlands_service.build_vendor_search_embed(res)
    await ctx.send(embed=embed)
    player = player_manager.get_player(ctx.guild)
    if player and player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(
            player.speak_tts(uo_outlands_service.VOICE_VENDOR_SEARCH_REPLY, wait=False, interrupt_music=True)
        )


@bot.hybrid_command(
    name="prezzi",
    aliases=["prezzo"],
    description="Cerca oggetti e prezzi sui Player Vendor di UO Outlands"
)
async def prezzi_cmd(ctx: commands.Context, *, oggetto: str):
    """Cerca i migliori prezzi per un oggetto sui Player Vendor di Ultima Online Outlands."""
    await vendor_cmd(ctx, oggetto=oggetto)


@bot.command(name="sync")
async def sync_cmd(ctx: commands.Context):
    """Sincronizza istantaneamente tutti i comandi slash sul server attuale (es. !sync)."""
    msg = await ctx.send("🔄 Sincronizzazione comandi slash in corso su questo server...")
    try:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced = await bot.tree.sync(guild=ctx.guild)
        await msg.edit(content=f"✅ Sincronizzati istantaneamente **{len(synced)}** comandi slash su questo server! (Inclusi `/vendor`, `/prezzi`, `/status`, `/patch`, ecc.)")
    except Exception as e:
        await msg.edit(content=f"❌ Errore sincronizzazione comandi slash: `{e}`")



@bot.hybrid_command(
    name="partita",
    aliases=["partita_live"],
    description="Cerca una partita, palinsesto o diretta streaming audio (es. /partita stream Milan, /partita Juve)"
)
async def partita_cmd(
    ctx: commands.Context,
    squadra_o_evento: Optional[str] = "stasera",
    *,
    dettaglio: Optional[str] = None
):
    """Cerca una partita o evento sportivo, o trasmetti la telecronaca in diretta nel canale vocale."""
    await ctx.defer()
    from sports_scraper import sports_scraper
    from central_hub import hub

    raw_parts = []
    if squadra_o_evento:
        raw_parts.append(str(squadra_o_evento))
    if dettaglio:
        raw_parts.append(str(dettaglio))
    raw_query = " ".join(raw_parts).strip() or "stasera"

    is_stream = bool(re.search(r"\b(stream|streamma|telecronaca|trasmetti|audio|ascolta)\b", raw_query, flags=re.IGNORECASE))

    if is_stream:
        clean_q = re.sub(
            r"^(?:(?:(?:hey+|ehi+|hei+|ey+|ei+)\s+)?(?:franchino\s+)?)?"
            r"(?:puoi\s+)?(?:per\s+favore\s+)?"
            r"(?:trasmetti|streamma|metti|fai\s+partire|ascolta|fammi\s+ascoltare|riproduci|avvia|collega|stream|streaming)?\s*"
            r"(?:(?:la|il|lo|l['’]|i|gli|le)\s+)?"
            r"(?:partita|telecronaca|streaming|diretta|audio|match|palinsesto)?\s*"
            r"(?:(?:di|del|della|dei|degli|dello|d['’])\s+)?",
            "",
            raw_query,
            flags=re.IGNORECASE
        )
        clean_q = re.sub(r"\b(stream|streamma|telecronaca|trasmetti|audio|ascolta)\b", "", clean_q, flags=re.IGNORECASE).strip(" ,.!?:;-") or "live"

        player = player_manager.get_player(ctx.guild)
        voice_ch = getattr(getattr(ctx.author, "voice", None), "channel", None)
        if not voice_ch and (not player.voice_client or not player.voice_client.is_connected()):
            await ctx.send(
                "⚠️ **Devi essere connesso a un canale vocale** per far trasmettere la telecronaca a Franchino!",
                ephemeral=True
            )
            return

        match = await sports_scraper.resolve_match_for_streaming(clean_q)
        if match:
            song, embed, view, voice_msg = await player.stream_sports_match(
                match=match,
                voice_channel=voice_ch,
                text_channel=ctx.channel,
                requester=ctx.author,
                send_embed=False
            )
            await ctx.send(
                content=f"🎙️ **Franchino**: *\"{voice_msg}\"*",
                embed=embed,
                view=view
            )
            asyncio.create_task(
                hub.archive_interaction(
                    user_id=str(ctx.author.id),
                    user_name=ctx.author.display_name,
                    raw_query=f"partita stream {clean_q}",
                    intent="sports_stream_voice",
                    ai_response=match.get("title", "Telecronaca"),
                    voice_summary=voice_msg,
                    channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else None,
                    metadata={"query": clean_q, "match_id": match.get("id")}
                )
            )
            return

    query = raw_query
    res = await sports_scraper.search_event(query)
    embed, view, voice_msg = sports_scraper.build_sports_embed(res)

    await ctx.send(
        content=f"🎙️ **Franchino**: *\"{voice_msg}\"*",
        embed=embed,
        view=view,
    )

    asyncio.create_task(
        hub.archive_interaction(
            user_id=str(ctx.author.id),
            user_name=ctx.author.display_name,
            raw_query=f"partita {query}",
            intent="sports_stream",
            ai_response=res.get("best_match", {}).get("title", "Partita"),
            voice_summary=voice_msg,
            channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else None,
            metadata={"query": query}
        )
    )

    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected() and voice_msg:
        await player.speak_tts(voice_msg, wait=False, interrupt_music=True)


@bot.hybrid_command(
    name="stream",
    aliases=["stream_partita", "telecronaca", "ascolta_partita"],
    description="Ascolta la telecronaca e la partita in diretta nel canale vocale da MadPlay77!"
)
async def stream_cmd(ctx: commands.Context, *, partita_o_squadra: Optional[str] = "live"):
    """Avvia la trasmissione audio in diretta della telecronaca della partita scelta nel canale vocale."""
    await ctx.defer()
    from sports_scraper import sports_scraper
    from central_hub import hub

    query = (partita_o_squadra or "live").strip()
    player = player_manager.get_player(ctx.guild)

    voice_ch = getattr(getattr(ctx.author, "voice", None), "channel", None)
    if not voice_ch and (not player.voice_client or not player.voice_client.is_connected()):
        await ctx.send(
            "⚠️ **Devi essere connesso a un canale vocale** per far trasmettere la telecronaca a Franchino!",
            ephemeral=True
        )
        return

    match = await sports_scraper.resolve_match_for_streaming(query)
    if not match:
        res = await sports_scraper.search_event(query)
        embed, view, voice_msg = sports_scraper.build_sports_embed(res)
        await ctx.send(
            content=f"🎙️ **Franchino**: *\"{voice_msg}\"*",
            embed=embed,
            view=view
        )
        if player.voice_client and player.voice_client.is_connected() and voice_msg:
            await player.speak_tts(voice_msg, wait=False, interrupt_music=True)
        return

    song, embed, view, voice_msg = await player.stream_sports_match(
        match=match,
        voice_channel=voice_ch,
        text_channel=ctx.channel,
        requester=ctx.author,
        send_embed=False
    )

    await ctx.send(
        content=f"🎙️ **Franchino**: *\"{voice_msg}\"*",
        embed=embed,
        view=view
    )

    asyncio.create_task(
        hub.archive_interaction(
            user_id=str(ctx.author.id),
            user_name=ctx.author.display_name,
            raw_query=f"stream {query}",
            intent="sports_stream_voice",
            ai_response=match.get("title", "Telecronaca"),
            voice_summary=voice_msg,
            channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else None,
            metadata={"query": query, "match_id": match.get("id")}
        )
    )


@bot.hybrid_command(
    name="partite",
    aliases=["match", "partite_stasera"],
    description="Palinsesto sportivo completo (Calcio, Serie A, Champions, NBA, Motori)"
)
async def partite_cmd(
    ctx: commands.Context,
    disciplina_o_lega: Optional[str] = None,
    squadra_o_filtro: Optional[str] = None
):
    """Mostra tutti i match di stasera per qualsiasi sport o consulta una specifica lega (es. Serie A, Champions, NBA)."""
    await ctx.defer()
    from sports_scraper import sports_scraper
    from central_hub import hub

    parts = [p for p in (disciplina_o_lega, squadra_o_filtro) if p]
    query = " ".join(parts).strip() or "stasera"

    res = await sports_scraper.search_event(query)
    embed, view, voice_msg = sports_scraper.build_sports_embed(res)

    await ctx.send(
        content=f"🎙️ **Franchino**: *\"{voice_msg}\"*",
        embed=embed,
        view=view,
    )

    asyncio.create_task(
        hub.archive_interaction(
            user_id=str(ctx.author.id),
            user_name=ctx.author.display_name,
            raw_query=f"partite {query}",
            intent="sports_schedule",
            ai_response=res.get("best_match", {}).get("title", "Palinsesto Sport"),
            voice_summary=voice_msg,
            channel_name=ctx.channel.name if hasattr(ctx.channel, 'name') else None,
            metadata={"query": query}
        )
    )

    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected() and voice_msg:
        await player.speak_tts(voice_msg, wait=False, interrupt_music=True)


@bot.hybrid_command(
    name="sport",
    aliases=["palinsesto", "match_stasera"],
    description="Palinsesto sportivo completo (Calcio, Serie A, Champions, NBA, Motori)"
)
async def sport_cmd(
    ctx: commands.Context,
    disciplina_o_lega: Optional[str] = None,
    squadra_o_filtro: Optional[str] = None
):
    """Consulta il palinsesto sportivo per oggi, stasera o per una specifica lega o squadra."""
    await partite_cmd(ctx, disciplina_o_lega=disciplina_o_lega, squadra_o_filtro=squadra_o_filtro)


# ==========================================
# INTERAZIONI SOCIALI CON GLI UTENTI DISCORD
# ==========================================
async def _handle_text_interaction(ctx: commands.Context, itype: str, utente: Optional[str] = None):
    await ctx.defer()
    from interactions import execute_interaction
    player = player_manager.get_player(ctx.guild)
    target_str = utente or ""

    async def _notify(message=None, embed=None, view=None):
        kwargs = {}
        if message:
            kwargs["content"] = message
        if embed:
            kwargs["embed"] = embed
        if view:
            kwargs["view"] = view
        await ctx.send(**kwargs)

    await execute_interaction(player, ctx.author, itype, target_str, _notify)


@bot.hybrid_command(
    name="saluta",
    aliases=["saluto"],
    description="Chiedi a Franchino di salutare qualcuno nel canale o tutti i presenti"
)
async def saluta_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "saluto", utente)


@bot.hybrid_command(
    name="brindisi",
    aliases=["brinda", "cincin"],
    description="Fai un brindisi speciale con Franchino per un amico o per tutta la console"
)
async def brindisi_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "brindisi", utente)


@bot.hybrid_command(
    name="abbraccio",
    aliases=["abbraccia"],
    description="Manda un caloroso abbraccio dalla console a un amico o a tutti"
)
async def abbraccio_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "abbraccio", utente)


@bot.hybrid_command(
    name="carica",
    aliases=["hype", "motiva"],
    description="Chiedi a Franchino di caricare al massimo qualcuno o la stanza intera"
)
async def carica_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "carica", utente)


@bot.hybrid_command(
    name="auguri",
    aliases=["compleanno", "buoncompleanno"],
    description="Fai fare gli auguri di buon compleanno a Franchino con fanfara DJ"
)
async def auguri_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "auguri", utente)


@bot.hybrid_command(
    name="buonanotte",
    aliases=["sognidoro"],
    description="Fai dare la buonanotte e i sogni d'oro da Franchino"
)
async def buonanotte_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "buonanotte", utente)


@bot.hybrid_command(
    name="cinque",
    aliases=["highfive", "battiilcinque"],
    description="Batti il cinque con Franchino o fallo battere a un amico"
)
async def cinque_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "cinque", utente)


@bot.hybrid_command(
    name="troll",
    aliases=["sfotti", "scherza"],
    description="Chiedi a Franchino di prendere in giro bonariamente qualcuno"
)
async def troll_cmd(ctx: commands.Context, *, utente: Optional[str] = None):
    await _handle_text_interaction(ctx, "troll", utente)


# ==========================================
# COMANDI DA ADMIN & MODERATORE DISCORD
# ==========================================
@bot.hybrid_command(name="sposta", description="[Admin] Sposta un utente in un altro canale vocale")
async def sposta_cmd(ctx: commands.Context, utente: discord.Member, canale: discord.VoiceChannel):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "move_members")
    if not allowed:
        await ctx.send("⛔ Non hai i permessi per spostare i membri!", ephemeral=True)
        return
    try:
        await utente.move_to(canale, reason=f"Spostato da {ctx.author.display_name}")
        await ctx.send(f"🚪 **{utente.display_name}** è stato spostato in **{canale.name}**!")
    except Exception as e:
        await ctx.send(f"⚠️ Impossibile spostare **{utente.display_name}**: `{e}`")


@bot.hybrid_command(name="spostatutti", description="[Admin] Sposta tutti i presenti nella tua stanza in un altro canale vocale")
async def spostatutti_cmd(ctx: commands.Context, canale: discord.VoiceChannel):
    from admin_commands import check_admin_permission, ensure_guild_member
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "move_members")
    if not allowed:
        await ctx.send("⛔ Non hai i permessi per spostare i membri!", ephemeral=True)
        return
    vc = ctx.guild.voice_client
    source_ch = (ctx.author.voice.channel if isinstance(ctx.author, discord.Member) and ctx.author.voice else None) or (vc.channel if vc else None)
    if not source_ch:
        await ctx.send("⚠️ Nessun canale vocale attivo da cui spostare gli utenti!")
        return
    count = 0
    for m_id in list(source_ch.voice_states.keys()):
        if m_id == bot.user.id:
            continue
        m_obj = await ensure_guild_member(ctx.guild, discord.Object(id=m_id))
        if m_obj:
            try:
                await m_obj.move_to(canale)
                count += 1
            except Exception:
                pass
    if vc and vc.channel != canale:
        try:
            await vc.move_to(canale)
        except Exception:
            pass
    await ctx.send(f"🌪️ Spostate **{count} persone** (+ Franchino) in **{canale.name}**!")


@bot.hybrid_command(name="scollega", description="[Admin] Disconnette un utente dal canale vocale")
async def scollega_cmd(ctx: commands.Context, utente: discord.Member):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "move_members")
    if not allowed:
        await ctx.send("⛔ Non hai i permessi per disconnettere i membri!", ephemeral=True)
        return
    try:
        await utente.move_to(None, reason=f"Disconnesso da {ctx.author.display_name}")
        await ctx.send(f"👢 **{utente.display_name}** è stato disconnesso dal canale vocale!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="muta", description="[Admin] Muta il microfono di un utente sul server")
async def muta_cmd(ctx: commands.Context, utente: discord.Member):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "mute_members")
    if not allowed:
        await ctx.send("⛔ Non hai i permessi per silenziare i membri!", ephemeral=True)
        return
    try:
        await utente.edit(mute=True, reason=f"Mutato da {ctx.author.display_name}")
        await ctx.send(f"🔇 **{utente.display_name}** è stato mutato sul server!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="smuta", description="[Admin] Riattiva il microfono di un utente sul server")
async def smuta_cmd(ctx: commands.Context, utente: discord.Member):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "mute_members")
    if not allowed:
        await ctx.send("⛔ Non hai i permessi per gestire il muto dei membri!", ephemeral=True)
        return
    try:
        await utente.edit(mute=False, reason=f"Smutato da {ctx.author.display_name}")
        await ctx.send(f"🎙️ **{utente.display_name}** può di nuovo parlare!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="ruolo", description="[Admin] Aggiunge o rimuove un ruolo a un utente del server")
async def ruolo_cmd(
    ctx: commands.Context,
    azione: Literal["aggiungi", "togli"],
    utente: discord.Member,
    ruolo: discord.Role
):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "manage_roles")
    if not allowed:
        await ctx.send("⛔ Non hai il permesso `Gestisci Ruoli`!", ephemeral=True)
        return
    try:
        if azione == "aggiungi":
            await utente.add_roles(ruolo, reason=f"Assegnato da {ctx.author.display_name}")
            await ctx.send(f"🎭 Ruolo {ruolo.mention} aggiunto a **{utente.display_name}**!")
        else:
            await utente.remove_roles(ruolo, reason=f"Rimosso da {ctx.author.display_name}")
            await ctx.send(f"🎭 Ruolo {ruolo.mention} rimosso da **{utente.display_name}**!")
    except discord.Forbidden:
        await ctx.send("⚠️ Il ruolo di Franchino deve trovarsi **sopra** il ruolo che vuoi assegnare nelle Impostazioni del Server!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="soprannome", description="[Admin] Cambia o ripristina il soprannome di un utente sul server")
async def soprannome_cmd(ctx: commands.Context, utente: discord.Member, *, nuovo_nome: Optional[str] = None):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "manage_nicknames")
    if not allowed:
        await ctx.send("⛔ Non hai il permesso `Gestisci Soprannomi`!", ephemeral=True)
        return
    try:
        old = utente.display_name
        await utente.edit(nick=nuovo_nome, reason=f"Modificato da {ctx.author.display_name}")
        if nuovo_nome:
            await ctx.send(f"🏷️ Il soprannome di **{old}** è ora **{nuovo_nome}**!")
        else:
            await ctx.send(f"🏷️ Soprannome di **{utente.name}** ripristinato!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="timeout", description="[Admin] Mette in timeout un utente per N minuti (0 per rimuovere)")
async def timeout_cmd(ctx: commands.Context, utente: discord.Member, minuti: int = 5):
    import datetime
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "moderate_members")
    if not allowed:
        await ctx.send("⛔ Non hai il permesso `Modera Membri`!", ephemeral=True)
        return
    try:
        if minuti <= 0:
            await utente.timeout(None, reason=f"Rimosso da {ctx.author.display_name}")
            await ctx.send(f"✅ Timeout rimosso a **{utente.display_name}**!")
        else:
            await utente.timeout(datetime.timedelta(minutes=minuti), reason=f"Timeout di {ctx.author.display_name}")
            await ctx.send(f"⏳ **{utente.display_name}** è in timeout per **{minuti} minuti**!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="pulisci", aliases=["purge", "puliscichat"], description="[Admin] Cancella gli ultimi N messaggi nella chat")
async def pulisci_cmd(ctx: commands.Context, messaggi: int = 15):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "manage_messages")
    if not allowed:
        await ctx.send("⛔ Non hai il permesso `Gestisci Messaggi`!", ephemeral=True)
        return
    await ctx.defer(ephemeral=True)
    limit = max(1, min(100, messaggi))
    deleted = await ctx.channel.purge(limit=limit)
    await ctx.send(f"🧹 Cancellati **{len(deleted)} messaggi**!", ephemeral=True)


@bot.hybrid_command(name="kick", description="[Admin] Espelle un utente dal server Discord")
async def kick_cmd(ctx: commands.Context, utente: discord.Member, *, motivo: str = "Espulso tramite Franchino"):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "kick_members")
    if not allowed:
        await ctx.send("⛔ Non hai il permesso `Espelli Membri`!", ephemeral=True)
        return
    try:
        await utente.kick(reason=f"{motivo} (da {ctx.author.display_name})")
        await ctx.send(f"👢 **{utente.display_name}** è stato espulso dal server!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


@bot.hybrid_command(name="ban", description="[Admin] Banna un utente dal server Discord")
async def ban_cmd(ctx: commands.Context, utente: discord.Member, *, motivo: str = "Bannato tramite Franchino"):
    from admin_commands import check_admin_permission
    allowed, _ = await check_admin_permission(ctx.guild, ctx.author, "ban_members")
    if not allowed:
        await ctx.send("⛔ Non hai il permesso `Banna Membri`!", ephemeral=True)
        return
    try:
        await utente.ban(reason=f"{motivo} (da {ctx.author.display_name})")
        await ctx.send(f"🔨 **{utente.display_name}** è stato bannato dal server!")
    except Exception as e:
        await ctx.send(f"⚠️ Errore: `{e}`")


# ==========================================
# GESTIONE ERRORI COMANDI
# ==========================================
@bot.hybrid_command(name="hub_stats", description="Mostra le statistiche dell'Hub Centrale e delle richieste archiviate")
async def hub_stats_cmd(ctx: commands.Context):
    from central_hub import hub
    stats = await hub.get_stats()
    embed = discord.Embed(
        title="📊 Hub Centrale Dati & Conoscenza di Franchino",
        color=0xE67E22,
        description="Tutte le richieste e interazioni del server vengono archiviate in modo permanente per arricchire il bot e condivise con tutti i progetti del server.",
    )
    embed.add_field(name="🗄️ Backend Storage", value=f"`{stats.get('backend', 'N/A')}`", inline=False)
    embed.add_field(name="💬 Interazioni Totali", value=f"**{stats.get('total_interactions', 0)}** salvate", inline=True)
    embed.add_field(name="👥 Utenti Unici", value=f"**{stats.get('unique_users', 0)}**", inline=True)
    top_str = "\n".join(f"• `{intent}`: {cnt}" for intent, cnt in stats.get("top_intents", []))
    embed.add_field(name="🎯 Categorie Più Richieste", value=top_str or "Nessun dato ancora", inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="hub_recent", description="Mostra le ultime 5 interazioni archiviate nell'Hub")
async def hub_recent_cmd(ctx: commands.Context):
    from central_hub import hub
    recent = await hub.get_recent_interactions(limit=5)
    if not recent:
        await ctx.send("📁 L'Hub è ancora vuoto! Fai qualche richiesta a Franchino per popolarlo.")
        return
    embed = discord.Embed(title="📜 Ultime Richieste Archiviate nell'Hub", color=0xE67E22)
    for r in recent:
        embed.add_field(
            name=f"👤 {r.get('user_name', 'Anonimo')} • `{r.get('intent', 'general')}`",
            value=f"🗣️ *\"{r.get('raw_query', '')}\"*\n🤖 Risposta: {r.get('voice_summary', '')[:100]}...",
            inline=False,
        )
    await ctx.send(embed=embed)


@bot.hybrid_command(name="favola_live", description="Chiede al Cervello Gemma 2 2B di generare una favola inedita in tempo reale")
async def favola_live_cmd(ctx: commands.Context, *, tema: Optional[str] = None):
    await ctx.defer()
    from brain import brain
    player = player_manager.get_player(ctx.guild)
    fable = await brain.generate_live_fable(ctx.author.display_name, topic=tema)
    embed = discord.Embed(
        title="🎙️🌙 Favola Inedita di Franchino il Vocalist (Gemma 2 2B)",
        description=f"> *\"{fable}\"*",
        color=0x9B59B6,
    )
    await ctx.send(embed=embed)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(fable, wait=True, interrupt_music=True, dramatic_fable=True))


@bot.hybrid_command(
    name="ora",
    aliases=["orario", "data"],
    description="Mostra l'ora e la data esatta (fuso Roma Europe/Rome) con stile e voce da vocalist"
)
async def ora_cmd(ctx: commands.Context):
    """Mostra l'orario e la data corrente italiana con animazione vocalist."""
    await ctx.defer()
    from smart_assistant import get_current_time_info
    time_info = get_current_time_info()
    await ctx.send(embed=time_info["embed"])
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(time_info["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False))


@bot.hybrid_command(
    name="meteo",
    aliases=["tempo", "previsioni"],
    description="Previsioni meteo in tempo reale via Open-Meteo per qualsiasi città"
)
async def meteo_cmd(ctx: commands.Context, citta: Optional[str] = "Roma"):
    """Consulta le condizioni meteo attuali e previsioni del giorno per la città indicata."""
    await ctx.defer()
    from smart_assistant import get_weather_info
    target_city = (citta or "Roma").strip()
    w_data = await get_weather_info(target_city)
    await ctx.send(embed=w_data["embed"])
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(w_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False))


@bot.hybrid_command(
    name="timer",
    aliases=["promemoria", "sveglia"],
    description="Imposta un timer vocale asincrono (es. '5m', '10 minuti', 'cancella')"
)
async def timer_cmd(ctx: commands.Context, durata: str, *, motivo: Optional[str] = None):
    """Gestisce timer e promemoria con squillo sveglia e annuncio vocale da vocalist."""
    await ctx.defer()
    from smart_assistant import (
        timer_manager,
        format_duration_italian,
        parse_timer_request,
    )
    player = player_manager.get_player(ctx.guild)
    clean_d = durata.strip().lower()

    if clean_d in ("status", "stato", "info", "tempo"):
        timers = timer_manager.get_timers(ctx.guild.id, user_id=ctx.author.id)
        if not timers:
            timers = timer_manager.get_timers(ctx.guild.id)
        if not timers:
            msg = f"Nessun timer attivo al momento, {ctx.author.display_name}!"
            await ctx.send(f"⏰ {msg}")
            if player.voice_client and player.voice_client.is_connected():
                asyncio.create_task(player.speak_tts("Ragazzi, vi ho scritto in chat: nessun timer attivo! Date un'occhiata!", wait=False, interrupt_music=True))
        else:
            t = timers[0]
            rem_str = format_duration_italian(int(t.remaining_seconds))
            msg = f"Mancano ancora {rem_str} al timer per '{t.label}', {ctx.author.display_name}!"
            embed = discord.Embed(
                title="⏰ Stato Timer Attivo",
                description=f"⏳ Mancano ancora **{rem_str}** al timer per **{t.label}**!",
                color=0x3498DB,
            )
            embed.add_field(name="Durata iniziale", value=format_duration_italian(t.duration_seconds), inline=True)
            embed.add_field(name="Tempo residuo", value=rem_str, inline=True)
            embed.add_field(name="Impostato da", value=t.user_name, inline=True)
            await ctx.send(embed=embed)
            if player.voice_client and player.voice_client.is_connected():
                asyncio.create_task(player.speak_tts("Ragazzi, vi ho scritto lo stato dei timer in chat! Date un'occhiata!", wait=False, interrupt_music=True))
        return

    if clean_d in ("cancella", "stop", "elimina", "annulla", "clear", "cancel"):
        cancelled = timer_manager.cancel_timers(ctx.guild.id, user_id=ctx.author.id)
        if not cancelled:
            cancelled = timer_manager.cancel_timers(ctx.guild.id)
        if cancelled:
            names = ", ".join([c.label for c in cancelled])
            msg = f"Timer per '{names}' cancellato con successo ragazzi!"
            await ctx.send(f"⏰ {msg}")
        else:
            msg = "Nessun timer attivo da cancellare ragazzi!"
            await ctx.send(f"⏰ {msg}")
        return

    full_text = f"{durata} {motivo or ''}".strip()
    seconds, label = parse_timer_request(full_text)
    if seconds <= 0:
        seconds = 300
    if motivo:
        cleaned_m = re.sub(r"^(?:per|di|a|che|il|la|lo|i|gli|le|l['’])\s+", "", motivo.strip()).strip()
        label = cleaned_m if cleaned_m else motivo.strip()

    dur_str = format_duration_italian(seconds)
    timer_manager.start_timer(
        guild_id=ctx.guild.id,
        channel=ctx.channel,
        user=ctx.author,
        duration_seconds=seconds,
        label=label,
        player=player,
    )
    v_msg = f"Timer di {dur_str} per {label} impostato ragazzi! Vi avviso appena scade! Magia!"
    embed = discord.Embed(
        title="⏰ Timer Impostato",
        description=f"🔔 Timer impostato per **{label}**!\n\n🎙️ *\"{v_msg}\"*",
        color=0x3498DB,
    )
    embed.add_field(name="Durata", value=f"`{dur_str}`", inline=True)
    embed.add_field(name="Motivo", value=f"`{label}`", inline=True)
    embed.add_field(name="Autore", value=ctx.author.display_name, inline=True)
    embed.set_footer(text="Franchino Bot • Timer & Promemoria")
    await ctx.send(embed=embed)


@bot.hybrid_command(
    name="calcola",
    aliases=["calc", "calcolatrice"],
    description="Calcola un'espressione matematica (es. '25 * 4', '150 / 3', 'radice di 16')"
)
async def calcola_cmd(ctx: commands.Context, *, operazione: str):
    """Calcola un'espressione matematica in sicurezza e restituisce il risultato vocale e chat."""
    await ctx.defer()
    from smart_assistant import evaluate_math_expression
    m_data = evaluate_math_expression(operazione)
    await ctx.send(embed=m_data["embed"])
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(m_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False))


@bot.hybrid_command(
    name="lancia",
    aliases=["dado", "moneta", "sorte", "roll"],
    description="Lancia una moneta (testa o croce) o un dado da gioco (es. dado, moneta, d20, d100)"
)
async def lancia_cmd(ctx: commands.Context, tipo: Optional[str] = "dado"):
    """Interroga la sorte con testa o croce o il lancio di dadi numerici."""
    await ctx.defer()
    from smart_assistant import flip_coin, roll_dice
    clean_t = (tipo or "dado").lower().strip()
    if any(w in clean_t for w in ("moneta", "coin", "testa", "croce")):
        res = flip_coin()
    else:
        m = re.search(r"(\d+)", clean_t)
        sides = int(m.group(1)) if m else 6
        res = roll_dice(sides)

    await ctx.send(embed=res["embed"])
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(res["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False))


@bot.hybrid_command(
    name="barzelletta",
    aliases=["battuta", "ridere"],
    description="Racconta una barzelletta o battuta divertente da vocalist della notte"
)
async def barzelletta_cmd(ctx: commands.Context):
    """Franchino intrattiene la compagnia con una battuta carica di buonumore."""
    await ctx.defer()
    from smart_assistant import get_vocalist_joke
    j_data = get_vocalist_joke()
    await ctx.send(embed=j_data["embed"])
    player = player_manager.get_player(ctx.guild)
    if player.voice_client and player.voice_client.is_connected():
        asyncio.create_task(player.speak_tts(j_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False))


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Parametro mancante: `{error.param.name}`. Usa `/help` per la sintassi corretta.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"⚠️ Valore non valido fornito: {error}")
    else:
        logger.error(f"Errore non gestito nel comando {ctx.command}: {error}", exc_info=True)
        try:
            await ctx.send(f"❌ Si è verificato un errore durante l'esecuzione del comando: `{error}`")
        except Exception:
            pass


# ==========================================
# AVVIO APPLICAZIONE
# ==========================================
def main():
    if not DISCORD_TOKEN or DISCORD_TOKEN == "inserisci_qui_il_tuo_token_discord":
        print("\n" + "=" * 60)
        print("❌ ERRORE: DISCORD_TOKEN non configurato nel file .env!")
        print("=" * 60)
        print("Per avviare Franchino:")
        print("1. Vai su https://discord.com/developers/applications")
        print("2. Crea la tua applicazione o seleziona quella esistente")
        print("3. Nella sezione 'Bot', clicca 'Reset Token' e copialo")
        print("4. Apri il file .env in questa cartella e incolla il token:")
        print("   DISCORD_TOKEN=il_tuo_token_qui")
        print("5. Riavvia con: python3 main.py")
        print("=" * 60 + "\n")
        sys.exit(1)

    logger.info("Avvio del bot musicale Franchino in corso...")
    try:
        bot.run(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        logger.critical("Impossibile connettersi a Discord: Il Token fornito in .env non è valido.")
    except Exception as e:
        logger.critical(f"Errore fatale all'avvio: {e}", exc_info=True)


if __name__ == "__main__":
    main()
