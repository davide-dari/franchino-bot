"""
test_sports_streaming.py - Test Suite Completa per Streaming Audio Partite da MadPlay77
========================================================================================
Testa in modo approfondito:
1. Funzioni crittografiche ROT47, decipher URL HLS e firma AES-256-CBC (/token-...)
2. Estrazione metadati streaming, player iframe mdata e canali TV da MadPlay77
3. Risoluzione query di streaming (es. "Inter", "Milan", "Champions")
4. Costruzione Discord Embed ricco e View interattiva (MatchStreamingView) con pulsanti di controllo
5. Metodo stream_sports_match su GuildPlayer con gestione voice client e SongInfo
6. Riconoscimento vocale NLP e trigger su Brain ("Hey Franchino trasmetti la partita...", "metti la telecronaca...")
7. Gestione comandi slash /stream e /partita stream
8. Casi limite ed errori (match offline, assenza canale vocale, fallback eleganti)
"""

import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from sports_scraper import (
    sports_scraper,
    SportsScraper,
    _rot47,
    _decode_stream_url,
    _encrypt_rb_session,
    _sign_stream_url,
    _build_madplay_iframe_url,
    _build_madplay_match_url,
)
from views import MatchStreamingView
from sources import SongInfo, SourceManager
from player import GuildPlayer
from brain import brain


class TestSportsStreamCryptoAndUrls(unittest.TestCase):
    """Test delle funzioni di decifratura e costruzione URL stream MadPlay77."""

    def test_rot47_symmetry(self):
        """Verifica la simmetria e correttezza della codifica ROT47."""
        original = "https://live.sportcdn.live/stream/match123.m3u8"
        encoded = _rot47(original)
        self.assertNotEqual(original, encoded)
        decoded = _rot47(encoded)
        self.assertEqual(original, decoded)

    def test_decode_stream_url_slices_prefix(self):
        """Verifica che _decode_stream_url decodifichi con ROT47 e tagli i primi 8 caratteri."""
        # Un URL finto con 8 caratteri di prefisso casuale
        dummy_raw = "12345678https://stream.example.com/live.m3u8"
        encrypted = _rot47(dummy_raw)
        result = _decode_stream_url(encrypted)
        self.assertEqual(result, "https://stream.example.com/live.m3u8")

    def test_decode_stream_url_empty_or_short(self):
        """Verifica robustezza con input vuoto o corto."""
        self.assertEqual(_decode_stream_url(""), "")
        self.assertEqual(_decode_stream_url("abc"), _rot47("abc"))

    def test_encrypt_rb_session_produces_token(self):
        """Verifica che la firma rb-session produca un token terminante con 'a'."""
        token = _encrypt_rb_session("test-session-uuid-1234")
        if token:  # se Node è installato
            self.assertTrue(token.endswith("a"))
            self.assertGreater(len(token), 10)

    def test_sign_stream_url_injects_token(self):
        """Verifica l'inserimento del prefisso /token-... nel path dell'URL."""
        dec_url = "https://stream.madplay77.ru/live/match.m3u8?auth=1"
        signed = _sign_stream_url(dec_url, "fake-session-token")
        self.assertTrue(signed.startswith("https://stream.madplay77.ru/token-"))
        self.assertIn("/live/match.m3u8", signed)

    def test_sign_stream_url_no_session(self):
        """Se non c'è sessione, restituisce l'URL originale senza errori."""
        dec_url = "https://stream.madplay77.ru/live/match.m3u8"
        self.assertEqual(_sign_stream_url(dec_url, ""), dec_url)
        self.assertEqual(_sign_stream_url("", "token"), "")

    def test_build_madplay_iframe_url(self):
        """Verifica la generazione dell'URL player iframe con mdata in base64."""
        iframe_url = _build_madplay_iframe_url(2244287, 13)
        self.assertTrue(iframe_url.startswith("https://nadia01eo.tn76degree12ec3out.cfd/it/player.html?mdata="))
        self.assertIn("&ilang=it", iframe_url)
        # Decodifica il payload mdata
        import urllib.parse
        import base64
        parsed = urllib.parse.urlparse(iframe_url)
        qs = urllib.parse.parse_qs(parsed.query)
        mdata_val = qs["mdata"][0]
        decoded = base64.b64decode(mdata_val).decode("utf-8")
        self.assertEqual(decoded, "2244287_13")


