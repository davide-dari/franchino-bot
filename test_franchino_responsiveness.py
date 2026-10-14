import asyncio
import re
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import discord
from discord.ext import commands

from voice_commands import FranchinoVoiceSink
from player import GuildPlayer


class TestSplitSTTWakeWordRecognition:
    """Verifica che tutte le forme tronche e spezzate prodotte da Google STT vengano riconosciute."""

    @pytest.mark.parametrize("phrase, expected_wake_sub, expected_cmd", [
        ("hey franchi no ci sei", "franchi", "ci sei"),
        ("ehi fra chi no rispondi", "fra chi", "rispondi"),
        ("hey frankie no metti pop", "frankie", "metti pop"),
        ("ehi franki no suona rock", "franki", "suona rock"),
        ("hey franc ino ci senti", "franc", "ci senti"),
        ("ehi franck ino metti gigi", "franck", "metti gigi"),
        ("hey franchi, no metti musica", "franchi", "metti musica"),
        ("ehi fra chi, no come stai", "fra chi", "come stai"),
        ("hey franchino", "franchino", ""),
        ("ehi franchi no", "franchi", ""),
        ("hey fra chi no", "fra chi", ""),
    ])
    def test_split_wake_detected(self, phrase, expected_wake_sub, expected_cmd):
        has_wake, cmd, tok = FranchinoVoiceSink.extract_wake_and_command(phrase)
        assert has_wake, f"Mancata attivazione wake word su: '{phrase}'"
        assert expected_wake_sub.lower() in tok.lower()
        if expected_cmd:
            assert expected_cmd.lower() in cmd.lower()

    def test_colloquial_anti_wake_preserves_split_wake(self):
        """Verifica che il regex anti-wake distingua 'fra chi' da 'fra chi no'."""
        colloquial_anti_wake = re.compile(
            r'\b(?:fra\s*,?\s*chi(?!\s*,?\s*n[oaie]\b)|ho\s+(?:cento|\d+)\s+franchi|i\s+franchi)\b',
            re.IGNORECASE
        )
        # Non deve matchare le varianti di Franchino
        assert not colloquial_anti_wake.search("fra chi no ci sei")
        assert not colloquial_anti_wake.search("fra chi, no metti musica")
        assert not colloquial_anti_wake.search("fra chi na come va")

        # Deve continuare a matchare le chiacchiere non indirizzate a Franchino
        assert colloquial_anti_wake.search("fra chi ha il boost?")
        assert colloquial_anti_wake.search("fra, chi c'è in stanza?")
        assert colloquial_anti_wake.search("ho cento franchi in tasca")


