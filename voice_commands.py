import asyncio
import audioop
import collections
import difflib
import logging
import random
import re
import time
from typing import Dict, Optional, TYPE_CHECKING

import discord
from discord.ext import voice_recv
from discord.ext.voice_recv.router import PacketDecoder, PacketRouter
import speech_recognition as sr

try:
    import davey
    HAS_DAVEY = True
except ImportError:
    HAS_DAVEY = False

if TYPE_CHECKING:
    from player import GuildPlayer

logger = logging.getLogger("Franchino.VoiceCommands")


class _FallbackSpeaker:
    """Rappresenta un utente vocale quando il profilo non è ancora nella cache locale di Discord."""

    def __init__(self, user_id: int, name: str = "Amico in vocale"):
        self.id = user_id
        self.display_name = name
        self.name = name
        self.bot = False
        self.display_avatar = None


# ==============================================================================
# 1. Patch DAVE E2EE Multi-Utente + Protezione anti-crash per tutti i membri
# ==============================================================================
_orig_push_packet = PacketDecoder.push_packet


def _get_all_channel_user_ids(vc) -> list[int]:
    """
    Restituisce gli ID di TUTTI gli utenti presenti nel canale vocale usando `voice_states`
    (che contiene sempre tutti i partecipanti anche senza Server Members Intent!).
    """
    candidates: list[int] = []
    bot_id = vc.client.user.id if vc and vc.client and vc.client.user else None

    if vc and vc.channel:
        # 1. voice_states contiene SEMPRE tutti gli utenti nel canale vocale
        try:
            for uid in vc.channel.voice_states.keys():
                if uid != bot_id and uid not in candidates:
                    candidates.append(int(uid))
        except Exception:
            pass

        # 2. members (fallback aggiuntivo)
        try:
            for m in vc.channel.members:
                if not m.bot and m.id not in candidates:
                    candidates.append(int(m.id))
        except Exception:
            pass

    # 3. Tutti gli stati vocali della guild
    if vc and vc.guild and hasattr(vc.guild, "_voice_states"):
        try:
            for uid in vc.guild._voice_states.keys():
                if uid != bot_id and uid not in candidates:
                    candidates.append(int(uid))
        except Exception:
            pass

    return candidates


def _decrypt_dave_for_packet(decoder: PacketDecoder, packet) -> None:
    """Decifra il payload DAVE E2EE per QUALSIASI utente del canale vocale."""
    if not HAS_DAVEY or not packet or not getattr(packet, "decrypted_data", None):
        return
    if packet.decrypted_data == b"\xf8\xff\xfe":
        return

    try:
        vc = decoder.sink.voice_client
        if not vc:
            return
        conn = getattr(vc, "_connection", None)
        dave_session = getattr(conn, "dave_session", None)
        if dave_session is None or not dave_session.ready:
            return

        raw_bytes = bytes(packet.decrypted_data)
        authoritative_uid = vc._get_id_from_ssrc(decoder.ssrc)
        if authoritative_uid:
            decoder._cached_id = int(authoritative_uid)
        user_id = decoder._cached_id

        if user_id:
            try:
                packet.decrypted_data = dave_session.decrypt(int(user_id), davey.MediaType.audio, raw_bytes)
                return
            except Exception:
                pass

        # Raccoglie TUTTI gli utenti presenti nel canale vocale (tramite voice_states + dave_session)
        all_candidates = _get_all_channel_user_ids(vc)
        try:
            bot_id = vc.client.user.id if vc.client and vc.client.user else None
            for uid in dave_session.get_user_ids():
                uid_int = int(uid)
                if uid_int != bot_id and uid_int not in all_candidates:
                    all_candidates.append(uid_int)
        except Exception:
            pass

        # Dà priorità agli utenti che non hanno ancora un SSRC diverso associato
        known_ssrc_users = set(getattr(vc, "_id_to_ssrc", {}).keys())
        unassigned = [u for u in all_candidates if u not in known_ssrc_users]
        assigned = [u for u in all_candidates if u in known_ssrc_users]
        candidates = unassigned + assigned

        for cand_id in candidates:
            try:
                decrypted = dave_session.decrypt(cand_id, davey.MediaType.audio, raw_bytes)
                packet.decrypted_data = decrypted
                decoder._cached_id = cand_id
                vc._add_ssrc(cand_id, decoder.ssrc)
                logger.info(f"🔗 SSRC {decoder.ssrc} associato automaticamente all'utente {cand_id}")
                return
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Errore in _decrypt_dave_for_packet: {e}")


def _safe_push_packet(self: PacketDecoder, packet) -> None:
    _decrypt_dave_for_packet(self, packet)
    _orig_push_packet(self, packet)


def _safe_decode_packet(self: PacketDecoder, packet):
    """Decodifica Opus senza mai mandare in crash il thread di ascolto."""
    if self._decoder is None:
        return packet, b"\x00" * 3840

    try:
        if packet:
            pcm = self._decoder.decode(packet.decrypted_data, fec=False)
            return packet, pcm

        next_packet = self._buffer.peek_next()
        if next_packet is not None and getattr(next_packet, "decrypted_data", None):
            pcm = self._decoder.decode(next_packet.decrypted_data, fec=True)
            return packet, pcm

        pcm = self._decoder.decode(None, fec=False)
        return packet, pcm
    except Exception as e:
        logger.debug(f"Pacchetto Opus ignorato (nessun crash): {e}")
        return packet, b"\x00" * 3840


def _resilient_router_do_run(self: PacketRouter) -> None:
    """Loop resiliente per PacketRouter: ascolta contemporaneamente tutti gli utenti del canale."""
    while not self._end_thread.is_set():
        try:
            self.waiter.wait()
            with self._lock:
                for decoder in list(self.waiter.items):
                    try:
                        data = decoder.pop_data()
                        if data is not None and data.pcm:
                            # Passiamo anche l'user_id identificato dal decoder se data.source è None
                            speaker = data.source
                            if speaker is None and decoder._cached_id:
                                speaker = _FallbackSpeaker(decoder._cached_id)
                            self.sink.write(speaker, data)
                    except Exception as dec_err:
                        logger.debug(f"Errore decodifica singolo pacchetto ignorato: {dec_err}")
        except Exception as loop_err:
            logger.debug(f"Errore nel loop PacketRouter ignorato: {loop_err}")
            time.sleep(0.05)


from discord.ext.voice_recv.reader import UDPKeepAlive
import discord.ext.voice_recv.gateway as vr_gateway
import discord.ext.voice_recv.voice_client as vr_vc
from discord.gateway import DiscordVoiceWebSocket
from discord.voice_state import VoiceConnectionState


async def _safe_voice_ws_hook(ws: DiscordVoiceWebSocket, msg: dict) -> None:
    """
    Corregge il bug critico di discord-ext-voice-recv con Discord Voice Gateway v8 (discord.py 2.7+):
    In v8, quando gli utenti sono connessi al canale vocale, Discord invia op=12 (CLIENT_CONNECT)
    con `{'user_id': ..., 'audio_ssrc': ...}` SENZA il campo `'streams'`.
    L'hook originale di voice_recv trattava op=12 come VIDEO e lanciava `KeyError: 'streams'`
    dopo aver registrato solo il 1° utente, facendo crashare `_poll_voice_ws` e ignorando tutti gli altri utenti!
    """
    try:
        op = msg.get("op")
        data = msg.get("d") or {}
        conn = getattr(ws, "_connection", None)
        vc = getattr(conn, "voice_client", None)
        if vc is None:
            return

        if op == 2:  # READY
            if "ssrc" in data and vc.guild and vc.guild.me:
                vc._add_ssrc(vc.guild.me.id, int(data["ssrc"]))

        elif op == 4:  # SESSION_DESCRIPTION
            if getattr(vc, "_reader", None) and getattr(ws, "secret_key", None):
                vc._reader.update_secret_key(bytes(ws.secret_key))

        elif op == 5:  # SPEAKING (inviato da Discord ogni volta che QUALSIASI utente parla nel canale!)
            if "user_id" in data and "ssrc" in data and data["ssrc"] is not None:
                uid = int(data["user_id"])
                ssrc = int(data["ssrc"])
                vc._add_ssrc(uid, ssrc)
                logger.debug(f"🎙️ Evento SPEAKING ricevuto: user_id={uid} -> ssrc={ssrc}")

        elif op == 11:  # CLIENTS_CONNECT
            pass

        elif op == 12:  # CLIENT_CONNECT / VIDEO
            if "user_id" in data:
                uid = int(data["user_id"])
                audio_ssrc = data.get("audio_ssrc") or data.get("ssrc")
                if audio_ssrc:
                    vc._add_ssrc(uid, int(audio_ssrc))
                    logger.info(f"👤 Utente connesso al flusso vocale: user_id={uid} -> ssrc={audio_ssrc}")

        elif op == 13:  # CLIENT_DISCONNECT
            if "user_id" in data:
                uid = int(data["user_id"])
                ssrc = vc._get_ssrc_from_id(uid)
                if getattr(vc, "_reader", None) and ssrc is not None:
                    vc._reader.packet_router.destroy_decoder(ssrc)
                vc._remove_ssrc(user_id=uid)
    except Exception as e:
        logger.debug(f"Ignorato errore in _safe_voice_ws_hook (op={msg.get('op')}): {e}")