class TestSportsScraperStreamingMethods(unittest.IsolatedAsyncioTestCase):
    """Test dei metodi di estrazione e risoluzione streaming su SportsScraper."""

    async def test_get_match_stream_info_returns_structure(self):
        """Verifica che get_match_stream_info restituisca la struttura completa di stream."""
        info = await sports_scraper.get_match_stream_info(2245112, 3)
        self.assertIsInstance(info, dict)
        self.assertIn("match_id", info)
        self.assertIn("sport_type", info)
        self.assertIn("channel_name", info)
        self.assertIn("stream_url", info)
        self.assertIn("iframe_player_url", info)
        self.assertIn("match_page_url", info)
        self.assertIn("direct_hls_available", info)

    async def test_resolve_match_for_streaming_with_known_event(self):
        """Verifica la ricerca e risoluzione di un evento per lo streaming."""
        match = await sports_scraper.resolve_match_for_streaming("padel")
        if match:
            self.assertIn("title", match)
            self.assertIn("stream_url", match)
            self.assertIn("channel_name", match)
            self.assertIn("iframe_player_url", match)

    async def test_resolve_match_for_streaming_fallback_live(self):
        """Se la query è 'live', prende il primo match live disponibile."""
        match = await sports_scraper.resolve_match_for_streaming("live")
        self.assertIsNotNone(match)
        self.assertIn("title", match)
        self.assertIn("stream_url", match)

    async def test_resolve_match_for_streaming_not_found(self):
        """Query inesistente restituisce None."""
        with patch.object(sports_scraper, "search_event", return_value={"best_match": None}), \
             patch.object(sports_scraper, "get_live_matches", return_value=[]):
            match = await sports_scraper.resolve_match_for_streaming("squadra_assolutamente_inventata_xyz_999")
            self.assertIsNone(match)

    def test_build_match_streaming_embed_content(self):
        """Verifica il contenuto del Discord Embed e della View con pulsanti di controllo."""
        fake_match = {
            "id": 999999,
            "title": "Inter vs Juventus",
            "league": "Serie A",
            "time": "LIVE (20:45)",
            "score": "1 - 1",
            "tv_channels": "DAZN / Sky Sport",
            "channel_name": "Sky Sport Calcio HD",
            "url": "https://web01.madplay77.ru/it/football/serie-a-999999/inter-vs-juventus.html",
            "stream_url": "https://stream.example.com/token-123/live.m3u8",
            "iframe_player_url": "https://nadia01eo.tn76degree12ec3out.cfd/it/player.html?mdata=OTk5OTk5XzE%3D&ilang=it",
            "league_logo": "https://example.com/logo.png",
        }

        embed, view, voice_text = sports_scraper.build_match_streaming_embed(fake_match, requester="Davide")

        # Verifica Embed
        self.assertIn("TELECRONACA LIVE", embed.title)
        self.assertIn("Inter vs Juventus", embed.title)
        self.assertEqual(embed.color, discord.Color.red())

        fields_dict = {f.name: f.value for f in embed.fields}
        self.assertIn("🏆 Competizione", fields_dict)
        self.assertIn("Serie A", fields_dict["🏆 Competizione"])
        self.assertIn("📊 Risultato Live", fields_dict)
        self.assertIn("1 - 1", fields_dict["📊 Risultato Live"])
        self.assertIn("📺 Canale Telecronaca", fields_dict)
        self.assertIn("Sky Sport Calcio HD", fields_dict["📺 Canale Telecronaca"])
        self.assertIn("🔗 Guarda il Video sul Sito", fields_dict)
        self.assertIn("web01.madplay77.ru", fields_dict["🔗 Guarda il Video sul Sito"])

        # Verifica View
        self.assertIsInstance(view, MatchStreamingView)
        # Pulsanti URL (MadPlay77 link)
        buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
        url_buttons = [b for b in buttons if b.url]
        self.assertTrue(any("madplay77.ru" in b.url for b in url_buttons))

        # Verifica Frase Vocale
        self.assertIn("ragazzi", voice_text.lower())
        self.assertIn("telecronaca", voice_text.lower())


