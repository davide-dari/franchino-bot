import asyncio
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

import config
from main import _get_human_ids_in_channel, _get_all_humans_in_voice
from player import GuildPlayer


class TestVoicePresenceAndVocalist(unittest.IsolatedAsyncioTestCase):

    def test_entrance_quotes_exist(self):
        """Verifica che le frasi di benvenuto vocalist siano definite e non vuote."""
        self.assertTrue(len(config.FRANCHINO_ENTRANCE_QUOTES) >= 4)
        for quote in config.FRANCHINO_ENTRANCE_QUOTES:
            self.assertTrue(len(quote.strip()) > 10)
            self.assertTrue("!" in quote or "Franchino" in quote or "Magia" in quote)

    def test_get_human_ids_in_channel(self):
        """Verifica che i bot vengano esclusi e che excluding_user_id funzioni correttamente."""
        mock_guild = MagicMock()
        mock_channel = MagicMock()
        mock_channel.guild = mock_guild

        # Setup members in channel
        user1 = MagicMock(id=101, bot=False)
        user2 = MagicMock(id=102, bot=False)
        bot_user = MagicMock(id=999, bot=True)

        mock_channel.members = [user1, user2, bot_user]
        mock_channel.voice_states = {101: MagicMock(), 102: MagicMock(), 999: MagicMock()}

        # 1. Nessun utente escluso
        humans = _get_human_ids_in_channel(mock_channel)
        self.assertIn(101, humans)
        self.assertIn(102, humans)
        self.assertNotIn(999, humans)
        self.assertEqual(len(humans), 2)

        # 2. Escludendo l'utente 101 che sta uscendo dal canale
        humans_after_exit = _get_human_ids_in_channel(mock_channel, excluding_user_id=101)
        self.assertNotIn(101, humans_after_exit)
        self.assertIn(102, humans_after_exit)
        self.assertEqual(len(humans_after_exit), 1)

        # 3. Escludendo entrambi (stanza vuota)
        humans_empty = _get_human_ids_in_channel(mock_channel, excluding_user_id=102)
        # Note: 101 is still in members, so 101 is returned
        self.assertEqual(humans_empty, [101])

    def test_get_all_humans_in_voice(self):
        """Verifica il calcolo complessivo degli umani in tutti i canali vocali del server."""
        mock_guild = MagicMock()
        ch1 = MagicMock(guild=mock_guild)
        ch2 = MagicMock(guild=mock_guild)

        user1 = MagicMock(id=201, bot=False)
        bot1 = MagicMock(id=999, bot=True)
        user2 = MagicMock(id=202, bot=False)

        ch1.members = [user1, bot1]
        ch1.voice_states = {201: MagicMock(), 999: MagicMock()}

        ch2.members = [user2]
        ch2.voice_states = {202: MagicMock()}

        mock_guild.voice_channels = [ch1, ch2]

        all_humans = _get_all_humans_in_voice(mock_guild)
        self.assertEqual(set(all_humans), {201, 202})

        # Escludendo l'utente 201 che si è disconnesso
        after_exit = _get_all_humans_in_voice(mock_guild, excluding_user_id=201)
        self.assertEqual(set(after_exit), {202})

    async def test_play_entrance_greeting_debounce(self):
        """Verifica il debouncing del saluto per evitare spam consecutivi."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)
        player.voice_client = MagicMock(is_connected=MagicMock(return_value=True))
        player.speak_tts = AsyncMock()

        mock_member = MagicMock(display_name="Marco")

        # Primo saluto: deve essere eseguito
        await player.play_entrance_greeting(mock_member)
        self.assertEqual(player.speak_tts.call_count, 1)

        # Secondo saluto immediato (< 8 sec): deve essere ignorato per debouncing
        await player.play_entrance_greeting(mock_member)
        self.assertEqual(player.speak_tts.call_count, 1)

        # Resettando il timestamp debounce, il prossimo saluto viene eseguito
        player._last_entrance_greeting_time = 0.0
        await player.play_entrance_greeting()
        self.assertEqual(player.speak_tts.call_count, 2)

    async def test_on_voice_state_update_auto_join_and_disconnect(self):
        """Verifica il ciclo completo di auto-join e auto-disconnect di Franchino."""
        from main import on_voice_state_update, player_manager

        mock_guild = MagicMock()
        mock_channel = MagicMock(guild=mock_guild, name="Generale")
        mock_user = MagicMock(id=501, bot=False, display_name="Luca", guild=mock_guild)

        # Mock player
        mock_player = MagicMock()
        mock_player.connect = AsyncMock()
        mock_player.disconnect = AsyncMock()
        mock_player.play_entrance_greeting = AsyncMock()
        mock_player.voice_client = None

        with patch.object(player_manager, "get_player", return_value=mock_player):
            # 1. Luca entra in un canale vocale vuoto
            before_state = MagicMock(channel=None)
            after_state = MagicMock(channel=mock_channel)

            mock_channel.members = [mock_user]
            mock_channel.voice_states = {501: MagicMock()}
            mock_guild.voice_channels = [mock_channel]

            await on_voice_state_update(mock_user, before_state, after_state)
            self.assertEqual(mock_player.connect.call_count, 1)

            # 2. Luca esce dal canale vocale (nessuno rimasto nel server)
            mock_player.voice_client = MagicMock(is_connected=MagicMock(return_value=True), channel=mock_channel)
            mock_guild.voice_client = mock_player.voice_client

            before_state_leave = MagicMock(channel=mock_channel)
            after_state_leave = MagicMock(channel=None)

            await on_voice_state_update(mock_user, before_state_leave, after_state_leave)
            self.assertEqual(mock_player.disconnect.call_count, 1)

    def test_vocalist_dsp_chain(self):
        """Verifica che i filtri DSP da vocalist contengano l'equalizzatore console, compressore ed eco."""
        import inspect
        from player import GuildPlayer
        src = inspect.getsource(GuildPlayer.speak_tts)

        self.assertIn("equalizer=f=140", src)
        self.assertIn("equalizer=f=3200", src)
        self.assertIn("acompressor=threshold=-16dB", src)
        self.assertIn("aecho=", src)
        self.assertIn("GiuseppeMultilingualNeural", src)


if __name__ == "__main__":
    unittest.main()

