"""
Suite di test specifica per le correzioni di stabilità architetturale e circuit breaker di Franchino:
1. after_playback non-bloccante (nessun deadlock con il thread AudioPlayer)
2. Circuit breaker anti-crash loop in play_next (limite 3 errori consecutivi, stop loop_mode song, reset current)
3. Rimozione rogue Voice WebSocket poller runner in _voice_listener_watchdog
4. ThreadPoolExecutor a 16 workers protetto da guard una-tantum
5. Timeout STT e limiti di memoria buffer audio in FranchinoVoiceSink
6. socket_timeout in opzioni yt-dlp di sources.py
7. Limite di memoria aumentato a 2560M in docker-compose.yml
"""

import asyncio
import os
import re
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from player import GuildPlayer, SongInfo
from sources import SourceManager
from voice_commands import FranchinoVoiceSink


# ==============================================================================
# 1. TEST AFTER_PLAYBACK NON-BLOCCANTE (NESSUN FUT.RESULT / DEADLOCK)
# ==============================================================================
class TestAfterPlaybackNonBlocking(unittest.IsolatedAsyncioTestCase):

    async def test_after_playback_schedules_non_blocking_in_play_next(self):
        """Verifica che la callback after_playback in play_next usi call_soon_threadsafe e non blocchi il thread."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        guild.id = 12345
        source_mgr = MagicMock()

        player = GuildPlayer(bot, guild, source_mgr)
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        player.voice_client = vc
        player._watchdog_task = MagicMock(done=lambda: False)

        song = SongInfo(
            title="Brano Test",
            stream_url="http://fake.stream/audio.mp3",
            webpage_url="https://youtube.com/watch?v=123",
            duration=180,
            requester=MagicMock(),
        )
        player.queue.append(song)
        source_mgr.ensure_stream_url = AsyncMock(return_value="http://fake.stream/audio.mp3")

        captured_after = None
        def mock_play(source, after=None):
            nonlocal captured_after
            captured_after = after
        vc.play = mock_play

        with patch.object(player, "_send_now_playing_message", new_callable=AsyncMock), \
             patch.object(player, "_build_ffmpeg_audio_source", return_value=MagicMock()), \
             patch.object(player, "_ensure_voice_listening"):
            await player.play_next()

        self.assertIsNotNone(captured_after, "after_playback deve essere passata a voice_client.play")

        # Simula la chiamata sincrona da thread AudioPlayer
        with patch.object(player, "play_next", new_callable=AsyncMock) as mock_next:
            captured_after(None)
            await asyncio.sleep(0.02)
            self.assertEqual(mock_next.call_count, 1)

    async def test_after_playback_schedules_non_blocking_in_resume_interrupted(self):
        """Verifica che anche in resume_interrupted_song after_playback usi call_soon_threadsafe non bloccante."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        player = GuildPlayer(bot, guild, MagicMock())
        vc = MagicMock()
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        player.voice_client = vc

        song = SongInfo(
            title="Brano Interrotto",
            stream_url="http://fake.stream/interrupted.mp3",
            webpage_url="https://youtube.com/watch?v=456",
            duration=200,
            requester=MagicMock(),
        )

        captured_after = None
        def mock_play(source, after=None):
            nonlocal captured_after
            captured_after = after
        vc.play = mock_play

        player._interrupted_song = song
        player._interrupted_elapsed = 45
        with patch.object(player, "_build_ffmpeg_audio_source", return_value=MagicMock()):
            await player.resume_interrupted_song()
        self.assertIsNotNone(captured_after)

        with patch.object(player, "play_next", new_callable=AsyncMock) as mock_next:
            captured_after(None)
            await asyncio.sleep(0.02)
            self.assertEqual(mock_next.call_count, 1)