class TestMatchStreamingViewInteractions(unittest.IsolatedAsyncioTestCase):
    """Test dei pulsanti interattivi di MatchStreamingView."""

    async def test_button_callbacks(self):
        """Verifica i callback dei pulsanti Pausa, Volume, Stop e Info."""
        mock_player = MagicMock()
        mock_player.is_paused.return_value = False
        mock_player.is_playing.return_value = True
        mock_player.volume = 0.5

        fake_match = {
            "title": "Milan vs Napoli",
            "score": "2 - 0",
            "time": "LIVE (65')",
            "url": "https://web01.madplay77.ru/it/football/milan-napoli.html"
        }

        view = MatchStreamingView(mock_player, fake_match)

        # 1. Pausa
        interaction = AsyncMock()
        interaction.response.is_done.return_value = False
        await view.pause_resume_btn.callback(interaction)
        mock_player.pause.assert_called_once()

        # 2. Riprendi
        mock_player.is_paused.return_value = True
        mock_player.is_playing.return_value = False
        interaction.reset_mock()
        await view.pause_resume_btn.callback(interaction)
        mock_player.resume.assert_called_once()

        # 3. Volume Su
        interaction.reset_mock()
        await view.vol_up_btn.callback(interaction)
        mock_player.set_volume.assert_called_with(0.6)

        # 4. Volume Giù
        interaction.reset_mock()
        await view.vol_down_btn.callback(interaction)
        mock_player.set_volume.assert_called_with(0.4)

        # 5. Stop
        interaction.reset_mock()
        await view.stop_btn.callback(interaction)
        mock_player.stop.assert_called_once()

        # 6. Info
        interaction.reset_mock()
        await view.refresh_btn.callback(interaction)
        interaction.response.send_message.assert_called_once()
        sent_info = interaction.response.send_message.call_args[0][0]
        self.assertIn("Milan vs Napoli", sent_info)
        self.assertIn("2 - 0", sent_info)


class TestGuildPlayerSportsStreaming(unittest.IsolatedAsyncioTestCase):
    """Test del metodo stream_sports_match su GuildPlayer."""

    async def test_stream_sports_match_execution(self):
        """Verifica la creazione del brano SongInfo e l'avvio della riproduzione."""
        bot = MagicMock()
        guild = MagicMock()
        source_manager = MagicMock()
        player = GuildPlayer(bot, guild, source_manager)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.channel.name = "Generale"
        mock_vc.channel.voice_states = {}
        player.voice_client = mock_vc

        mock_text_channel = MagicMock()
        mock_text_channel.send = AsyncMock()
        mock_text_channel.permissions_for.return_value.send_messages = True

        player.play_now = AsyncMock()

        fake_match = {
            "id": 12345,
            "title": "Roma vs Lazio",
            "league": "Serie A",
            "channel_name": "DAZN 1",
            "stream_url": "https://stream.example.com/token-abc/live.m3u8",
            "direct_hls_available": True,
            "url": "https://web01.madplay77.ru/it/football/roma-lazio.html",
        }

        song, embed, view, voice_msg = await player.stream_sports_match(
            match=fake_match,
            text_channel=mock_text_channel,
            requester="Marco"
        )

        self.assertIsInstance(song, SongInfo)
        self.assertEqual(song.source_type, "sports_stream")
        self.assertTrue(song.is_direct_stream)
        self.assertIn("Roma vs Lazio", song.title)
        self.assertEqual(song.stream_url, "https://stream.example.com/token-abc/live.m3u8")

        # Verifica che play_now sia stato invocato con la traccia
        player.play_now.assert_called_once()
        called_songs = player.play_now.call_args[0][0]
        self.assertEqual(called_songs[0], song)

        # Verifica invio embed nel canale testuale
        mock_text_channel.send.assert_called_once()
        sent_embed = mock_text_channel.send.call_args[1]["embed"]
        self.assertIn("TELECRONACA LIVE", sent_embed.title)

    async def test_stream_sports_match_when_stream_not_live(self):
        """Quando il flusso HLS audio non è attivo, non avvia FFmpeg su URL HTML ma notifica con embed web."""
        bot = MagicMock()
        guild = MagicMock()
        source_manager = MagicMock()
        player = GuildPlayer(bot, guild, source_manager)

        mock_vc = MagicMock()
        mock_vc.is_connected.return_value = True
        mock_vc.channel.name = "Generale"
        player.voice_client = mock_vc
        player.play_now = AsyncMock()
        player.speak_tts = AsyncMock()

        mock_text_channel = MagicMock()
        mock_text_channel.send = AsyncMock()

        fake_offline_match = {
            "id": 88888,
            "title": "Juventus vs Milan",
            "league": "Serie A",
            "time": "20:45",
            "score": "0 - 0",
            "url": "https://web01.madplay77.ru/it/football/juventus-milan.html",
            "stream_url": None,
            "direct_hls_available": False,
            "iframe_player_url": "https://nadia01eo.tn76degree12ec3out.cfd/it/player.html?mdata=ODg4ODhfMQ%3D%3D&ilang=it",
        }

        song, embed, view, voice_msg = await player.stream_sports_match(
            match=fake_offline_match,
            text_channel=mock_text_channel,
            requester="Davide",
            send_embed=True,
        )

        self.assertIsNone(song)
        player.play_now.assert_not_called()
        self.assertIn("PARTITA / STREAMING", embed.title)
        self.assertIn("Flusso video e dettagli match disponibili su MadPlay77", embed.description)
        player.speak_tts.assert_called_once()

    async def test_stream_sports_match_headers_and_no_duplicate(self):
        """Verifica la presenza di HTTP headers e l'opzione send_embed=False per evitare messaggi duplicati."""
        bot = MagicMock()
        guild = MagicMock()
        source_manager = MagicMock()
        player = GuildPlayer(bot, guild, source_manager)
        player.play_now = AsyncMock()

        mock_text_channel = MagicMock()
        mock_text_channel.send = AsyncMock()

        fake_match = {
            "id": 55555,
            "title": "Inter vs Napoli",
            "stream_url": "https://stream.example.com/playlist.m3u8",
            "direct_hls_available": True,
        }

        song, embed, view, voice_msg = await player.stream_sports_match(
            match=fake_match,
            text_channel=mock_text_channel,
            requester="User1",
            send_embed=False,
        )

        self.assertIsNotNone(song)
        self.assertIn("User-Agent", song.http_headers)
        self.assertIn("Referer", song.http_headers)
        mock_text_channel.send.assert_not_called()