_orig_received_binary = DiscordVoiceWebSocket.received_binary_message


async def _safe_received_binary_message(self: DiscordVoiceWebSocket, msg: bytes) -> None:
    """Impedisce che errori temporanei MLS/DAVE interrompano il task _poll_voice_ws."""
    try:
        await _orig_received_binary(self, msg)
    except Exception as e:
        logger.debug(f"Ignorato errore binario DAVE MLS sul voice websocket: {e}")


def _safe_create_connection_state(self: vr_vc.VoiceRecvClient) -> VoiceConnectionState:
    return VoiceConnectionState(self, hook=_safe_voice_ws_hook)


def _fixed_udp_keepalive_run(self: UDPKeepAlive) -> None:
    """
    Corregge un bug critico di discord-ext-voice-recv con discord.py 2.7+:
    In discord.py 2.7+, `endpoint_ip` è MISSING e il socket UDP è già connesso (`send_packet`).
    L'originale UDPKeepAlive andava in eccezione in un loop infinito senza sleep e non inviava
    alcun keepalive quando la musica andava in STOP, facendo chiudere il canale UDP di ascolto!
    """
    self.voice_client.wait_until_connected()

    while not self._end_thread.is_set():
        vc = self.voice_client
        try:
            if vc and vc.is_connected() and hasattr(vc, "_connection"):
                packet = self.counter.to_bytes(8, "big")
                vc._connection.send_packet(packet)
                self.counter = (self.counter + 1) % 0xFFFFFFFFFFFF
                # Se il bot è in STOP (non sta suonando musica), invia anche un frame Opus muto
                # per mantenere attivo il flusso RTP bidirezionale su Discord e sul NAT Docker/Oracle
                if not vc.is_playing() and not vc.is_paused():
                    try:
                        vc.send_audio_packet(b"\xf8\xff\xfe", encode=False)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Keepalive UDP warning: {e}")

        self._end_thread.wait(2.0)


PacketDecoder.push_packet = _safe_push_packet
PacketDecoder._decode_packet = _safe_decode_packet
PacketRouter._do_run = _resilient_router_do_run
UDPKeepAlive.run = _fixed_udp_keepalive_run
vr_gateway.hook = _safe_voice_ws_hook
vr_vc.hook = _safe_voice_ws_hook
vr_vc.VoiceRecvClient.create_connection_state = _safe_create_connection_state
DiscordVoiceWebSocket.received_binary_message = _safe_received_binary_message


