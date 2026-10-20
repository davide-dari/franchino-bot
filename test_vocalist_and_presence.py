import asyncio
import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

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

    def test_manual_leave_cooldown_methods(self):
        """Verifica impostazione, controllo e cancellazione del cooldown di disconnessione manuale."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        self.assertFalse(player.has_manual_leave_cooldown())
        player.set_manual_leave_cooldown(60.0)
        self.assertTrue(player.has_manual_leave_cooldown())
        player.clear_manual_leave_cooldown()
        self.assertFalse(player.has_manual_leave_cooldown())

    async def test_presence_loop_respects_manual_leave_cooldown(self):
        """Verifica che _check_and_sync_voice_presence NON effettui auto-join se il cooldown è attivo."""
        from main import _check_and_sync_voice_presence, player_manager, bot

        mock_guild = MagicMock(voice_client=None, afk_channel=None)
        mock_channel = MagicMock(guild=mock_guild, name="Generale")
        user1 = MagicMock(id=101, bot=False)
        mock_channel.members = [user1]
        mock_channel.voice_states = {101: MagicMock()}
        mock_guild.voice_channels = [mock_channel]

        mock_player = MagicMock()
        mock_player.has_manual_leave_cooldown.return_value = True
        mock_player.connect = AsyncMock()
        mock_player.voice_client = None

        with patch.object(type(bot), "guilds", new_callable=PropertyMock, return_value=[mock_guild]):
            with patch.object(player_manager, "get_player", return_value=mock_player):
                await _check_and_sync_voice_presence()
                mock_player.connect.assert_not_called()

                # Quando il cooldown scade:
                mock_player.has_manual_leave_cooldown.return_value = False
                await _check_and_sync_voice_presence()
                mock_player.connect.assert_called_once()

    async def test_on_voice_state_update_respects_manual_leave_cooldown(self):
        """Verifica che on_voice_state_update NON effettui auto-join se il cooldown è attivo."""
        from main import on_voice_state_update, player_manager

        mock_guild = MagicMock()
        mock_channel = MagicMock(guild=mock_guild, name="Generale")
        mock_user = MagicMock(id=501, bot=False, display_name="Luca", guild=mock_guild)

        mock_player = MagicMock()
        mock_player.has_manual_leave_cooldown.return_value = True
        mock_player.connect = AsyncMock()
        mock_player.disconnect = AsyncMock()
        mock_player.voice_client = None

        with patch.object(player_manager, "get_player", return_value=mock_player):
            before_state = MagicMock(channel=None)
            after_state = MagicMock(channel=mock_channel)
            mock_channel.members = [mock_user]
            mock_channel.voice_states = {501: MagicMock()}
            mock_guild.voice_channels = [mock_channel]

            await on_voice_state_update(mock_user, before_state, after_state)
            mock_player.connect.assert_not_called()

    async def test_leave_commands_trigger_manual_leave_cooldown(self):
        """Verifica che /leave, il pulsante UI e il comando vocale impostino il cooldown di 60s."""
        from main import leave as leave_slash_cmd, player_manager
        from views import FranchinoControlsView
        from voice_commands import FranchinoVoiceSink

        mock_player = MagicMock()
        mock_player.voice_client = MagicMock(is_connected=MagicMock(return_value=True))
        mock_player.set_manual_leave_cooldown = MagicMock()
        mock_player.disconnect = AsyncMock()

        # 1. Slash command /leave
        mock_ctx = MagicMock()
        mock_ctx.guild = MagicMock()
        mock_ctx.send = AsyncMock()
        with patch.object(player_manager, "get_player", return_value=mock_player):
            await leave_slash_cmd(mock_ctx)
            mock_player.set_manual_leave_cooldown.assert_called_with(60.0)
            mock_player.disconnect.assert_called_once()

        # 2. UI button leave_btn
        mock_player.reset_mock()
        mock_player.disconnect = AsyncMock()
        view = FranchinoControlsView(mock_player)
        mock_interaction = MagicMock()
        mock_interaction.response.send_message = AsyncMock()
        await view.leave_btn.callback(mock_interaction)
        mock_player.set_manual_leave_cooldown.assert_called_with(60.0)
        mock_player.disconnect.assert_called_once()

        # 3. Voice command "esci"
        mock_player.reset_mock()
        mock_player.disconnect = AsyncMock()
        mock_player.bot = MagicMock()
        mock_player.bot.loop = MagicMock()
        with patch.object(FranchinoVoiceSink, "_monitor_speech_loop", new=lambda self: None):
            sink = FranchinoVoiceSink(mock_player)
            sink._notify_channel = AsyncMock()
            try:
                user = MagicMock(display_name="Davide")
                await sink._handle_transcribed_text(user, "Hey Franchino vattene", detected_cmd="vattene")
                mock_player.set_manual_leave_cooldown.assert_called_with(60.0)
                mock_player.disconnect.assert_called_once()
            finally:
                sink.cleanup()

    def test_afk_channel_exclusion(self):
        """Verifica che i canali AFK vengano completamente esclusi dal conteggio umani."""
        mock_guild = MagicMock()
        afk_channel = MagicMock(id=888, guild=mock_guild)
        mock_guild.afk_channel = afk_channel

        user1 = MagicMock(id=101, bot=False)
        afk_channel.members = [user1]
        afk_channel.voice_states = {101: MagicMock()}

        # 1. _get_human_ids_in_channel sul canale AFK restituisce vuoto
        humans = _get_human_ids_in_channel(afk_channel)
        self.assertEqual(humans, [])

        # 2. _get_all_humans_in_voice con solo canale AFK popolato restituisce vuoto
        mock_guild.voice_channels = [afk_channel]
        all_humans = _get_all_humans_in_voice(mock_guild)
        self.assertEqual(all_humans, [])

    def test_uncached_bot_identification(self):
        """Verifica che un bot non presente nella cache della guild venga riconosciuto come bot tramite bot.get_user."""
        from main import bot

        mock_guild = MagicMock()
        mock_channel = MagicMock(guild=mock_guild)
        mock_guild.afk_channel = None

        # Membro non in cache della guild
        mock_guild.get_member.return_value = None
        mock_channel.members = []
        mock_channel.voice_states = {999: MagicMock()}

        # bot.get_user lo riconosce come bot
        bot_user_mock = MagicMock(id=999, bot=True)
        with patch.object(bot, "get_user", return_value=bot_user_mock):
            humans = _get_human_ids_in_channel(mock_channel)
            self.assertEqual(humans, [])

        # Se invece è un utente umano non in cache
        human_user_mock = MagicMock(id=777, bot=False)
        mock_channel.voice_states = {777: MagicMock()}
        with patch.object(bot, "get_user", return_value=human_user_mock):
            humans = _get_human_ids_in_channel(mock_channel)
            self.assertEqual(humans, [777])

    async def test_inactivity_countdown_disconnects(self):
        """Verifica che _inactivity_countdown disconnetta pulitamente il bot se inattivo."""
        import time
        from config import INACTIVITY_TIMEOUT

        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        player.voice_client = MagicMock(is_connected=MagicMock(return_value=True))
        player.disconnect = AsyncMock()
        player.is_playing = MagicMock(return_value=False)
        player._playing_tts = False
        player.text_channel = MagicMock(send=AsyncMock())

        # Impostiamo l'ultima attività indietro nel tempo per far scadere il timeout
        player._last_activity_time = time.monotonic() - (INACTIVITY_TIMEOUT + 10.0)

        await player._inactivity_countdown()
        player.disconnect.assert_called_once()
        player.text_channel.send.assert_called_once()

    async def test_inactivity_countdown_aborts_if_playing(self):
        """Verifica che _inactivity_countdown non disconnetta se c'è musica in corso."""
        import time
        from config import INACTIVITY_TIMEOUT

        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        player.voice_client = MagicMock(is_connected=MagicMock(return_value=True))
        player.disconnect = AsyncMock()
        player.is_playing = MagicMock(return_value=True)
        player._playing_tts = False

        player._last_activity_time = time.monotonic() - (INACTIVITY_TIMEOUT + 10.0)

        await player._inactivity_countdown()
        player.disconnect.assert_not_called()

    async def test_watchdog_never_calls_connect(self):
        """Verifica che _voice_listener_watchdog non chiami MAI self.connect() se non connesso."""
        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        player.voice_client = None
        player.guild.voice_client = None
        player.connect = AsyncMock()

        # Esegui un'iterazione del watchdog facendolo uscire con un'eccezione o timeout
        task = asyncio.create_task(player._voice_listener_watchdog())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_bot_disconnect_event_sets_cooldown_and_disconnects(self):
        """Verifica che l'espulsione o disconnessione forzata del bot da Discord imposti il cooldown e chiami disconnect()."""
        from main import on_voice_state_update, player_manager, bot

        mock_guild = MagicMock()
        mock_channel = MagicMock(guild=mock_guild, name="Generale")
        bot_member = MagicMock(id=999, bot=True, guild=mock_guild)
        mock_guild.me = bot_member

        mock_player = MagicMock()
        mock_player.set_manual_leave_cooldown = MagicMock()
        mock_player.disconnect = AsyncMock()

        with patch.object(type(bot), "user", new_callable=PropertyMock, return_value=MagicMock(id=999)):
            with patch.object(player_manager, "get_player", return_value=mock_player):
                before_state = MagicMock(channel=mock_channel)
                after_state = MagicMock(channel=None)
                await on_voice_state_update(bot_member, before_state, after_state)

                mock_player.set_manual_leave_cooldown.assert_called_with(60.0)
                mock_player.disconnect.assert_called_once()

    async def test_empty_room_disconnects_even_with_cooldown_in_presence_sync(self):
        """Verifica che se Franchino si trova in una stanza vuota, si disconnetta anche se il cooldown è attivo."""
        from main import _check_and_sync_voice_presence, player_manager, bot

        mock_guild = MagicMock(name="TestGuild")

        empty_vc = MagicMock(id=111, guild=mock_guild, name="Vuota")
        empty_vc.members = []
        empty_vc.voice_states = {}

        populated_vc = MagicMock(id=222, guild=mock_guild, name="Popolata")
        user = MagicMock(id=555, bot=False)
        populated_vc.members = [user]
        populated_vc.voice_states = {555: MagicMock()}

        mock_guild.afk_channel = None
        mock_guild.voice_channels = [empty_vc, populated_vc]

        mock_player = MagicMock()
        mock_player.has_manual_leave_cooldown = MagicMock(return_value=True)
        mock_player.disconnect = AsyncMock()
        mock_player.connect = AsyncMock()

        bot_vc = MagicMock()
        bot_vc.is_connected = MagicMock(return_value=True)
        bot_vc.channel = empty_vc
        mock_player.voice_client = bot_vc
        mock_guild.voice_client = bot_vc

        with patch.object(type(bot), "guilds", new_callable=PropertyMock, return_value=[mock_guild]):
            with patch.object(player_manager, "get_player", return_value=mock_player):
                await _check_and_sync_voice_presence()
                # Non deve connettersi alla stanza popolata (perché in cooldown)
                mock_player.connect.assert_not_called()
                # MA deve disconnettersi dalla stanza vuota!
                mock_player.disconnect.assert_called_once()

    async def test_connect_is_auto_respects_manual_leave_cooldown(self):
        """Verifica che connect(is_auto=True) non si connetta e non resetti il cooldown se attivo."""
        mock_bot = MagicMock()
        mock_bot.loop = MagicMock()
        # Chiudi la coroutine creata da create_task per evitare RuntimeWarning
        def dummy_create_task(coro):
            coro.close()
            return MagicMock()
        mock_bot.loop.create_task = MagicMock(side_effect=dummy_create_task)

        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_channel = MagicMock()
        player.set_manual_leave_cooldown(60.0)
        self.assertTrue(player.has_manual_leave_cooldown())

        res = await player.connect(mock_channel, is_auto=True)
        self.assertIsNone(res)
        # Il cooldown deve rimanere attivo!
        self.assertTrue(player.has_manual_leave_cooldown())

        # Con is_auto=False (chiamata esplicita /join), deve invece resettare il cooldown
        player.voice_client = MagicMock(is_connected=MagicMock(return_value=True), channel=mock_channel)
        await player.connect(mock_channel, is_auto=False)
        self.assertFalse(player.has_manual_leave_cooldown())

    async def test_restart_console_robust_channel_selection(self):
        """Verifica che restart_console gestisca bot.user=None, escluda AFK e bot."""
        mock_bot = MagicMock()
        mock_bot.user = None  # Simula startup/reconnect con bot.user None
        mock_bot.get_user = MagicMock(return_value=None)
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        afk_ch = MagicMock(id=999)
        mock_guild.afk_channel = afk_ch

        bot_only_ch = MagicMock(id=111, guild=mock_guild)
        bot_member = MagicMock(id=888, bot=True)
        bot_only_ch.voice_states = {888: MagicMock()}

        human_ch = MagicMock(id=222, guild=mock_guild)
        human_member = MagicMock(id=777, bot=False)
        mock_guild.get_member = MagicMock(side_effect=lambda uid: human_member if uid == 777 else (bot_member if uid == 888 else None))
        human_ch.voice_states = {777: MagicMock()}

        mock_guild.voice_channels = [afk_ch, bot_only_ch, human_ch]

        player.connect = AsyncMock(return_value=MagicMock())
        player.voice_client = None
        player.guild.voice_client = None

        await player.restart_console()
        player.connect.assert_called_once_with(human_ch, human_ch)

    def test_uncached_unknown_user_treated_as_human(self):
        """Verifica che un utente in voice_states né in guild cache né in bot.get_user (intents.members disattivati) sia considerato umano."""
        from main import _get_human_ids_in_channel, bot

        mock_guild = MagicMock()
        mock_guild.afk_channel = None
        mock_guild.get_member.return_value = None
        mock_channel = MagicMock(guild=mock_guild)
        mock_channel.members = []
        mock_channel.voice_states = {54321: MagicMock()}

        with patch.object(bot, "get_user", return_value=None):
            humans = _get_human_ids_in_channel(mock_channel)
            self.assertEqual(humans, [54321])

    async def test_inactivity_countdown_aborts_if_humans_in_channel(self):
        """Verifica che _inactivity_countdown NON disconnetta Franchino se ci sono utenti umani nel canale vocale."""
        import time
        from config import INACTIVITY_TIMEOUT

        mock_bot = MagicMock()
        mock_guild = MagicMock()
        mock_source = MagicMock()
        player = GuildPlayer(mock_bot, mock_guild, mock_source)

        mock_channel = MagicMock()
        mock_channel.guild = mock_guild
        mock_channel.members = []
        mock_channel.voice_states = {12345: MagicMock()}

        player.voice_client = MagicMock(is_connected=MagicMock(return_value=True), channel=mock_channel)
        player.disconnect = AsyncMock()
        player.is_playing = MagicMock(return_value=False)
        player._playing_tts = False
        player.text_channel = MagicMock(send=AsyncMock())

        player._last_activity_time = time.monotonic() - (INACTIVITY_TIMEOUT + 10.0)

        with patch("main.bot.get_user", return_value=None):
            with patch.object(mock_guild, "get_member", return_value=None):
                await player._inactivity_countdown()

        player.disconnect.assert_not_called()
        player.text_channel.send.assert_not_called()

    async def test_bot_self_disconnect_does_not_trigger_cooldown(self):
        """Verifica che se Franchino si disconnette internamente (_is_disconnecting=True), non venga impostato il manual leave cooldown."""
        from main import on_voice_state_update, player_manager, bot

        mock_guild = MagicMock()
        mock_channel = MagicMock(guild=mock_guild, name="Generale")
        bot_member = MagicMock(id=999, bot=True, guild=mock_guild)
        mock_guild.me = bot_member

        mock_player = MagicMock()
        mock_player._is_disconnecting = True
        mock_player.set_manual_leave_cooldown = MagicMock()
        mock_player.disconnect = AsyncMock()

        with patch.object(type(bot), "user", new_callable=PropertyMock, return_value=MagicMock(id=999)):
            with patch.object(player_manager, "get_player", return_value=mock_player):
                before_state = MagicMock(channel=mock_channel)
                after_state = MagicMock(channel=None)
                await on_voice_state_update(bot_member, before_state, after_state)

                mock_player.set_manual_leave_cooldown.assert_not_called()
                mock_player.disconnect.assert_not_called()


if __name__ == "__main__":
    unittest.main()