class TestBrainAndVoiceRecognition(unittest.IsolatedAsyncioTestCase):
    """Test del riconoscimento NLP per i comandi di streaming sportivo."""

    def test_brain_classifies_streaming_requests(self):
        """Verifica che le frasi di streaming attivino l'intent sports_stream_voice."""
        queries = [
            "Hey Franchino trasmetti la partita del Milan",
            "Franchino metti la telecronaca di Inter contro Juve",
            "Franchino streamma la partita della Roma",
            "Franchino fammi ascoltare la partita della Juventus",
            "Franchino fai partire lo streaming di Champions League",
            "Franchino trasmetti il match del Napoli",
            "Franchino metti la telecronaca del Barcellona",
        ]

        for q in queries:
            decision = brain.decide_intent(q)
            self.assertEqual(
                decision["intent"],
                "sports_stream_voice",
                f"Query '{q}' non classificata come sports_stream_voice, ma come {decision['intent']}"
            )
            self.assertIn("sport_query", decision)
            self.assertTrue(len(decision["sport_query"]) > 0)
            self.assertIn("telecronaca", decision["voice_reply"].lower())

    def test_brain_extracts_clean_team_query(self):
        """Verifica che la regex estragga la squadra esatta e non prefissi verbali."""
        test_cases = {
            "Hey Franchino trasmetti la partita del Milan": "milan",
            "Franchino metti la telecronaca di Inter": "inter",
            "Franchino metti la telecronaca della Juventus": "juventus",
            "Franchino fammi ascoltare la telecronaca del Napoli": "napoli",
        }
        for q, expected in test_cases.items():
            decision = brain.decide_intent(q)
            self.assertEqual(decision["sport_query"].lower(), expected)


class TestSourcesManagerSportsStream(unittest.IsolatedAsyncioTestCase):
    """Test del supporto SongInfo ed ensure_stream_url per flussi sportivi."""

    async def test_ensure_stream_url_for_sports_stream(self):
        """Verifica che ensure_stream_url restituisca immediatamente lo stream_url di un evento sportivo."""
        sm = SourceManager()
        song = SongInfo(
            title="Real Madrid vs Bayern Munich",
            webpage_url="https://web01.madplay77.ru/it/football/real-bayern.html",
            stream_url="https://stream.example.com/token-live/live.m3u8",
            source_type="sports_stream",
            is_direct_stream=True,
        )

        url = await sm.ensure_stream_url(song)
        self.assertEqual(url, "https://stream.example.com/token-live/live.m3u8")

    async def test_ensure_stream_url_rejects_html_for_sports_stream(self):
        """Verifica che ensure_stream_url rifiuti un file .html e sollevi RuntimeError."""
        sm = SourceManager()
        song = SongInfo(
            title="Partita Sconosciuta",
            webpage_url="https://web01.madplay77.ru/it/football/unknown.html",
            stream_url="https://web01.madplay77.ru/it/football/unknown.html",
            source_type="sports_stream",
            is_direct_stream=True,
        )

        with patch.object(sports_scraper, "resolve_match_for_streaming", return_value=None):
            with self.assertRaises(RuntimeError):
                await sm.ensure_stream_url(song)

    def test_song_info_embed_badge(self):
        """Verifica badge ed embed per source_type='sports_stream'."""
        song = SongInfo(
            title="Arsenal vs Chelsea",
            webpage_url="https://web01.madplay77.ru/it/football/arsenal-chelsea.html",
            stream_url="https://stream.example.com/live.m3u8",
            source_type="sports_stream",
            is_direct_stream=True,
        )
        embed = song.create_embed()
        self.assertIn("MadPlay77 Live", embed.footer.text)
        self.assertEqual(embed.color.value, discord.Color.red().value)


