import asyncio
import audioop
import collections
import difflib
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

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

from outlands_wiki import normalize_uo_speech

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
    MIN_PHRASE_SEC = 0.45         # 450ms minimi per eliminare rumori brevi di microfono, respiri e colpi di tosse
    MAX_PHRASE_SEC = 10.0         # Fino a 10 secondi per titoli lunghi + nome artista
    START_RMS_ENERGY = 60         # Più reattivo grazie al pre-roll buffer da 300ms (cattura anche voci dolci/basse)
    CONTINUE_RMS_ENERGY = 35      # Sensibile per catturare anche l'ultima sillaba debole o sospiro
    PREROLL_CHUNKS = 15           # 300ms di pre-roll ring buffer (15 pacchetti da 20ms)

    WAKE_PATTERN = re.compile(
        r'(?<!\w)('
        # Varianti canoniche con flessioni finali complete (richiede vocale finale [oaie])
        r'franchin[oaie]'
        r'|franchin\b'               # forma tronca con word boundary esplicito
        r'|frankin[oaie]'           # frankino (k per ch)
        r'|francin[oaie]'           # francino (ch→c)
        r'|franghin[oaie]'          # franghino
        r'|franckin[oaie]'          # franckino
        r'|frannchin[oaie]'         # frannchino (nn)
        r'|frannkin[oaie]'          # frannkino
        r'|franceschin[oaie]'       # franceschino
        r'|franchinn[oaie]'         # finale doppia n
        r'|franchì'                 # accento romanesco vocativo esplicito
        # Varianti STT con split o spazio interno
        r'|fran\s+chin[oaie]'       # "fran chino"
        r'|frank\s+in[oaie]'        # "frank ino"
        r'|franco\s+in[oaie]'       # "franco ino" (frequente su STT italiana)
        r'|fran\s+k[iy]n[oaie]'     # "fran kino"
        r'|fran\s+cin[oaie]'        # "fran cino"
        r'|franchi\s*,?\s*n[oaie]'   # "franchi no", "franchi, no"
        r'|fra\s+chi\s*,?\s*n[oaie]'  # "fra chi no", "fra chi, no"
        r'|frankie\s*,?\s*n[oaie]'   # "frankie no"
        r'|franki\s*,?\s*n[oaie]'    # "franki no"
        r'|franc\s+in[oaie]'         # "franc ino"
        r'|franck\s+in[oaie]'        # "franck ino"
        r')(?!\w)',
        re.IGNORECASE
    )

    # Esclusione esplicita di parole italiane comuni foneticamente vicine che NON devono mai svegliare Franchino
    EXCLUDED_WAKE_WORDS = {
        'francia', 'frangia', 'francobollo', 'francobolli',
        'granchio', 'granchi', 'panchina', 'panchine', 'panchino', 'panchini',
        'bianchino', 'bianchini', 'rancido', 'rancidi',
        'mancino', 'mancini', 'lancino', 'pancino',
        'facchino', 'facchini', 'fachiro', 'fachiri',
        'francisco', 'francesco', 'francesca', 'francese', 'francesi',
        'francamente', 'franchezza', 'franchigia', 'franchigie',
        'franchi', 'franco',
        'fiaschino', 'fiaschini', 'fischietto', 'fiaschetto',
        'banchina', 'banchine', 'banchino', 'banchini', 'banchiere', 'banchieri',
        'macchina', 'macchine', 'cucchiaino', 'orecchino', 'orecchini',
        'forchetta', 'forchettone', 'chicchino', 'piccolino',
        'tranchino', 'tranchini', 'ranchino', 'ranchini', 'franklin', 'frankie',
        'fanchino', 'frenchino', 'fraccino',
    }

    # Lista per il fallback fuzzy token-per-token
    WAKE_WORDS = (
        "franchino", "frankino", "francino", "franchì",
        "franchini", "franchin", "franghino", "franceschino",
        "franckino", "franchina", "franchinn",
    )

    @classmethod
    def extract_wake_and_command(cls, text: str) -> tuple[bool, str, str]:
        """
        Riconosce con la massima accuratezza la wake word 'Franchino' e le sue varianti fonetiche/STT,
        evitando rigorosamente falsi positivi durante le normali conversazioni tra utenti.

        Applica tre livelli di detection in cascata:
          1. Regex rigorosa: varianti fonetiche + split-word STT ('franco ino', 'fran chino', ecc.)
          2. Fuzzy token matching selettivo: SequenceMatcher >= 0.84 su token >= 7 caratteri (non esclusi)
          3. Bigram join mirato: unisce coppie adiacenti solo se il prefisso è fran/franc/frank/franco
             e il suffisso è chino/kino/ino con SequenceMatcher >= 0.84

        Ritorna: (has_wake: bool, command_part: str, matched_wake_token: str)
        """
        if not text or not text.strip():
            return False, "", ""

        clean_text = text.strip()
        m = cls.WAKE_PATTERN.search(clean_text)
        wake_matched = None
        start = -1
        end = -1

        if m:
            wake_matched = m.group(0)
            start, end = m.span()
        else:
            # --- Livello 2: Fuzzy token-per-token (>= 0.84, token >= 7 chars, non in esclusioni) ---
            tokens = list(re.finditer(r'[a-zA-ZàèéìòùÀÈÉÌÒÙ]{7,}', clean_text, re.IGNORECASE))
            for tok_match in tokens:
                tok = tok_match.group(0).lower()
                if tok in cls.EXCLUDED_WAKE_WORDS:
                    continue
                ratio = difflib.SequenceMatcher(None, tok, "franchino").ratio()
                if ratio >= 0.84:
                    wake_matched = tok_match.group(0)
                    start, end = tok_match.span()
                    break

            # --- Livello 3: Bigram join mirato (solo prefissi fran/franc/frank/franco + suffissi chino/kino/ino) ---
            if not wake_matched:
                all_tokens = list(re.finditer(r'[a-zA-ZàèéìòùÀÈÉÌÒÙ]+', clean_text, re.IGNORECASE))
                for i in range(len(all_tokens) - 1):
                    t1 = all_tokens[i].group(0).lower()
                    t2 = all_tokens[i + 1].group(0).lower()
                    if not re.match(r'^(?:fran|franc|frank|franco)$', t1):
                        continue
                    if not re.match(r'^(?:chin[oaie]|kin[oaie]|in[oaie]|cin[oaie])$', t2):
                        continue
                    joined = t1 + t2
                    if len(joined) < 8 or joined in cls.EXCLUDED_WAKE_WORDS:
                        continue
                    ratio = difflib.SequenceMatcher(None, joined, "franchino").ratio()
                    if ratio >= 0.84:
                        wake_matched = all_tokens[i].group(0) + " " + all_tokens[i + 1].group(0)
                        start = all_tokens[i].start()
                        end = all_tokens[i + 1].end()
                        break

        if not wake_matched:
            return False, "", ""

        before = clean_text[:start].strip(" ,.!?:;-")
        after = clean_text[end:].strip(" ,.!?:;-")

        # Rimuove parole di saluto/filler all'inizio del comando
        filler_pat = re.compile(
            r'^(?:ehi|hey|ei|oh|o|ciao|senti|ascolta|allora|ma|vai|grande|caro|dimmi|scusa|dai|su|amo|ueh|uè|beh|insomma)\s*',
            re.IGNORECASE
        )
        before_clean = filler_pat.sub("", before).strip(" ,.!?:;-")

        if before_clean and after:
            cmd = f"{before_clean} {after}".strip(" ,.!?:;-")
        elif after:
            cmd = after
        elif before_clean:
            cmd = before_clean
        else:
            cmd = ""

        return True, cmd, wake_matched

    @classmethod
    def _clean_transcription(cls, text: str) -> str:
        """
        Pulisce e normalizza la trascrizione da Google Speech-to-Text (it-IT):
        - Applica le mappature fonetiche per termini gaming e UO Outlands (es. 'pat'/'pad' -> 'pet',
          'bild' -> 'build', 'teming' -> 'taming', 'temer' -> 'tamer', 'dreik' -> 'drake',
          'sleier' -> 'slayer', 'dexer' -> 'dexxer', 'aspet' -> 'aspect', 'dangeon' -> 'dungeon').
        - Normalizza comandi musicali e vocali ('skippa' -> 'skip', 'stoppa' -> 'stop', ecc.).
        """
        if not text:
            return ""
        norm = normalize_uo_speech(text)
        norm = re.sub(
            r'\b(?:franchi\s*,?\s*n[oaie]|fra\s+chi\s*,?\s*n[oaie]|frankie\s*,?\s*n[oaie]|franki\s*,?\s*n[oaie]|fran\s+chin[oaie]|franco\s+in[oaie]|frank\s+in[oaie]|franc\s+in[oaie]|franck\s+in[oaie])\b',
            'franchino',
            norm,
            flags=re.IGNORECASE
        )
        return norm

    def __init__(self, player: "GuildPlayer"):
        super().__init__(None)
        self.player = player
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 280
        self.recognizer.dynamic_energy_threshold = False
        self._stt_semaphore = asyncio.Semaphore(2)  # Limita a 2 chiamate STT concorrenti per risparmiare CPU/RAM
        self._loop_tick = 0
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

    def _prune_stale_buffers(self, now: float) -> None:
        """Rimuove dati inattivi e pulisce la memoria RAM per prevenire accumuli e memory leak."""
        stale_keys = [
            k for k, t in self._last_packet_time.items()
            if now - t > 25.0 and k not in self._buffers
        ]
        for k in stale_keys:
            self._last_packet_time.pop(k, None)
            self._preroll_buffers.pop(k, None)
            self._users.pop(k, None)

        expired_wakes = [
            uid for uid, (t, _) in self._pending_wake.items()
            if now - t > 12.0
        ]
        for uid in expired_wakes:
            self._pending_wake.pop(uid, None)

        expired_dms = [
            uid for uid, (t, _) in self._pending_dm.items()
            if now - t > 40.0
        ]
        for uid in expired_dms:
            self._pending_dm.pop(uid, None)

        if len(self._user_cache) > 200:
            self._user_cache.clear()

    async def _monitor_speech_loop(self):
        """Controlla continuamente i buffer vocali di tutti i partecipanti nel canale con ciclo rapido a 40ms."""
        try:
            while self._running:
                try:
                    await asyncio.sleep(0.04)
                    now = time.monotonic()

                    self._loop_tick = (self._loop_tick + 1) % 50
                    if self._loop_tick == 0:
                        self._prune_stale_buffers(now)

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
                    break
                except Exception as inner_err:
                    logger.debug(f"Errore iterazione monitor vocale (recupero automatico): {inner_err}")
                    await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Errore nel loop di monitoraggio vocale: {e}", exc_info=True)

    async def _process_utterance(self, key: int, speaker_obj: object, stereo_pcm_48k: bytes):
        """
        Elabora l'audio catturato con la massima efficienza hardware e precisione:
        1. VAD Gate: scarta esclusivamente rumori di fondo impercettibili o silenzio puro senza mai tagliare la voce umana.
        2. Mixdown stereo mono nativo a 48kHz.
        3. Downsample a 16kHz ad alta efficienza: riduce del 66% RAM, CPU FLAC e payload di rete verso Google Speech API.
        4. Rimozione DC Bias e Soft AGC ottimizzati su campioni a 16kHz.
        5. Padding antirimbalzo sui transienti FLAC.
        6. Chiamata Google Speech API protetta da semaforo di concorrenza.
        7. Valutazione intelligente delle ipotesi STT per evitare attivazioni spurie.
        """
        try:
            # 1. VAD Gate / Controllo silenzio puro e fruscii (soglia a 35 RMS e 250 peak per non tagliare mai la voce umana)
            avg_rms = audioop.rms(stereo_pcm_48k, self.SAMPLE_WIDTH)
            max_amp = audioop.max(stereo_pcm_48k, self.SAMPLE_WIDTH)
            if avg_rms < 35 and max_amp < 250:
                logger.debug(f"🔇 Audio da {key} ignorato: silenzio o rumore impercettibile ({avg_rms} RMS, {max_amp} max amp)")
                return

            # 2. Mixdown stereo -> mono
            mono_48k = audioop.tomono(stereo_pcm_48k, self.SAMPLE_WIDTH, 0.5, 0.5)

            # 3. Downsample a 16kHz mono (lo standard audio di Google Speech API: 3x più leggero e veloce!)
            target_rate = 16000
            mono_16k, _ = audioop.ratecv(mono_48k, self.SAMPLE_WIDTH, 1, self.SAMPLE_RATE, target_rate, None)

            # 4. Rimozione DC Bias (offset continuo)
            dc_bias = audioop.avg(mono_16k, self.SAMPLE_WIDTH)
            if abs(dc_bias) > 25:
                mono_16k = audioop.bias(mono_16k, self.SAMPLE_WIDTH, -dc_bias)

            # 5. Soft AGC / Normalizzazione del volume della voce
            max_amp = audioop.max(mono_16k, self.SAMPLE_WIDTH)
            if max_amp > 200:
                target_peak = 26500
                if max_amp < target_peak:
                    gain = min(5.5, target_peak / max_amp)
                    if gain > 1.05:
                        mono_16k = audioop.mul(mono_16k, self.SAMPLE_WIDTH, gain)
                elif max_amp > 32000:
                    gain = 30000.0 / max_amp
                    mono_16k = audioop.mul(mono_16k, self.SAMPLE_WIDTH, gain)

            # 6. Padding di silenzio (60ms a 16kHz = 960 campioni = 1920 byte)
            pad = b"\x00" * (target_rate * self.SAMPLE_WIDTH // 16)
            padded_mono_16k = pad + mono_16k + pad
            audio_data = sr.AudioData(padded_mono_16k, target_rate, self.SAMPLE_WIDTH)

            # 7. Riconoscimento vocale regolato dal semaforo anti-sovraccarico CPU
            async with self._stt_semaphore:
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

        # Arricchimento candidati con normalizzazione fonetica specifica per l'italiano e gaming terms
        all_candidates = []
        for cand in candidates:
            if cand not in all_candidates:
                all_candidates.append(cand)
            clean_cand = self._clean_transcription(cand)
            if clean_cand and clean_cand not in all_candidates:
                all_candidates.append(clean_cand)

        user = await self._resolve_user_object(key, speaker_obj)
        uid = getattr(user, "id", 0)
        now = time.monotonic()

        # Verifica se l'utente ha una sessione attiva (pending wake follow-up di 10s o dettatura DM)
        in_active_session = (uid in self._pending_wake and now - self._pending_wake[uid][0] <= 10.0) or (uid in self._pending_dm)

        # Anti-Feedback Loop: Se Franchino sta parlando in voce (TTS), ignora solo se è una vera eco priva di comandi e del nome Franchino.
        # Se l'utente chiama Franchino (es. "Franchino ..."), impartisce un comando di interruzione, o è in sessione attiva: interrompi subito il TTS ed esegui!
        is_bot_speaking = getattr(self.player, "is_speaking_tts", False)
        if callable(is_bot_speaking):
            try:
                is_bot_speaking = bool(is_bot_speaking())
            except Exception:
                is_bot_speaking = False
        else:
            is_bot_speaking = bool(is_bot_speaking)

        if is_bot_speaking:
            has_wake = any(self.extract_wake_and_command(c)[0] for c in all_candidates)
            interrupt_terms = (
                "stop", "stoppa", "ferma", "fermati", "basta", "zitto", "silenzio",
                "pausa", "skip", "salta", "riavviati", "riavvia", "reset", "sbloccati"
            )
            has_interrupt = any(
                any(w in c.lower() for w in interrupt_terms)
                for c in all_candidates
            )
            if has_wake or has_interrupt or in_active_session:
                try:
                    if self.player.voice_client:
                        if hasattr(self.player.voice_client, "stop_playing"):
                            self.player.voice_client.stop_playing()
                        else:
                            self.player.voice_client.stop()
                except Exception:
                    pass
            else:
                logger.debug(f"🔇 Audio da {key} ignorato: Franchino sta parlando in TTS (anti-feedback loop)")
                return

        selected_text = None
        detected_cmd = None
        matched_wake = None

        # Log tutte le alternative STT per facilitare il debug (max 3)
        if len(all_candidates) > 1:
            logger.debug(f"🎙️ STT alternatives [{user.display_name}]: {all_candidates[:3]}")

        # Scansiona le alternative acustiche con priorità all'ipotesi primaria (candidates[0])
        # per non attivarsi a sproposito su rumori o conversazioni ordinarie.
        wake_hits: list[tuple[str, str, str, float]] = []  # (cand, cmd, wake_tok, score)
        uo_priority_terms = re.compile(
            r'\b(?:pet|pets|build|builds|taming|tamer|drake|slayer|dexxer|aspect|'
            r'mastery\s+chain|codex|dungeon|ostard|wiki|outlands|bestiario)\b',
            re.IGNORECASE
        )
        music_bot_terms = re.compile(
            r'\b(?:metti|riproduci|suona|play|skip|stop|pausa|volume|loop|shuffle|coda)\b',
            re.IGNORECASE
        )
        question_terms = re.compile(
            r'\b(?:miglior|migliori|quali|quale|come|dove|guida|consigli|consigliami|spiegami|info)\b',
            re.IGNORECASE
        )

        top_cand = candidates[0]
        top_has_wake, _, _ = self.extract_wake_and_command(top_cand)
        top_clean_cand = self._clean_transcription(top_cand)
        top_clean_has_wake, _, _ = self.extract_wake_and_command(top_clean_cand)
        primary_has_wake = top_has_wake or top_clean_has_wake

        # Se l'ipotesi primaria è un tipico intercalare colloquiale non rivolto a Franchino
        # (es. "Fra chi...", "Fra, chi ha il boost", "Fra chi gioca"), e NESSUN candidato alternativo contiene Franchino,
        # blocca i candidati secondari per evitare false attivazioni.
        colloquial_anti_wake = re.compile(
            r'\b(?:fra\s*,?\s*chi\s+(?:ha|c[\x27\']è|gioca|vuole|viene|va|sa|dice|sta|è|prende|serve|boosta|lo|la|li|le)\b|ho\s+(?:cento|\d+)\s+franchi|i\s+franchi\s+svizzeri)\b',
            re.IGNORECASE
        )
        if not primary_has_wake and colloquial_anti_wake.search(top_cand):
            has_any_wake = any(self.extract_wake_and_command(c)[0] for c in all_candidates)
            if not has_any_wake:
                all_candidates = []

        for idx, cand in enumerate(all_candidates):
            has_w, cmd, w_tok = self.extract_wake_and_command(cand)
            if has_w:
                norm_cmd = self._clean_transcription(cmd)
                score = float(len(norm_cmd))

                # Bonus per l'ipotesi primaria di Google Speech
                if primary_has_wake and (cand == top_cand or cand == top_clean_cand):
                    score += 100.0

                # Termini prioritari UO / Bot / Domande
                if uo_priority_terms.search(norm_cmd):
                    score += 50.0
                if music_bot_terms.search(norm_cmd):
                    score += 40.0
                if question_terms.search(norm_cmd):
                    score += 20.0

                # Se l'ipotesi primaria NON conteneva wake word, le alternative successive
                # sono ammesse solo se provengono dai primi candidati e hanno un intento legittimo verso il bot
                if not primary_has_wake:
                    cmd_clean_low = norm_cmd.lower().strip()
                    has_command_intent = (
                        not norm_cmd  # Richiamo diretto al solo nome "Franchino"
                        or uo_priority_terms.search(norm_cmd)
                        or music_bot_terms.search(norm_cmd)
                        or question_terms.search(norm_cmd)
                        or any(w in cmd_clean_low for w in (
                            "ci sei", "ci senti", "mi senti", "rispondi", "sei vivo", "sei sveglio", "batti un colpo",
                            "funzioni", "funziona", "tutto bene", "come va",
                            "ciao", "salve", "buongiorno", "buonasera", "grazie", "grande", "ti voglio bene",
                            "comandi", "aiuto", "help", "favola", "storia", "partita", "partite", "sport",
                            "boost", "/boost", "bassboost", "bassi", "effetto", "suono",
                            "stop", "salta", "skip", "pausa", "riprendi", "volume", "riavviati", "reset",
                            "non ti sento", "si sente male", "ti sento a scatti", "parla più forte", "alza la voce",
                            "saluta", "brindisi", "abbraccia", "carica", "auguri", "buonanotte", "cinque", "troll",
                            "sposta", "caccia", "muta", "smuta", "ruolo", "soprannome", "timeout", "libera", "pulisci", "cancella",
                            "vorrei", "puoi", "canta", "fammi", "radio", "preferiti", "salva"
                        ))
                        or bool(re.search(
                            r'^(?:cosa|che|chi|come|dove|quando|perch[eé]|quali|quale|quanto|spiegami|dimmi|parlami|raccontami|metti|suona|play|attiva|alza|abbassa|togli|fammi|puoi|fammi\s+sentire)\b|\?$',
                            cmd_clean_low
                        ))
                    )
                    # Escludi categoricamente frasi conversazionali o chiacchiere che contengono 'boost' o 'bassi'
                    is_boost_chatter = False
                    if re.search(r'\b(?:boost(?:a|are|ato)?|bass[io])\b', norm_cmd, re.IGNORECASE):
                        is_boost_chatter = bool(re.search(
                            r'\b(?:chi\s+(?:ha|c\'ha|usa|fa|prende|vuole|boosta)|avete|hai|abbiamo|hanno|'
                            r'del\s+server|al\s+server|per\s+il|serve\s+il|prezzi|volumi|toni)\b',
                            norm_cmd,
                            re.IGNORECASE
                        ))
                    if not has_command_intent or is_boost_chatter:
                        continue
                    score += 10.0

                wake_hits.append((cand, norm_cmd, w_tok, score))

        if wake_hits:
            wake_hits.sort(key=lambda x: x[3], reverse=True)
            raw_cand, detected_cmd, matched_wake, _ = wake_hits[0]
            selected_text = self._clean_transcription(raw_cand)

        if selected_text:
            logger.info(f"🎙️ [{user.display_name}] ha chiamato Franchino ('{matched_wake}') → frase: '{selected_text}' | comando: '{detected_cmd}'")
        elif in_active_session:
            # L'utente è in sessione attiva (aveva appena chiamato Franchino e sta completando il comando)
            # Seleziona il miglior candidato tra tutte le trascrizioni e alternative fonetiche
            best_cand = candidates[0]
            best_score = float(len(best_cand))
            for cand_item in all_candidates:
                c_clean = self._clean_transcription(cand_item)
                sc = float(len(c_clean))
                if uo_priority_terms.search(c_clean):
                    sc += 50.0
                if music_bot_terms.search(c_clean):
                    sc += 40.0
                if question_terms.search(c_clean):
                    sc += 20.0
                if sc > best_score:
                    best_score = sc
                    best_cand = c_clean
            selected_text = self._clean_transcription(best_cand)
            detected_cmd = selected_text.strip()
            logger.info(f"🔗 [{user.display_name}] sessione attiva (completamento comando): '{selected_text}'")
        else:
            # NESSUN RICHIAMO A FRANCHINO: Ignora totalmente! Non risponde e non interrompe la conversazione.
            logger.debug(f"🔇 [{user.display_name}] audio ignorato (nessun richiamo a Franchino): '{candidates[0]}'")
            return


        try:
            await self._handle_transcribed_text(user, selected_text, detected_cmd=detected_cmd)
        except Exception as e:
            logger.error(f"❌ Errore critico gestione comando vocale '{selected_text}' da {user.display_name}: {e}", exc_info=True)
            await self._notify_channel(f"⚠️ Si è verificato un errore durante l'elaborazione del comando: `{e}`")

    async def _handle_transcribed_text(self, user, text: str, detected_cmd: Optional[str] = None):
        """Analizza il testo trascritto ed esegue il comando per qualsiasi utente del canale, SOLTANTO se è stato chiamato Franchino."""
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
                # Se l'utente ha ripetuto "Franchino...", puliamo il wake word
                has_w, w_cmd, _ = self.extract_wake_and_command(msg_body)
                if has_w:
                    msg_body = w_cmd
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

        command_part = self._clean_transcription(detected_cmd) if detected_cmd is not None else None

        if command_part is None:
            has_w, cmd, _ = self.extract_wake_and_command(text)
            if has_w:
                command_part = self._clean_transcription(cmd)

        # Se l'utente era in sessione di attesa (aveva detto "Franchino" o "Franchino metti" e ora dice il comando/brano):
        if uid in self._pending_wake:
            wake_time, pending_prefix = self._pending_wake.pop(uid)
            if now - wake_time <= 10.0:
                raw_followup = command_part if command_part else text.strip()
                raw_followup = self._clean_transcription(raw_followup)
                rf_low = raw_followup.lower().strip()
                # Se l'utente ha detto un comando di stop, cancellazione o liveness, non anteporre il prefisso musicale
                if any(rf_low.startswith(w) for w in ("stop", "stoppa", "basta", "ferma", "fermati", "zitto", "silenzio", "muto", "spegni", "annulla", "niente", "lascia stare", "taci", "smettila")):
                    command_part = raw_followup
                else:
                    command_part = f"{pending_prefix} {raw_followup}".strip() if pending_prefix else raw_followup
                logger.info(f"🔗 Continuazione comando vocale per {user.display_name}: '{command_part}'")

        # NESSUN RICHIAMO A FRANCHINO: Ignora totalmente! Non risponde mai se non viene chiamato per nome.
        if command_part is None:
            logger.debug(f"🔇 Ignorato comando vocale senza richiamo Franchino da {user.display_name}: '{text}'")
            return

        cmd_lower = command_part.lower().strip()

        # 1. Se l'utente ha detto solo "Franchino" (per testare la presenza o prima del comando):
        if not cmd_lower:
            self._pending_wake[uid] = (now, "")
            await self._notify_channel(
                f"🎙️✨ **{user.display_name}** ha chiamato Franchino in console! *(Microfono aperto, chiedimi un brano o una domanda!)*"
            )
            asyncio.create_task(
                self.player.speak_tts("Magia! Dimmi tutto, sono qui in console!", wait=False, interrupt_music=True)
            )
            return

        # 2. Annullamento / Cancellazione / "Lascia stare"
        if cmd_lower in (
            "lascia stare", "lascia perdere", "niente", "non fa niente",
            "fa niente", "annulla", "come non detto", "lascia", "nessuna", "nulla"
        ):
            await self._notify_channel(
                f"🎙️ **Franchino**: Ricevuto **{user.display_name}**, nessun problema! Sono sempre qui in console quando vuoi. ✨"
            )
            asyncio.create_task(self.player.speak_tts("Va bene, sono sempre qui se hai bisogno!", wait=False, interrupt_music=True))
            return

        # 3. Ringraziamenti e saluti amichevoli
        if re.match(r'^(?:grazie(?:\s+mille|\s+franchino|\s+mille\s+franchino)?|grande\s+franchino|ti\s+ringrazio|bravo\s+franchino|ottimo\s+franchino)$', cmd_lower):
            await self._notify_channel(
                f"🎙️✨ **Franchino**: Prego **{user.display_name}**! Sempre un piacere farvi divertire in console! 🚀"
            )
            asyncio.create_task(self.player.speak_tts(f"Prego {user.display_name}! La magia della notte è sempre accesa!", wait=False, interrupt_music=True))
            return

        # 4. Feedback audio / microfono / qualità connessione
        audio_feedback_pat = re.compile(
            r'\b(?:non\s+(?:ti|si)\s+sente|si\s+sente\s+(?:male|basso|piano)|lagghi|'
            r'ti\s+sento\s+(?:a\s+scatti|male|piano|basso)|non\s+ti\s+sento|non\s+ti\s+sentiamo|'
            r'audio\s+a\s+scatti|non\s+va\s+(?:l[\x27\']audio|la\s+musica|niente)|'
            r'non\s+sento\s+(?:niente|nulla|la\s+musica)|'
            r'parla\s+più\s+forte|alza\s+la\s+voce|balbetti)\b',
            re.IGNORECASE
        )
        if audio_feedback_pat.search(cmd_lower):
            await self._notify_channel(
                f"🎙️ **Franchino**: Ricevuto **{user.display_name}**! Se l'audio balbetta o lagga, dì pure *\"Franchino riavviati\"* per resettare la console in un attimo! 🔄"
            )
            asyncio.create_task(self.player.speak_tts("Ti sento forte e chiaro! Se mi senti a scatti, dimmi pure Franchino riavviati!", wait=False, interrupt_music=True))
            return

        # 5. Comandi parziali che indicano "voglio ascoltare musica" ma non hanno ancora il titolo:
        _play_triggers = {
            "metti", "mette", "mettimi", "metti su", "mettimi su",
            "riproduci", "suona", "suonami",
            "play", "fammi sentire", "fammi ascoltare",
            "avvia", "cerca", "cercami", "fai partire",
            "fai sentire", "metti qualcosa", "metti un po' di musica",
            "su", "dai su", "andiamo", "su dai", "via",
        }
        if cmd_lower in _play_triggers:
            self._pending_wake[uid] = (now, "metti")
            asyncio.create_task(self.player.speak_tts("Dimmi il titolo!", wait=False, interrupt_music=True))
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

        # 6. Comando LIVENESS / PRESENZA VOCALE ("ci senti?", "ci sei?", "sei vivo?", "mi senti?", "rispondi", "batti un colpo")
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

        # 7. Controllo discorso in terza persona: se gli utenti stanno parlando *di* Franchino tra loro
        # anziché rivolgersi a lui direttamente (es. "secondo me Franchino non funziona", "anche Franchino lagga")
        is_direct_action = any(
            cmd_lower.startswith(p)
            for p in (
                "metti", "suona", "play", "stop", "stoppa", "ferma", "pausa", "comandi",
                "cosa", "che", "chi", "come", "dove", "quando", "perch", "quanto", "quale", "quali",
                "salva", "esci", "dimmi", "spiegami", "parlami", "racconta",
                "boost", "/boost", "bassi", "favola", "partita", "partite", "sport",
                "saluta", "brindisi", "script", "razor", "wiki", "guida", "volume", "alza", "abbassa",
                "riavviati", "riavvia", "reset", "sbloccati", "svegliati", "non", "rispondi", "funziona",
                "funzioni", "batti", "fatti", "di", "dì", "prova",
                "sposta", "porta", "caccia", "scollega", "muta", "smuta", "ruolo", "timeout", "libera",
                "pulisci", "cancella", "crea", "chiudi", "apri", "espelli", "banna", "sbanna",
                "abbraccia", "carica", "auguri", "buonanotte", "cinque", "troll",
                "manda", "invia", "scrivi", "canta", "ascolta", "fammi", "posso", "puoi", "radio", "preferiti",
                "ora", "orario", "meteo", "tempo", "timer", "promemoria", "sveglia", "calcola", "lancia", "tira", "testa", "barzelletta", "battuta"
            )
        ) or cmd_lower.endswith("?") or bool(re.search(r'\b(?:ti|te|tu|mi|ci|noi|vi)\b', cmd_lower))

        if not is_direct_action:
            third_person_pat = re.compile(
                r'\b(?:parlavo\s+di|dicevo\s+di|si\s+parla\s+di)\s+(?:franchino|frankino|francino)\b|'
                r'\b(?:secondo\s+me|anche|pure|credo\s+che|penso\s+che|dicevo\s+che|dicono\s+che|dice\s+che)\s+'
                r'(?:franchino|frankino|francino)\s+'
                r'(?:è|ha|fa|non|sembra|era|dice|va|lagga|crasha|funziona|esiste)\b',
                re.IGNORECASE
            )
            if third_person_pat.search(lower_text) or third_person_pat.search(cmd_lower):
                logger.info(f"🗣️ Rilevata conversazione in terza persona su Franchino da {user.display_name}: '{lower_text}' — Nessuna falsa attivazione.")
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
                    for prefix in ("stop", "stoppa", "ferma", "fermati", "basta", "zitto", "stai zitto", "spegni", "silenzio", "muto", "taci", "smettila")
                )
            )
            or any(
                phrase in cmd_lower
                for phrase in ("ferma tutto", "basta parlare", "stai zitto", "fai silenzio", "stop tutto", "spegni tutto", "stop musica", "basta musica")
            )
        )
        if is_stop_cmd:
            self.player.stop()
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

        # 0B-sexies. Ricerca Sport, Palinsesto & Partite di Stasera (Calcio, Basket, F1, MotoGP, Tennis, ecc.)
        # Esempi: "Franchino mi dici i match di stasera?", "Franchino chi gioca stasera?", "Franchino chi gioca in Serie A?",
        # "Franchino prossima gara F1", "Franchino streaming NBA", "Franchino cerca la partita del Milan"
        sport_detected = False
        sport_query = ""

        # Match ricerca puntuale team / streaming
        specific_match = re.search(
            r"(?:cerca\s+(?:la\s+)?partita|dove\s+(?:fanno\s+vedere|posso\s+vedere|vedere|trasmettono)|streaming\s+(?:della\s+)?partita|trova\s+(?:la\s+)?partita|partita\s+(?:del|della|di|dei)|streaming\s+di|guarda\s+(?:la\s+)?partita)\s+(.+)",
            cmd_lower
        )
        if specific_match:
            sport_detected = True
            sport_query = specific_match.group(1).strip(" ,.!?:;-")
        else:
            # Pattern palinsesto, oggi/stasera, leghe e motori
            schedule_patterns = (
                r"(?:mi\s+dici\s+(?:i\s+|le\s+)?|dimmi\s+(?:i\s+|le\s+)?|cosa\s+c['’]?\s*e\s+|ci\s+sono\s+)?(?:quali|che)?\s*(?:match|partite|incontri|gare|sfide)\s+(?:ci\s+sono\s+)?(?:di\s+)?(?:stasera|oggi)(?:\s+(?:a|in|nella)\s+[a-zA-Z0-9_]+)*",
                r"chi\s+gioca(?:\s+stasera|\s+oggi)?(?:\s+(?:a|in|nella)\s+[a-zA-Z0-9_]+)*",
                r"chi\s+scende\s+in\s+campo(?:\s+stasera|\s+oggi)?",
                r"(?:quali|che)\s+(?:partite|match|incontri|gare)\s+(?:ci\s+sono|fanno|trasmettono)",
                r"(?:partite|match|incontri)\s+(?:di\s+)?(?:champions|serie\s+a|serie\s+b|premier|la\s+liga|liga|bundesliga|nba|basket|tennis|eurolega|serie\s+a\s+basket)(?:\s+stasera|\s+oggi)?",
                r"(?:partite|match|incontri)\s+(?:di\s+)?(?:oggi|stasera)",
                r"(?:prossima\s+gara|prossimo\s+gp|prossimo\s+gran\s+premio|quando\s+corre|orari\s+(?:della\s+)?|quando\s+c['’]?\s*e\s+(?:il\s+)?gp)\s*(?:formula\s*1|f1|motogp|moto\s*gp|motori|ferrari)?",
                r"(?:quando\s+gareggia|quando\s+corrono)\s+(?:la\s+)?(?:f1|formula\s*1|motogp|ferrari)",
                r"(?:palinsesto|partite\s+live|match\s+live|sport\s+live|palinsesto\s+sportivo)",
            )
            for spat in schedule_patterns:
                if re.search(spat, cmd_lower):
                    sport_detected = True
                    sport_query = cmd_lower
                    break

            if not sport_detected and any(w in cmd_lower for w in ("partita", "streaming", "formula 1", "motogp", "f1", "nba", "chi gioca", "partite live")):
                sports_kw_match = re.search(r"(?:partita|streaming|evento|sport)\s+(?:del|della|di|per)?\s*(.+)", cmd_lower)
                if sports_kw_match:
                    sport_detected = True
                    sport_query = sports_kw_match.group(1).strip(" ,.!?:;-")

        if sport_detected and sport_query:
            from sports_scraper import sports_scraper
            from central_hub import hub

            res = await sports_scraper.search_event(sport_query)
            embed, view, voice_msg = sports_scraper.build_sports_embed(res)

            await self._notify_channel(
                message=f"🎙️⚽ **Franchino Sports ({user.display_name}):**\n> *\"{voice_msg}\"*",
                embed=embed,
                view=view
            )
            asyncio.create_task(self.player.speak_tts(voice_msg, wait=False, interrupt_music=True))

            asyncio.create_task(
                hub.archive_interaction(
                    user_id=str(user.id),
                    user_name=user.display_name,
                    raw_query=f"voce_partita {sport_query}",
                    intent="sports_stream",
                    ai_response=res.get("best_match", {}).get("title", "Palinsesto Sport"),
                    voice_summary=voice_msg,
                    metadata={"query": sport_query}
                )
            )
            return

        # 0B-septies. INTERAZIONI SOCIALI CON GLI UTENTI DEL CANALE
        # (Saluto, Brindisi, Abbraccio, Carica, Cinque, Auguri, Buonanotte, Benvenuto, Troll, Amore)
        # Esempi: "Franchino fai un brindisi per Giulia", "Franchino dai la carica a Luca", "Franchino saluta tutti"
        from interactions import handle_voice_interaction
        if await handle_voice_interaction(self.player, user, command_part, self._notify_channel):
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

        # 0C-bis-2. Richiesta SCRIPT RAZOR ("script per magery", "dammi lo script di lockpicking", ecc.)
        from razor_scripts import is_script_question, build_razor_scripts_response
        is_script, script_skill = is_script_question(command_part)
        if is_script:
            target_skill = script_skill if script_skill and script_skill != "lista" else "lista"
            logger.info(f"📜 Richiesta script Razor per {target_skill} da {user.display_name}: '{command_part}'")
            embed, view, file, voice_ans, code = build_razor_scripts_response(target_skill)
            if embed:
                preview_text = ""
                if code:
                    clean_code = code.replace("```", "'''")
                    if len(clean_code) <= 1200:
                        preview_text = f"\n```razor\n{clean_code}\n```"
                    else:
                        preview_text = f"\n```razor\n{clean_code[:1100]}\n# ... [File completo allegato sotto!] ...\n```"
                skill_header = f"**{target_skill.title()}**" if target_skill != "lista" else "**UO Outlands**"
                await self._notify_channel(
                    f"📜 **{user.display_name}** ha chiesto gli script Razor per {skill_header} — Ecco tutto pronto!{preview_text}",
                    embed=embed,
                    view=view,
                    file=file,
                )
                if voice_ans:
                    asyncio.create_task(
                        self.player.speak_tts(voice_ans, wait=False, interrupt_music=True, keep_paused_after=False)
                    )
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=command_part,
                        intent="razor_script",
                        ai_response=f"Script Razor per {target_skill}",
                        voice_summary=voice_ans,
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"skill": target_skill},
                    )
                )
                return

        # 0C-bis-3. Richiesta Formula Matematica & Super Matematico Jack (/boost jack)
        from jack_math_engine import jack_engine, hardware_optimizer
        jack_check = jack_engine.classify(command_part)
        if jack_check.intent == "jack_math_boost" and jack_check.probability >= 0.25:
            logger.info(f"🧠 Richiesta Formula Super Matematico Jack da {user.display_name}: '{command_part}'")
            telem = hardware_optimizer.get_hardware_telemetry()
            embed = discord.Embed(
                title="🧠 Formula Matematica Jack & Ottimizzazione Hardware ARM",
                description=(
                    "Franchino opera con il motore di intelligenza analitica **Super Matematico Jack**:\n\n"
                    "$$\\mathcal{S}(\\text{Intent}, q) = w_{\\text{wake}} \\cdot \\phi_{\\text{wake}} + "
                    "w_{\\text{phonetic}} \\cdot \\sigma_{\\text{jaro}} + w_{\\text{domain}} \\cdot \\text{BM25}(q, D) + "
                    "w_{\\text{action}} \\cdot \\alpha_{\\text{syntax}} - \\lambda_{\\text{noise}}$$\n\n"
                    f"**Classificazione Istantanea di '{command_part}':**\n"
                    f"`{jack_check.to_math_summary()}`\n\n"
                    "**Distribuzione Probabilistica Softmax:**\n"
                    "$$\\mathcal{P}(\\text{Intent} \\mid q) = \\frac{e^{\\beta \\cdot \\mathcal{S}(\\text{Intent}, q)}}{\\sum_k e^{\\beta \\cdot \\mathcal{S}(k, q)}}$$"
                ),
                color=0xF1C40F,
            )
            embed.add_field(
                name="⚖️ Pesi & Parametri Jack",
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
            voice_jack = "Magia ragazzi! Ecco la formula del super matematico Jack! Ho ottimizzato la console con Okapi BM25 e Jaro-Winkler per 2 core ARM!"
            await self._notify_channel(
                f"🧠 **{user.display_name}** ha invocato la formula del **Super Matematico Jack**! Ecco i dettagli completi:",
                embed=embed,
            )
            asyncio.create_task(
                self.player.speak_tts(voice_jack, wait=False, interrupt_music=True, keep_paused_after=False)
            )
            from central_hub import hub
            asyncio.create_task(
                hub.archive_interaction(
                    user_id=str(getattr(user, "id", 0)),
                    user_name=user.display_name,
                    raw_query=command_part,
                    intent="jack_math_boost",
                    ai_response="Formula Matematica Jack & Ottimizzazione ARM",
                    voice_summary=voice_jack,
                    channel_name=self.player.text_channel.name if self.player.text_channel else None,
                    metadata={"query": command_part, "telemetry": telem},
                )
            )
            return

        # 0C-bis-4. SUITE ASSISTENTE INTELLIGENTE VOCALE (Ora, Meteo, Timer, Calcoli, Dadi/Moneta, Barzellette)
        from smart_assistant import (
            detect_smart_assistant_intent,
            get_current_time_info,
            get_weather_info,
            timer_manager,
            format_duration_italian,
            parse_timer_request,
            is_timer_status_query,
            is_timer_cancel_query,
            evaluate_math_expression,
            flip_coin,
            roll_dice,
            choose_option,
            get_vocalist_joke,
        )

        smart_type = detect_smart_assistant_intent(command_part)
        if smart_type:
            logger.info(f"🤖 Suite Assistente Intelligente ({smart_type}) da {user.display_name}: '{command_part}'")
            if smart_type == "time":
                time_data = get_current_time_info(command_part)
                await self._notify_channel(
                    f"🕒 **{user.display_name}** ha chiesto l'orario a Franchino:",
                    embed=time_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        time_data["voice_reply"],
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=command_part,
                        intent="time_query",
                        ai_response=time_data["chat_reply"],
                        voice_summary=time_data["voice_reply"],
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"time": time_data["time_str"]},
                    )
                )
                return

            elif smart_type == "weather":
                weather_data = await get_weather_info(command_part)
                await self._notify_channel(
                    f"🌤️ **{user.display_name}** ha chiesto le previsioni meteo:",
                    embed=weather_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        weather_data["voice_reply"],
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=command_part,
                        intent="weather_query",
                        ai_response=weather_data["chat_reply"],
                        voice_summary=weather_data["voice_reply"],
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"city": weather_data.get("city")},
                    )
                )
                return

            elif smart_type == "timer":
                guild_id = self.player.guild.id if self.player.guild else 0
                if is_timer_status_query(command_part):
                    timers = timer_manager.get_timers(guild_id, user_id=user.id)
                    if not timers:
                        timers = timer_manager.get_timers(guild_id)
                    if not timers:
                        ans = f"Non c'è nessun timer attivo al momento {user.display_name}!"
                        await self._notify_channel(f"⏰ **{user.display_name}**: Nessun timer attivo.")
                        asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                    else:
                        t = timers[0]
                        rem_str = format_duration_italian(int(t.remaining_seconds))
                        ans = f"Mancano ancora {rem_str} al timer per {t.label} ragazzi!"
                        await self._notify_channel(f"⏰ **{user.display_name}**: Mancano ancora **{rem_str}** al timer per **{t.label}**.")
                        asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                    return

                elif is_timer_cancel_query(command_part):
                    cancelled = timer_manager.cancel_timers(guild_id, user_id=user.id)
                    if not cancelled:
                        cancelled = timer_manager.cancel_timers(guild_id)
                    if cancelled:
                        labels = ", ".join([c.label for c in cancelled])
                        ans = f"Timer per {labels} cancellato con successo ragazzi!"
                        await self._notify_channel(f"⏰ **{user.display_name}**: Timer per **{labels}** cancellato.")
                        asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                    else:
                        ans = f"Nessun timer attivo da cancellare {user.display_name}!"
                        await self._notify_channel(f"⏰ **{user.display_name}**: Nessun timer da cancellare.")
                        asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                    return

                else:
                    duration, label = parse_timer_request(command_part)
                    if duration <= 0:
                        duration = 300
                    dur_str = format_duration_italian(duration)
                    active_timer = timer_manager.start_timer(
                        guild_id=guild_id,
                        channel=self.player.text_channel,
                        user=user,
                        duration_seconds=duration,
                        label=label,
                        player=self.player,
                    )
                    v_msg = f"Timer di {dur_str} per {label} impostato ragazzi! Vi avviso appena scade! Magia!"
                    embed = discord.Embed(
                        title="⏰ Timer Impostato da Console",
                        description=f"🔔 Timer di **{dur_str}** impostato per **{label}**!\n\n🎙️ *\"{v_msg}\"*",
                        color=0x3498DB,
                    )
                    embed.add_field(name="Durata", value=f"`{dur_str}`", inline=True)
                    embed.add_field(name="Motivo", value=f"`{label}`", inline=True)
                    embed.add_field(name="Impostato da", value=user.display_name, inline=True)
                    embed.set_footer(text="Franchino Bot • Timer & Promemoria")
                    await self._notify_channel(
                        f"⏰ **{user.display_name}** ha impostato un timer in console:",
                        embed=embed,
                    )
                    asyncio.create_task(
                        self.player.speak_tts(v_msg, wait=False, interrupt_music=True, keep_paused_after=False)
                    )
                    from central_hub import hub
                    asyncio.create_task(
                        hub.archive_interaction(
                            user_id=str(getattr(user, "id", 0)),
                            user_name=user.display_name,
                            raw_query=command_part,
                            intent="timer_set",
                            ai_response=f"Timer di {dur_str} per {label}",
                            voice_summary=v_msg,
                            channel_name=self.player.text_channel.name if self.player.text_channel else None,
                            metadata={"duration": duration, "label": label},
                        )
                    )
                    return

            elif smart_type == "math":
                math_data = evaluate_math_expression(command_part)
                await self._notify_channel(
                    f"🧮 **{user.display_name}** ha chiesto un calcolo a Franchino:",
                    embed=math_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        math_data["voice_reply"],
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=command_part,
                        intent="math_calc",
                        ai_response=math_data["chat_reply"],
                        voice_summary=math_data["voice_reply"],
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"operation": math_data.get("operation")},
                    )
                )
                return

            elif smart_type == "random":
                c_low = command_part.lower()
                if "testa o croce" in c_low or "moneta" in c_low:
                    rand_data = flip_coin()
                elif any(w in c_low for w in ("scegli tra", "decidi tra", "meglio")):
                    rand_data = choose_option(command_part)
                else:
                    m_sides = re.search(r"dado\s+(?:a|da)\s+(\d+)", c_low)
                    sides = int(m_sides.group(1)) if m_sides else 6
                    rand_data = roll_dice(sides)

                await self._notify_channel(
                    f"🎲 **{user.display_name}** ha consultato la sorte di Franchino:",
                    embed=rand_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        rand_data["voice_reply"],
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=command_part,
                        intent="random_choice",
                        ai_response=rand_data["chat_reply"],
                        voice_summary=rand_data["voice_reply"],
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"result": str(rand_data.get("result", ""))},
                    )
                )
                return

            elif smart_type == "joke":
                joke_data = get_vocalist_joke()
                await self._notify_channel(
                    f"😄 **{user.display_name}** ha chiesto una battuta a Franchino:",
                    embed=joke_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        joke_data["voice_reply"],
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
                from central_hub import hub
                asyncio.create_task(
                    hub.archive_interaction(
                        user_id=str(getattr(user, "id", 0)),
                        user_name=user.display_name,
                        raw_query=command_part,
                        intent="joke_vocalist",
                        ai_response=joke_data["chat_reply"],
                        voice_summary=joke_data["voice_reply"],
                        channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        metadata={"joke": joke_data.get("question")},
                    )
                )
                return

        # 0C-ter. Domande AIUTO/GUIDA su UO OUTLANDS ("cosa puoi fare su outlands", "aiuto outlands", ecc.)
        from outlands_wiki import is_help_question, is_wiki_question, query_outlands_wiki, get_outlands_help_embed
        if is_help_question(command_part):
            logger.info(f"🗺️ Richiesta aiuto Outlands da {user.display_name}: '{command_part}'")
            help_embed, voice_answer = get_outlands_help_embed()
            await self._notify_channel(
                f"🗺️ **{user.display_name}** ha chiesto cosa so fare su **UO Outlands** — Ecco la guida completa!",
                embed=help_embed,
            )
            asyncio.create_task(
                self.player.speak_tts(
                    voice_answer,
                    wait=False,
                    interrupt_music=True,
                    keep_paused_after=False,
                )
            )
            return

        # 0C-quater. Domande sulla WIKI DI UO OUTLANDS (https://wiki.uooutlands.com) tradotte e lette a voce in Italiano!
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
        # Evita rigorosamente collisioni con richieste musicali (es. "metti il concerto di Vasco Rossi" -> brano, NON riverbero!)
        eff_keywords = (
            "effetto", "effetti", "boost", "bassboost", "bass boost", "bassi",
            "megabass", "mega bass", "sub bass", "subbass",
            "riverbero", "reverb", "eco", "nightcore", "night core",
            "slowed", "8d", "otto d", "karaoke", "vaporwave", "lofi", "lo-fi",
            "chipmunk", "scoiattolo", "demone", "demon", "vintage", "audio normale", "normale"
        )
        is_explicit_music_req = (
            cmd_lower.startswith((
                "metti ", "riproduci ", "suona ", "play ", "cerca ",
                "fammi sentire ", "fammi ascoltare ", "fai partire ", "aggiungi ", "accoda "
            ))
            and (
                any(m_word in cmd_lower for m_word in ("canzone", "canzoni", "brano", "traccia", "pezzo", "album", "disco", "playlist"))
                or not any(eff_kw in cmd_lower for eff_kw in eff_keywords)
            )
            and not any(cmd_lower.startswith(p) for p in (
                "metti l'effetto ", "metti effetto ", "metti il boost", "metti boost",
                "metti bass boost", "metti il bass boost", "metti l'audio normale", "metti audio normale"
            ))
        )
        if not is_explicit_music_req:
            # 1. Rileva disattivazione/rimozione di qualsiasi effetto audio DJ ("togli il boost", "disattiva riverbero", "no boost", "normale", ecc.)
            off_pat = re.compile(
                r'^(?:normale|audio\s+normale|effetto\s+normale|reset\s+effetti|resetta\s+effetti|togli\s+effetti?|senza\s+effetti?|originale|no\s+boost|senza\s+boost|basta\s+boost|stop\s+effetti)$|'
                r'\b(?:togli(?:ere)?|disattiva(?:re)?|leva(?:re)?|rimuovi(?:ere)?|spegni(?:ere)?|'
                r'stoppa(?:re)?|basta|cancella|elimina|senza|no)\s+'
                r'(?:il\s+|l\'|gli\s+|l\'effetto\s+|l\'audio\s+|i\s+)?'
                r'(?:effetti|effetto|filtri|filtro|boost|bass\s*boost(?:er)?|bassboost|megabass|nightcore|slowed|8d|reverb|riverbero|eco|karaoke|vaporwave|lo-?fi|scoiattolo|chipmunk|demone|demon|vintage|radio)\b',
                re.IGNORECASE
            )

            # 2. Domande o frasi conversazionali contenenti parole di effetti (es. "chi ha il boost?", "cos'è il boost?", "come funziona il boost?", "il boost del server", "grazie per il boost")
            # NON devono attivare filtri DJ! Vanno instradate verso Brain AI / Outlands Wiki.
            effect_question_or_chatter = re.compile(
                r'\b(?:chi\s+(?:ha|c\'ha|usa|fa|prende|vuole|boosta)|avete|hai|abbiamo|hanno|'
                r'del\s+server|al\s+server|per\s+il\s+boost|grazie|'
                r'come\s+funziona|cos\'[eè]|cosa\s+[eè]|a\s+cosa\s+serve|spiegami|'
                r'perch[eé]|serve\s+il\s+boost|prezzi\s+bassi|volumi\s+bassi|toni\s+bassi|'
                r'suoni\s+bassi|passi\s+bassi)\b|\?',
                re.IGNORECASE
            )

            hit_effect = None
            if off_pat.search(cmd_lower):
                hit_effect = "off"
            elif not effect_question_or_chatter.search(cmd_lower):
                effect_patterns = [
                    ("megabass", re.compile(r'^(?:megabass|mega\s*bass|sub\s*bass|subbass)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:mega\s*bass|sub\s*bass|bassi\s+estremi|bassi\s+devastanti|ultra\s+bassi)\b|\b(?:mega\s*bass|sub\s*bass|bassi\s+estremi|bassi\s+devastanti|ultra\s+bassi)\b', re.I)),
                    ("bassboost", re.compile(r'^(?:boost|/boost|bass\s*boost(?:er)?|bassboost|bassi|effetto\s+bassi)$|\b(?:metti|attiva|imposta|applica|alza|aumenta|pompa|spingi|spara|carica|abilita|accendi|voglio|dammi)\s+(?:il\s+|l\'|un\s+|i\s+|l\'effetto\s+|l\'audio\s+|di\s+|pi[uù]\s+)?(?:bass\s*boost(?:er)?|bassboost|boost|bassi|sub\s*bass)\b|\b(?:bassi\s+a\s+palla|bassi\s+potenti|pi[uù]\s+bassi|effetto\s+bassi|bassi\s+forti|bassi\s+devastanti)\b', re.I)),
                    ("nightcore", re.compile(r'^(?:night\s*core|nightcore|velocizza)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:night\s*core|effetto\s+veloce)\b|\b(?:night\s*core|effetto\s+veloce)\b', re.I)),
                    ("slowed", re.compile(r'^(?:slowed|slow|rallenta)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:slowed|effetto\s+lento)\b|\b(?:slowed|effetto\s+lento)\b', re.I)),
                    ("8d", re.compile(r'^(?:8d|otto\s*d|surround)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:8d|otto\s*d|surround|effetto\s+8d)\b|\b(?:8d|otto\s*d|surround|effetto\s+8d)\b', re.I)),
                    ("reverb", re.compile(r'^(?:reverb|riverbero|eco)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:reverb|riverbero|eco|effetto\s+eco|eco\s+da\s+concerto|effetto\s+arena)\b|\b(?:reverb|riverbero|effetto\s+eco|eco\s+da\s+concerto|effetto\s+arena)\b', re.I)),
                    ("karaoke", re.compile(r'^(?:karaoke|strumentale|base\s+musicale)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?karaoke\b|\b(?:togli\s+la\s+voce|senza\s+voce|base\s+musicale|versione\s+strumentale)\b', re.I)),
                    ("vaporwave", re.compile(r'^(?:vaporwave|vapor\s*wave|lo-?fi)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:vapor\s*wave|lo-?fi)\b|\b(?:vapor\s*wave|lo-?fi)\b', re.I)),
                    ("chipmunk", re.compile(r'^(?:scoiattolo|chipmunk|elio)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:scoiattolo|chipmunk|voce\s+acuta|voce\s+da\s+scoiattolo|elio)\b|\b(?:scoiattolo|chipmunk|voce\s+acuta|voce\s+da\s+scoiattolo|elio)\b', re.I)),
                    ("demon", re.compile(r'^(?:demone|demon|mostro|darth\s+vader)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:demone|demon|voce\s+profonda|voce\s+grave|voce\s+cupa|effetto\s+mostro|darth\s+vader)\b|\b(?:demone|demon|voce\s+profonda|voce\s+grave|voce\s+cupa|effetto\s+mostro|darth\s+vader)\b', re.I)),
                    ("vintage", re.compile(r'^(?:vintage|vinile|vecchia\s+radio|radio\s+anni\s+60)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:vintage|radio\s+vecchia|radio\s+anni\s+60|vinile|vecchia\s+radio|effetto\s+telefono)\b|\b(?:vintage|radio\s+vecchia|radio\s+anni\s+60|vinile|vecchia\s+radio|effetto\s+telefono)\b', re.I)),
                ]
                for eff_key, pat in effect_patterns:
                    if pat.search(cmd_lower):
                        hit_effect = eff_key
                        break

            if hit_effect:
                label = await self.player.set_effect(hit_effect)
                if hit_effect == "off":
                    await self._notify_channel(
                        f"🎛️ **{user.display_name}** ha disattivato gli effetti DJ (audio originale normale)!"
                    )
                else:
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
            r"^(?:metti(?:\s+su)?|mette(?:\s+su)?|mettimi(?:\s+su)?|riproduci|riproduce|suona|suonami|play|fammi sentire|fammi ascoltare|avvia|cerca|cercami|fai partire)\s+(.+)$",
            command_part,
            re.IGNORECASE,
        )
        if play_match:
            raw_query = play_match.group(1).strip()
            q_low = raw_query.lower()

            # Disambiguazione immediata: controlli DJ espliciti con "metti in pausa", "metti pausa", "metti in loop", ecc.
            if q_low in ("in pausa", "pausa", "aspetta", "un attimo"):
                if self.player.is_playing():
                    self.player.pause()
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha messo in pausa con la voce ⏸️")
                return

            if q_low in ("in stop", "stop", "muto", "silenzio", "basta", "ferma", "stoppa", "fermati", "zitto", "fai silenzio", "spegni", "taci", "smettila"):
                self.player.stop()
                return

            if q_low in ("in loop", "loop", "ripeti"):
                self.player.loop_mode = "off" if self.player.loop_mode != "off" else "song"
                stato = "attivato 🔂" if self.player.loop_mode == "song" else "disattivato ➡️"
                await self._notify_channel(f"🎙️ **{user.display_name}** ha {stato} il loop con la voce!")
                return

            if q_low in ("in shuffle", "shuffle", "casuale", "mescola"):
                if len(self.player.queue) > 1:
                    self.player.shuffle()
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha mescolato la coda con la voce! 🔀")
                return

            if q_low.startswith(("il volume", "volume", "a ")):
                v_match = re.search(r"(\d{1,3})\s*%?", q_low)
                if v_match:
                    val = max(1, min(100, int(v_match.group(1))))
                    self.player.set_volume(val / 100.0)
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha impostato il volume al **{val}%** 🔊")
                    return

            if any(w in q_low for w in ("guida", "wiki", "dungeon", "aspect")):
                from outlands_wiki import is_outlands_entity, query_outlands_wiki
                is_ent, ent_name, _ = is_outlands_entity(q_low)
                if is_ent:
                    wiki_target = ent_name or raw_query
                    try:
                        wiki_res = await asyncio.wait_for(query_outlands_wiki(wiki_target), timeout=18.0)
                        if wiki_res:
                            await self._notify_channel(
                                f"📖 **{user.display_name}**, ecco la guida da [**wiki.uooutlands.com** ({wiki_res['page_title']} → {wiki_res['section_title']})]({wiki_res['section_url']}) tradotta in italiano:",
                                embed=wiki_res["embed"],
                            )
                            asyncio.create_task(
                                self.player.speak_tts(wiki_res["voice_answer"], wait=False, interrupt_music=True, keep_paused_after=False)
                            )
                            return
                    except Exception:
                        pass

            query = raw_query
            if query:
                # Normalizzazione: rimuove filler italiani che rovinano la ricerca YouTube
                query = re.sub(
                    r'^(?:su\s+|la\s+(?:canzone|canzoni|musica|traccia|bella\s+canzone)|'
                    r'il\s+(?:brano|pezzo)|un\s+(?:brano|pezzo|po\s+di|po\'\s+di)|'
                    r'una\s+(?:canzone|traccia|bella|bella\s+canzone)|'
                    r'qualcosa\s+di|'
                    r'del|della|di|un\s+po\'\s+di|un\s+po\s+di)\s+',
                    '', query.strip(), flags=re.IGNORECASE
                ).strip()
                # Rimuove suffissi inutili
                query = re.sub(
                    r'\s+(?:per\s+(?:me|favore|piacere)|adesso|subito|dai|su|please|now|'
                    r'in\s+questo\s+momento|al\s+più\s+presto)\s*$',
                    '', query, flags=re.IGNORECASE
                ).strip()

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

        # 10. Fallback: Disambiguazione tra Domande/Outlands e Ricerca Musicale YouTube
        if command_part:
            # Controllo di sicurezza -1: Richiesta Formula Super Matematico Jack in fallback
            from jack_math_engine import jack_engine, hardware_optimizer
            jack_fb = jack_engine.classify(command_part)
            if jack_fb.intent == "jack_math_boost" and jack_fb.probability >= 0.25:
                logger.info(f"🧠 Intercettata richiesta Formula Super Matematico Jack in fallback da {user.display_name}: '{command_part}'")
                telem = hardware_optimizer.get_hardware_telemetry()
                embed = discord.Embed(
                    title="🧠 Formula Matematica Jack & Ottimizzazione Hardware ARM",
                    description=(
                        "Franchino opera con il motore di intelligenza analitica **Super Matematico Jack**:\n\n"
                        "$$\\mathcal{S}(\\text{Intent}, q) = w_{\\text{wake}} \\cdot \\phi_{\\text{wake}} + "
                        "w_{\\text{phonetic}} \\cdot \\sigma_{\\text{jaro}} + w_{\\text{domain}} \\cdot \\text{BM25}(q, D) + "
                        "w_{\\text{action}} \\cdot \\alpha_{\\text{syntax}} - \\lambda_{\\text{noise}}$$\n\n"
                        f"**Classificazione Istantanea di '{command_part}':**\n"
                        f"`{jack_fb.to_math_summary()}`\n\n"
                        "**Distribuzione Probabilistica Softmax:**\n"
                        "$$\\mathcal{P}(\\text{Intent} \\mid q) = \\frac{e^{\\beta \\cdot \\mathcal{S}(\\text{Intent}, q)}}{\\sum_k e^{\\beta \\cdot \\mathcal{S}(k, q)}}$$"
                    ),
                    color=0xF1C40F,
                )
                embed.add_field(
                    name="⚖️ Pesi & Parametri Jack",
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
                voice_jack = "Magia ragazzi! Ecco la formula del super matematico Jack! Ho ottimizzato la console con Okapi BM25 e Jaro-Winkler per 2 core ARM!"
                await self._notify_channel(
                    f"🧠 **{user.display_name}**, ecco la formula del **Super Matematico Jack**:",
                    embed=embed,
                )
                asyncio.create_task(
                    self.player.speak_tts(voice_jack, wait=False, interrupt_music=True, keep_paused_after=False)
                )
                return

            # Controllo di sicurezza 0: Richiesta Script Razor per una skill o catalogo generale
            from razor_scripts import is_script_question, build_razor_scripts_response
            is_script, script_skill = is_script_question(command_part)
            if not is_script and jack_fb.intent == "razor_script" and jack_fb.probability >= 0.20:
                is_script = True
                script_skill = jack_fb.extracted_entities.get("skill_slug") or "lista"
            if is_script:
                target_skill = script_skill if script_skill and script_skill != "lista" else "lista"
                logger.info(f"📜 Intercettata richiesta script Razor in fallback per {target_skill} da {user.display_name}: '{command_part}'")
                embed, view, file, voice_ans, code = build_razor_scripts_response(target_skill)
                if embed:
                    preview_text = ""
                    if code:
                        clean_code = code.replace("```", "'''")
                        if len(clean_code) <= 1200:
                            preview_text = f"\n```razor\n{clean_code}\n```"
                        else:
                            preview_text = f"\n```razor\n{clean_code[:1100]}\n# ... [File completo allegato sotto!] ...\n```"
                    skill_header = f"**{target_skill.title()}**" if target_skill != "lista" else "**UO Outlands**"
                    await self._notify_channel(
                        f"📜 **{user.display_name}** ha chiesto gli script Razor per {skill_header} — Ecco tutto pronto!{preview_text}",
                        embed=embed,
                        view=view,
                        file=file,
                    )
                    if voice_ans:
                        asyncio.create_task(
                            self.player.speak_tts(voice_ans, wait=False, interrupt_music=True, keep_paused_after=False)
                        )
                    return

            # Controllo di sicurezza 1: Richiesta aiuto/guida Outlands
            from outlands_wiki import is_help_question, is_wiki_question, query_outlands_wiki, get_outlands_help_embed
            if is_help_question(command_part):
                logger.info(f"🗺️ Intercettata richiesta aiuto Outlands in fallback da {user.display_name}: '{command_part}'")
                help_embed, voice_answer = get_outlands_help_embed()
                await self._notify_channel(
                    f"🗺️ **{user.display_name}** ha chiesto cosa so fare su **UO Outlands** — Ecco la guida completa!",
                    embed=help_embed,
                )
                asyncio.create_task(
                    self.player.speak_tts(
                        voice_answer,
                        wait=False,
                        interrupt_music=True,
                        keep_paused_after=False,
                    )
                )
                return

            # Controllo di sicurezza 2: Domanda esplicita o ontologica sulla Wiki di Outlands
            is_wiki, wiki_q = is_wiki_question(command_part)
            if not is_wiki and jack_fb.intent == "wiki_guide" and jack_fb.probability >= 0.20:
                is_wiki = True
                wiki_q = jack_fb.extracted_entities.get("wiki_topic") or command_part
            if is_wiki:
                logger.info(f"📖 Intercettata domanda Wiki Outlands in fallback da {user.display_name}: '{wiki_q}'")
                self.player._manual_stop = False
                try:
                    await self._notify_channel(
                        f"📖 **{user.display_name}** ha chiesto su **UO Outlands**: *\"{wiki_q}\"* — Cerco sulla Wiki e traduco in italiano..."
                    )
                    wiki_res = await asyncio.wait_for(query_outlands_wiki(wiki_q), timeout=18.0)
                    if wiki_res:
                        title = wiki_res["page_title"]
                        sec_title = wiki_res["section_title"]
                        sec_url = wiki_res["section_url"]
                        voice_answer = wiki_res["voice_answer"]
                        wiki_embed = wiki_res["embed"]
                        await self._notify_channel(
                            f"📖 **{user.display_name}**, ecco la guida da [**wiki.uooutlands.com** ({title} → {sec_title})]({sec_url}) tradotta in italiano:",
                            embed=wiki_embed,
                        )
                        asyncio.create_task(
                            self.player.speak_tts(
                                voice_answer,
                                wait=False,
                                interrupt_music=True,
                                keep_paused_after=False,
                            )
                        )
                        return
                except Exception as e:
                    logger.warning(f"Errore gestione fallback wiki: {e}")

            # Controllo di sicurezza 2-bis: Intenti intelligenti aggiuntivi (smart assistant, sport, favole vocalist, controlli dj, admin, social, presenza)
            if jack_fb.intent in ("smart_assistant", "sports_match", "sports_schedule", "live_fable", "dj_control", "admin_control", "social_interaction", "liveness_check") and jack_fb.probability >= 0.25:
                from brain import brain
                decision = brain.decide_intent(
                    command_part,
                    user.display_name,
                    current_song_title=self.player.current.title if self.player.current else None,
                )
                if await self._dispatch_brain_decision(user, decision, command_part):
                    return

            # Controllo di sicurezza 3: Se la frase è chiaramente una domanda o un intento discorsivo
            # NON inviarla MAI a YouTube come canzone!
            cmd_strip = command_part.strip().lower()
            is_question = (
                cmd_strip.endswith("?")
                or re.match(
                    r"^(?:cosa|che|chi|come|dove|quando|perch[eé]|quali|quale|quanto|quanta|quanti|quante|"
                    r"spiegami|dimmi|parlami|raccontami|sai\s+dirmi|puoi\s+dirmi|mi\s+dici|mi\s+spieghi|"
                    r"sai\s+cos'è|sai\s+cosa|sai|conosci|esiste|ci\s+sono|si\s+può|posso|c'è|hai|avete|"
                    r"chi\s+ha|come\s+funziona|cosa\s+serve|a\s+cosa\s+serve|cosa\s+fa|chi\s+sei|come\s+stai|cosa\s+fai|dove\s+sei|come\s+va)\b",
                    cmd_strip,
                    re.IGNORECASE,
                ) is not None
                or any(q_term in cmd_strip for q_term in (
                    "outlands", "ultima online", "come funziona", "come si fa", "dove trovo",
                    "dove si trova", "cosa serve", "a cosa serve", "cosa fa", "cosa fanno",
                    "perché", "perche", "chi ha il boost", "cos'è il boost", "come funziona il boost"
                ))
            )

            # Saluti e complimenti amichevoli rivolti a Franchino
            if re.match(r'^(?:ciao|buongiorno|buonasera|buonanotte|buona\s+notte|salve|ueh|uè|bella|grande|ti\s+voglio\s+bene|sei\s+un\s+grande|sei\s+il\s+migliore)\b', cmd_strip):
                vocalist_greetings = [
                    "Magia ragazzi! Buona serata a tutti da Franchino in console!",
                    "Ciao ragazzi! La magia della musica della notte è sempre con noi!",
                    "Grande! La notte è giovane e siamo pronti a volare!",
                    "Grazie ragazzi! Siete il cuore pulsante di questa pista da ballo!",
                ]
                v_msg = random.choice(vocalist_greetings)
                await self._notify_channel(f"🎙️ **Franchino**: *\"{v_msg}\"*")
                asyncio.create_task(self.player.speak_tts(v_msg, wait=False, interrupt_music=True))
                return

            # Feedback audio o microfono
            if re.match(r'^(?:non\s+ti\s+sento|ti\s+sento\s+piano|parla\s+più\s+forte|alza\s+la\s+voce|si\s+sente\s+basso)\b', cmd_strip):
                v_fb = "Ti sento forte e chiaro! Regola il mio volume dal tuo Discord o dì 'Franchino volume a 100'!"
                await self._notify_channel(f"🎙️ **Franchino**: *\"{v_fb}\"*")
                asyncio.create_task(self.player.speak_tts(v_fb, wait=False, interrupt_music=True))
                return

            if is_question:
                logger.info(f"❓ Rilevata domanda non-musicale da {user.display_name}: '{command_part}' — Evito ricerca YouTube!")
                # Tenta prima se c'è un riscontro informativo sulla Wiki di Outlands
                try:
                    from outlands_wiki import is_wiki_question, query_outlands_wiki
                    is_wiki, wiki_q = is_wiki_question(command_part)
                    if is_wiki:
                        wiki_res = await asyncio.wait_for(query_outlands_wiki(wiki_q), timeout=10.0)
                        if wiki_res:
                            title = wiki_res["page_title"]
                            sec_title = wiki_res["section_title"]
                            sec_url = wiki_res["section_url"]
                            voice_answer = wiki_res["voice_answer"]
                            wiki_embed = wiki_res["embed"]
                            await self._notify_channel(
                                f"📖 **{user.display_name}**, ecco la guida da [**wiki.uooutlands.com** ({title} → {sec_title})]({sec_url}) tradotta in italiano:",
                                embed=wiki_embed,
                            )
                            asyncio.create_task(
                                self.player.speak_tts(
                                    voice_answer,
                                    wait=False,
                                    interrupt_music=True,
                                    keep_paused_after=False,
                                )
                            )
                            return
                except Exception:
                    pass

                # Fallback con AI / Brain per domande generali o quesiti sul bot/boost
                from brain import brain
                try:
                    decision = await asyncio.wait_for(
                        brain.process_user_speech(
                            user_name=user.display_name,
                            user_id=str(getattr(user, "id", 0)),
                            text=command_part,
                            current_song_title=self.player.current.title if self.player.current else None,
                            channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        ),
                        timeout=5.0,
                    )
                except Exception as be:
                    logger.debug(f"Brain timeout o errore fallback euristico: {be}")
                    decision = brain._fallback_heuristic_intent(command_part, user.display_name)

                if await self._dispatch_brain_decision(user, decision, command_part):
                    return

            # Controllo di sicurezza 4: Disambiguazione tra Conversazione Ordinaria e Ricerca Musicale YouTube
            # Se la frase non contiene indicatori musicali espliciti, interroga Gemma Brain
            # per comprendere l'intento prima di sparare brani casuali su YouTube!
            music_intent_indicators = (
                "canzone", "canzoni", "musica", "brano", "traccia", "pezzo", "album", "disco",
                "cantante", "artista", "band", "remix", "feat", "mix", "dj", "soundtrack", "ost",
                "playlist", "metti", "suona", "ascolta", "fammi sentire", "live", "concerto", "acustico"
            )
            has_music_kw = any(k in cmd_strip for k in music_intent_indicators)

            if not has_music_kw:
                logger.info(f"🧠 Frase senza termine musicale esplicito da {user.display_name}: '{command_part}' — Classifico con Brain AI...")
                from brain import brain
                try:
                    decision = await asyncio.wait_for(
                        brain.process_user_speech(
                            user_name=user.display_name,
                            user_id=str(getattr(user, "id", 0)),
                            text=command_part,
                            current_song_title=self.player.current.title if self.player.current else None,
                            channel_name=self.player.text_channel.name if self.player.text_channel else None,
                        ),
                        timeout=5.0,
                    )
                except Exception:
                    decision = brain.decide_intent(
                        command_part,
                        user.display_name,
                        current_song_title=self.player.current.title if self.player.current else None,
                    )

                if await self._dispatch_brain_decision(user, decision, command_part):
                    return
                elif decision.get("intent") == "play_music" and decision.get("music_query"):
                    command_part = decision.get("music_query")

            # Normalizzazione query musicale (rimuove filler tipici del parlato italiano)
            music_query = re.sub(
                r'^(?:la\s+(?:canzone|canzoni|musica|traccia|bella\s+canzone)|'
                r'il\s+(?:brano|pezzo)|un\s+(?:brano|pezzo|po\s+di|po\'\s+di)|'
                r'una\s+(?:canzone|traccia|bella|bella\s+canzone)|'
                r'qualcosa\s+di|'
                r'del|della|di|un\s+po\'\s+di|un\s+po\s+di)\s+',
                '', command_part.strip(), flags=re.IGNORECASE
            ).strip() or command_part.strip()
            music_query = re.sub(
                r'\s+(?:per\s+(?:me|favore|piacere)|adesso|subito|dai|su|please|now|'
                r'in\s+questo\s+momento|al\s+più\s+presto)\s*$',
                '', music_query, flags=re.IGNORECASE
            ).strip() or music_query

            # Scarta query vuote, troppo corte o monosillabi privi di senso
            if len(music_query) < 3 or music_query.lower() in ("eh", "ah", "boh", "mah", "ok", "beh", "si", "no", "uhm", "ehm"):
                logger.debug(f"🔇 Query musicale priva di significato ignorata: '{music_query}'")
                return

            logger.info(f"🎵 Fallback ricerca musicale per {user.display_name}: '{music_query}'")
            self.player._manual_stop = False
            await self._notify_channel(
                f"🎙️ **{user.display_name}**: *\"Franchino {command_part}\"* — Cerco e avvio in console! 🎶"
            )
            try:
                songs = await asyncio.wait_for(
                    self.player.source_manager.resolve_url_or_query(music_query, requester=requester_arg),
                    timeout=25.0,
                )
                if songs:
                    await self.player.play_now(
                        songs,
                        text_channel=self.player.text_channel,
                        voice_announce=f"Subito! Metto {music_query}",
                    )
                else:
                    await self._notify_channel(
                        f"⚠️ Non ho trovato una traccia musicale per *\"{music_query}\"*. Dì *\"Franchino comandi\"* per l'elenco comandi!"
                    )
            except Exception as e:
                logger.warning(f"Fallback ricerca brano fallito per '{music_query}': {e}")
                await self._notify_channel(
                    f"⚠️ Non sono riuscito a trovare **{music_query}**: `{e}`"
                )

    async def _dispatch_brain_decision(self, user, decision: Dict[str, Any], command_part: str) -> bool:
        """
        Esegue l'intento deciso da Gemma Brain o dal fallback euristico:
        - sports_match / sports_schedule: invoca sports_scraper, crea Embed e parla a voce
        - wiki_guide: invoca query_outlands_wiki con embed e TTS
        - outlands_help: invoca get_outlands_help_embed con embed e TTS
        - live_fable: racconta monologo poetico inedito
        - play_music: ritorna False per far proseguire la ricerca musicale YouTube
        - chat: invia chat_reply e legge voice_reply
        Ritorna True se gestito, False se deve proseguire (es. musica).
        """
        intent = decision.get("intent", "chat")

        if intent == "jack_math_boost":
            from jack_math_engine import jack_engine, hardware_optimizer
            telem = hardware_optimizer.get_hardware_telemetry()
            embed = discord.Embed(
                title="🧠 Formula Matematica Jack & Ottimizzazione Hardware ARM",
                description=(
                    "Franchino opera con il motore di intelligenza analitica **Super Matematico Jack**:\n\n"
                    "$$\\mathcal{S}(\\text{Intent}, q) = w_{\\text{wake}} \\cdot \\phi_{\\text{wake}} + "
                    "w_{\\text{phonetic}} \\cdot \\sigma_{\\text{jaro}} + w_{\\text{domain}} \\cdot \\text{BM25}(q, D) + "
                    "w_{\\text{action}} \\cdot \\alpha_{\\text{syntax}} - \\lambda_{\\text{noise}}$$\n\n"
                    "**Distribuzione Probabilistica Softmax:**\n"
                    "$$\\mathcal{P}(\\text{Intent} \\mid q) = \\frac{e^{\\beta \\cdot \\mathcal{S}(\\text{Intent}, q)}}{\\sum_k e^{\\beta \\cdot \\mathcal{S}(k, q)}}$$"
                ),
                color=0xF1C40F,
            )
            embed.add_field(
                name="⚖️ Pesi & Parametri Jack",
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
            voice_jack = decision.get("voice_reply") or "Magia ragazzi! Ecco la formula del super matematico Jack! Ho ottimizzato la console con Okapi BM25 e Jaro-Winkler per 2 core ARM!"
            await self._notify_channel(
                f"🧠 **{user.display_name}**, ecco la formula del **Super Matematico Jack**:",
                embed=embed,
            )
            asyncio.create_task(
                self.player.speak_tts(voice_jack, wait=False, interrupt_music=True, keep_paused_after=False)
            )
            return True

        if intent == "smart_assistant":
            from smart_assistant import (
                get_current_time_info,
                get_weather_info,
                evaluate_math_expression,
                flip_coin,
                roll_dice,
                choose_option,
                get_vocalist_joke,
                timer_manager,
                format_duration_italian,
            )
            sub = decision.get("subintent", "general")
            if sub == "time":
                time_data = get_current_time_info(command_part)
                await self._notify_channel(
                    f"🕒 **{user.display_name}** ha chiesto l'orario a Franchino:",
                    embed=time_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(time_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False)
                )
                return True
            elif sub == "weather":
                city = decision.get("city") or command_part
                weather_data = await get_weather_info(city)
                await self._notify_channel(
                    f"🌤️ **{user.display_name}** ha chiesto il meteo a Franchino:",
                    embed=weather_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(weather_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False)
                )
                return True
            elif sub == "math":
                math_data = evaluate_math_expression(command_part)
                await self._notify_channel(
                    f"🧮 **{user.display_name}** ha chiesto un calcolo a Franchino:",
                    embed=math_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(math_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False)
                )
                return True
            elif sub == "joke":
                joke_data = get_vocalist_joke()
                await self._notify_channel(
                    f"😄 **{user.display_name}** ha chiesto una battuta a Franchino:",
                    embed=joke_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(joke_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False)
                )
                return True
            elif sub == "timer_set":
                dur = decision.get("duration_seconds", 300)
                lbl = decision.get("label", "Timer")
                dur_str = format_duration_italian(dur)
                guild_id = self.player.guild.id if self.player.guild else 0
                timer_manager.start_timer(
                    guild_id=guild_id,
                    channel=self.player.text_channel,
                    user=user,
                    duration_seconds=dur,
                    label=lbl,
                    player=self.player,
                )
                v_msg = f"Timer di {dur_str} per {lbl} impostato ragazzi! Vi avviso appena scade! Magia!"
                embed = discord.Embed(
                    title="⏰ Timer Impostato da Console",
                    description=f"🔔 Timer di **{dur_str}** impostato per **{lbl}**!\n\n🎙️ *\"{v_msg}\"*",
                    color=0x3498DB,
                )
                await self._notify_channel(f"⏰ **{user.display_name}** ha impostato un timer in console:", embed=embed)
                asyncio.create_task(self.player.speak_tts(v_msg, wait=False, interrupt_music=True, keep_paused_after=False))
                return True
            elif sub == "timer_status":
                guild_id = self.player.guild.id if self.player.guild else 0
                timers = timer_manager.get_timers(guild_id, user_id=user.id)
                if not timers:
                    timers = timer_manager.get_timers(guild_id)
                if not timers:
                    ans = f"Non c'è nessun timer attivo al momento {user.display_name}!"
                    await self._notify_channel(f"⏰ **{user.display_name}**: Nessun timer attivo.")
                    asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                else:
                    t = timers[0]
                    rem_str = format_duration_italian(int(t.remaining_seconds))
                    ans = f"Mancano ancora {rem_str} al timer per {t.label} ragazzi!"
                    await self._notify_channel(f"⏰ **{user.display_name}**: Mancano ancora **{rem_str}** al timer per **{t.label}**.")
                    asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                return True
            elif sub == "timer_cancel":
                guild_id = self.player.guild.id if self.player.guild else 0
                cancelled = timer_manager.cancel_timers(guild_id, user_id=user.id)
                if not cancelled:
                    cancelled = timer_manager.cancel_timers(guild_id)
                if cancelled:
                    labels = ", ".join([c.label for c in cancelled])
                    ans = f"Timer per {labels} cancellato con successo ragazzi!"
                    await self._notify_channel(f"⏰ **{user.display_name}**: Timer per **{labels}** cancellato.")
                    asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                else:
                    ans = f"Nessun timer attivo da cancellare {user.display_name}!"
                    await self._notify_channel(f"⏰ **{user.display_name}**: Nessun timer da cancellare.")
                    asyncio.create_task(self.player.speak_tts(ans, wait=False, interrupt_music=True, keep_paused_after=False))
                return True
            elif sub == "random":
                c_low = command_part.lower()
                if "testa o croce" in c_low or "moneta" in c_low:
                    rand_data = flip_coin()
                elif any(w in c_low for w in ("scegli tra", "decidi tra", "meglio")):
                    rand_data = choose_option(command_part)
                else:
                    m_sides = re.search(r"dado\s+(?:a|da)\s+(\d+)", c_low)
                    sides = int(m_sides.group(1)) if m_sides else 6
                    rand_data = roll_dice(sides)
                await self._notify_channel(
                    f"🎲 **{user.display_name}** ha consultato la sorte di Franchino:",
                    embed=rand_data["embed"],
                )
                asyncio.create_task(
                    self.player.speak_tts(rand_data["voice_reply"], wait=False, interrupt_music=True, keep_paused_after=False)
                )
                return True
            else:
                voice_msg = decision.get("voice_reply") or "Magia ragazzi! Assistente pronto in console!"
                chat_msg = decision.get("chat_reply") or voice_msg
                emb = decision.get("embed")
                if emb:
                    await self._notify_channel(f"🎙️ **Franchino**: {chat_msg}", embed=emb)
                else:
                    await self._notify_channel(f"🎙️ **Franchino**: {chat_msg}")
                asyncio.create_task(self.player.speak_tts(voice_msg, wait=False, interrupt_music=True, keep_paused_after=False))
                return True

        if intent == "razor_script":
            from razor_scripts import build_razor_scripts_response
            skill_target = decision.get("skill_slug") or "lista"
            embed, view, file, voice_ans, code = build_razor_scripts_response(skill_target)
            if embed:
                preview_text = ""
                if code:
                    clean_code = code.replace("```", "'''")
                    if len(clean_code) <= 1200:
                        preview_text = f"\n```razor\n{clean_code}\n```"
                    else:
                        preview_text = f"\n```razor\n{clean_code[:1100]}\n# ... [File completo allegato sotto!] ...\n```"
                skill_header = f"**{skill_target.title()}**" if skill_target != "lista" else "**UO Outlands**"
                await self._notify_channel(
                    f"📜 **{user.display_name}**, ecco gli script Razor per {skill_header}!{preview_text}",
                    embed=embed,
                    view=view,
                    file=file,
                )
                if voice_ans:
                    asyncio.create_task(
                        self.player.speak_tts(voice_ans, wait=False, interrupt_music=True, keep_paused_after=False)
                    )
                return True

        if intent in ("sports_match", "sports_schedule"):
            from sports_scraper import sports_scraper
            from central_hub import hub
            sq = decision.get("sport_query") or command_part
            res = await sports_scraper.search_event(sq)
            embed, view, voice_msg = sports_scraper.build_sports_embed(res)

            await self._notify_channel(
                message=f"🎙️⚽ **Franchino Sports ({user.display_name}):**\n> *\"{voice_msg}\"*",
                embed=embed,
                view=view,
            )
            asyncio.create_task(self.player.speak_tts(voice_msg, wait=False, interrupt_music=True))
            asyncio.create_task(
                hub.archive_interaction(
                    user_id=str(user.id),
                    user_name=user.display_name,
                    raw_query=f"brain_sport {sq}",
                    intent=intent,
                    ai_response=res.get("best_match", {}).get("title", "Palinsesto Sport"),
                    voice_summary=voice_msg,
                    metadata={"query": sq},
                )
            )
            return True

        if intent == "wiki_guide":
            from outlands_wiki import query_outlands_wiki
            wq = decision.get("wiki_topic") or command_part
            try:
                wiki_res = await asyncio.wait_for(query_outlands_wiki(wq), timeout=12.0)
                if wiki_res:
                    title = wiki_res["page_title"]
                    sec_title = wiki_res["section_title"]
                    sec_url = wiki_res["section_url"]
                    voice_answer = wiki_res["voice_answer"]
                    wiki_embed = wiki_res["embed"]
                    await self._notify_channel(
                        f"📖 **{user.display_name}**, ecco la guida da [**wiki.uooutlands.com** ({title} → {sec_title})]({sec_url}) tradotta in italiano:",
                        embed=wiki_embed,
                    )
                    asyncio.create_task(
                        self.player.speak_tts(
                            voice_answer,
                            wait=False,
                            interrupt_music=True,
                            keep_paused_after=False,
                        )
                    )
                    return True
            except Exception as e:
                logger.debug(f"Brain wiki dispatch error: {e}")

        if intent == "outlands_help":
            from outlands_wiki import get_outlands_help_embed
            help_embed, voice_answer = get_outlands_help_embed()
            await self._notify_channel(
                f"🗺️ **{user.display_name}** ha chiesto la guida su **UO Outlands**:",
                embed=help_embed,
            )
            asyncio.create_task(
                self.player.speak_tts(
                    voice_answer,
                    wait=False,
                    interrupt_music=True,
                    keep_paused_after=False,
                )
            )
            return True

        if intent == "live_fable":
            from brain import brain
            fable = await brain.generate_live_fable(user.display_name)
            await self._notify_channel(f"🎙️✨ **Franchino racconta a {user.display_name}**:\n> *\"{fable}\"*")
            asyncio.create_task(
                self.player.speak_tts(fable, wait=False, interrupt_music=True, keep_paused_after=False)
            )
            return True

        if intent == "dj_control":
            act = decision.get("action", "general")
            c_low = command_part.lower()
            if act == "pause" or any(w in c_low for w in ("pausa", "aspetta")):
                if self.player.is_playing():
                    self.player.pause()
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha messo in pausa con la voce ⏸️")
                return True
            elif act == "resume" or any(w in c_low for w in ("riprendi", "continua", "vai", "togli la pausa")):
                if self.player.is_paused():
                    self.player.resume()
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha ripreso la musica con la voce ▶️")
                return True
            elif act == "stop" or any(w in c_low for w in ("stop", "ferma", "stoppa", "fermati", "basta", "zitto", "stai zitto", "muto", "silenzio", "fai silenzio", "spegni", "taci", "smettila", "svuota")):
                self.player.stop()
                return True
            elif act == "skip" or any(w in c_low for w in ("skip", "salta", "prossima", "avanti", "cambia")):
                if self.player.is_active:
                    self.player.skip()
                    await self._notify_channel(f"🎙️ **{user.display_name}** — Salto al prossimo brano! ⏭️")
                return True
            elif act == "loop" or "loop" in c_low or "ripeti" in c_low:
                self.player.loop_mode = "off" if self.player.loop_mode != "off" else "song"
                stato = "attivato 🔂" if self.player.loop_mode == "song" else "disattivato ➡️"
                await self._notify_channel(f"🎙️ **{user.display_name}** ha {stato} il loop con la voce!")
                return True
            elif act == "shuffle" or "shuffle" in c_low or "mescola" in c_low:
                if len(self.player.queue) > 1:
                    self.player.shuffle()
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha mescolato la coda con la voce! 🔀")
                return True
            elif act == "volume" or "volume" in c_low:
                v_match = re.search(r"(\d{1,3})\s*%?", c_low)
                if v_match:
                    val = max(1, min(100, int(v_match.group(1))))
                    self.player.set_volume(val / 100.0)
                    await self._notify_channel(f"🎙️ **{user.display_name}** ha impostato il volume al **{val}%** 🔊")
                    return True
                elif any(w in c_low for w in ("alza", "più alto", "piu alto", "più forte")):
                    new_vol = min(1.0, self.player.volume + 0.20)
                    self.player.set_volume(new_vol)
                    await self._notify_channel(f"🎙️ Volume alzato al **{int(new_vol * 100)}%** 🔊")
                    return True
                elif any(w in c_low for w in ("abbassa", "più basso", "piu basso", "più piano")):
                    new_vol = max(0.05, self.player.volume - 0.20)
                    self.player.set_volume(new_vol)
                    await self._notify_channel(f"🎙️ Volume abbassato al **{int(new_vol * 100)}%** 🔉")
                    return True
            elif act == "effect" or any(w in c_low for w in ("boost", "bass", "riverbero", "eco", "effetto")):
                eff = "bassboost"
                if "riverbero" in c_low or "reverb" in c_low:
                    eff = "reverb"
                elif "megabass" in c_low:
                    eff = "megabass"
                elif "slowed" in c_low:
                    eff = "slowed"
                elif "nightcore" in c_low:
                    eff = "nightcore"
                elif "8d" in c_low:
                    eff = "8d"
                elif any(w in c_low for w in ("togli", "senza", "no boost", "disattiva")):
                    eff = "off"
                lbl = await self.player.set_effect(eff)
                await self._notify_channel(f"🎛️ **{user.display_name}**: Effetto DJ impostato: **{lbl}**!")
                return True
            return True

        if intent == "liveness_check":
            v_msg = "Forte e chiaro ragazzi! Franchino è sveglio, carico e sempre sul pezzo in console! Magia!"
            await self._notify_channel(f"🎙️ **Franchino**: *\"{v_msg}\"*")
            asyncio.create_task(self.player.speak_tts(v_msg, wait=False, interrupt_music=True, keep_paused_after=False))
            return True

        if intent == "social_interaction":
            from interactions import handle_voice_interaction
            if await handle_voice_interaction(self.player, user, command_part, self._notify_channel):
                return True
            v_msg = "Magia pura! Un grande abbraccio e un brindisi a tutta la sala da Franchino!"
            await self._notify_channel(f"🥂 **Franchino**: *\"{v_msg}\"*")
            asyncio.create_task(self.player.speak_tts(v_msg, wait=False, interrupt_music=True, keep_paused_after=False))
            return True

        if intent == "admin_control":
            from admin_commands import handle_voice_admin_command
            if await handle_voice_admin_command(self.player, user, command_part, self._notify_channel):
                return True
            await self._notify_channel(f"🛡️ **Franchino Admin**: Comando elaborato per **{user.display_name}**.")
            return True

        if intent == "play_music":
            return False

        # Chat / generale
        chat_reply = decision.get("chat_reply") or "🎧 **Franchino**: Sempre presente in console! Dimmi tutto!"
        voice_reply = decision.get("voice_reply") or chat_reply
        await self._notify_channel(
            f"🎙️ **Franchino** risponde a **{user.display_name}**:\n> *\"{chat_reply or voice_reply}\"*"
        )
        asyncio.create_task(
            self.player.speak_tts(
                voice_reply,
                wait=False,
                interrupt_music=True,
                keep_paused_after=False,
            )
        )
        return True

    async def _notify_channel(
        self,
        message: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
        file: Optional[discord.File] = None,
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
            if file:
                kwargs["file"] = file
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
        if hasattr(self, "_buffers"):
            self._buffers.clear()
        if hasattr(self, "_preroll_buffers"):
            self._preroll_buffers.clear()
        if hasattr(self, "_last_packet_time"):
            self._last_packet_time.clear()
        if hasattr(self, "_users"):
            self._users.clear()
        if hasattr(self, "_user_cache"):
            self._user_cache.clear()
        if hasattr(self, "_pending_wake"):
            self._pending_wake.clear()