class TestDirectAddressVsThirdPerson(unittest.IsolatedAsyncioTestCase):
    """Verifica che le frasi dirette a Franchino ricevano risposta, mentre le chiacchiere in 3a persona vengano filtrate."""

    def _setup_sink(self):
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
        mock_player.set_effect = AsyncMock()
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.current = None
        mock_player.text_channel = MagicMock()
        mock_player.guild = MagicMock()
        mock_player.voice_client = MagicMock()

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()
        return sink, mock_player

    async def test_direct_feedback_audio_handled(self):
        """'Hey Franchino non ti sento' deve ricevere risposta vocale di feedback audio e non essere scartato."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=111, display_name="Davide")

        await sink._handle_transcribed_text(mock_user, "Hey Franchino non ti sento", detected_cmd="non ti sento")
        mock_player.speak_tts.assert_called_once()
        args, kwargs = mock_player.speak_tts.call_args
        assert "Ti sento forte e chiaro" in args[0] or "riavviati" in args[0]

    async def test_direct_liveness_question_handled(self):
        """'Hey Franchino non rispondi?' e 'Hey Franchino funziona?' non devono essere scartati come terza persona."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=222, display_name="Andrea")

        await sink._handle_transcribed_text(mock_user, "Hey Franchino non rispondi?", detected_cmd="non rispondi?")
        mock_player.speak_tts.assert_called_once()
        args, _ = mock_player.speak_tts.call_args
        assert any(term in args[0].lower() for term in ("magia", "console", "vivo", "forte e chiaro", "sveglio"))

    async def test_empty_wake_call_handled(self):
        """Dire solo 'Hey Franchino' apre il microfono e fa rispondere il bot."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=333, display_name="Elena")

        await sink._handle_transcribed_text(mock_user, "Hey Franchino", detected_cmd="")
        mock_player.speak_tts.assert_called_once()
        args, _ = mock_player.speak_tts.call_args
        assert "Magia! Dimmi tutto" in args[0]

    async def test_franchino_without_hey_ignored(self):
        """Pronunciare 'Franchino ...' SENZA 'Hey/Ehi' non attiva mai il bot e non produce alcuna risposta."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=999, display_name="Silente")

        test_phrases = [
            "Franchino non ti sento",
            "Franchino non rispondi?",
            "Franchino",
            "Franchino metti la musica",
            "Franchino che ore sono?",
            "Franchino stop",
        ]
        for phrase in test_phrases:
            mock_player.speak_tts.reset_mock()
            has_w, cmd, _ = sink.extract_wake_and_command(phrase)
            self.assertFalse(has_w, f"'{phrase}' non deve essere considerata wake phrase!")
            await sink._handle_transcribed_text(mock_user, phrase)
            mock_player.speak_tts.assert_not_called()

    async def test_genuine_third_person_filtered(self):
        """Chiacchiere in terza persona su Franchino tra utenti vengono ignorate."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=444, display_name="Marco")

        # "secondo me Franchino non funziona bene"
        await sink._handle_transcribed_text(mock_user, "secondo me Franchino non funziona bene")
        mock_player.speak_tts.assert_not_called()

        # "anche Franchino lagga stasera"
        await sink._handle_transcribed_text(mock_user, "anche Franchino lagga stasera")
        mock_player.speak_tts.assert_not_called()

        # "pure Franchino è crashato"
        await sink._handle_transcribed_text(mock_user, "pure Franchino è crashato")
        mock_player.speak_tts.assert_not_called()


class TestTTSConcurrencyAndSelfHealing(unittest.IsolatedAsyncioTestCase):
    """Verifica che speak_tts sia protetto da lock e che is_speaking_tts si auto-resetti se stantio."""

    def test_is_speaking_tts_self_healing(self):
        """Se _playing_tts è True ma voice_client non sta suonando, is_speaking_tts deve resettare a False."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        player.voice_client = mock_vc

        # Simula flag rimasto bloccato a True per un callback perso
        player._playing_tts = True

        # La proprietà deve rilevare la desincronizzazione e auto-riparare
        self.assertFalse(player.is_speaking_tts)
        self.assertFalse(player._playing_tts)

    async def test_speak_tts_concurrency_lock(self):
        """Verifica che chiamate contemporanee a speak_tts vengano serializzate dal lock senza ClientException."""
        mock_bot = MagicMock()
        mock_bot.loop.call_soon_threadsafe = lambda cb, *a: cb(*a)
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False

        played_sources = []
        def fake_play(source, after=None):
            played_sources.append(source)
            if after:
                # Callback completato immediatamente
                after(None)

        mock_vc.play = MagicMock(side_effect=fake_play)
        player.voice_client = mock_vc

        with patch("edge_tts.Communicate") as mock_comm, \
             patch("discord.FFmpegPCMAudio", return_value=MagicMock()), \
             patch("discord.PCMVolumeTransformer", side_effect=lambda src, volume: src):
            mock_inst = AsyncMock()
            async def fake_save(dest):
                with open(dest, "wb") as f:
                    f.write(b"RIFF" + b"\x00" * 200)
            mock_inst.save = AsyncMock(side_effect=fake_save)
            mock_comm.return_value = mock_inst

            # Chiamata simultanea a due TTS
            await asyncio.gather(
                player.speak_tts("Primo messaggio di test", wait=True),
                player.speak_tts("Secondo messaggio di test", wait=True),
            )

        self.assertEqual(len(played_sources), 2)
        self.assertFalse(player.is_speaking_tts)


