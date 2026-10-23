import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import discord
from sources import SongInfo, SourceManager
from player import GuildPlayer


class TestMusicPlaybackSpeedAndOptimization(unittest.IsolatedAsyncioTestCase):
    """Test suite per la massima velocità di avvio della riproduzione musicale (YouTube & Spotify)."""

    def setUp(self):
        self.source_manager = SourceManager()

    def test_in_memory_cache_hit_returns_song_instantly(self):
        """Verifica che una query già presente nella cache in memoria ritorni istantaneamente (0 ms) con requester aggiornato."""
        dummy_song = SongInfo(
            title="Franchino - Magia",
            webpage_url="https://www.youtube.com/watch?v=TESTMAGIA11",
            stream_url="https://rr1---sn-dummy.googlevideo.com/videoplayback?id=123",
            duration=320,
            thumbnail="https://i.ytimg.com/vi/TESTMAGIA11/hqdefault.jpg",
            uploader="Franchino Official",
            requester="UserA",
            source_type="youtube",
            http_headers={"User-Agent": "FastStreamer/1.0"},
            is_direct_stream=True,
        )
        self.source_manager._cache_song("franchino magia", dummy_song)

        # Recupero da cache
        cached = self.source_manager._get_cached_song("franchino magia", requester="UserB")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.title, "Franchino - Magia")
        self.assertEqual(cached.stream_url, dummy_song.stream_url)
        self.assertEqual(cached.requester, "UserB")  # Aggiornato al nuovo richiedente
        self.assertTrue(cached.is_direct_stream)
        self.assertEqual(cached.http_headers, {"User-Agent": "FastStreamer/1.0"})

    def test_ensure_stream_url_returns_immediately_for_direct_http_url(self):
        """Verifica che ensure_stream_url ritorni in 0 ms senza invocare yt-dlp se lo stream_url è già presente."""
        song = SongInfo(
            title="Fast Direct Stream",
            webpage_url="https://www.youtube.com/watch?v=FASTSTREAM1",
            stream_url="https://rr1---sn-direct.googlevideo.com/videoplayback?id=fast",
            duration=180,
            source_type="youtube",
            is_direct_stream=True,
            http_headers={"User-Agent": "DirectUserAgent/1.0"}
        )

        with patch.object(self.source_manager, "_extract_youtube_single") as mock_extract, \
             patch.object(self.source_manager, "_download_youtube_audio") as mock_dl:
            import asyncio
            result = asyncio.run(self.source_manager.ensure_stream_url(song))
            self.assertEqual(result, song.stream_url)
            mock_extract.assert_not_called()
            mock_dl.assert_not_called()

    def test_ensure_stream_url_returns_immediately_for_cached_local_file(self):
        """Verifica che ensure_stream_url ritorni subito se stream_url punta a un file locale esistente."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(b"RIFF dummy audio data")
            local_path = f.name

        try:
            song = SongInfo(
                title="Cached Local Track",
                webpage_url="https://www.youtube.com/watch?v=LOCALTRACK1",
                stream_url=local_path,
                duration=200,
                source_type="youtube",
                is_direct_stream=False
            )
            import asyncio
            result = asyncio.run(self.source_manager.ensure_stream_url(song))
            self.assertEqual(result, local_path)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    def test_ffmpeg_audio_source_probe_and_headers_configuration(self):
        """Verifica che _build_ffmpeg_audio_source configuri probe ultra-veloce e -headers per stream di rete."""
        bot_mock = MagicMock()
        guild_mock = MagicMock()
        player = GuildPlayer(bot_mock, guild_mock, self.source_manager)

        headers = {
            "User-Agent": "FranchinoTest/2.0",
            "Accept": "*/*"
        }
        stream_url = "https://rr2---sn-direct.googlevideo.com/videoplayback?id=abc"

        with patch("discord.FFmpegPCMAudio") as mock_ffmpeg, \
             patch("discord.PCMVolumeTransformer", side_effect=lambda src, volume: src):
            player._build_ffmpeg_audio_source(stream_url, seek_seconds=15, http_headers=headers)
            mock_ffmpeg.assert_called_once()
            _, kwargs = mock_ffmpeg.call_args
            before_options = kwargs.get("before_options", "")

            # Verifica flag di probe ultra-veloce
            self.assertIn("-probesize 65536", before_options)
            self.assertIn("-analyzeduration 100000", before_options)
            self.assertIn("-fflags +nobuffer+fastseek", before_options)
            self.assertIn("-reconnect 1", before_options)
            self.assertIn("-ss 15", before_options)
            # Verifica che le intestazioni HTTP siano passate
            self.assertIn('-headers "', before_options)
            self.assertIn("User-Agent: FranchinoTest/2.0", before_options)

    @patch("discord.FFmpegPCMAudio")
    @patch("discord.PCMVolumeTransformer", side_effect=lambda src, volume: src)
    async def test_play_next_uses_direct_stream_with_headers(self, mock_volume, mock_ffmpeg):
        """Verifica che play_next passi stream_url e http_headers alla sorgente FFmpeg."""
        bot_mock = MagicMock()
        guild_mock = MagicMock()
        guild_mock.id = 12345
        player = GuildPlayer(bot_mock, guild_mock, self.source_manager)
        player._ensure_voice_listening = MagicMock()
        player.voice_client = MagicMock()
        player.voice_client.is_connected.return_value = True

        song = SongInfo(
            title="Direct Stream Test",
            webpage_url="https://www.youtube.com/watch?v=DIRECTTEST1",
            stream_url="https://rr3---sn-direct.googlevideo.com/videoplayback?id=direct",
            duration=240,
            http_headers={"User-Agent": "DirectAgent/1.0"},
            is_direct_stream=True
        )
        player.queue.append(song)

        with patch.object(player.source_manager, "ensure_stream_url", new=AsyncMock(return_value=song.stream_url)):
            with patch.object(player, "_send_now_playing_message", new=AsyncMock()):
                await player.play_next()
                player.voice_client.play.assert_called_once()
                mock_ffmpeg.assert_called_once()
                _, kwargs = mock_ffmpeg.call_args
                before_options = kwargs.get("before_options", "")
                self.assertIn("-probesize 65536", before_options)
                self.assertIn("User-Agent: DirectAgent/1.0", before_options)

    @patch("discord.FFmpegPCMAudio")
    @patch("discord.PCMVolumeTransformer", side_effect=lambda src, volume: src)
    async def test_play_next_fallback_to_download_if_direct_stream_fails(self, mock_volume, mock_ffmpeg):
        """Verifica che se la riproduzione diretta genera un errore, scatta il fallback automatico a download audio locale."""
        bot_mock = MagicMock()
        guild_mock = MagicMock()
        guild_mock.id = 12345
        player = GuildPlayer(bot_mock, guild_mock, self.source_manager)
        player._ensure_voice_listening = MagicMock()
        player.voice_client = MagicMock()
        player.voice_client.is_connected.return_value = True

        song = SongInfo(
            title="Broken Direct Stream",
            webpage_url="https://www.youtube.com/watch?v=BROKENSTREAM",
            stream_url="https://rr1---sn-invalid.googlevideo.com/videoplayback?err=403",
            duration=240,
            http_headers={"User-Agent": "Agent/1.0"},
            is_direct_stream=True
        )
        player.queue.append(song)

        downloaded_song = SongInfo(
            title="Broken Direct Stream (Downloaded)",
            webpage_url="https://www.youtube.com/watch?v=BROKENSTREAM",
            stream_url="/tmp/franchino_cache/BROKENSTREAM.opus",
            duration=240,
            is_direct_stream=False
        )

        call_count = 0
        def play_side_effect(source, after=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Il primo tentativo su direct stream fallisce
                raise RuntimeError("FFmpeg direct stream connection refused")
            # Il secondo tentativo su file locale scaricato ha successo
            return None

        player.voice_client.play.side_effect = play_side_effect

        with patch.object(player.source_manager, "ensure_stream_url", new=AsyncMock(return_value=song.stream_url)), \
             patch.object(player.source_manager, "_download_youtube_audio", return_value=downloaded_song), \
             patch.object(player, "_send_now_playing_message", new=AsyncMock()):
            await player.play_next()
            self.assertEqual(call_count, 2)
            self.assertEqual(player.current.stream_url, "/tmp/franchino_cache/BROKENSTREAM.opus")
            self.assertFalse(player.current.is_direct_stream)

    def test_is_youtube_bot_or_cookie_error_detection(self):
        """Verifica che la funzione riconosca tutte le varianti di errore antibot o cookie scaduti."""
        from sources import is_youtube_bot_or_cookie_error

        err_samples = [
            "ERROR: [youtube] uIylHZcRMBI: Sign in to confirm you’re not a bot. Use --cookies-from-browser",
            "ERROR: [youtube] Sign in to confirm you're not a bot.",
            "Please sign in. Use --cookies-from-browser or --cookies for the authentication",
            "yt_dlp.utils.ExtractorError: Bot verification failed",
            "Login required to confirm you’re not a bot",
            "Requested format is not available. Use --list-formats",
            "The page needs to be reloaded.",
        ]
        for err in err_samples:
            self.assertTrue(is_youtube_bot_or_cookie_error(err), f"Fallito riconoscimento per: {err}")

        # Errori normali non devono essere scambiati per bot check
        self.assertFalse(is_youtube_bot_or_cookie_error("HTTP Error 404: Not Found"))
        self.assertFalse(is_youtube_bot_or_cookie_error("Video unavailable in this country"))

    def test_disable_cookies_strips_cookiefile_and_prevents_reloading(self):
        """Verifica che disable_cookies rimuova cookiefile dalle opzioni e non li ricarichi."""
        sm = SourceManager()
        sm.ydl_opts_stream["cookiefile"] = "/tmp/fake_cookies.txt"
        sm.ydl_opts_flat["cookiefile"] = "/tmp/fake_cookies.txt"
        sm._cookies_enabled = True

        sm.disable_cookies("Test auto-heal")
        self.assertNotIn("cookiefile", sm.ydl_opts_stream)
        self.assertNotIn("cookiefile", sm.ydl_opts_flat)
        self.assertFalse(sm._cookies_enabled)
        self.assertTrue(sm._cookies_explicitly_disabled)

        # Tentativo di re-configure cookies deve essere ignorato
        sm._configure_cookies()
        self.assertNotIn("cookiefile", sm.ydl_opts_stream)

    def test_extract_youtube_single_auto_heals_on_bot_error(self):
        """Verifica che _extract_youtube_single disabiliti i cookie al primo errore bot e riesca con client mobili."""
        sm = SourceManager()
        sm.ydl_opts_stream["cookiefile"] = "/tmp/corrupt_cookies.txt"
        sm._cookies_enabled = True

        call_records = []
        expected_song = SongInfo(
            title="Bypassed Song",
            webpage_url="https://www.youtube.com/watch?v=BYPASSED123",
            stream_url="https://rr1.googlevideo.com/videoplayback?id=bypassed",
            duration=150,
            is_direct_stream=True,
        )

        def mock_do_extract(target, requester, opts, download=False):
            call_records.append({"opts": opts, "download": download})
            if "cookiefile" in opts:
                raise RuntimeError("ERROR: [youtube] ABC: Sign in to confirm you’re not a bot.")
            return expected_song

        with patch.object(sm, "_do_extract_single", side_effect=mock_do_extract):
            res = sm._extract_youtube_single("https://www.youtube.com/watch?v=BYPASSED123", None)

            self.assertEqual(res.title, "Bypassed Song")
            self.assertEqual(res.stream_url, expected_song.stream_url)
            # Verifica che i cookie siano stati disabilitati automaticamente
            self.assertFalse(sm._cookies_enabled)
            self.assertNotIn("cookiefile", sm.ydl_opts_stream)
            # Verifica che il secondo tentativo abbia usato client mobili senza cookie
            self.assertEqual(len(call_records), 2)
            second_call = call_records[1]
            self.assertNotIn("cookiefile", second_call["opts"])
            clients = second_call["opts"]["extractor_args"]["youtube"]["player_client"]
            self.assertIn("android", clients)
            self.assertIn("visionos", clients)

    async def test_ensure_stream_url_falls_back_to_download_audio_on_error(self):
        """Verifica che ensure_stream_url ripieghi su _download_youtube_audio se l'estrazione streaming solleva eccezione."""
        sm = SourceManager()
        song = SongInfo(
            title="Download Fallback Test",
            webpage_url="https://www.youtube.com/watch?v=DOWNLOADFALL1",
            stream_url=None,
            duration=120,
        )
        downloaded = SongInfo(
            title="Download Fallback Test",
            webpage_url="https://www.youtube.com/watch?v=DOWNLOADFALL1",
            stream_url="/tmp/franchino_cache/DOWNLOADFALL1.webm",
            duration=120,
            is_direct_stream=False,
        )

        with patch.object(sm, "_extract_youtube_single", side_effect=RuntimeError("Sign in to confirm you're not a bot")):
            with patch.object(sm, "_download_youtube_audio", return_value=downloaded):
                stream_url = await sm.ensure_stream_url(song)
                self.assertEqual(stream_url, "/tmp/franchino_cache/DOWNLOADFALL1.webm")
                self.assertEqual(song.stream_url, "/tmp/franchino_cache/DOWNLOADFALL1.webm")
                self.assertFalse(song.is_direct_stream)

    def test_extract_youtube_single_falls_back_to_soundcloud_on_bot_block(self):
        """Verifica che _extract_youtube_single ripieghi su SoundCloud se YouTube è bloccato da bot-check."""
        sm = SourceManager()
        expected_sc_song = SongInfo(
            title="Vamos A La Playa (SoundCloud)",
            webpage_url="https://soundcloud.com/test/vamos-a-la-playa",
            stream_url="https://playback.media-streaming.soundcloud.cloud/test.m3u8",
            duration=180,
            source_type="soundcloud",
            is_direct_stream=True,
        )

        with patch.object(sm, "_do_extract_single", side_effect=RuntimeError("Sign in to confirm you're not a bot")):
            with patch.object(sm, "_extract_soundcloud_fallback", return_value=expected_sc_song) as mock_sc:
                song = sm._extract_youtube_single("ytsearch1:Vamos a la playa", None)
                self.assertIsNotNone(song)
                self.assertEqual(song.source_type, "soundcloud")
                self.assertEqual(song.title, "Vamos A La Playa (SoundCloud)")
                mock_sc.assert_called_once()

    def test_soundcloud_embed_badge_and_color(self):
        """Verifica che SongInfo con source_type='soundcloud' generi il badge arancione SoundCloud."""
        sc_song = SongInfo(
            title="SoundCloud Hit",
            webpage_url="https://soundcloud.com/artist/hit",
            stream_url="https://stream.soundcloud.com/hit.m3u8",
            duration=200,
            source_type="soundcloud",
        )
        embed = sc_song.create_embed()
        self.assertEqual(embed.color.value, 0xFF5500)
        self.assertIn("SoundCloud", embed.footer.text)


if __name__ == "__main__":
    unittest.main()