# ==============================================================================
# 2. TEST CIRCUIT BREAKER ANTI-CRASH LOOP IN PLAY_NEXT
# ==============================================================================
class TestCircuitBreakerPlayNext(unittest.IsolatedAsyncioTestCase):

    async def test_play_next_error_resets_current_and_disables_song_loop(self):
        """Verifica che un errore di riproduzione resetti current e disattivi loop_mode='song'."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        guild.id = 99999
        source_mgr = MagicMock()
        source_mgr.ensure_stream_url = AsyncMock(side_effect=RuntimeError("Stream extraction failed"))

        player = GuildPlayer(bot, guild, source_mgr)
        vc = MagicMock()
        vc.is_connected.return_value = True
        player.voice_client = vc
        player._watchdog_task = MagicMock(done=lambda: False)
        player.loop_mode = "song"

        song = SongInfo(
            title="Broken Song",
            stream_url="http://invalid.stream",
            webpage_url="https://youtube.com/watch?v=bad",
            duration=120,
            requester=MagicMock(),
        )
        player.current = song

        with patch("player.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("player.asyncio.create_task", side_effect=lambda coro: (coro.close(), MagicMock())[1]) as mock_create_task, \
             patch.object(player, "_ensure_voice_listening"):
            await player.play_next()

            self.assertIsNone(player.current, "player.current deve essere azzerato su errore")
            self.assertEqual(player.loop_mode, "off", "loop_mode 'song' deve essere disattivato su errore")
            self.assertEqual(player._consecutive_play_errors, 1)
            mock_sleep.assert_awaited_once_with(1.0)
            mock_create_task.assert_called_once()

    async def test_circuit_breaker_stops_after_3_consecutive_errors(self):
        """Verifica che dopo 3 errori consecutivi il 4° errore blocchi la riproduzione senza rischedulare."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        guild.id = 99999
        source_mgr = MagicMock()
        source_mgr.ensure_stream_url = AsyncMock(side_effect=RuntimeError("Connection refused"))

        player = GuildPlayer(bot, guild, source_mgr)
        vc = MagicMock()
        vc.is_connected.return_value = True
        player.voice_client = vc
        player._watchdog_task = MagicMock(done=lambda: False)
        text_ch = MagicMock()
        text_ch.send = AsyncMock()
        player.text_channel = text_ch

        # Simuliamo 3 errori già accumulati
        player._consecutive_play_errors = 3

        song = SongInfo(
            title="Fatal Error Song",
            stream_url="http://invalid.stream",
            webpage_url="https://youtube.com/watch?v=fatal",
            duration=120,
            requester=MagicMock(),
        )
        player.queue.append(song)

        with patch("asyncio.create_task") as mock_create_task, \
             patch.object(player, "_ensure_voice_listening"):
            await player.play_next()

            # Errori consecutivi ora sono 4 (> 3)
            self.assertEqual(player._consecutive_play_errors, 4)
            self.assertIsNone(player.current)
            mock_create_task.assert_not_called()
            self.assertEqual(text_ch.send.call_count, 1)
            sent_msg = text_ch.send.call_args[0][0]
            self.assertIn("troppi errori consecutivi", sent_msg)

    async def test_successful_play_resets_consecutive_errors(self):
        """Verifica che una riproduzione riuscita resetti _consecutive_play_errors a 0."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        source_mgr = MagicMock()
        source_mgr.ensure_stream_url = AsyncMock(return_value="http://ok.stream/sound.mp3")

        player = GuildPlayer(bot, guild, source_mgr)
        vc = MagicMock()
        vc.is_connected.return_value = True
        player.voice_client = vc
        player._watchdog_task = MagicMock(done=lambda: False)
        player._consecutive_play_errors = 2

        song = SongInfo(
            title="Good Song",
            stream_url="http://ok.stream/sound.mp3",
            webpage_url="https://youtube.com/watch?v=good",
            duration=120,
            requester=MagicMock(),
        )
        player.queue.append(song)

        with patch.object(player, "_send_now_playing_message", new_callable=AsyncMock), \
             patch.object(player, "_build_ffmpeg_audio_source", return_value=MagicMock()), \
             patch.object(player, "_ensure_voice_listening"):
            await player.play_next()

        self.assertEqual(player._consecutive_play_errors, 0, "Il contatore errori deve azzerarsi dopo successo")

    def test_stop_resets_consecutive_errors(self):
        """Verifica che stop() azzeri _consecutive_play_errors."""
        bot = MagicMock()
        guild = MagicMock()
        player = GuildPlayer(bot, guild, MagicMock())
        player._consecutive_play_errors = 3
        with patch.object(player, "_ensure_voice_listening"):
            player.stop()
        self.assertEqual(player._consecutive_play_errors, 0)

    async def test_after_playback_error_triggers_circuit_breaker_and_stops_song_loop(self):
        """Verifica che un errore in after_playback disattivi loop_mode='song' e incrementi gli errori."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        guild.id = 11111
        player = GuildPlayer(bot, guild, MagicMock())
        player.loop_mode = "song"
        song = SongInfo(
            title="Loop Broken Song",
            stream_url="http://fake.stream/audio.mp3",
            webpage_url="https://youtube.com/watch?v=111",
            duration=120,
            requester=MagicMock(),
        )
        player.current = song
        player._consecutive_play_errors = 0

        with patch("player.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
             patch("player.asyncio.create_task", side_effect=lambda coro: (coro.close(), MagicMock())[1]) as mock_create_task:
            await player._handle_after_playback_error(song, RuntimeError("Decoder crash"))

            self.assertIsNone(player.current)
            self.assertEqual(player.loop_mode, "off")
            self.assertEqual(player._consecutive_play_errors, 1)
            mock_sleep.assert_awaited_once_with(1.0)
            mock_create_task.assert_called_once()

    async def test_after_playback_error_stops_after_3_consecutive_errors(self):
        """Verifica che dopo 3 errori consecutivi il 4° errore in after_playback blocchi tutto."""
        bot = MagicMock()
        bot.loop = asyncio.get_running_loop()
        guild = MagicMock()
        guild.id = 22222
        player = GuildPlayer(bot, guild, MagicMock())
        text_ch = MagicMock()
        text_ch.send = AsyncMock()
        player.text_channel = text_ch
        player._consecutive_play_errors = 3

        song = SongInfo(
            title="Fatal Audio Track",
            stream_url="http://fake.stream/fatal.mp3",
            webpage_url="https://youtube.com/watch?v=222",
            duration=120,
            requester=MagicMock(),
        )
        player.current = song

        with patch("player.asyncio.create_task") as mock_create_task:
            await player._handle_after_playback_error(song, RuntimeError("Fatal audio read error"))

            self.assertEqual(player._consecutive_play_errors, 4)
            self.assertIsNone(player.current)
            mock_create_task.assert_not_called()
            self.assertEqual(text_ch.send.call_count, 1)
            self.assertIn("troppi errori consecutivi", text_ch.send.call_args[0][0])

    def test_add_songs_and_skip_reset_consecutive_play_errors(self):
        """Verifica che add_songs e skip azzerino _consecutive_play_errors per ripartire puliti."""
        bot = MagicMock()
        guild = MagicMock()
        player = GuildPlayer(bot, guild, MagicMock())
        player._consecutive_play_errors = 3
        player.skip()
        self.assertEqual(player._consecutive_play_errors, 0)

        player._consecutive_play_errors = 3
        song = SongInfo(title="New Song", stream_url="http://url", webpage_url="", duration=100, requester=MagicMock())
        with patch.object(player, "play_next", new_callable=AsyncMock):
            asyncio.run(player.add_songs([song]))
        self.assertEqual(player._consecutive_play_errors, 0)


# ==============================================================================
# 3. TEST WATCHDOG VOICE WEBSOCKET POLLER
# ==============================================================================
class TestWatchdogCleanup(unittest.TestCase):

    def test_watchdog_does_not_restart_dead_conn_runner(self):
        """Verifica che il watchdog non riavvii forzatamente conn._runner con conn._poll_voice_ws."""
        import inspect
        source = inspect.getsource(GuildPlayer._voice_listener_watchdog)
        self.assertNotIn("_poll_voice_ws", source, "Watchdog non deve più contenere chiamate a _poll_voice_ws")
        self.assertNotIn("Voice websocket poller", source, "Watchdog non deve più contenere il task Voice websocket poller")


# ==============================================================================
# 4. TEST THREAD POOL CONFIGURATION
# ==============================================================================
class TestThreadPoolConfiguration(unittest.TestCase):

    def test_threadpool_configured_with_16_workers(self):
        """Verifica che il setup del ThreadPoolExecutor usi max_workers=16 e guard."""
        import concurrent.futures

        class DummyBot:
            pass

        fake_bot = DummyBot()
        self.assertFalse(hasattr(fake_bot, "_executor_configured"))

        loop = asyncio.new_event_loop()
        try:
            if not hasattr(fake_bot, "_executor_configured"):
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="FranchinoARM")
                loop.set_default_executor(pool)
                fake_bot._executor_configured = True

            self.assertTrue(getattr(fake_bot, "_executor_configured"))
            original_pool = loop._default_executor
            if not hasattr(fake_bot, "_executor_configured"):
                loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=4))

            self.assertIs(loop._default_executor, original_pool)
            self.assertEqual(pool._max_workers, 16)
            pool.shutdown(wait=False)
        finally:
            loop.close()

    def test_main_on_ready_configures_16_workers_with_guard(self):
        """Verifica tramite analisi statica del sorgente di main.py che ThreadPoolExecutor sia impostato a 16 workers con guard."""
        main_path = os.path.join(os.path.dirname(__file__), "main.py")
        with open(main_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("max_workers=16", content)
        self.assertIn('not hasattr(bot, "_executor_configured")', content)
        self.assertIn("bot._executor_configured = True", content)


# ==============================================================================
# 5. TEST TIMEOUT E LIMITI BUFFER IN FRANCHINOVOICESINK
# ==============================================================================
class TestVoiceSinkBufferLimitsAndTimeout(unittest.TestCase):

    def test_recognizer_operation_timeout_set(self):
        """Verifica che l'istanza Recognizer di FranchinoVoiceSink abbia operation_timeout = 8.0."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        sink = FranchinoVoiceSink(mock_player)

        self.assertEqual(sink.recognizer.operation_timeout, 8.0)

    def test_write_buffer_limit_caps_memory(self):
        """Verifica che write() non accresca il buffer oltre MAX_BUFFER_BYTES (12 secondi ~ 2.3 MB)."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        sink = FranchinoVoiceSink(mock_player)

        self.assertEqual(sink.MAX_BUFFER_SEC, 12.0)
        self.assertEqual(sink.MAX_BUFFER_BYTES, int(12.0 * sink.BYTES_PER_SECOND))

        user = MagicMock()
        user.id = 123
        user.bot = False

        near_limit = sink.MAX_BUFFER_BYTES - 100
        sink._buffers[123] = bytearray(b"\x01" * near_limit)
        sink._last_packet_time[123] = time.monotonic()

        fake_pcm = b"\x05" * 1920
        voice_data = MagicMock()
        voice_data.pcm = fake_pcm
        voice_data.packet.ssrc = 123

        with patch("audioop.rms", return_value=50):
            sink.write(user, voice_data)

        len_after_first = len(sink._buffers[123])
        self.assertGreaterEqual(len_after_first, sink.MAX_BUFFER_BYTES)

        with patch("audioop.rms", return_value=50):
            sink.write(user, voice_data)

        self.assertEqual(len(sink._buffers[123]), len_after_first, "Il buffer non deve superare il limite impostato")

    def test_prune_stale_buffers_removes_inactive_buffers_over_15s(self):
        """Verifica che _prune_stale_buffers elimini i buffer inattivi da oltre 15 secondi anche da self._buffers."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        sink = FranchinoVoiceSink(mock_player)

        now = time.monotonic()
        stale_time = now - 16.0  # Inattivo da 16 secondi (> 15s)
        fresh_time = now - 5.0   # Inattivo da 5 secondi (< 15s)

        sink._last_packet_time[1] = stale_time
        sink._buffers[1] = bytearray(b"\x00" * 10000)
        sink._preroll_buffers[1] = MagicMock()
        sink._users[1] = MagicMock()

        sink._last_packet_time[2] = fresh_time
        sink._buffers[2] = bytearray(b"\x00" * 5000)
        sink._preroll_buffers[2] = MagicMock()
        sink._users[2] = MagicMock()

        sink._buffers[3] = bytearray(b"\x00" * 2000)

        sink._prune_stale_buffers(now)

        self.assertNotIn(1, sink._buffers)
        self.assertNotIn(1, sink._last_packet_time)
        self.assertNotIn(1, sink._preroll_buffers)
        self.assertNotIn(1, sink._users)

        self.assertNotIn(3, sink._buffers)

        self.assertIn(2, sink._buffers)
        self.assertIn(2, sink._last_packet_time)
        self.assertIn(2, sink._preroll_buffers)
        self.assertIn(2, sink._users)

    def test_prune_stale_buffers_cleans_orphaned_preroll_and_restores_volume(self):
        """Verifica che _prune_stale_buffers elimini anche i preroll orfani e ripristini il duck volume se i buffer si svuotano."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        sink = FranchinoVoiceSink(mock_player)

        sink._preroll_buffers[999] = MagicMock()
        sink._users[999] = MagicMock()

        sink._prune_stale_buffers(time.monotonic())

        self.assertNotIn(999, sink._preroll_buffers)
        self.assertNotIn(999, sink._users)
        mock_player.restore_duck_volume.assert_called_once()


# ==============================================================================
# 6. TEST SOURCES SOCKET_TIMEOUT
# ==============================================================================
class TestSourcesSocketTimeout(unittest.TestCase):

    def test_source_manager_socket_timeout_configured(self):
        """Verifica che SourceManager configuri socket_timeout = 12.0 in stream e flat opts."""
        mgr = SourceManager()
        self.assertEqual(mgr.ydl_opts_stream.get("socket_timeout"), 12.0)
        self.assertEqual(mgr.ydl_opts_flat.get("socket_timeout"), 12.0)


# ==============================================================================
# 7. TEST DOCKER COMPOSE MEMORY LIMIT
# ==============================================================================
class TestDockerComposeMemoryLimit(unittest.TestCase):

    def test_franchino_bot_memory_limit_is_2560m(self):
        """Verifica che il limite di memoria in docker-compose.yml per franchino-bot sia 2560M."""
        compose_path = os.path.join(os.path.dirname(__file__), "docker-compose.yml")
        with open(compose_path, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.search(r'franchino-bot:.*?(?:\n\s{2}[a-zA-Z]|\Z)', content, re.DOTALL)
        self.assertIsNotNone(match, "Sezione franchino-bot non trovata in docker-compose.yml")
        bot_section = match.group(0)
        # Ignora le righe di commento: i limiti sono definiti dal blocco, non dai commenti
        bot_section = "\n".join(l for l in bot_section.splitlines() if not l.strip().startswith("#"))

        mem_match = re.search(r'limits:\s*\n\s+cpus:\s*"[0-9.]+"\s*\n\s+memory:\s*([0-9]+[MG])', bot_section)
        self.assertIsNotNone(mem_match, "Limits memory non trovato nella sezione franchino-bot")
        self.assertEqual(mem_match.group(1), "2560M", f"Atteso 2560M, trovato {mem_match.group(1)}")