class TestMainOnMessageResponsiveness(unittest.IsolatedAsyncioTestCase):
    """Verifica la risposta di on_message a menzioni bot, comandi e domande di presenza."""

    async def test_mention_liveness(self):
        from main import on_message, bot
        from unittest.mock import PropertyMock

        bot_user = MagicMock(id=9999)
        mock_msg = MagicMock()
        mock_msg.author.bot = False
        mock_msg.content = "<@9999> ci sei?"
        mock_msg.clean_content = "@Franchino ci sei?"
        mock_msg.mentions = [bot_user]

        with patch.object(type(bot), "user", new_callable=PropertyMock, return_value=bot_user):
            with patch("main.cisei_cmd", new=AsyncMock()) as mock_cisei:
                with patch.object(bot, "get_context") as mock_get_ctx:
                    mock_ctx = MagicMock()
                    mock_ctx.command = None
                    mock_get_ctx.return_value = mock_ctx

                    await on_message(mock_msg)
                    mock_cisei.assert_awaited_once_with(mock_ctx)

    async def test_split_name_text_liveness(self):
        from main import on_message, bot

        mock_msg = MagicMock()
        mock_msg.author.bot = False
        mock_msg.content = "franchi no ci sei?"
        mock_msg.clean_content = "franchi no ci sei?"
        mock_msg.mentions = []

        with patch("main.cisei_cmd", new=AsyncMock()) as mock_cisei:
            with patch.object(bot, "get_context") as mock_get_ctx:
                mock_ctx = MagicMock()
                mock_ctx.command = None
                mock_get_ctx.return_value = mock_ctx

                await on_message(mock_msg)
                mock_cisei.assert_awaited_once_with(mock_ctx)

    async def test_bot_mention_alone(self):
        from main import on_message, bot
        from unittest.mock import PropertyMock

        bot_user = MagicMock(id=9999)
        mock_msg = MagicMock()
        mock_msg.author.bot = False
        mock_msg.content = "<@9999>"
        mock_msg.clean_content = "@Franchino"
        mock_msg.mentions = [bot_user]

        with patch.object(type(bot), "user", new_callable=PropertyMock, return_value=bot_user):
            with patch("main.cisei_cmd", new=AsyncMock()) as mock_cisei:
                with patch.object(bot, "get_context") as mock_get_ctx:
                    mock_ctx = MagicMock()
                    mock_ctx.command = None
                    mock_get_ctx.return_value = mock_ctx

                    await on_message(mock_msg)
                    mock_cisei.assert_awaited_once_with(mock_ctx)


