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

    async def test_play_entrance_greeting_disabled(self):
        """Verifica che play_entrance_greeting sia disabilitato e non riproduca mai audio/TTS."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)
        player.voice_client = MagicMock(is_connected=MagicMock(return_value=True))
        player.speak_tts = AsyncMock()

        mock_member = MagicMock(display_name="Marco")
        await player.play_entrance_greeting(mock_member)
        self.assertEqual(player.speak_tts.call_count, 0)

        await player.play_entrance_greeting()
        self.assertEqual(player.speak_tts.call_count, 0)

    async def test_on_voice_state_update_auto_join_and_disconnect(self):
        """Verifica il ciclo completo di auto-join e auto-disconnect di Franchino senza saluti."""
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
            # Verifica che NON venga salutato né vocalmente né testualmente
            self.assertEqual(mock_player.play_entrance_greeting.call_count, 0)

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

    async def test_voice_stop_command_silence(self):
        """Verifica che i comandi vocali STOP fermino la musica in silenzio assoluto senza risposte verbali o testuali."""
        from voice_commands import FranchinoVoiceSink

        mock_player = MagicMock()
        mock_player.bot = MagicMock()
        mock_player.bot.loop = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(return_value=MagicMock())
        mock_player.stop = MagicMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.voice_client = MagicMock()
        mock_player.current = MagicMock(title="Disco Inferno")

        with patch.object(FranchinoVoiceSink, "_monitor_speech_loop", new=lambda self: None):
            sink = FranchinoVoiceSink(mock_player)
            sink._notify_channel = AsyncMock()
            try:
                user = MagicMock(display_name="Davide")
                stop_phrases = [
                    "stop", "stoppa", "ferma tutto", "ferma la musica", "basta",
                    "basta musica", "silenzio", "fai silenzio", "zitto", "stai zitto",
                    "spegni", "spegni tutto", "muto", "taci", "smettila", "fermati"
                ]
                for phrase in stop_phrases:
                    mock_player.stop.reset_mock()
                    mock_player.speak_tts.reset_mock()
                    sink._notify_channel.reset_mock()

                    await sink._handle_transcribed_text(user, f"Hey Franchino {phrase}", detected_cmd=phrase)
                    mock_player.stop.assert_called_once()
                    sink._notify_channel.assert_not_called()
                    mock_player.speak_tts.assert_not_called()
            finally:
                sink.cleanup()

    def test_brain_and_jack_stop_silence(self):
        """Verifica che sia Jack Math Engine che Brain classifichino i comandi STOP come dj_control con azione stop e reply vuote."""
        from brain import brain
        from jack_math_engine import jack_engine

        stop_phrases = [
            "stop", "stoppa", "ferma tutto", "ferma la musica", "basta",
            "basta musica", "basta parlare", "silenzio", "fai silenzio", "zitto",
            "stai zitto", "spegni", "spegni tutto", "muto", "taci", "smettila", "fermati"
        ]
        for phrase in stop_phrases:
            jack_res = jack_engine.classify(phrase)
            self.assertEqual(
                jack_res.intent,
                "dj_control",
                f"Jack Math ha classificato '{phrase}' come '{jack_res.intent}' anziché 'dj_control'"
            )
            self.assertEqual(
                jack_res.extracted_entities.get("action"),
                "stop",
                f"Jack Math non ha estratto l'azione 'stop' per '{phrase}'"
            )

            decision = brain.decide_intent(phrase)
            self.assertEqual(
                decision.get("intent"),
                "dj_control",
                f"Brain ha classificato '{phrase}' come '{decision.get('intent')}' anziché 'dj_control'"
            )
            self.assertEqual(
                decision.get("action"),
                "stop",
                f"Brain non ha impostato 'action': 'stop' per '{phrase}'"
            )
            self.assertEqual(
                decision.get("voice_reply"),
                "",
                f"Brain ha fornito una risposta vocale non-silente per '{phrase}': {decision.get('voice_reply')}"
            )
            self.assertEqual(
                decision.get("chat_reply"),
                "",
                f"Brain ha fornito una risposta di chat non-silente per '{phrase}': {decision.get('chat_reply')}"
            )

    async def test_slash_stop_command_silence(self):
        """Verifica che il comando /stop fermi il player senza inviare messaggi di chat (sia con prefisso che slash)."""
        from main import stop as stop_slash_cmd, player_manager

        mock_player = MagicMock()
        mock_player.stop = MagicMock()

        # 1. Invocazione con comando prefissato (!stop)
        mock_ctx = MagicMock()
        mock_ctx.guild = MagicMock()
        mock_ctx.send = AsyncMock()
        mock_ctx.interaction = None

        with patch.object(player_manager, "get_player", return_value=mock_player):
            await stop_slash_cmd(mock_ctx)
            mock_player.stop.assert_called_once()
            mock_ctx.send.assert_not_called()

        # 2. Invocazione come Slash Command Discord (/stop)
        mock_player.stop.reset_mock()
        mock_interaction = MagicMock()
        mock_interaction.response.is_done = MagicMock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.delete_original_response = AsyncMock()
        mock_ctx.interaction = mock_interaction

        with patch.object(player_manager, "get_player", return_value=mock_player):
            await stop_slash_cmd(mock_ctx)
            mock_player.stop.assert_called_once()
            mock_ctx.send.assert_not_called()
            mock_interaction.response.defer.assert_called_once()
            mock_interaction.delete_original_response.assert_called_once()

    async def test_view_stop_button_silence(self):
        """Verifica che il pulsante stop nella view non mandi messaggi testuali 'Tutto fermato...'."""
        from views import FranchinoControlsView

        mock_player = MagicMock()
        mock_player.stop = MagicMock()
        mock_player.is_paused = MagicMock(return_value=False)
        mock_player.loop_mode = "off"
        mock_player.autoplay = False
        mock_player.audio_effect = "off"
        mock_player.bass_boost = "off"

        view = FranchinoControlsView(mock_player)

        mock_interaction = MagicMock()
        mock_interaction.response.is_done = MagicMock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()

        await view.stop_btn.callback(mock_interaction)
        mock_player.stop.assert_called_once()
        mock_interaction.response.send_message.assert_not_called()
        mock_interaction.response.defer.assert_called_once()


if __name__ == "__main__":
    unittest.main()