# ==============================================================================
# 2. Sink Multi-Utente per ascolto di TUTTO il canale vocale
# ==============================================================================
class FranchinoVoiceSink(voice_recv.AudioSink):
    """
    Ascolta contemporaneamente TUTTI gli utenti presenti nel canale vocale.
    Ogni partecipante ha il proprio buffer indipendente e può dire 'Franchino...'.
    """

    SAMPLE_RATE = 48000
    SAMPLE_WIDTH = 2  # 16-bit
    CHANNELS = 2      # Stereo
    BYTES_PER_SECOND = SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS  # 192.000 byte/s

    SILENCE_THRESHOLD_SEC = 0.65  # Rileva la fine della frase in soli 650ms per risposte ultra-veloci!
    MIN_PHRASE_SEC = 0.35
    MAX_PHRASE_SEC = 10.0         # Fino a 10 secondi per titoli lunghi + nome artista
    START_RMS_ENERGY = 120        # Più reattivo grazie al pre-roll buffer da 300ms
    CONTINUE_RMS_ENERGY = 50      # Sensibile per catturare anche l'ultima sillaba debole o sospiro
    PREROLL_CHUNKS = 15           # 300ms di pre-roll ring buffer (15 pacchetti da 20ms)

    WAKE_WORDS = (
        "franchino", "frankino", "francino", "franchi", "franchì",
        "franchini", "franklin", "frankie", "franci", "franchin",
        "franchi no", "tranchino", "franceschino", "franco", "francesco",
        "frank", "fra", "panchino", "fanchino", "banchino", "chino", "franchina"
    )

    def __init__(self, player: "GuildPlayer"):
        super().__init__(None)
        self.player = player
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 280
        self.recognizer.dynamic_energy_threshold = False
        self._buffers: Dict[int, bytearray] = {}
        self._preroll_buffers: Dict[int, collections.deque] = {}
        self._last_packet_time: Dict[int, float] = {}
        self._users: Dict[int, object] = {}
        self._user_cache: Dict[int, discord.abc.User] = {}
        # Memoria per chi dice "Franchino" o "Franchino metti" e fa una pausa prima di dire il titolo:
        # uid -> (timestamp, prefix_command)
        self._pending_wake: Dict[int, tuple[float, str]] = {}
        # Memoria per chi ha detto "Franchino manda messaggio a <nome>" e deve dettare il testo del messaggio:
        # uid -> (timestamp, target_member)
        self._pending_dm: Dict[int, tuple[float, object]] = {}
        self._dm_timeout_tasks: Dict[int, asyncio.Task] = {}
        self._running = True

        self._monitor_task = self.player.bot.loop.create_task(self._monitor_speech_loop())

    def wants_opus(self) -> bool:
        return False

    def write(self, user: Optional[discord.abc.User], data: voice_recv.VoiceData) -> None:
        if not self._running:
            return

        if user is not None and getattr(user, "bot", False):
            return

        pcm = data.pcm
        if not pcm:
            return

        try:
            rms = audioop.rms(pcm, self.SAMPLE_WIDTH)
        except Exception:
            return

        now = time.monotonic()
        ssrc = getattr(data.packet, "ssrc", 0)
        vc = self.player.voice_client

        # Determina l'ID univoco di chi sta parlando (funziona per qualsiasi utente nel canale!)
        uid = None
        if user is not None and getattr(user, "id", None):
            uid = int(user.id)
        elif vc and ssrc:
            uid = vc._get_id_from_ssrc(ssrc)

        key = uid if uid else ssrc

        # Rolling pre-roll buffer di ~300ms (15 pacchetti da 20ms) per catturare l'attacco della prima consonante ("F", "P", "S")
        if key not in self._preroll_buffers:
            self._preroll_buffers[key] = collections.deque(maxlen=self.PREROLL_CHUNKS)
        self._preroll_buffers[key].append(pcm)

        # Se la frase NON è ancora iniziata, ignora il rumore di fondo sotto START_RMS_ENERGY
        if key not in self._buffers:
            if rms < self.START_RMS_ENERGY:
                return
            self._buffers[key] = bytearray()
            # Recupera tutti i frame del pre-roll buffer per non tagliare l'attacco della prima parola!
            for pre_chunk in self._preroll_buffers[key]:
                self._buffers[key].extend(pre_chunk)

        # Una volta che l'utente ha iniziato a parlare, conserva TUTTI i frame (anche consonanti
        # deboli e pause naturali tra le parole) così Google Speech non taglia mai metà titolo!
        self._buffers[key].extend(pcm)

        # Aggiorna il timer di fine parlato anche se l'utente pronuncia a bassa voce la fine del titolo
        if rms >= self.CONTINUE_RMS_ENERGY:
            self._last_packet_time[key] = now
        elif key not in self._last_packet_time:
            self._last_packet_time[key] = now

        if user is not None and not isinstance(user, _FallbackSpeaker):
            self._users[key] = user
        elif uid and key not in self._users:
            self._users[key] = _FallbackSpeaker(uid)

    async def _resolve_user_object(self, key: int, speaker_obj: object):
        """Ottiene il vero oggetto utente/membro Discord (con nickname del server e supporto DM .send())."""
        if speaker_obj is not None and not isinstance(speaker_obj, _FallbackSpeaker):
            return speaker_obj

        uid = getattr(speaker_obj, "id", None) or key
        if not uid:
            return _FallbackSpeaker(0, "Utente")

        if uid in self._user_cache:
            return self._user_cache[uid]

        member = self.player.guild.get_member(uid)
        if member is not None:
            self._user_cache[uid] = member
            return member

        # Recupera il Member completo dal server via HTTP (include il nickname del server e permette .send()!)
        try:
            fetched_member = await self.player.guild.fetch_member(uid)
            if fetched_member:
                self._user_cache[uid] = fetched_member
                return fetched_member
        except Exception:
            pass

        user_cached = self.player.bot.get_user(uid)
        if user_cached is not None:
            self._user_cache[uid] = user_cached
            return user_cached

        try:
            fetched = await self.player.bot.fetch_user(uid)
            if fetched:
                self._user_cache[uid] = fetched
                return fetched
        except Exception:
            pass

        return _FallbackSpeaker(uid, "Amico in vocale")

    async def _find_member_in_voice_channel(self, name_query: str, exclude_uid: int = 0):
        """
        Cerca tra le persone presenti nella stanza vocale quella che corrisponde al nome pronunciato
        (es. "Ema" -> trova "Ema", "Emanuele", "ema_99", ecc.).
        Restituisce (best_member, lista_nomi_presenti).
        """
        vc = self.player.voice_client or self.player.guild.voice_client
        if not vc or not vc.channel:
            return None, []

        clean_query = re.sub(r"^(?:a|ad|al|allo|alla|ai|agli|alle|il|lo|la)\s+", "", name_query.lower().strip())
        clean_query = re.sub(r"[^\w\s]", "", clean_query).strip()
        if not clean_query:
            return None, []

        member_ids = _get_all_channel_user_ids(vc)
        members = []
        present_names = []
        for m_uid in member_ids:
            m_obj = await self._resolve_user_object(m_uid, None)
            if m_obj and not getattr(m_obj, "bot", False):
                members.append(m_obj)
                present_names.append(getattr(m_obj, "display_name", str(m_uid)))

        best_member = None
        best_score = 0.0

        for m in members:
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

            # Leggera preferenza per gli altri membri rispetto a chi sta parlando a parità di nome
            if getattr(m, "id", 0) != exclude_uid and member_score >= 0.60:
                member_score += 0.01

            if member_score > best_score:
                best_score = member_score
                best_member = m

        if best_score >= 0.58:
            return best_member, present_names
        return None, present_names

    def _cancel_dm_timeout(self, uid: int):
        task = self._dm_timeout_tasks.pop(uid, None)
        if task and not task.done():
            task.cancel()

    def _start_dm_timeout(self, uid: int, timeout_sec: float = 30.0):
        self._cancel_dm_timeout(uid)

        async def _timeout_coro():
            try:
                await asyncio.sleep(timeout_sec)
                if uid in self._pending_dm:
                    self._pending_dm.pop(uid, None)
                    await self.player.resume_interrupted_song()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self._dm_timeout_tasks[uid] = self.player.bot.loop.create_task(_timeout_coro())

    async def _monitor_speech_loop(self):
        """Controlla continuamente i buffer vocali di tutti i partecipanti nel canale con ciclo rapido a 40ms."""
        try:
            while self._running:
                await asyncio.sleep(0.04)
                now = time.monotonic()

                for key in list(self._buffers.keys()):
                    buf = self._buffers.get(key)
                    if not buf:
                        continue

                    last_t = self._last_packet_time.get(key, now)
                    silence_dur = now - last_t
                    phrase_dur = len(buf) / self.BYTES_PER_SECOND

                    if silence_dur >= self.SILENCE_THRESHOLD_SEC or phrase_dur >= self.MAX_PHRASE_SEC:
                        raw_pcm = bytes(buf)
                        self._buffers.pop(key, None)
                        self._last_packet_time.pop(key, None)
                        speaker_obj = self._users.get(key)

                        if not self._buffers:
                            self.player.restore_duck_volume()

                        if phrase_dur >= self.MIN_PHRASE_SEC:
                            asyncio.create_task(self._process_utterance(key, speaker_obj, raw_pcm))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Errore nel loop di monitoraggio vocale: {e}", exc_info=True)

    async def _process_utterance(self, key: int, speaker_obj: object, stereo_pcm_48k: bytes):
        """
        Elabora l'audio catturato con la massima qualità di riconoscimento vocale:
        1. Mixdown in mono nativo a 48kHz (piena risoluzione spettrale senza aliasing).
        2. Rimozione DC Bias (rumore continuo a frequenza zero tipico dei microfoni USB).
        3. Normalizzazione Soft AGC (Automatic Gain Control) per voci basse o lontane dal microfono.
        4. Padding antirimbalzo sui transienti.
        5. Trascrizione multi-candidato Google Speech API (show_all=True) per estrarre la miglior alternativa acustica.
        """
        try:
            # 1. Mixdown mono a 48kHz nativo (senza decimation/aliasing)
            mono_48k = audioop.tomono(stereo_pcm_48k, self.SAMPLE_WIDTH, 0.5, 0.5)

            # 2. Rimozione DC Bias (offset continuo)
            dc_bias = audioop.avg(mono_48k, self.SAMPLE_WIDTH)
            if abs(dc_bias) > 25:
                mono_48k = audioop.bias(mono_48k, self.SAMPLE_WIDTH, -dc_bias)

            # 3. Soft AGC / Normalizzazione del volume della voce
            max_amp = audioop.max(mono_48k, self.SAMPLE_WIDTH)
            if max_amp > 200:
                target_peak = 26500
                if max_amp < target_peak:
                    gain = min(5.5, target_peak / max_amp)
                    if gain > 1.05:
                        mono_48k = audioop.mul(mono_48k, self.SAMPLE_WIDTH, gain)
                elif max_amp > 32000:
                    gain = 30000.0 / max_amp
                    mono_48k = audioop.mul(mono_48k, self.SAMPLE_WIDTH, gain)

            # 4. Padding di silenzio (80ms) per evitare transienti sui bordi FLAC
            pad = b"\x00" * (self.SAMPLE_RATE * self.SAMPLE_WIDTH // 12)
            padded_mono_48k = pad + mono_48k + pad
            audio_data = sr.AudioData(padded_mono_48k, self.SAMPLE_RATE, self.SAMPLE_WIDTH)

            # 5. Riconoscimento con show_all=True per ottenere tutte le alternative acustiche
            res = await asyncio.to_thread(
                self.recognizer.recognize_google,
                audio_data,
                language="it-IT",
                show_all=True,
            )
        except sr.UnknownValueError:
            return
        except Exception as e:
            logger.debug(f"Errore SpeechRecognition: {e}")
            return

        if not res:
            return

        candidates = []
        if isinstance(res, dict) and "alternative" in res:
            for alt in res["alternative"]:
                t = alt.get("transcript", "").strip()
                if t:
                    candidates.append(t)
        elif isinstance(res, str):
            candidates.append(res.strip())

        if not candidates:
            return

        # Selezione intelligente:
        # Se il primo candidato contiene già la wake word, usalo.
        # Altrimenti, se una delle alternative include la wake word, seleziona l'alternativa migliore!
        selected_text = candidates[0]
        has_wake = any(w in selected_text.lower() for w in self.WAKE_WORDS)
        if not has_wake:
            for alt in candidates[1:]:
                alt_lower = alt.lower()
                if any(w in alt_lower for w in self.WAKE_WORDS):
                    selected_text = alt
                    logger.info(f"🎯 Alternativa acustica selezionata con successo: '{selected_text}' (invece di '{candidates[0]}')")
                    break

        user = await self._resolve_user_object(key, speaker_obj)
        logger.info(f"🎙️ [{user.display_name}] ha detto: '{selected_text}'")
        try:
            await self._handle_transcribed_text(user, selected_text)
        except Exception as e:
            logger.error(f"❌ Errore critico gestione comando vocale '{selected_text}' da {user.display_name}: {e}", exc_info=True)
            await self._notify_channel(f"⚠️ Si è verificato un errore durante l'elaborazione del comando: `{e}`")

    async def _handle_transcribed_text(self, user, text: str):
        """Analizza il testo trascritto ed esegue il comando per qualsiasi utente del canale."""
        from player import resolve_preferred_text_channel, build_commands_embed, COLOR_FRANCHINO

        lower_text = text.lower().strip()
        uid = getattr(user, "id", 0)
        now = time.monotonic()

        # ======================================================================
        # FASE INTERATTIVA DM: L'utente sta rispondendo a "Cosa scrivo nel messaggio?"
        # ======================================================================
        if uid in self._pending_dm:
            dm_time, target_member = self._pending_dm.pop(uid)
            self._cancel_dm_timeout(uid)
            if now - dm_time <= 35.0:
                msg_body = text.strip()
                # Se l'utente ha ripetuto "Franchino..." o "scrivi..." o "digli...", puliamo il prefisso
                for wake in self.WAKE_WORDS:
                    if msg_body.lower().startswith(wake):
                        msg_body = msg_body[len(wake):].strip(" ,.!?:;-")
                        break
                msg_body = re.sub(
                    r"^(?:scrivi|scrivigli|scrivile|digli|dille|manda|mandagli|che)\s+",
                    "",
                    msg_body,
                    flags=re.IGNORECASE,
                ).strip()

                if not msg_body:
                    self._pending_dm[uid] = (now, target_member)
                    self._start_dm_timeout(uid, 25.0)
                    return

                if msg_body.lower() in ("annulla", "niente", "lascia stare", "cancella", "stop", "basta"):
                    await self._notify_channel(
                        f"❌ **{user.display_name}** ha annullato l'invio del messaggio privato a **{target_member.display_name}**."
                    )
                    await self.player.speak_tts(
                        "Va bene, messaggio annullato!",
                        wait=True,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                    return

                dm_embed = discord.Embed(
                    title=f"💌 Nuovo messaggio vocale da {user.display_name}!",
                    description=f"❝ **{msg_body}** ❞",
                    color=COLOR_FRANCHINO,
                )
                vc = self.player.voice_client or self.player.guild.voice_client
                room_name = vc.channel.name if vc and vc.channel else "Canale Vocale"
                dm_embed.set_footer(
                    text=f"Dettato a voce tramite Franchino DJ • Stanza: {room_name} ({self.player.guild.name})"
                )
                if getattr(user, "display_avatar", None):
                    dm_embed.set_thumbnail(url=user.display_avatar.url)

                try:
                    await target_member.send(
                        content=f"👋 Ciao **{target_member.display_name}**! **{user.display_name}** ti ha appena mandato un messaggio dalla stanza vocale:",
                        embed=dm_embed,
                    )
                    await self._notify_channel(
                        f"📨 **{user.display_name}** ha inviato un messaggio privato a **{target_member.display_name}** tramite Franchino!"
                    )
                    await self.player.speak_tts(
                        f"Messaggio inviato a {target_member.display_name}!",
                        wait=True,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                except discord.Forbidden:
                    await self._notify_channel(
                        f"⚠️ {target_member.mention} ha i messaggi privati chiusi! Ecco il messaggio vocale di **{user.display_name}**: *\"{msg_body}\"*"
                    )
                    await self.player.speak_tts(
                        f"{target_member.display_name} ha i messaggi privati chiusi, gliel'ho scritto nella chat musica!",
                        wait=True,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                except Exception as e:
                    logger.error(f"Errore nell'invio DM a {target_member}: {e}")
                    await self.player.resume_interrupted_song()
                return

        command_part = None
        for wake in self.WAKE_WORDS:
            idx = lower_text.find(wake)
            if idx != -1:
                after_wake = text[idx + len(wake):].strip(" ,.!?:;-")
                before_wake = text[:idx].strip(" ,.!?:;-")
                if after_wake:
                    command_part = after_wake
                elif before_wake:
                    command_part = before_wake
                else:
                    command_part = ""
                break

        # Se l'utente aveva detto "Franchino" o "Franchino metti" pochi secondi fa e ora ha detto il nome della canzone:
        if command_part is None and uid in self._pending_wake:
            wake_time, pending_prefix = self._pending_wake.pop(uid)
            if now - wake_time <= 7.0:
                command_part = f"{pending_prefix} {text}".strip() if pending_prefix else text.strip()
                logger.info(f"🔗 Continuazione comando vocale per {user.display_name}: '{command_part}'")

        # Se non è stata detta la wake word esplicita, controlla se è un comando vocale diretto nella stanza:
        if command_part is None:
            direct_triggers = (
                # Liveness & presenza
                "ci senti", "ci sentite", "mi senti", "mi sentite",
                "ci sei", "ci siete", "sei vivo", "sei sveglio",
                "batti un colpo", "mi ascolti", "ci ascolti", "rispondi",
                # Musica
                "metti ", "mette ", "mettimi ", "riproduci ", "suona ", "suonami ", "play ",
                "fammi sentire ", "fammi ascoltare ", "avvia ", "cerca ", "cercami ", "fai partire ",
                "aggiungi ", "accoda ", "metti in coda ",
                # Controlli player
                "stop", "stoppa", "ferma", "fermati", "basta", "zitto", "silenzio", "muto", "spegni",
                "pausa", "riprendi", "continua", "salta", "skip", "skippa", "prossima", "cambia",
                "alza il volume", "abbassa il volume", "volume a ", "volume al ",
                # Funzionalità
                "raccontami una favola", "racconta una favola", "dimmi una favola", "favola",
                "chi canta", "che canzone è", "partita", "streaming",
                "riavviati", "sbloccati", "reset", "comandi", "aiuto"
            )
            clean_l = re.sub(r"[^\w\s]", " ", lower_text).strip()
            for trig in direct_triggers:
                trig_clean = trig.strip()
                if clean_l == trig_clean or clean_l.startswith(trig) or (len(trig_clean) >= 4 and trig_clean in clean_l):
                    command_part = text.strip()
                    logger.info(f"🎯 Comando diretto senza wake word rilevato da {user.display_name}: '{command_part}'")
                    break

        if command_part is None:
            logger.info(f"ℹ️ Nessun comando vocale riconosciuto in: '{text}' (da {user.display_name})")
            return

        cmd_lower = command_part.lower().strip()

        # Se l'utente ha detto solo "Franchino" (per testare la presenza o prima del comando):
        if not cmd_lower:
            self._pending_wake[uid] = (now, "")
            await self._notify_channel(
                f"🎙️✨ **{user.display_name}** ha chiamato Franchino in console! *(Microfono aperto, chiedimi un brano o una domanda!)*"
            )
            asyncio.create_task(
                self.player.speak_tts("Magia! Dimmi tutto, sono qui in console!", wait=False, interrupt_music=False)
            )
            return
        if cmd_lower in ("metti", "mette", "mettimi", "riproduci", "suona", "suonami", "play", "fammi sentire", "fammi ascoltare", "avvia", "cerca", "cercami", "fai partire"):
            self._pending_wake[uid] = (now, "metti")
            return
        if cmd_lower in ("metti in coda", "mette in coda", "mettimi in coda", "aggiungi in coda", "aggiungi", "accoda"):
            self._pending_wake[uid] = (now, "aggiungi")
            return
        if cmd_lower in (
            "manda messaggio", "manda un messaggio", "invia messaggio", "invia un messaggio",
            "scrivi messaggio", "scrivi un messaggio", "manda messaggio a", "scrivi a"
        ):
            self._pending_wake[uid] = (now, "manda messaggio a")
            asyncio.create_task(self.player.speak_tts("A chi vuoi mandare il messaggio?"))
            return

        self._pending_wake.pop(uid, None)

        vc = self.player.voice_client or self.player.guild.voice_client
        self.player.text_channel = resolve_preferred_text_channel(
            self.player.guild,
            self.player.text_channel or (vc.channel if vc else None)
        )

        requester_arg = user if isinstance(user, (discord.Member, discord.User)) else None
        logger.info(f"✨ Comando vocale rilevato da {user.display_name}: '{command_part}'")

        # 0. Comando LIVENESS / PRESENZA VOCALE ("ci senti?", "ci sei?", "sei vivo?", "mi senti?", "rispondi", "batti un colpo")
        liveness_triggers = (
            "ci senti", "ci sentite", "mi senti", "mi sentite", "puoi sentirmi", "puoi sentirci",
            "ci sei", "ci siete", "ci sei o ci fai", "ci sei ancora", "sei li", "sei lì", "sei qui",
            "sei vivo", "sei ancora vivo", "sei sveglio", "ti sei svegliato", "stai dormendo",
            "rispondi", "rispondimi", "batti un colpo", "fatti sentire", "di qualcosa", "dì qualcosa",
            "mi ascolti", "ci ascolti", "tutto bene franchino", "tutto bene", "come va",
            "test voce", "test microfono", "prova microfono", "prova voce", "prova audio", "funzioni"
        )
        is_music_prefix = cmd_lower.startswith((
            "metti ", "riproduci ", "suona ", "play ", "cerca ",
            "fammi sentire ", "fai partire ", "aggiungi ", "accoda "
        ))
        is_liveness_cmd = not is_music_prefix and (
            any(w in cmd_lower for w in liveness_triggers)
            or bool(re.search(r"\b(?:ci\s+senti|mi\s+senti|ci\s+sei|ci\s+siete|sei\s+vivo|sei\s+sveglio|batti\s+un\s+colpo|rispondi|fatti\s+sentire|mi\s+ascolti|ci\s+ascolti)\b", cmd_lower))
        )
        if is_liveness_cmd:
            from config import FRANCHINO_LIVENESS_QUOTES
            chosen_phrase = random.choice(FRANCHINO_LIVENESS_QUOTES)
            ping_ms = round(self.player.bot.latency * 1000)
            embed = discord.Embed(
                title="🎙️✨ Franchino è Online e vi sente!",
                description=(
                    f"> *\"{chosen_phrase}\"*\n\n"
                    f"🎧 **Console Vocale:** Sempre in Ascolto 24/7 ✅\n"
                    f"📶 **Latenza:** `{ping_ms}ms`\n"
                    f"🔊 **Volume Console:** `{int(self.player.volume * 100)}%`"
                ),
                color=0xFF6F00,
            )
            embed.set_footer(text="Franchino Vocalist • Microfono Sempre Aperto ✨")
            await self._notify_channel(
                message=f"🎙️ **{user.display_name}** ha detto: *\"Franchino {command_part}\"*",
                embed=embed,
            )
            asyncio.create_task(
                self.player.speak_tts(chosen_phrase, wait=False, interrupt_music=True, dramatic_fable=True)
            )
            from central_hub import hub
            asyncio.create_task(
                hub.archive_interaction(
                    user_id=str(getattr(user, "id", 0)),
                    user_name=user.display_name,
                    raw_query=f"voce_liveness {command_part}",
                    intent="liveness_check",
                    ai_response=chosen_phrase,
                    voice_summary=chosen_phrase,
                    channel_name=self.player.text_channel.name if self.player.text_channel else None,
                )
            )
            return

        # 0A. Comando RIAVVIATI / SBLOCCATI / RESET (solo comando diretto, non "come si sblocca un aspect"!)
        if cmd_lower in (
            "riavviati", "riavvia", "riavvia la console", "riavvia console",
            "reset", "resettati", "sbloccati", "sblocca la console",
            "riconnettiti", "riconnetti", "svegliati", "sveglia", "riprenditi"
        ):
            await self._notify_channel(
                f"🔄 **{user.display_name}** ha detto *\"Franchino {command_part}\"* — Riavvio la console vocale..."
            )
            await self.player.restart_console()
            await self._notify_channel(
                "🎧 **Franchino si è riavviato ed è di nuovo in ascolto!** Pronti per la magia! ✨"
            )
            asyncio.create_task(self.player.speak_tts("Eccomi ragazzi! Console riavviata e pronta per la magia!"))
            return

        # 0A-bis. Comando STOP / FERMA / BASTA / ZITTO / SILENZIO / MUTO / SPEGNI (Funziona SEMPRE al 100%!)
        # Interrompe istantaneamente qualsiasi cosa: canzoni, voce di Franchino (TTS / favole / wiki), effetti sonori e svuota la coda.
        stop_exact = (
            "stop", "stoppa", "ferma", "fermati", "ferma tutto", "fermati subito",
            "basta", "basta cosi", "basta così", "basta parlare", "basta musica",
            "zitto", "stai zitto", "muto", "silenzio", "fai silenzio", "taci",
            "spegni", "spegni tutto", "smettila", "svuota", "cancella tutto",
            "annulla", "calma", "staccati", "shh", "shhh",
            "stop musica", "stop canzone", "stop audio", "stop parlare", "stop tutto",
            "stoppa tutto", "stoppa musica", "stoppa la musica"
        )
        is_stop_cmd = (
            cmd_lower in stop_exact
            or (
                not cmd_lower.startswith(("metti ", "riproduci ", "suona ", "play ", "cerca ", "fammi sentire ", "fai partire ", "aggiungi ", "accoda "))
                and any(
                    cmd_lower.startswith(prefix + " ")
                    for prefix in ("stop", "stoppa", "ferma", "basta", "zitto", "stai zitto", "spegni")
                )
            )
            or any(
                phrase in cmd_lower
                for phrase in ("ferma tutto", "basta parlare", "stai zitto", "fai silenzio", "stop tutto", "spegni tutto", "stop musica")
            )
        )
        if is_stop_cmd:
            self.player.stop()
            await self._notify_channel(
                f"⏹️ **{user.display_name}** ha detto *\"Franchino {command_part}\"* — Tutto fermato e silenzio assoluto in console! 🔇"
            )
            return

        # 0B. Comando COMANDI / DIMMI I COMANDI / AIUTO -> Inviato in MESSAGGIO PRIVATO (DM)!
        if cmd_lower in (
            "comandi", "i comandi", "comando", "dimmi i comandi", "quali sono i comandi",
            "mandami i comandi", "invia i comandi", "aiuto", "help",
            "cosa puoi fare", "cosa sai fare", "istruzioni", "lista comandi"
        ):
            embed = build_commands_embed()
            sent_dm = False
            if hasattr(user, "send"):
                try:
                    await user.send(
                        content=f"🎧 Ciao **{user.display_name}**! Ecco tutti i comandi vocali e testuali di **Franchino DJ**:",
                        embed=embed,
                    )
                    sent_dm = True
                except Exception as e:
                    logger.warning(f"Impossibile inviare DM dei comandi a {user.display_name}: {e}")

            if sent_dm:
                await self._notify_channel(
                    f"📬 **{user.display_name}** ha chiesto i comandi — glieli ho inviati in **messaggio privato**!"
                )
                asyncio.create_task(
                    self.player.speak_tts(f"Ti ho mandato la lista dei comandi in messaggio privato, {user.display_name}!")
                )
            else:
                await self._notify_channel(
                    f"🎙️ **{user.display_name}** ha chiesto i comandi *(messaggi privati chiusi, li mostro qui)*:",
                    embed=embed,
                )
                asyncio.create_task(self.player.speak_tts("Hai i messaggi privati chiusi, ti ho mandato i comandi nella chat musica!"))
            return

        # 0B-bis. Comandi VOCE DEL VERO FRANCHINO IL VOCALIST (Favole, Magia, Eco da Console)
        if any(w in cmd_lower for w in (
            "voce senza eco", "togli eco", "disattiva eco", "voce nitida", "voce pulita"
        )):
            self.player.vocalist_mode = False
            await self._notify_channel("🎙️ **Voce di Franchino impostata su: Nitida (senza eco da console).**")
            asyncio.create_task(self.player.speak_tts("Perfetto ragazzi, ho tolto l'eco del microfono da discoteca.", interrupt_music=True))
            return

        if any(w in cmd_lower for w in (
            "voce vocalist", "attiva eco", "voce da vocalist", "voce franchino", "effetto vocalist"
        )):
            self.player.vocalist_mode = True
            await self._notify_channel("🎙️✨ **Voce del Vero Franchino il Vocalist attiva (Timbro profondo + Eco da Console Insomnia)!**")
            asyncio.create_task(self.player.speak_tts("Magia... Il microfono della notte è acceso. Chiudi gli occhi e vola...", interrupt_music=True, dramatic_fable=True))
            return

        if any(w in cmd_lower for w in (
            "raccontami una favola", "racconta una favola", "dimmi una favola", "una favola", "favola",
            "fai il vocalist", "vocalist", "c'era una volta", "metempsicosi", "insomnia", "magia", "fai magia",
            "favola della notte", "raccontaci una favola"
        )):
            from brain import brain
            chosen_fable = await brain.generate_live_fable(user.display_name)
            await self._notify_channel(f"🎙️🌙 **Franchino il Vocalist ({user.display_name}):**\n> *\"{chosen_fable}\"*")
            asyncio.create_task(
                self.player.speak_tts(chosen_fable, wait=True, interrupt_music=True, dramatic_fable=True)
            )
            return

        # 0B-quinquies. Curiosità & Chi canta la canzone in corso
        if any(w in cmd_lower for w in (
            "chi canta", "chi è che canta", "chi sta cantando", "che canzone è", "che brano è",
            "parlami di questa canzone", "parlami di questo pezzo", "cosa sta suonando"
        )):
            if self.player.current:
                cur = self.player.current
                from brain import brain
                commentary = await brain.explain_song_context(cur.title, cur.uploader or "")
                await self._notify_channel(f"🎙️🎧 **Franchino DJ ({user.display_name})**:\n> *\"{commentary}\"*")
                asyncio.create_task(self.player.speak_tts(commentary, wait=True, interrupt_music=True))
            else:
                await self._notify_channel("⚠️ Al momento non sta suonando nessuna traccia!")
                asyncio.create_task(self.player.speak_tts("Non c'è musica attiva al momento ragazzi!"))
            return

        # 0B-sexies. Ricerca Partita / Streaming Live Sport (MadPlay77)
        # Esempi: "Franchino cerca la partita del Milan", "Franchino dove fanno vedere la Juventus", "Franchino streaming NBA"
        sports_match = re.search(
            r"(?:cerca\s+(?:la\s+)?partita|dove\s+(?:fanno\s+vedere|posso\s+vedere|vedere|trasmettono)|streaming\s+(?:della\s+)?partita|trova\s+(?:la\s+)?partita|partita\s+(?:del|della|di|dei)|streaming\s+di|guarda\s+(?:la\s+)?partita)\s+(.+)",
            cmd_lower
        )
        if not sports_match and any(w in cmd_lower for w in ("partita", "streaming", "formula 1", "motogp", "f1", "nba", "chi gioca", "partite live")):
            sports_kw_match = re.search(r"(?:partita|streaming|evento|sport)\s+(?:del|della|di|per)?\s*(.+)", cmd_lower)
            if sports_kw_match:
                sports_match = sports_kw_match

        if sports_match:
            sport_query = sports_match.group(1).strip(" ,.!?:;-")
            from sports_scraper import sports_scraper
            from central_hub import hub

            res = await sports_scraper.search_event(sport_query)
            if res.get("found"):
                match = res["best_match"]
                embed = discord.Embed(
                    title=f"🏆 {match['title']}",
                    description="Diretta streaming live disponibile su **MadPlay77**!",
                    color=discord.Color.green(),
                )
                embed.add_field(name="⚽ Disciplina", value=match['sport_name'], inline=True)
                embed.add_field(name="⏰ Orario / Stato", value=f"`{match['time']}`", inline=True)
                embed.add_field(name="🔗 Link Streaming", value=f"[Guarda la Partita in Diretta]({match['url']})", inline=False)
                embed.set_footer(text="Franchino Sports • MadPlay77 Live")

                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="Guarda lo Streaming", url=match['url'], style=discord.ButtonStyle.link, emoji="📺"))
                view.add_item(discord.ui.Button(label="Tutti gli Eventi Live", url=match['portal_url'], style=discord.ButtonStyle.link, emoji="🌐"))

                await self._notify_channel(
                    message=f"🎙️⚽ **Franchino Sports ({user.display_name}):**\n> *\"Magia ragazzi! Ho trovato **{match['title']}** in programma alle **{match['time']}**! Vi ho messo il link in chat!\"*",
                    embed=embed,
                    view=view
                )
                voice_msg = f"Magia ragazzi! Ho trovato {match['title']}! Vi ho inviato il link dello streaming in chat, andate a vederla!"
                asyncio.create_task(self.player.speak_tts(voice_msg, wait=False, interrupt_music=True))

                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(user.id),
                        user_name=user.display_name,
                        raw_query=f"voce_partita {sport_query}",
                        intent="sports_stream",
                        ai_response=f"Partita: {match['title']}",
                        voice_summary=voice_msg,
                        metadata={"url": match['url']}
                    )
                )
                return
            else:
                embed = discord.Embed(
                    title="⚠️ Nessuna Partita Trovata",
                    description=res.get("message", f"Nessun evento sportivo trovato per *\"{sport_query}\"*."),
                    color=discord.Color.orange(),
                )
                featured = res.get("featured", [])
                if featured:
                    lines = [f"• **{f['title']}** ({f['sport_name']}) - `{f['time']}` → [Streaming]({f['url']})" for f in featured]
                    embed.add_field(name="🔥 Eventi Principali Attivi Adesso:", value="\n".join(lines), inline=False)

                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="Apri Palinsesto MadPlay77", url="https://web01.madplay77.ru/it", style=discord.ButtonStyle.link, emoji="🌐"))

                await self._notify_channel(
                    message=f"🎙️ **Franchino ({user.display_name})**: Non ho trovato eventi attivi per *\"{sport_query}\"* in questo momento, ecco le partite live adesso:",
                    embed=embed,
                    view=view
                )
                voice_fail = f"Non ho trovato partite per {sport_query} al momento ragazzi, vi ho mostrato gli eventi attivi in chat!"
                asyncio.create_task(self.player.speak_tts(voice_fail, wait=False, interrupt_music=True))
                return

        # 0C. Comando MANDA MESSAGGIO PRIVATO A <PERSONA NELLA STANZA>
        # Esempio: "Franchino manda messaggio ad Ema", "Franchino scrivi a Luca", "Franchino manda un messaggio a Marco"
        dm_match = re.match(
            r"^(?:manda|mandagli|invia|inviagli|scrivi|scrivigli|fai)\s+"
            r"(?:un\s+)?(?:messaggio\s+)?(?:privato\s+)?(?:in\s+privato\s+)?"
            r"(?:a|ad|al|allo|alla)\s+(.+)$",
            command_part.strip(),
            flags=re.IGNORECASE,
        ) or re.match(
            r"^(?:messaggio|messaggio\s+privato)\s+(?:a|ad|per)\s+(.+)$",
            command_part.strip(),
            flags=re.IGNORECASE,
        )
        if dm_match:
            target_query = dm_match.group(1).strip(" ,.!?:;-")
            target_member, present_names = await self._find_member_in_voice_channel(target_query, exclude_uid=uid)
            if not target_member or not hasattr(target_member, "send"):
                present_str = ", ".join(present_names) if present_names else "nessuno"
                await self._notify_channel(
                    f"⚠️ **{user.display_name}**, non ho trovato nessuno chiamato **\"{target_query}\"** qui nel canale vocale! "
                    f"*(Presenti nella stanza: {present_str})*"
                )
                asyncio.create_task(
                    self.player.speak_tts(f"Non trovo {target_query} qui nella stanza vocale!")
                )
                return

            self._pending_dm[uid] = (time.monotonic(), target_member)
            self._start_dm_timeout(uid, 35.0)
            await self._notify_channel(
                f"💬 **{user.display_name}** vuole mandare un messaggio privato a **{target_member.display_name}** — "
                f"Franchino chiede: *\"Cosa scrivo nel messaggio?\"* (detta pure a voce!)"
            )
            await self.player.speak_tts(
                "Cosa scrivo nel messaggio?",
                wait=True,
                interrupt_music=True,
                keep_paused_after=False,
            )
            # Rinnova il timestamp appena Franchino finisce di parlare così l'utente ha 30 secondi pieni per rispondere
            self._pending_dm[uid] = (time.monotonic(), target_member)
            self._start_dm_timeout(uid, 30.0)
            return

        # 0C-bis. Comandi da ADMIN & MODERATORE DISCORD (Sposta utenti/stanza, Ruoli, Muta/Smuta, Soprannomi, Timeout, Kick, Ban, Stanze, Pulisci chat)
        from admin_commands import handle_voice_admin_command
        if await handle_voice_admin_command(self.player, user, command_part, self._notify_channel):
            return

        # 0C-ter. Domande sulla WIKI DI UO OUTLANDS (https://wiki.uooutlands.com) tradotte e lette a voce in Italiano!
        from outlands_wiki import is_wiki_question, query_outlands_wiki
        is_wiki, wiki_q = is_wiki_question(command_part)
        if is_wiki:
            logger.info(f"📖 Domanda Wiki Outlands riconosciuta da {user.display_name}: '{wiki_q}'")
            self.player._manual_stop = False
            try:
                await self._notify_channel(
                    f"📖 **{user.display_name}** ha chiesto su **UO Outlands**: *\"{wiki_q}\"* — Cerco sulla Wiki e traduco in italiano..."
                )
                wiki_res = await asyncio.wait_for(query_outlands_wiki(wiki_q), timeout=18.0)

                if not wiki_res:
                    await self._notify_channel(
                        f"⚠️ Non ho trovato risultati su `wiki.uooutlands.com` per **\"{wiki_q}\"**."
                    )
                    asyncio.create_task(
                        self.player.speak_tts(
                            "Mi dispiace, non ho trovato questa informazione sulla Wiki di Outlands!",
                            wait=False,
                            interrupt_music=True,
                            keep_paused_after=False,
                        )
                    )
                    return

                title = wiki_res["page_title"]
                sec_title = wiki_res["section_title"]
                sec_url = wiki_res["section_url"]
                voice_answer = wiki_res["voice_answer"]
                wiki_embed = wiki_res["embed"]
                logger.info(f"✅ Wiki Outlands: trovata risposta '{title} → {sec_title}'")

                # Invia subito l'embed nel canale del server per tutta la stanza
                await self._notify_channel(
                    f"📖 **{user.display_name}**, ecco la guida da [**wiki.uooutlands.com** ({title} → {sec_title})]({sec_url}) tradotta in italiano:",
                    embed=wiki_embed,
                )

                # Invia anche in privato (DM) in background senza attendere
                async def _send_dm_bg():
                    if hasattr(user, "send"):
                        try:
                            await user.send(
                                content=(
                                    f"📖 Ciao **{user.display_name}**! Ecco il link diretto alla sezione di riferimento per la tua domanda "
                                    f"(*\"{wiki_q}\"*):\n🔗 **{title} → {sec_title}**: {sec_url}"
                                ),
                                embed=wiki_embed,
                            )
                        except Exception as e:
                            logger.warning(f"Impossibile inviare DM Wiki a {user.display_name}: {e}")
                asyncio.create_task(_send_dm_bg())

                # Archivia l'interazione nell'Hub centrale
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=wiki_q,
                        intent="wiki_guide",
                        ai_response=title,
                        voice_summary=voice_answer,
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"title": title, "url": sec_url},
                    )
                )

                # Parla nel canale vocale con la voce di Franchino (asincrono!)
                asyncio.create_task(
                    self.player.speak_tts(
                        voice_answer,
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
            except asyncio.TimeoutError:
                logger.warning(f"⏰ Timeout ricerca Wiki Outlands per '{wiki_q}'")
                await self._notify_channel(
                    f"⚠️ La ricerca su `wiki.uooutlands.com` per **\"{wiki_q}\"** ha impiegato troppo tempo. Riprova tra poco!"
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        "Ci ho messo troppo tempo a consultare la Wiki di Outlands, riprova tra poco!",
                        wait=False,
                        interrupt_music=True,
                    )
                )
            except Exception as e:
                logger.error(f"Errore gestione domanda Wiki Outlands '{wiki_q}': {e}", exc_info=True)
                await self._notify_channel(
                    f"⚠️ Si è verificato un errore durante la ricerca sulla Wiki di Outlands: {e}"
                )
            return

        # 0D. Comando SALVA NEI PREFERITI ("salva", "salva questa canzone", "aggiungi ai preferiti")
        if any(w in cmd_lower for w in (
            "salva questa", "salva canzone", "salva brano", "aggiungi ai preferiti", "salva nei preferiti", "salvala"
        )) or cmd_lower == "salva":
            added, song, total = self.player.save_current_to_favorites(user)
            if not song:
                await self._notify_channel("⚠️ Non c'è nessun brano in riproduzione da salvare!")
            elif added:
                await self._notify_channel(
                    f"❤️ **{user.display_name}** ha salvato **{song.title}** nei Preferiti del Server! *(Totale: {total} brani)*"
                )
            else:
                await self._notify_channel(
                    f"❤️ **{song.title}** è già nei Preferiti del Server! *({total} brani salvati)*"
                )
            return

        # 0D. Comando RIPRODUCI I PREFERITI ("metti i preferiti", "metti i nostri preferiti", "avvia i preferiti")
        if any(w in cmd_lower for w in (
            "metti i preferiti", "metti i nostri preferiti", "riproduci i preferiti",
            "avvia i preferiti", "suona i preferiti", "metti preferiti", "nostri preferiti"
        )):
            fav_songs = self.player.load_favorites_as_songs(requester=requester_arg)
            if not fav_songs:
                await self._notify_channel(
                    "⚠️ La playlist **Preferiti del Server** è ancora vuota! Dì *\"Franchino salva questa canzone\"* mentre suona un brano."
                )
                asyncio.create_task(self.player.speak_tts("Non avete ancora salvato canzoni nei preferiti!"))
            else:
                random.shuffle(fav_songs)
                await self._notify_channel(
                    f"❤️ **{user.display_name}** ha avviato i **Preferiti del Server** ({len(fav_songs)} brani in rotazione)!"
                )
                await self.player.play_now(
                    fav_songs,
                    text_channel=self.player.text_channel,
                    voice_announce=f"Subito! Metto i vostri {len(fav_songs)} brani preferiti!",
                )
            return

        # 0E. Comando MODALITÀ RADIO / AUTOPLAY ("attiva radio", "disattiva radio", "modalità radio", "autoplay")
        if any(w in cmd_lower for w in ("attiva radio", "disattiva radio", "modalità radio", "modalita radio", "autoplay")) or cmd_lower == "radio":
            if "disattiva" in cmd_lower or "spegni" in cmd_lower:
                self.player.autoplay = False
            elif "attiva" in cmd_lower or "accendi" in cmd_lower:
                self.player.autoplay = True
            else:
                self.player.autoplay = not self.player.autoplay
            stato = "ATTIVATA ✅ (a fine coda metterò brani correlati!)" if self.player.autoplay else "DISATTIVATA ❌"
            await self._notify_channel(f"📻 **{user.display_name}**: Modalità Radio AutoPlay **{stato}**")
            asyncio.create_task(
                self.player.speak_tts(
                    "Modalità radio infinita attivata!" if self.player.autoplay else "Modalità radio disattivata!"
                )
            )
            return

        # 0F. Comando 12 EFFETTI AUDIO DJ DAL VIVO (Filtri sulla canzone in corso)
        effect_map = [
            (("mega bass", "megabass", "sub bass", "bassi estremi", "bassi devastanti", "ultra bassi"), "megabass"),
            (("bass boost", "bassboost", "aumenta i bassi", "bassi a palla", "effetto bassi", "bassi"), "bassboost"),
            (("nightcore", "night core", "velocizza", "effetto veloce"), "nightcore"),
            (("slowed", "rallenta", "effetto lento"), "slowed"),
            (("8d", "otto d", "surround", "effetto 8d"), "8d"),
            (("reverb", "riverbero", "eco", "effetto eco", "arena", "concerto"), "reverb"),
            (("karaoke", "togli la voce", "senza voce", "strumentale", "base musicale"), "karaoke"),
            (("vaporwave", "vapor wave", "lo-fi", "lofi"), "vaporwave"),
            (("scoiattolo", "chipmunk", "voce acuta", "voce da scoiattolo", "elio"), "chipmunk"),
            (("demone", "demon", "voce profonda", "voce grave", "voce cupa", "mostro", "darth vader"), "demon"),
            (("vintage", "radio vecchia", "radio anni 60", "vinile", "vecchia radio", "telefono"), "vintage"),
            (("effetto normale", "audio normale", "togli effetti", "togli effetto", "senza effetti", "originale", "reset effetti"), "off"),
        ]
        for keywords, eff_key in effect_map:
            if any(k in cmd_lower for k in keywords) or (eff_key == "off" and cmd_lower == "normale"):
                label = await self.player.set_effect(eff_key)
                await self._notify_channel(
                    f"🎛️ **{user.display_name}** ha attivato con la voce l'effetto DJ: **{label}**!"
                )
                return

        # 0G. Comando 12 EFFETTI SONORI SOUNDBOARD ISTANTANEI ("trombetta", "sirena", "applausi", "rullo di tamburi", ecc.)
        sfx_map = [
            (("trombetta", "tromba", "airhorn", "air horn", "corno", " da stadio"), "airhorn"),
            (("sirena", "allarme", "polizia", "ambulanza"), "siren"),
            (("sub drop", "bass drop", "drop", "bomba", "esplosione"), "drop"),
            (("rullo di tamburi", "rullo", "tamburi", "battuta", "ba dum", "badum"), "drumroll"),
            (("applausi", "applauso", "bravo", "bravi", "ovazione", "battimani"), "applause"),
            (("fail", "figura di merda", "figuraccia", "trombone", "wah wah", "peccato", "sconfitta"), "fail"),
            (("vittoria", "fanfara", "campioni", "vinto", "trionfo", "win"), "victory"),
            (("laser", "raggio laser", "sparo", "pew pew", "star wars"), "laser"),
            (("censura", "beep", "bip", "parolaccia", "censurato"), "censor"),
            (("fischio", "fischietto", "arbitro", "fallo", "cartellino"), "whistle"),
            (("risata", "ridi", "ahah", "divertente", "sitcom"), "laugh"),
            (("buuu", "buu", "booo", "dissenso", "vergogna", "errore", "sbagliato", "buzzer"), "booo"),
        ]
        for sfx_keywords, sfx_key in sfx_map:
            # Evita falsi positivi se l'utente dice "metti la canzone..."
            if not cmd_lower.startswith(("metti ", "riproduci ", "suona la canzone", "cerca ")):
                if any(k in cmd_lower for k in sfx_keywords):
                    from sound_effects import SOUNDBOARD_EFFECTS
                    meta = SOUNDBOARD_EFFECTS[sfx_key]
                    await self._notify_channel(
                        f"{meta['emoji']} **{user.display_name}** ha attivato con la voce l'effetto sonoro: **{meta['label']}**!"
                    )
                    await self.player.play_sound_effect(sfx_key)
                    return

        # 1A. Comando AGGIUNGI IN CODA ("metti in coda...", "aggiungi...")
        queue_match = re.match(
            r"^(?:metti in coda|mette in coda|mettimi in coda|aggiungi in coda|aggiungi|accoda)\s+(.+)$",
            command_part,
            re.IGNORECASE,
        )
        if queue_match:
            query = queue_match.group(1).strip()
            if query:
                self.player._manual_stop = False
                await self._notify_channel(
                    f"🎙️ **{user.display_name}**: *\"Franchino aggiungi {query}\"* — Aggiungo in coda!"
                )
                try:
                    songs = await asyncio.wait_for(
                        self.player.source_manager.resolve_url_or_query(query, requester=requester_arg),
                        timeout=25.0,
                    )
                    if songs:
                        await self.player.add_songs(songs, text_channel=self.player.text_channel)
                except Exception as e:
                    await self._notify_channel(f"⚠️ Non sono riuscito a trovare **{query}**: `{e}`")
            return

        # 1B. Comando RIPRODUCI SUBITO ("metti...", "riproduci...", "suona...", "play...", "fammi sentire...")
        play_match = re.match(
            r"^(?:metti|mette|mettimi|riproduci|riproduce|suona|suonami|play|fammi sentire|fammi ascoltare|avvia|cerca|cercami|fai partire)\s+(.+)$",
            command_part,
            re.IGNORECASE,
        )
        if play_match:
            query = play_match.group(1).strip()
            if query:
                self.player._manual_stop = False
                # Se è una richiesta descrittiva/mood, Gemma la trasforma nella miglior query YouTube
                from brain import brain
                if any(k in query.lower() for k in ("qualcosa", "un pezzo", "una canzone", "musica per", "un po di", "un po' di", "traccia da", "musica da")):
                    try:
                        refined = await brain.process_user_speech(
                            user_name=user.display_name,
                            user_id=str(getattr(user, "id", 0)),
                            text=f"metti {query}",
                            current_song_title=self.player.current.title if self.player.current else None,
                            channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        )
                        if refined.get("music_query"):
                            logger.info(f"🧠 Gemma ha convertito il mood '{query}' in query: '{refined['music_query']}'")
                            query = refined["music_query"]
                    except Exception as e:
                        logger.debug(f"Mood refinement fallback: {e}")

                await self._notify_channel(
                    f"🎙️ **{user.display_name}**: *\"Franchino metti {query}\"* — Avvio subito nel canale!"
                )
                try:
                    songs = await asyncio.wait_for(
                        self.player.source_manager.resolve_url_or_query(query, requester=requester_arg),
                        timeout=25.0,
                    )
                    if songs:
                        await self.player.play_now(
                            songs,
                            text_channel=self.player.text_channel,
                            voice_announce=f"Subito! Metto {query}",
                        )
                        from central_hub import hub
                        asyncio.create_task(
                            hub.archive_interaction(
                                user_id=str(getattr(user, "id", 0)),
                                user_name=user.display_name,
                                raw_query=command_part,
                                intent="play_music",
                                ai_response=songs[0].title if songs else query,
                                voice_summary=f"Subito! Metto {query}",
                                channel_name=self.player.text_channel.name if self.player.text_channel else None,
                                metadata={"music_query": query},
                            )
                        )
                except Exception as e:
                    await self._notify_channel(f"⚠️ Non sono riuscito a trovare **{query}**: `{e}`")
            return

        # 2. Comando SKIP / SALTA / PROSSIMA / CAMBIA
        if any(w in cmd_lower for w in (
            "skip", "skippa", "salta", "prossima", "prossimo", "avanti", "cambia"
        )):
            if self.player.is_active:
                self.player.skip()
                await self._notify_channel(f"🎙️ **{user.display_name}** ha detto *\"Franchino {command_part}\"* — Salto al prossimo brano! ⏭️")
            return

        # 3. Comando PAUSA
        if any(w in cmd_lower for w in ("pausa", "aspetta", "un attimo")):
            if self.player.is_playing():
                self.player.pause()
                await self._notify_channel(f"🎙️ **{user.display_name}** ha messo in pausa con la voce ⏸️")
            return

        # 4. Comando RIPRENDI / CONTINUA
        if any(w in cmd_lower for w in ("riprendi", "continua", "riparti", "togli la pausa", "vai")):
            if self.player.is_paused():
                self.player.resume()
                await self._notify_channel(f"🎙️ **{user.display_name}** ha ripreso la musica con la voce ▶️")
            return

        # 5. Comando STOP / BASTA / FERMA / ZITTO (Sempre attivo al 100% per qualsiasi suono)
        if any(w in cmd_lower for w in (
            "stop", "stoppa", "basta", "ferma", "fermati", "zitto", "muto", "silenzio", "spegni", "svuota"
        )):
            self.player.stop()
            await self._notify_channel(
                f"⏹️ **{user.display_name}** ha detto *\"Franchino {command_part}\"* — Tutto fermato e silenzio assoluto in console! 🔇"
            )
            return

        # 6. Comando LOOP / RIPETI
        if any(w in cmd_lower for w in ("loop", "ripeti")):
            self.player.loop_mode = "off" if self.player.loop_mode != "off" else "song"
            stato = "attivato 🔂" if self.player.loop_mode == "song" else "disattivato ➡️"
            await self._notify_channel(f"🎙️ **{user.display_name}** ha {stato} il loop con la voce!")
            return

        # 7. Comando SHUFFLE / MESCOLA
        if any(w in cmd_lower for w in ("shuffle", "mescola", "casuale", "mischia")):
            if len(self.player.queue) > 1:
                self.player.shuffle()
                await self._notify_channel(f"🎙️ **{user.display_name}** ha mescolato la coda con la voce! 🔀")
            return

        # 8. Comando VOLUME ("volume a 50", "alza", "abbassa", "massimo")
        vol_match = re.search(r"(?:volume\s+)?(?:a\s+|al\s+)?(\d{1,3})\s*%?", cmd_lower)
        if vol_match and ("volume" in cmd_lower or "metti a" in cmd_lower):
            val = max(1, min(100, int(vol_match.group(1))))
            self.player.set_volume(val / 100.0)
            await self._notify_channel(f"🎙️ **{user.display_name}** ha impostato il volume al **{val}%** 🔊")
            return

        if "massimo" in cmd_lower or "a palla" in cmd_lower:
            self.player.set_volume(1.0)
            await self._notify_channel(f"🎙️ **{user.display_name}** ha messo il volume al **100%**! 🔥🔊")
            return

        if any(w in cmd_lower for w in ("alza", "più alto", "piu alto", "più forte", "piu forte")):
            new_vol = min(1.0, self.player.volume + 0.20)
            self.player.set_volume(new_vol)
            await self._notify_channel(f"🎙️ Volume alzato al **{int(new_vol * 100)}%** 🔊")
            return

        if any(w in cmd_lower for w in ("abbassa", "più basso", "piu basso", "più piano", "piu piano")):
            new_vol = max(0.05, self.player.volume - 0.20)
            self.player.set_volume(new_vol)
            await self._notify_channel(f"🎙️ Volume abbassato al **{int(new_vol * 100)}%** 🔉")
            return

        # 9. Comando ESCI / DISCONNETTI
        if any(w in cmd_lower for w in ("esci", "vattene", "disconnetti", "ciao", "fuori", "slogga")):
            await self._notify_channel(f"🎙️ **{user.display_name}** mi ha salutato. Franchino lascia la console! 👋")
            await self.player.disconnect()
            return

        # 10. Fallback intelligente su ricerca musicale
        if command_part:
            logger.info(f"🎵 Fallback ricerca musicale per {user.display_name}: '{command_part}'")
            self.player._manual_stop = False
            await self._notify_channel(
                f"🎙️ **{user.display_name}**: *\"Franchino {command_part}\"* — Cerco e avvio in console! 🎶"
            )
            try:
                songs = await asyncio.wait_for(
                    self.player.source_manager.resolve_url_or_query(command_part, requester=requester_arg),
                    timeout=25.0,
                )
                if songs:
                    await self.player.play_now(
                        songs,
                        text_channel=self.player.text_channel,
                        voice_announce=f"Subito! Metto {command_part}",
                    )
            except Exception as e:
                logger.warning(f"Fallback ricerca brano fallito per '{command_part}': {e}")
                await self._notify_channel(
                    f"⚠️ Non ho trovato una traccia musicale per *\"{command_part}\"*. Dì *\"Franchino comandi\"* per l'elenco comandi!"
                )

    async def _notify_channel(
        self,
        message: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
    ):
        from player import resolve_preferred_text_channel

        vc = self.player.voice_client or self.player.guild.voice_client
        fallback = self.player.text_channel or (vc.channel if vc and vc.channel else None)
        target_ch = resolve_preferred_text_channel(self.player.guild, fallback)
        if target_ch:
            kwargs = {}
            if message:
                kwargs["content"] = message
            if embed:
                kwargs["embed"] = embed
            if view:
                kwargs["view"] = view
            try:
                await target_ch.send(**kwargs)
                logger.info(f"💬 Notifica inviata con successo nel canale #{target_ch.name}")
            except Exception as e:
                logger.warning(f"⚠️ Errore invio notifica a #{target_ch.name} ({e}), provo canale fallback...")
                if fallback and fallback != target_ch:
                    try:
                        await fallback.send(**kwargs)
                        logger.info(f"💬 Notifica inviata con successo nel canale fallback #{fallback.name}")
                    except Exception as e2:
                        logger.error(f"❌ Impossibile inviare notifica Discord in nessun canale: {e2}")
        else:
            logger.warning(f"⚠️ Nessun canale testuale trovato per inviare notifiche sul server {self.player.guild.name}")


    def cleanup(self) -> None:
        self._running = False
        if hasattr(self, "_monitor_task") and self._monitor_task:
            self._monitor_task.cancel()
        for t in list(getattr(self, "_dm_timeout_tasks", {}).values()):
            if t and not t.done():
                t.cancel()
        if hasattr(self, "_dm_timeout_tasks"):
            self._dm_timeout_tasks.clear()
        if hasattr(self, "_pending_dm"):
            self._pending_dm.clear()
        self._buffers.clear()
        self._last_packet_time.clear()
        self._users.clear()
