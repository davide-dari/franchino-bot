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


if __name__ == "__main__":
    unittest.main()
