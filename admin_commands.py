import datetime
import difflib
import logging
import os
import re
from typing import List, Optional, Tuple, TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from player import GuildPlayer

logger = logging.getLogger("Franchino.Admin")


def _get_extra_admin_ids() -> set[int]:
    """Legge eventuali ID utente extra autorizzati come admin da ADMIN_USER_IDS nel file .env."""
    raw = os.getenv("ADMIN_USER_IDS", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


async def ensure_guild_member(guild: discord.Guild, user) -> Optional[discord.Member]:
    """Si assicura di avere un oggetto discord.Member completo (con ruoli e guild_permissions)."""
    if isinstance(user, discord.Member):
        return user
    uid = getattr(user, "id", 0)
    if not uid or not guild:
        return None
    member = guild.get_member(uid)
    if member:
        return member
    try:
        return await guild.fetch_member(uid)
    except Exception:
        return None


async def check_admin_permission(guild: discord.Guild, user, required_perm: str = "administrator") -> Tuple[bool, Optional[discord.Member]]:
    """
    Controlla se l'utente che ha impartito il comando (a voce o in chat) è il proprietario del server,
    è in ADMIN_USER_IDS, ha il permesso Administrator, oppure possiede lo specifico permesso richiesto
    (es. 'move_members', 'mute_members', 'manage_roles', 'manage_nicknames', 'manage_channels', ecc.).
    """
    uid = getattr(user, "id", 0)
    if not uid or not guild:
        return False, None

    member = await ensure_guild_member(guild, user)

    if uid == guild.owner_id or uid in _get_extra_admin_ids():
        return True, member

    if not member:
        return False, None

    perms = member.guild_permissions
    if perms.administrator:
        return True, member

    if hasattr(perms, required_perm) and getattr(perms, required_perm):
        return True, member

    return False, member


async def find_member_in_guild(guild: discord.Guild, player: "GuildPlayer", name_query: str, exclude_uid: int = 0) -> Optional[discord.Member]:
    """
    Cerca un membro in tutto il server Discord (prima nei canali vocali attivi, poi nella cache
    e infine tramite ricerca API Discord) partendo dal nome o soprannome pronunciato a voce.
    """
    clean_query = re.sub(r"^(?:a|ad|al|allo|alla|ai|agli|alle|il|lo|la|di|da)\s+", "", name_query.lower().strip())
    clean_query = re.sub(r"[^\w\s]", "", clean_query).strip()
    if not clean_query:
        return None

    candidate_members: Dict[int, discord.Member] = {}

    # 1. Raccogli tutti gli utenti connessi in QUALSIASI canale vocale del server
    for vch in guild.voice_channels:
        for m_uid in list(vch.voice_states.keys()):
            if m_uid == guild.me.id:
                continue
            m_obj = guild.get_member(m_uid)
            if not m_obj:
                try:
                    m_obj = await guild.fetch_member(m_uid)
                except Exception:
                    pass
            if m_obj and not m_obj.bot:
                candidate_members[m_obj.id] = m_obj

    # 2. Aggiungi i membri già presenti nella cache del server
    for m_obj in guild.members:
        if not m_obj.bot and m_obj.id not in candidate_members:
            candidate_members[m_obj.id] = m_obj

    # 3. Cerca anche via API HTTP/Gateway per nome (funziona anche senza Server Members Intent!)
    try:
        queried = await guild.query_members(query=clean_query[:32], limit=10)
        for m_obj in queried:
            if not m_obj.bot:
                candidate_members[m_obj.id] = m_obj
    except Exception:
        pass

    vc = player.voice_client or guild.voice_client
    in_same_room_ids = set(vc.channel.voice_states.keys()) if (vc and vc.channel) else set()

    best_member: Optional[discord.Member] = None
    best_score = 0.0

    for m_id, m in candidate_members.items():
        raw_candidates = set()
        for attr in ("display_name", "nick", "global_name", "name"):
            val = getattr(m, attr, None)
            if val and isinstance(val, str):
                cleaned_val = re.sub(r"[^\w\s]", " ", val.lower()).strip()
                if cleaned_val:
                    raw_candidates.add(cleaned_val)
                    for part in cleaned_val.split():
                        if len(part) >= 2:
                            raw_candidates.add(part)

        member_score = 0.0
        for cand in raw_candidates:
            if cand == clean_query:
                score = 1.0
            elif cand.startswith(clean_query) or clean_query.startswith(cand):
                score = 0.92
            elif clean_query in cand or cand in clean_query:
                score = 0.84
            else:
                score = difflib.SequenceMatcher(None, clean_query, cand).ratio()
            if score > member_score:
                member_score = score

        # Leggero bonus se l'utente è nella stessa stanza vocale (a parità di nome)
        if member_score >= 0.60:
            if m_id in in_same_room_ids:
                member_score += 0.02
            if m_id != exclude_uid:
                member_score += 0.01

        if member_score > best_score:
            best_score = member_score
            best_member = m

    if best_score >= 0.58:
        return best_member
    return None


def find_voice_channel_in_guild(guild: discord.Guild, query: str, current_channel: Optional[discord.VoiceChannel] = None) -> Optional[discord.VoiceChannel]:
    """Trova un canale vocale nel server partendo dal nome pronunciato a voce."""
    clean_q = re.sub(
        r"^(?:nella\s+stanza|nel\s+canale|in\s+stanza|in\s+canale|nella|nel|in|su|a|ad|al|alla)\s+",
        "",
        query.lower().strip(),
    )
    clean_q = re.sub(r"[^\w\s]", " ", clean_q).strip()
    if not clean_q:
        return None

    if clean_q in ("qui", "qua", "questa stanza", "questo canale", "da noi", "con noi") and current_channel:
        return current_channel

    if clean_q in ("afk", "pausa", "assenti") and guild.afk_channel:
        return guild.afk_channel

    best_ch = None
    best_score = 0.0

    for ch in guild.voice_channels:
        ch_clean = re.sub(r"[^\w\s]", " ", ch.name.lower()).strip()
        ch_clean = re.sub(r"\s+", " ", ch_clean)
        if not ch_clean:
            continue

        if ch_clean == clean_q:
            score = 1.0
        elif ch_clean.startswith(clean_q) or clean_q in ch_clean:
            score = 0.92
        else:
            score = difflib.SequenceMatcher(None, clean_q, ch_clean).ratio()
            for word in ch_clean.split():
                w_score = difflib.SequenceMatcher(None, clean_q, word).ratio()
                if w_score > score:
                    score = w_score

        if score > best_score:
            best_score = score
            best_ch = ch

    if best_score >= 0.52:
        return best_ch
    return None


def find_role_in_guild(guild: discord.Guild, query: str) -> Optional[discord.Role]:
    """Trova un ruolo nel server Discord partendo dal nome pronunciato a voce."""
    clean_q = re.sub(
        r"^(?:il\s+ruolo|ruolo|grado|tag|di|da)\s+",
        "",
        query.lower().strip(),
    )
    clean_q = re.sub(r"[^\w\s]", " ", clean_q).strip()
    if not clean_q:
        return None

    best_role = None
    best_score = 0.0

    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        r_clean = re.sub(r"[^\w\s]", " ", role.name.lower()).strip()
        r_clean = re.sub(r"\s+", " ", r_clean)
        if not r_clean:
            continue

        if r_clean == clean_q:
            score = 1.0
        elif r_clean.startswith(clean_q) or clean_q in r_clean:
            score = 0.92
        else:
            score = difflib.SequenceMatcher(None, clean_q, r_clean).ratio()

        if score > best_score:
            best_score = score
            best_role = role

    if best_score >= 0.55:
        return best_role
    return None


async def handle_voice_admin_command(
    player: "GuildPlayer",
    user,
    command_part: str,
    notify_cb,
) -> bool:
    """
    Analizza il comando vocale per verificare se è un comando da Admin / Moderatore di Discord.
    Se corrisponde a un comando Admin, verifica i permessi, lo esegue, risponde a voce + in chat e restituisce True.
    Altrimenti restituisce False e lascia proseguire i normali comandi musicali.
    """
    guild = player.guild
    vc = player.voice_client or guild.voice_client
    current_vch = vc.channel if vc and vc.channel else None
    cmd = command_part.strip()
    cmd_lower = cmd.lower().strip()
    uid = getattr(user, "id", 0)

    async def _deny_perm(perm_label: str):
        await notify_cb(
            f"⛔ **{user.display_name}**, non hai il permesso **{perm_label}** per eseguire questo comando da Admin!"
        )
        await player.speak_tts(f"Eh no {user.display_name}, non hai i permessi da amministratore per farlo!")

    async def _bot_missing_perm(action_desc: str):
        await notify_cb(
            f"⚠️ Non posso **{action_desc}** perché il ruolo di **Franchino** non ha il permesso `Amministratore` "
            f"o si trova sotto il ruolo dell'utente nelle impostazioni del server!"
        )
        await player.speak_tts("Non ho i permessi necessari sul server, sposta il ruolo di Franchino più in alto!")

    # ==========================================================================
    # 1. SPOSTA TUTTI IN UN'ALTRA STANZA VOCALE ("sposta tutti in...", "porta tutti in...")
    # ==========================================================================
    move_all_match = re.match(
        r"^(?:sposta|porta|manda|trasferisci)\s+(?:tutti|tutta\s+la\s+stanza|tutti\s+quanti)\s+(?:in|nel\s+canale|nella\s+stanza|su|a|ad)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if move_all_match:
        allowed, _ = await check_admin_permission(guild, user, "move_members")
        if not allowed:
            await _deny_perm("Sposta Membri")
            return True

        dest_query = move_all_match.group(1).strip(" ,.!?:;-")
        dest_ch = find_voice_channel_in_guild(guild, dest_query, current_vch)
        if not dest_ch:
            ch_names = ", ".join(c.name for c in guild.voice_channels)
            await notify_cb(f"⚠️ Non trovo il canale vocale **\"{dest_query}\"**! *(Canali vocali: {ch_names})*")
            await player.speak_tts(f"Non trovo il canale vocale {dest_query}!")
            return True

        if not current_vch:
            return True

        moved_count = 0
        member_ids = [m_id for m_id in list(current_vch.voice_states.keys()) if m_id != guild.me.id]
        for m_id in member_ids:
            m_obj = await ensure_guild_member(guild, discord.Object(id=m_id))
            if m_obj:
                try:
                    await m_obj.move_to(dest_ch, reason=f"Comando vocale Admin di {user.display_name}")
                    moved_count += 1
                except Exception:
                    pass

        # Sposta anche Franchino nella nuova stanza così continua a suonare con loro!
        if vc and vc.channel != dest_ch:
            try:
                await vc.move_to(dest_ch)
            except Exception:
                pass

        await notify_cb(
            f"🌪️ **{user.display_name}** ha spostato tutta la stanza (**{moved_count} persone** + Franchino) in **{dest_ch.name}**!"
        )
        await player.speak_tts(f"Ho spostato tutti in {dest_ch.name}!")
        return True

    # ==========================================================================
    # 2. PORTA QUI UN UTENTE DA UN'ALTRA STANZA ("porta qui Ema", "porta Ema da noi", "sposta qui Ema")
    # ==========================================================================
    bring_here_match = re.match(
        r"^(?:porta|sposta|trascina|chiama)\s+(?:qui|qua|da\s+noi|nella\s+nostra\s+stanza)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(?:porta|sposta|trascina)\s+(.+?)\s+(?:qui|qua|da\s+noi|con\s+noi|in\s+questa\s+stanza)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if bring_here_match and current_vch:
        allowed, _ = await check_admin_permission(guild, user, "move_members")
        if not allowed:
            await _deny_perm("Sposta Membri")
            return True

        target_name = bring_here_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_name, exclude_uid=uid)
        if not target:
            await notify_cb(f"⚠️ Non trovo nessun utente chiamato **\"{target_name}\"**!")
            await player.speak_tts(f"Non trovo {target_name} sul server!")
            return True

        try:
            await target.move_to(current_vch, reason=f"Portato qui da {user.display_name}")
            await notify_cb(f"🧲 **{user.display_name}** ha portato **{target.display_name}** nella stanza **{current_vch.name}**!")
            await player.speak_tts(f"Ho portato {target.display_name} qui da noi!")
        except discord.Forbidden:
            await _bot_missing_perm(f"spostare {target.display_name}")
        except Exception as e:
            await notify_cb(f"⚠️ **{target.display_name}** non è connesso a nessun canale vocale al momento! ({e})")
            await player.speak_tts(f"{target.display_name} non è collegato in vocale!")
        return True

    # ==========================================================================
    # 3. SPOSTA UN UTENTE IN UN'ALTRA STANZA ("sposta Ema in Generale", "manda Ema in AFK")
    # ==========================================================================
    move_one_match = re.match(
        r"^(?:sposta|manda|trasferisci|porta)\s+(.+?)\s+(?:in|nel\s+canale|nella\s+stanza|nella|nel|su|a|ad)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if move_one_match and not cmd_lower.startswith(("manda messaggio", "manda un messaggio")):
        target_name = move_one_match.group(1).strip(" ,.!?:;-")
        dest_query = move_one_match.group(2).strip(" ,.!?:;-")
        # Verifica che dest_query non sia una parola chiave di messaggi privati
        if target_name.lower() not in ("messaggio", "un messaggio", "il messaggio"):
            allowed, _ = await check_admin_permission(guild, user, "move_members")
            if not allowed:
                await _deny_perm("Sposta Membri")
                return True

            target = await find_member_in_guild(guild, player, target_name, exclude_uid=uid)
            if not target:
                await notify_cb(f"⚠️ Non ho trovato nessun membro chiamato **\"{target_name}\"**!")
                await player.speak_tts(f"Non trovo {target_name}!")
                return True

            dest_ch = find_voice_channel_in_guild(guild, dest_query, current_vch)
            if not dest_ch:
                ch_names = ", ".join(c.name for c in guild.voice_channels)
                await notify_cb(f"⚠️ Non ho trovato il canale vocale **\"{dest_query}\"**! *(Canali: {ch_names})*")
                await player.speak_tts(f"Non trovo la stanza {dest_query}!")
                return True

            try:
                await target.move_to(dest_ch, reason=f"Spostato da {user.display_name} via Franchino")
                await notify_cb(
                    f"🚪 **{user.display_name}** ha spostato **{target.display_name}** nel canale vocale **{dest_ch.name}**!"
                )
                await player.speak_tts(f"Ho spostato {target.display_name} in {dest_ch.name}!")
            except discord.Forbidden:
                await _bot_missing_perm(f"spostare {target.display_name}")
            except Exception:
                await notify_cb(f"⚠️ Non posso spostare **{target.display_name}** (forse non è in vocale).")
                await player.speak_tts(f"{target.display_name} non è collegato in vocale!")
            return True

    # ==========================================================================
    # 4. SCOLLEGA / CACCIA DALLA STANZA VOCALE ("caccia Ema dalla stanza", "scollega Ema", "disconnetti Ema")
    # ==========================================================================
    kick_vc_match = re.match(
        r"^(?:caccia|butta\s+fuori|scollega|disconnetti|rimuovi)\s+(.+?)(?:\s+dalla\s+stanza|\s+dalla\s+vocale|\s+dal\s+canale|\s+dalla\s+chiamata)?$",
        cmd,
        flags=re.IGNORECASE,
    )
    if kick_vc_match and "dal server" not in cmd_lower:
        target_name = kick_vc_match.group(1).strip(" ,.!?:;-")
        if target_name.lower() not in ("te", "te stesso", "franchino", "tutti", "la musica"):
            allowed, _ = await check_admin_permission(guild, user, "move_members")
            if not allowed:
                await _deny_perm("Sposta/Scollega Membri")
                return True

            target = await find_member_in_guild(guild, player, target_name, exclude_uid=uid)
            if not target:
                await notify_cb(f"⚠️ Non trovo nessuno chiamato **\"{target_name}\"**!")
                await player.speak_tts(f"Non trovo {target_name}!")
                return True

            try:
                await target.move_to(None, reason=f"Disconnesso dalla vocale da {user.display_name}")
                await notify_cb(f"👢 **{user.display_name}** ha disconnesso **{target.display_name}** dal canale vocale!")
                await player.speak_tts(f"Ho buttato fuori {target.display_name} dalla stanza!")
            except discord.Forbidden:
                await _bot_missing_perm(f"disconnettere {target.display_name}")
            except Exception as e:
                await notify_cb(f"⚠️ Errore nel disconnettere **{target.display_name}**: `{e}`")
            return True

    # ==========================================================================
    # 5. MUTA / SMUTA TUTTI O UN SINGOLO UTENTE ("muta Ema", "smuta Ema", "muta tutti", "smuta tutti")
    # ==========================================================================
    if cmd_lower in ("muta tutti", "silenzia tutti", "zittisci tutti", "muta la stanza") and current_vch:
        allowed, _ = await check_admin_permission(guild, user, "mute_members")
        if not allowed:
            await _deny_perm("Silenzia Membri")
            return True

        count = 0
        for m_id in list(current_vch.voice_states.keys()):
            if m_id in (guild.me.id, uid):
                continue
            m_obj = await ensure_guild_member(guild, discord.Object(id=m_id))
            if m_obj:
                try:
                    await m_obj.edit(mute=True, reason=f"Muta tutti di {user.display_name}")
                    count += 1
                except Exception:
                    pass
        await notify_cb(f"🔇 **{user.display_name}** ha mutato **{count} persone** nella stanza!")
        await player.speak_tts("Tutti mutati! Silenzio in sala!")
        return True

    if cmd_lower in ("smuta tutti", "togli il muto a tutti", "ridai la voce a tutti", "riattiva tutti") and current_vch:
        allowed, _ = await check_admin_permission(guild, user, "mute_members")
        if not allowed:
            await _deny_perm("Silenzia Membri")
            return True

        count = 0
        for m_id in list(current_vch.voice_states.keys()):
            if m_id == guild.me.id:
                continue
            m_obj = await ensure_guild_member(guild, discord.Object(id=m_id))
            if m_obj:
                try:
                    await m_obj.edit(mute=False, reason=f"Smuta tutti di {user.display_name}")
                    count += 1
                except Exception:
                    pass
        await notify_cb(f"🎙️ **{user.display_name}** ha tolto il muto a tutta la stanza (**{count} persone**)!")
        await player.speak_tts("Ho ridato la voce a tutti!")
        return True

    unmute_match = re.match(
        r"^(?:smuta|togli\s+il\s+muto\s+a|ridai\s+la\s+voce\s+a|riattiva\s+il\s+microfono\s+a|attiva\s+la\s+voce\s+a)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if unmute_match:
        allowed, _ = await check_admin_permission(guild, user, "mute_members")
        if not allowed:
            await _deny_perm("Silenzia Membri")
            return True

        target_name = unmute_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_name, exclude_uid=0)
        if not target:
            await notify_cb(f"⚠️ Non trovo nessuno chiamato **\"{target_name}\"**!")
            await player.speak_tts(f"Non trovo {target_name}!")
            return True

        try:
            await target.edit(mute=False, reason=f"Smutato da {user.display_name}")
            await notify_cb(f"🎙️ **{user.display_name}** ha tolto il muto a **{target.display_name}**!")
            await player.speak_tts(f"Ho tolto il muto a {target.display_name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"smutare {target.display_name}")
        except Exception as e:
            await notify_cb(f"⚠️ Impossibile smutare **{target.display_name}**: `{e}`")
        return True

    mute_match = re.match(
        r"^(?:muta|silenzia|zittisci|togli\s+la\s+voce\s+a|tappa\s+la\s+bocca\s+a|metti\s+muto\s+a)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if mute_match:
        target_name = mute_match.group(1).strip(" ,.!?:;-")
        if target_name.lower() not in ("la musica", "canzone", "franchino", "te", "tutto"):
            allowed, _ = await check_admin_permission(guild, user, "mute_members")
            if not allowed:
                await _deny_perm("Silenzia Membri")
                return True

            target = await find_member_in_guild(guild, player, target_name, exclude_uid=uid)
            if not target:
                await notify_cb(f"⚠️ Non trovo nessuno chiamato **\"{target_name}\"**!")
                await player.speak_tts(f"Non trovo {target_name}!")
                return True

            try:
                await target.edit(mute=True, reason=f"Mutato da {user.display_name}")
                await notify_cb(f"🔇 **{user.display_name}** ha mutato il microfono di **{target.display_name}**!")
                await player.speak_tts(f"Ho mutato {target.display_name}!")
            except discord.Forbidden:
                await _bot_missing_perm(f"mutare {target.display_name}")
            except Exception as e:
                await notify_cb(f"⚠️ Impossibile mutare **{target.display_name}**: `{e}`")
            return True

    # ==========================================================================
    # 6. DEAFEN / UNDEAFEN CUFFIE ("togli l'audio a Ema", "ridai l'audio a Ema")
    # ==========================================================================
    undeafen_match = re.match(
        r"^(?:ridai\s+l'audio\s+a|riattiva\s+l'audio\s+a|attiva\s+le\s+cuffie\s+a|togli\s+il\s+sordo\s+a)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if undeafen_match:
        allowed, _ = await check_admin_permission(guild, user, "deafen_members")
        if not allowed:
            await _deny_perm("Silenzia Audio Membri")
            return True
        target_name = undeafen_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_name, exclude_uid=0)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_name}**!")
            return True
        try:
            await target.edit(deafen=False, reason=f"Undeafen da {user.display_name}")
            await notify_cb(f"🎧 **{user.display_name}** ha riattivato l'audio in cuffia a **{target.display_name}**!")
            await player.speak_tts(f"Ho riattivato le cuffie a {target.display_name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"riattivare l'audio a {target.display_name}")
        return True

    deafen_match = re.match(
        r"^(?:togli\s+l'audio\s+a|disattiva\s+l'audio\s+a|disattiva\s+le\s+cuffie\s+a|rendi\s+sordo)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if deafen_match:
        allowed, _ = await check_admin_permission(guild, user, "deafen_members")
        if not allowed:
            await _deny_perm("Silenzia Audio Membri")
            return True
        target_name = deafen_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_name, exclude_uid=uid)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_name}**!")
            return True
        try:
            await target.edit(deafen=True, reason=f"Deafen da {user.display_name}")
            await notify_cb(f"🙉 **{user.display_name}** ha disattivato l'audio in cuffia a **{target.display_name}**!")
            await player.speak_tts(f"Ho disattivato l'audio a {target.display_name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"disattivare l'audio a {target.display_name}")
        return True

    # ==========================================================================
    # 7. GESTIONE RUOLI ("dai il ruolo VIP a Ema", "togli il ruolo DJ a Ema", "crea il ruolo...")
    # ==========================================================================
    create_role_match = re.match(
        r"^(?:crea\s+il\s+ruolo|crea\s+ruolo|nuovo\s+ruolo)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if create_role_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_roles")
        if not allowed:
            await _deny_perm("Gestisci Ruoli")
            return True
        role_name = create_role_match.group(1).strip(" ,.!?:;-")
        try:
            new_role = await guild.create_role(
                name=role_name,
                color=discord.Color.purple(),
                reason=f"Creato da {user.display_name} via Franchino",
            )
            await notify_cb(f"🎭 **{user.display_name}** ha creato il nuovo ruolo {new_role.mention}!")
            await player.speak_tts(f"Ho creato il ruolo {role_name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"creare il ruolo {role_name}")
        return True

    # Rimozione ruolo: "togli il ruolo <ruolo> a <utente>" oppure "togli a <utente> il ruolo <ruolo>"
    rem_role_match = (
        re.match(
            r"^(?:togli|rimuovi|leva)\s+(?:il\s+)?ruolo\s+(.+?)\s+(?:a|ad|da)\s+(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
        or re.match(
            r"^(?:togli|rimuovi|leva)\s+(?:a|ad)\s+(.+?)\s+(?:il\s+)?ruolo\s+(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
    )
    if rem_role_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_roles")
        if not allowed:
            await _deny_perm("Gestisci Ruoli")
            return True

        # Determina quale gruppo è il ruolo e quale è l'utente in base alla frase usata
        if re.match(r"^(?:togli|rimuovi|leva)\s+(?:a|ad)\s+", cmd, flags=re.IGNORECASE):
            target_q, role_q = rem_role_match.group(1), rem_role_match.group(2)
        else:
            role_q, target_q = rem_role_match.group(1), rem_role_match.group(2)

        target = await find_member_in_guild(guild, player, target_q, exclude_uid=0)
        role = find_role_in_guild(guild, role_q)
        if not target:
            await notify_cb(f"⚠️ Non ho trovato l'utente **\"{target_q}\"**!")
            await player.speak_tts(f"Non trovo l'utente {target_q}!")
            return True
        if not role:
            roles_list = ", ".join(r.name for r in guild.roles if not r.is_default() and not r.managed)[:200]
            await notify_cb(f"⚠️ Non ho trovato il ruolo **\"{role_q}\"**! *(Ruoli presenti: {roles_list})*")
            await player.speak_tts(f"Non trovo il ruolo {role_q}!")
            return True

        try:
            await target.remove_roles(role, reason=f"Rimosso da {user.display_name} via Franchino")
            await notify_cb(
                f"🎭 **{user.display_name}** ha rimosso il ruolo {role.mention} a **{target.display_name}**!"
            )
            await player.speak_tts(f"Ho tolto il ruolo {role.name} a {target.display_name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"togliere il ruolo {role.name}")
        return True

    # Aggiunta / Cambio ruolo:
    # "metti il ruolo <ruolo> a <utente>", "dai il ruolo <ruolo> a <utente>", "cambia ruolo a <utente> in <ruolo>", "promuovi <utente> a <ruolo>"
    add_role_match = (
        re.match(
            r"^(?:metti|dai|aggiungi|assegna|imposta)\s+(?:il\s+)?ruolo\s+(.+?)\s+(?:a|ad)\s+(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
        or re.match(
            r"^(?:cambia|metti|dai|assegna|imposta)\s+(?:il\s+)?ruolo\s+(?:di|a|ad)\s+(.+?)\s+(?:in|su|come|a)\s+(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
        or re.match(
            r"^(?:promuovi|nomina|fai\s+diventare)\s+(.+?)\s+(?:a|come|in)?\s*(?:ruolo\s+)?(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
    )
    if add_role_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_roles")
        if not allowed:
            await _deny_perm("Gestisci Ruoli")
            return True

        if re.match(r"^(?:metti|dai|aggiungi|assegna|imposta)\s+(?:il\s+)?ruolo\s+(?!di\s|a\s|ad\s)", cmd, flags=re.IGNORECASE):
            role_q, target_q = add_role_match.group(1), add_role_match.group(2)
        else:
            target_q, role_q = add_role_match.group(1), add_role_match.group(2)

        target = await find_member_in_guild(guild, player, target_q, exclude_uid=0)
        role = find_role_in_guild(guild, role_q)
        if not target:
            await notify_cb(f"⚠️ Non ho trovato l'utente **\"{target_q}\"**!")
            await player.speak_tts(f"Non trovo l'utente {target_q}!")
            return True
        if not role:
            roles_list = ", ".join(r.name for r in guild.roles if not r.is_default() and not r.managed)[:200]
            await notify_cb(f"⚠️ Non ho trovato il ruolo **\"{role_q}\"**! *(Ruoli presenti: {roles_list})*")
            await player.speak_tts(f"Non trovo il ruolo {role_q}!")
            return True

        try:
            await target.add_roles(role, reason=f"Assegnato da {user.display_name} via Franchino")
            await notify_cb(
                f"🎭 **{user.display_name}** ha assegnato il ruolo {role.mention} a **{target.display_name}**!"
            )
            await player.speak_tts(f"Ho assegnato il ruolo {role.name} a {target.display_name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"assegnare il ruolo {role.name} a {target.display_name}")
        return True

    # ==========================================================================
    # 8. CAMBIA / RESETTA SOPRANNOME ("cambia soprannome a Ema in...", "rinomina Ema in...")
    # ==========================================================================
    reset_nick_match = re.match(
        r"^(?:resetta|togli|ripristina)\s+(?:il\s+)?(?:soprannome|nickname|nome)\s+(?:a|ad|di)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if reset_nick_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_nicknames")
        if not allowed:
            await _deny_perm("Gestisci Soprannomi")
            return True
        target_q = reset_nick_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_q, exclude_uid=0)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_q}**!")
            return True
        try:
            await target.edit(nick=None, reason=f"Reset nick da {user.display_name}")
            await notify_cb(f"🏷️ **{user.display_name}** ha ripristinato il nome originale di **{target.name}**!")
            await player.speak_tts(f"Ho ripristinato il nome di {target.name}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"cambiare il soprannome di {target.display_name}")
        return True

    nick_match = (
        re.match(
            r"^(?:cambia|modifica|imposta|metti)\s+(?:il\s+)?(?:soprannome|nickname|nome)\s+(?:a|ad|di)\s+(.+?)\s+(?:in|come|con)\s+(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
        or re.match(
            r"^(?:rinomina)\s+(.+?)\s+(?:in|come)\s+(.+)$",
            cmd,
            flags=re.IGNORECASE,
        )
    )
    if nick_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_nicknames")
        if not allowed:
            await _deny_perm("Gestisci Soprannomi")
            return True
        target_q = nick_match.group(1).strip(" ,.!?:;-")
        new_nick = nick_match.group(2).strip(" ,.!?:;-")[:32]
        target = await find_member_in_guild(guild, player, target_q, exclude_uid=0)
        if not target:
            await notify_cb(f"⚠️ Non trovo l'utente **\"{target_q}\"**!")
            await player.speak_tts(f"Non trovo {target_q}!")
            return True
        try:
            old_name = target.display_name
            await target.edit(nick=new_nick, reason=f"Rinominato da {user.display_name} via Franchino")
            await notify_cb(f"🏷️ **{user.display_name}** ha cambiato il soprannome di **{old_name}** in **{new_nick}**!")
            await player.speak_tts(f"Da adesso {old_name} si chiama {new_nick}!")
        except discord.Forbidden:
            await _bot_missing_perm(f"cambiare il soprannome di {target.display_name}")
        return True

    # ==========================================================================
    # 9. TIMEOUT / CASTIGO ("metti in timeout Ema per 5 minuti", "togli timeout a Ema")
    # ==========================================================================
    untimeout_match = re.match(
        r"^(?:togli\s+il\s+timeout\s+a|togli\s+timeout\s+a|togli\s+il\s+castigo\s+a|libera)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if untimeout_match:
        allowed, _ = await check_admin_permission(guild, user, "moderate_members")
        if not allowed:
            await _deny_perm("Modera Membri (Timeout)")
            return True
        target_q = untimeout_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_q, exclude_uid=0)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_q}**!")
            return True
        try:
            await target.timeout(None, reason=f"Timeout rimosso da {user.display_name}")
            await notify_cb(f"✅ **{user.display_name}** ha rimosso il timeout a **{target.display_name}**!")
            await player.speak_tts(f"Ho liberato {target.display_name} dal timeout!")
        except discord.Forbidden:
            await _bot_missing_perm(f"rimuovere il timeout a {target.display_name}")
        return True

    timeout_match = re.match(
        r"^(?:metti\s+in\s+timeout|timeout\s+a|isola|metti\s+in\s+castigo)\s+(.+?)(?:\s+per\s+(\d+)\s*(?:minuti|minuto|min|secondi|secondo|ore|ora))?$",
        cmd,
        flags=re.IGNORECASE,
    )
    if timeout_match:
        allowed, _ = await check_admin_permission(guild, user, "moderate_members")
        if not allowed:
            await _deny_perm("Modera Membri (Timeout)")
            return True
        target_q = timeout_match.group(1).strip(" ,.!?:;-")
        mins = int(timeout_match.group(2)) if timeout_match.group(2) else 2
        mins = max(1, min(1440, mins))
        target = await find_member_in_guild(guild, player, target_q, exclude_uid=uid)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_q}**!")
            return True
        try:
            await target.timeout(datetime.timedelta(minutes=mins), reason=f"Timeout vocale di {user.display_name}")
            await notify_cb(f"⏳ **{user.display_name}** ha messo in timeout **{target.display_name}** per **{mins} minuti**!")
            await player.speak_tts(f"Ho messo {target.display_name} in timeout per {mins} minuti!")
        except discord.Forbidden:
            await _bot_missing_perm(f"mettere in timeout {target.display_name}")
        return True

    # ==========================================================================
    # 10. PULIZIA MESSAGGI CHAT ("pulisci la chat", "cancella 20 messaggi")
    # ==========================================================================
    purge_match = re.match(
        r"^(?:pulisci\s+la\s+chat|svuota\s+la\s+chat|cancella\s+(?:gli\s+ultimi\s+)?(\d+)\s+messaggi|elimina\s+(?:gli\s+ultimi\s+)?(\d+)\s+messaggi)$",
        cmd_lower,
    )
    if purge_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_messages")
        if not allowed:
            await _deny_perm("Gestisci Messaggi")
            return True
        num_str = purge_match.group(1) or purge_match.group(2)
        limit = int(num_str) if num_str else 15
        limit = max(1, min(100, limit))
        ch = player.text_channel
        if ch and hasattr(ch, "purge"):
            try:
                deleted = await ch.purge(limit=limit)
                await notify_cb(f"🧹 **{user.display_name}** ha ripulito **{len(deleted)} messaggi** nella chat!")
                await player.speak_tts(f"Ho cancellato {len(deleted)} messaggi dalla chat!")
            except discord.Forbidden:
                await _bot_missing_perm("cancellare i messaggi")
        return True

    # ==========================================================================
    # 11. CREAZIONE STANZE & LIMITE UTENTI ("crea stanza vocale...", "crea canale testuale...", "chiudi la stanza")
    # ==========================================================================
    create_vch_match = re.match(
        r"^(?:crea\s+stanza\s+vocale|crea\s+canale\s+vocale|crea\s+vocale)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if create_vch_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_channels")
        if not allowed:
            await _deny_perm("Gestisci Canali")
            return True
        ch_name = create_vch_match.group(1).strip(" ,.!?:;-")
        category = current_vch.category if current_vch else None
        try:
            new_ch = await guild.create_voice_channel(name=ch_name, category=category, reason=f"Creato da {user.display_name}")
            await notify_cb(f"🔊 **{user.display_name}** ha creato il nuovo canale vocale **{new_ch.name}**!")
            await player.speak_tts(f"Ho creato la stanza vocale {ch_name}!")
        except discord.Forbidden:
            await _bot_missing_perm("creare canali vocali")
        return True

    create_tch_match = re.match(
        r"^(?:crea\s+canale\s+testuale|crea\s+chat|crea\s+canale)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if create_tch_match:
        allowed, _ = await check_admin_permission(guild, user, "manage_channels")
        if not allowed:
            await _deny_perm("Gestisci Canali")
            return True
        ch_name = create_tch_match.group(1).strip(" ,.!?:;-")
        category = current_vch.category if current_vch else None
        try:
            new_ch = await guild.create_text_channel(name=ch_name, category=category, reason=f"Creato da {user.display_name}")
            await notify_cb(f"💬 **{user.display_name}** ha creato il nuovo canale testuale {new_ch.mention}!")
            await player.speak_tts(f"Ho creato il canale testuale {ch_name}!")
        except discord.Forbidden:
            await _bot_missing_perm("creare canali testuali")
        return True

    limit_vch_match = re.match(
        r"^(?:limita\s+la\s+stanza\s+a|imposta\s+limite\s+stanza\s+a)\s+(\d+)(?:\s+persone|\s+utenti)?$",
        cmd_lower,
    )
    if (limit_vch_match or cmd_lower in ("chiudi la stanza", "blocca la stanza", "apri la stanza", "sblocca la stanza")) and current_vch:
        allowed, _ = await check_admin_permission(guild, user, "manage_channels")
        if not allowed:
            await _deny_perm("Gestisci Canali")
            return True
        if limit_vch_match:
            new_limit = max(0, min(99, int(limit_vch_match.group(1))))
        elif "chiudi" in cmd_lower or "blocca" in cmd_lower:
            new_limit = max(1, len(current_vch.voice_states))
        else:
            new_limit = 0
        try:
            await current_vch.edit(user_limit=new_limit)
            if new_limit > 0:
                await notify_cb(f"🔒 **{user.display_name}** ha impostato il limite di **{current_vch.name}** a **{new_limit} utenti**!")
                await player.speak_tts(f"Stanza limitata a {new_limit} persone!")
            else:
                await notify_cb(f"🔓 **{user.display_name}** ha riaperto la stanza **{current_vch.name}** senza limiti!")
                await player.speak_tts("Ho riaperto la stanza a tutti!")
        except discord.Forbidden:
            await _bot_missing_perm("modificare la stanza")
        return True

    # ==========================================================================
    # 12. KICK / BAN DAL SERVER ("espelli <nome> dal server", "banna <nome> dal server", "sbanna <nome>")
    # ==========================================================================
    kick_server_match = re.match(
        r"^(?:espelli|kicka|caccia)\s+(.+?)\s+dal\s+server$",
        cmd,
        flags=re.IGNORECASE,
    )
    if kick_server_match:
        allowed, _ = await check_admin_permission(guild, user, "kick_members")
        if not allowed:
            await _deny_perm("Espelli Membri")
            return True
        target_q = kick_server_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_q, exclude_uid=uid)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_q}**!")
            return True
        try:
            await target.kick(reason=f"Espulso da {user.display_name} via Franchino")
            await notify_cb(f"👢 **{user.display_name}** ha espulso **{target.display_name}** dal server!")
            await player.speak_tts(f"{target.display_name} è stato espulso dal server!")
        except discord.Forbidden:
            await _bot_missing_perm(f"espellere {target.display_name}")
        return True

    ban_server_match = re.match(
        r"^(?:banna|bandisci)\s+(.+?)(?:\s+dal\s+server)?$",
        cmd,
        flags=re.IGNORECASE,
    )
    if ban_server_match:
        allowed, _ = await check_admin_permission(guild, user, "ban_members")
        if not allowed:
            await _deny_perm("Banna Membri")
            return True
        target_q = ban_server_match.group(1).strip(" ,.!?:;-")
        target = await find_member_in_guild(guild, player, target_q, exclude_uid=uid)
        if not target:
            await notify_cb(f"⚠️ Non trovo **{target_q}**!")
            return True
        try:
            await target.ban(reason=f"Bannato da {user.display_name} via Franchino")
            await notify_cb(f"🔨 **{user.display_name}** ha bannato **{target.display_name}** dal server!")
            await player.speak_tts(f"{target.display_name} è stato bannato dal server!")
        except discord.Forbidden:
            await _bot_missing_perm(f"bannare {target.display_name}")
        return True

    unban_match = re.match(
        r"^(?:sbanna|togli\s+il\s+ban\s+a|perdona)\s+(.+)$",
        cmd,
        flags=re.IGNORECASE,
    )
    if unban_match:
        allowed, _ = await check_admin_permission(guild, user, "ban_members")
        if not allowed:
            await _deny_perm("Banna Membri")
            return True
        target_q = unban_match.group(1).strip(" ,.!?:;-").lower()
        try:
            unbanned_user = None
            async for ban_entry in guild.bans(limit=100):
                u = ban_entry.user
                if target_q in u.name.lower() or (u.global_name and target_q in u.global_name.lower()):
                    await guild.unban(u, reason=f"Sbannato da {user.display_name} via Franchino")
                    unbanned_user = u
                    break
            if unbanned_user:
                await notify_cb(f"🕊️ **{user.display_name}** ha rimosso il ban a **{unbanned_user.name}**!")
                await player.speak_tts(f"Ho tolto il ban a {unbanned_user.name}!")
            else:
                await notify_cb(f"⚠️ Nessun utente bannato trovato con il nome **\"{target_q}\"**.")
        except discord.Forbidden:
            await _bot_missing_perm("gestire i ban")
        return True

    return False