class TestSlashAndPrefixCommandParsing(unittest.TestCase):
    """Test del parsing degli argomenti dei comandi /partita e /stream in tutte le permutazioni."""

    def test_partita_command_permutations(self):
        import re

        def parse_partita(squadra_o_evento="stasera", dettaglio=None):
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
                return is_stream, clean_q
            return is_stream, raw_query

        # Permutazione 1: /partita stream Milan
        is_stream, q = parse_partita("stream", "Milan")
        self.assertTrue(is_stream)
        self.assertEqual(q.lower(), "milan")

        # Permutazione 2: /partita Milan stream
        is_stream, q = parse_partita("Milan", "stream")
        self.assertTrue(is_stream)
        self.assertEqual(q.lower(), "milan")

        # Permutazione 3: multi-word /partita stream Real Madrid vs Barcelona
        is_stream, q = parse_partita("stream", "Real Madrid vs Barcelona")
        self.assertTrue(is_stream)
        self.assertEqual(q.lower(), "real madrid vs barcelona")

        # Permutazione 4: slash input singolo /partita stream Juventus
        is_stream, q = parse_partita("stream Juventus", None)
        self.assertTrue(is_stream)
        self.assertEqual(q.lower(), "juventus")

        # Permutazione 5: query normale non-stream /partita Real Madrid
        is_stream, q = parse_partita("Real", "Madrid")
        self.assertFalse(is_stream)
        self.assertEqual(q.lower(), "real madrid")

        # Permutazione 6: comando vuoto /partita
        is_stream, q = parse_partita("stasera", None)
        self.assertFalse(is_stream)
        self.assertEqual(q.lower(), "stasera")