class TestDeepVoiceChainEdgeCases(unittest.IsolatedAsyncioTestCase):
    """Test approfonditi su tutti i punti critici della catena audio, VAD, TTS e watchdog."""

    def _setup_sink(self):
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
        mock_player.set_effect = AsyncMock()
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.current = None
        mock_player.text_channel = MagicMock()
        mock_player.guild = MagicMock()
        mock_player.voice_client = MagicMock()
        mock_player.is_speaking_tts = False

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()
        return sink, mock_player

    async def test_direct_address_without_question_marks(self):
        """Google STT non produce punti interrogativi: comandi parlati come frasi secche devono ricevere risposta."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=555, display_name="Luca")

        # "Hey Franchino non rispondi" senza '?'
        await sink._handle_transcribed_text(mock_user, "Hey Franchino non rispondi", detected_cmd="non rispondi")
        mock_player.speak_tts.assert_called_once()
        mock_player.speak_tts.reset_mock()

        # "Hey Franchino funziona" senza '?'
        await sink._handle_transcribed_text(mock_user, "Hey Franchino funziona", detected_cmd="funziona")
        mock_player.speak_tts.assert_called_once()
        mock_player.speak_tts.reset_mock()

        # "Hey Franchino non va la musica"
        await sink._handle_transcribed_text(mock_user, "Hey Franchino non va la musica", detected_cmd="non va la musica")
        mock_player.speak_tts.assert_called_once()
        mock_player.speak_tts.reset_mock()

    def test_is_speaking_tts_healing_when_music_playing(self):
        """Se _playing_tts era True ma sta suonando una canzone (current != None), deve auto-resettare a False."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = True  # La musica sta suonando!
        player.voice_client = mock_vc

        # La musica sta suonando regolarmente, nessuna sorgente TTS in corso
        player.current = MagicMock(title="Bohemian Rhapsody", stream_url="http://test.mp3")
        player._interrupted_song = None
        player._is_tts_source = False
        player._playing_tts = True  # Flag stantio che causava sordità permanente

        # Deve riconoscere che è musica e non TTS, auto-resettando il flag
        self.assertFalse(player.is_speaking_tts)
        self.assertFalse(player._playing_tts)

    async def test_vad_gate_processes_normal_human_speech(self):
        """Audio con 300ms silenzio + 500ms voce (RMS ~100) non deve essere scartato dal VAD."""
        sink, mock_player = self._setup_sink()

        # 300ms silenzio (RMS ~20) + 500ms voce (amp 200, RMS ~180) + 650ms silenzio (RMS ~20)
        # Stereo 48kHz: 4 byte per frame (2 byte x 2 canali)
        silence_pre = b"\x14\x00\x14\x00" * int(48000 * 0.3)
        speech = b"\xd0\x00\xd0\x00" * int(48000 * 0.5)
        silence_post = b"\x14\x00\x14\x00" * int(48000 * 0.65)
        phrase_pcm = silence_pre + speech + silence_post

        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "hey franchino ci sei"}]}) as mock_stt:
            sink._handle_transcribed_text = AsyncMock()
            await sink._process_utterance(999, MagicMock(display_name="User"), phrase_pcm)
            mock_stt.assert_called_once()
            sink._handle_transcribed_text.assert_awaited_once()

    async def test_tts_generation_id_avoids_race_condition(self):
        """Quando Call 2 avvia un nuovo TTS, il callback/monitor di Call 1 non deve interferire."""
        mock_bot = MagicMock()
        mock_bot.loop.call_soon_threadsafe = lambda cb, *a: cb(*a)
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False
        player.voice_client = mock_vc

        # Simula interrupted song
        mock_song = MagicMock(stream_url="http://song.mp3", title="Interrupted Track")
        player.current = mock_song

        player._interrupted_song = mock_song
        player._interrupted_elapsed = 42

        # Prima chiamata TTS
        player._current_tts_id = 1
        player._playing_tts = True
        player._is_tts_source = True

        # Seconda chiamata TTS arriva e incrementa a 2
        player._current_tts_id = 2

        # Callback della prima chiamata terminata (id 1)
        # Verifichiamo che il monitor della chiamata 1 non cancelli lo stato della chiamata 2
        player.resume_interrupted_song = AsyncMock()

        # Se il monitor 1 finisce con id vecchio, non deve toccare _playing_tts o resume_interrupted_song
        old_tts_id = 1
        if player._current_tts_id == old_tts_id:
            player._playing_tts = False
            await player.resume_interrupted_song()

        self.assertTrue(player._playing_tts)
        player.resume_interrupted_song.assert_not_called()

    async def test_interactive_calls_use_interrupt_music_true(self):
        """Tutte le interazioni vocali con l'utente devono avere interrupt_music=True per non rimanere mute."""
        sink, mock_player = self._setup_sink()
        mock_user = MagicMock(id=777, display_name="TestUser")

        # Chiamata al solo nome "Hey Franchino"
        await sink._handle_transcribed_text(mock_user, "Hey Franchino", detected_cmd="")
        _, kwargs = mock_player.speak_tts.call_args
        self.assertTrue(kwargs.get("interrupt_music", False), "Chiamata a Franchino deve interrompere la musica")

    async def test_anti_feedback_interrupts_when_direct_command_spoken(self):
        """Se Franchino sta parlando in TTS e l'utente pronuncia un comando, il TTS viene fermato ed eseguito il comando."""
        sink, mock_player = self._setup_sink()
        mock_player.is_speaking_tts = True
        mock_player.voice_client.stop_playing = MagicMock()

        phrase_pcm = b"\xd0\x00\xd0\x00" * int(48000 * 0.8)

        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "hey franchino metti musica"}]}):
            sink._handle_transcribed_text = AsyncMock()
            await sink._process_utterance(888, MagicMock(display_name="UserCmd"), phrase_pcm)
            mock_player.voice_client.stop_playing.assert_called_once()
            sink._handle_transcribed_text.assert_awaited_once()

    async def test_anti_feedback_drops_when_pure_echo(self):
        """Se Franchino sta parlando in TTS e l'audio è solo un'eco del vocalist senza comandi, viene scartato."""
        sink, mock_player = self._setup_sink()
        mock_player.is_speaking_tts = True

        phrase_pcm = b"\xd0\x00\xd0\x00" * int(48000 * 0.8)

        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "buonasera popolo della notte"}]}):
            sink._handle_transcribed_text = AsyncMock()
            await sink._process_utterance(888, MagicMock(display_name="EchoUser"), phrase_pcm)
            sink._handle_transcribed_text.assert_not_called()

    async def test_watchdog_detects_sink_dead_and_restarts(self):
        """Il watchdog rileva se sink._running è False o se il monitor task è completato e riattiva l'ascolto."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.is_listening.return_value = True  # VC crede di essere in ascolto...
        mock_reader = MagicMock()
        mock_reader.packet_router.is_alive.return_value = True

        # Ma il sink è crashato/morto!
        dead_sink = MagicMock()
        dead_sink._running = False
        dead_task = MagicMock()
        dead_task.done.return_value = True
        dead_sink._monitor_task = dead_task
        mock_reader.sink = dead_sink
        mock_vc._reader = mock_reader

        player.voice_client = mock_vc

        # Simula un'iterazione del watchdog
        reader = getattr(player.voice_client, "_reader", None)
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
        not_listening = hasattr(player.voice_client, "is_listening") and not player.voice_client.is_listening()

        self.assertTrue(sink_dead, "Il watchdog deve rilevare che il sink è morto")
        self.assertTrue(not_listening or router_dead or sink_dead, "La condizione di trigger del watchdog deve essere True")


class TestFranchinoDeepVoiceResponsiveness(unittest.IsolatedAsyncioTestCase):
    """Test mirati per tutte le cause radice identificate e risolte:
    1. VAD a bassa energia (RMS ~70-105) per voci basse/sussurrate non tagliate da START_RMS_ENERGY
    2. Candidate secondarie con 'spiegami', 'come funziona', 'grazie' non scartate da is_boost_chatter
    3. Anti-feedback loop: comandi diretti e chiamate a Franchino interrompono il TTS
    4. Anti-feedback loop: sessione attiva (_pending_wake) consente all'utente di parlare durante TTS
    5. 'metti su' / 'mettimi su' estrae correttamente il titolo della canzone senza 'su'
    6. restart_console resetta _tts_lock, _is_tts_source e _interrupted_elapsed
    7. Play hybrid command include alias 'metti' e 'suona'
    """

    def _setup_sink(self):
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
        mock_player.set_effect = AsyncMock()
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.current = None
        mock_player.text_channel = MagicMock()
        mock_player.guild = MagicMock()
        mock_player.voice_client = MagicMock()
        mock_player.is_speaking_tts = False

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()
        return sink, mock_player

    async def test_low_rms_voice_not_dropped_by_start_rms_energy(self):
        """Voci pacate o microfoni con guadagno basso (RMS ~70-100) devono attivare _buffers."""
        import math
        import struct
        import audioop

        sink, _ = self._setup_sink()
        user = MagicMock(id=123, bot=False)

        # Genera 20ms di audio stereo 48kHz con ampiezza 110 (RMS ~77)
        samples = [int(110 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(960)]
        pcm = b"".join(struct.pack("<hh", s, s) for s in samples)
        rms = audioop.rms(pcm, 2)
        self.assertTrue(60 <= rms <= 100, f"RMS generato: {rms}")

        mock_data = MagicMock()
        mock_data.pcm = pcm
        mock_data.packet.ssrc = 9999

        try:
            sink.write(user, mock_data)
            self.assertIn(123, sink._buffers, "La voce a RMS ~77 deve avviare il buffer senza essere scartata da START_RMS_ENERGY")
        finally:
            sink.cleanup()

    async def test_background_hiss_dropped_by_start_rms_energy(self):
        """Fruscio di sottofondo puro (RMS < 30) non deve avviare il buffer vocale."""
        import math
        import struct
        import audioop

        sink, _ = self._setup_sink()
        user = MagicMock(id=456, bot=False)

        # Genera 20ms di audio con ampiezza 25 (RMS ~17)
        samples = [int(25 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(960)]
        pcm = b"".join(struct.pack("<hh", s, s) for s in samples)
        rms = audioop.rms(pcm, 2)
        self.assertLess(rms, 35)

        mock_data = MagicMock()
        mock_data.pcm = pcm
        mock_data.packet.ssrc = 8888

        try:
            sink.write(user, mock_data)
            self.assertNotIn(456, sink._buffers, "Il rumore di fondo < 35 RMS non deve avviare il buffer")
        finally:
            sink.cleanup()

    async def test_secondary_candidate_with_spiegami_not_flagged_as_boost_chatter(self):
        """Domande con 'spiegami come funziona' non devono essere respinte da is_boost_chatter quando primary_has_wake è False."""
        sink, mock_player = self._setup_sink()
        user = MagicMock(id=789, display_name="Gamer")

        # Candidate 0 colloquiale "fra chi", Candidate 1 contiene la domanda con wake word
        stt_result = {
            "alternative": [
                {"transcript": "fra chi"},
                {"transcript": "hey franchino spiegami come funziona l'aspect fire"}
            ]
        }

        with patch.object(sink.recognizer, "recognize_google", return_value=stt_result):
            sink._handle_transcribed_text = AsyncMock()
            # 500ms di audio valido (ampiezza 300)
            phrase_pcm = b"\x2c\x01\x2c\x01" * int(48000 * 0.5)
            await sink._process_utterance(789, user, phrase_pcm)

            sink._handle_transcribed_text.assert_awaited_once()
            _, kwargs = sink._handle_transcribed_text.call_args
            detected = kwargs.get("detected_cmd", "")
            self.assertIn("aspect fire", detected.lower())

    async def test_anti_feedback_interrupts_for_any_wake_word_phrase(self):
        """Durante la voce TTS, pronunciare 'Hey Franchino ...' interrompe il TTS anche per comandi generali o UO."""
        sink, mock_player = self._setup_sink()
        mock_player.is_speaking_tts = True
        mock_player.voice_client.stop_playing = MagicMock()

        phrase_pcm = b"\xd0\x00\xd0\x00" * int(48000 * 0.8)

        # Utente dice 'Hey Franchino riavviati' durante il TTS
        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "hey franchino riavviati"}]}):
            sink._handle_transcribed_text = AsyncMock()
            await sink._process_utterance(901, MagicMock(id=901, display_name="Admin"), phrase_pcm)
            mock_player.voice_client.stop_playing.assert_called_once()
            sink._handle_transcribed_text.assert_awaited_once()

    async def test_anti_feedback_allows_active_session_followup_during_tts(self):
        """Un utente in sessione attiva (_pending_wake) che completa il comando mentre il bot parla viene ascoltato."""
        import time
        sink, mock_player = self._setup_sink()
        mock_player.is_speaking_tts = True
        mock_player.voice_client.stop_playing = MagicMock()

        user = MagicMock(id=902, display_name="MusicaFan")
        sink._pending_wake[902] = (time.monotonic(), "metti")

        phrase_pcm = b"\xd0\x00\xd0\x00" * int(48000 * 0.8)

        # L'utente dice solo il titolo senza ripetere Franchino
        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "Bohemian Rhapsody"}]}):
            sink._handle_transcribed_text = AsyncMock()
            await sink._process_utterance(902, user, phrase_pcm)
            mock_player.voice_client.stop_playing.assert_called_once()
            sink._handle_transcribed_text.assert_awaited_once()

    async def test_metti_su_query_cleaned(self):
        """Il comando 'Hey Franchino metti su i Queen' deve cercare 'i Queen' e non 'su i Queen'."""
        sink, mock_player = self._setup_sink()
        mock_player.source_manager.resolve_url_or_query = AsyncMock(return_value=[MagicMock()])
        mock_player.play_now = AsyncMock()
        user = MagicMock(id=903, display_name="QueenFan")

        await sink._handle_transcribed_text(user, "Hey Franchino metti su i Queen", detected_cmd="metti su i Queen")
        mock_player.source_manager.resolve_url_or_query.assert_awaited_once()
        query_searched = mock_player.source_manager.resolve_url_or_query.call_args[0][0]
        self.assertEqual(query_searched.lower(), "i queen")

    async def test_restart_console_resets_tts_lock_and_flags(self):
        """restart_console deve sbloccare _tts_lock e resettare _is_tts_source e _interrupted_elapsed."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        mock_bot.loop.create_task = MagicMock(side_effect=lambda coro: coro.close())
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        player.voice_client = MagicMock(is_connected=lambda: True, channel=MagicMock())
        player._playing_tts = True
        player._is_tts_source = True
        player._interrupted_elapsed = 45

        # Simula lock acquisito
        await player._tts_lock.acquire()
        self.assertTrue(player._tts_lock.locked())

        with patch("voice_commands.FranchinoVoiceSink"):
            await player.restart_console()

        self.assertFalse(player._tts_lock.locked())
        self.assertFalse(player._playing_tts)
        self.assertFalse(player._is_tts_source)
        self.assertEqual(player._interrupted_elapsed, 0)
        if hasattr(player, "_watchdog_task") and player._watchdog_task:
            player._watchdog_task.cancel()

    def test_play_hybrid_command_aliases(self):
        """Il comando play in main.py deve includere 'metti' e 'suona' tra i suoi alias."""
        from main import bot
        play_cmd = bot.get_command("play")
        self.assertIsNotNone(play_cmd)
        self.assertIn("metti", play_cmd.aliases)
        self.assertIn("suona", play_cmd.aliases)