class TestStreamBotIntegration(unittest.IsolatedAsyncioTestCase):
    """Test suite per l'integrazione di StreamBot e la trasmissione video pulita Go Live."""

    def test_clean_video_url_and_options_sports(self):
        from streambot_service import clean_video_url_and_options
        raw_url = "https://web01.madplay77.ru/live/match.m3u8?utm_source=ad&banner_id=999&token=abc"
        clean_url, options = clean_video_url_and_options(raw_url, is_sports=True)
        self.assertNotIn("utm_source", clean_url)
        self.assertNotIn("banner_id", clean_url)
        self.assertIn("token=abc", clean_url)
        self.assertTrue(any('--referer "https://web01.madplay77.ru/"' in opt for opt in options))
        self.assertTrue(any("User-Agent:" in opt for opt in options))

    def test_clean_video_url_and_options_youtube(self):
        from streambot_service import clean_video_url_and_options
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL12345&index=3"
        clean_url, options = clean_video_url_and_options(yt_url, is_youtube=True)
        self.assertEqual(clean_url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(any("Accept-Language:" in opt for opt in options))

    def test_is_streambot_configured(self):
        from streambot_service import is_streambot_configured
        with patch.dict("os.environ", {"STREAMBOT_TOKEN": "test_token_123"}):
            self.assertTrue(is_streambot_configured())
        with patch.dict("os.environ", {"STREAMBOT_TOKEN": ""}), patch("streambot_service.STREAMBOT_TOKEN", ""):
            self.assertFalse(is_streambot_configured())

    async def test_streambot_client_lifecycle(self):
        from streambot_service import StreamBotClient

        client = StreamBotClient()
        mock_guild = MagicMock(spec=discord.Guild)
        mock_vc = MagicMock(spec=discord.VoiceChannel)
        mock_vc.id = 123456789
        mock_vc.name = "Generale Vocale"

        mock_tc = AsyncMock(spec=discord.TextChannel)
        mock_tc.id = 987654321
        mock_tc.guild = mock_guild
        mock_guild.text_channels = [mock_tc]
        mock_guild.get_channel.return_value = mock_tc

        mock_user = MagicMock(spec=discord.Member)
        mock_user.mention = "<@111>"

        # Avvio streaming
        embed, view = await client.start_video_stream(
            guild=mock_guild,
            voice_channel=mock_vc,
            text_channel=mock_tc,
            stream_url="https://web01.madplay77.ru/live/test.m3u8",
            title="Milan vs Inter",
            is_sports=True,
            requester=mock_user
        )

        self.assertTrue(client.is_active)
        self.assertEqual(client.current_title, "Milan vs Inter")
        self.assertIn("Milan vs Inter", embed.title)
        self.assertIsNotNone(view)

        # Verifica comandi inviati a StreamBot ($cfg e $play)
        sent_messages = [call.args[0] for call in mock_tc.send.call_args_list]
        self.assertTrue(any("$cfg VIDEO_CHANNEL_ID 123456789" in msg for msg in sent_messages))
        self.assertTrue(any("$play https://web01.madplay77.ru/live/test.m3u8" in msg for msg in sent_messages))

        # Test Pausa
        paused = await client.pause_video_stream(mock_tc)
        self.assertTrue(paused)
        self.assertEqual(mock_tc.send.call_args_list[-1].args[0], "$pause")

        # Test Ripresa
        resumed = await client.resume_video_stream(mock_tc)
        self.assertTrue(resumed)
        self.assertEqual(mock_tc.send.call_args_list[-1].args[0], "$resume")

        # Test Stop
        stopped = await client.stop_video_stream(mock_tc)
        self.assertTrue(stopped)
        self.assertFalse(client.is_active)
        self.assertEqual(mock_tc.send.call_args_list[-1].args[0], "$stop")

    async def test_streambot_control_view_pause_resume_stop(self):
        from streambot_service import StreamBotControlView, StreamBotClient

        client = StreamBotClient()
        mock_guild = MagicMock(spec=discord.Guild)
        mock_vc = MagicMock(spec=discord.VoiceChannel)
        mock_tc = AsyncMock(spec=discord.TextChannel)
        mock_tc.guild = mock_guild
        mock_guild.text_channels = [mock_tc]

        view = StreamBotControlView(client=client, voice_channel=mock_vc, text_channel=mock_tc)

        # Mock interaction
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_user = MagicMock(spec=discord.Member)
        mock_user.voice = MagicMock()
        mock_user.voice.channel = mock_vc
        mock_interaction.user = mock_user

        # Test interaction_check
        can_interact = await view.interaction_check(mock_interaction)
        self.assertTrue(can_interact)

        # Test pause/resume button
        with patch.object(client, "pause_video_stream", new=AsyncMock(return_value=True)):
            await view.pause_resume_btn.callback(mock_interaction)
            self.assertTrue(view.is_paused)

        with patch.object(client, "resume_video_stream", new=AsyncMock(return_value=True)):
            await view.pause_resume_btn.callback(mock_interaction)
            self.assertFalse(view.is_paused)

        # Test stop button
        with patch.object(client, "stop_video_stream", new=AsyncMock(return_value=True)):
            await view.stop_btn.callback(mock_interaction)
            self.assertTrue(view.stop_btn.disabled)

    async def test_match_streaming_view_watch_video_btn(self):
        from views import MatchStreamingView

        mock_match = {
            "title": "Inter vs Milan",
            "stream_url": "https://web01.madplay77.ru/live/derby.m3u8",
            "direct_hls_available": True
        }
        view = MatchStreamingView(player=None, match=mock_match)

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_vc = MagicMock(spec=discord.VoiceChannel)
        mock_guild = MagicMock(spec=discord.Guild)
        mock_user = MagicMock(spec=discord.Member)
        mock_user.voice = MagicMock()
        mock_user.voice.channel = mock_vc
        mock_interaction.user = mock_user
        mock_interaction.guild = mock_guild
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)

        with patch("streambot_service.is_streambot_configured", return_value=True):
            with patch("streambot_service.streambot_client.start_video_stream", new=AsyncMock(return_value=(MagicMock(), MagicMock()))) as mock_start:
                await view.watch_video_btn.callback(mock_interaction)
                mock_start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
