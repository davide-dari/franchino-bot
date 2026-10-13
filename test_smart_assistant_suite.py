"""
test_smart_assistant_suite.py
=============================
Test suite completa per la suite Assistente Intelligente Vocale & Slash di Franchino Bot:
1. Ora e Data esatta (Europe/Rome).
2. Meteo in tempo reale via Open-Meteo (geocoding, previsioni, codici WMO, fallback offline).
3. Timer e Promemoria asincroni in background (parsing, worker, squillo sveglia alarm, annuncio vocale).
4. Calcolatrice matematica sicura (AST parser, operazioni, divisione per zero, sicurezza).
5. Scelta casuale, Testa o Croce, Lancio Dadi (D6, D20, D100).
6. Barzellette e battute da vocalist.
7. Classificazione Jack Math Engine (integrazione Okapi BM25 e Jaro-Winkler sotto il cofano).
8. Integrazione nel cervello Gemma / fallback (brain.py).
9. Comandi Slash in main.py (/ora, /meteo, /timer, /calcola, /lancia, /barzelletta) e rimozione di /jack.
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest

from smart_assistant import (
    ROME_TZ,
    get_current_time_info,
    extract_city_from_query,
    get_weather_info,
    WMO_WEATHER_MAP,
    format_duration_italian,
    parse_timer_request,
    is_timer_status_query,
    is_timer_cancel_query,
    TimerManager,
    ActiveTimer,
    evaluate_math_expression,
    flip_coin,
    roll_dice,
    choose_option,
    get_vocalist_joke,
    detect_smart_assistant_intent,
)
from sound_effects import SOUNDBOARD_EFFECTS, ensure_sfx_file
from jack_math_engine import jack_engine, INTENTS
from brain import brain
from main import (
    bot,
    ora_cmd,
    meteo_cmd,
    timer_cmd,
    calcola_cmd,
    lancia_cmd,
    barzelletta_cmd,
    boost_cmd,
)


# ==============================================================================
# 1. TEST ORA E DATA ESATTA (Europe/Rome)
# ==============================================================================

class TestTimeAndDate(unittest.TestCase):
    """Verifica la precisione dell'orario, del fuso orario di Roma e della formattazione."""

    def test_current_time_info_structure(self):
        info = get_current_time_info("che ore sono?")
        self.assertIn("time_str", info)
        self.assertIn("date_str", info)
        self.assertIn("voice_reply", info)
        self.assertIn("embed", info)
        self.assertIsInstance(info["embed"], discord.Embed)
        self.assertIn("🕒", info["embed"].title)

    def test_time_query_voice_vocalist_style(self):
        info = get_current_time_info("che ore sono?")
        self.assertIn("console", info["voice_reply"])
        self.assertIn("Magia!", info["voice_reply"])
        self.assertIn(info["time_str"], info["voice_reply"])

    def test_date_query_vocalist_style(self):
        info = get_current_time_info("che giorno è oggi?")
        self.assertIn("Oggi è", info["voice_reply"])
        self.assertIn(info["day_name"], info["voice_reply"])
        self.assertIn(str(info["day_num"]), info["voice_reply"])

    def test_embed_fields_presence(self):
        info = get_current_time_info()
        field_names = [f.name for f in info["embed"].fields]
        self.assertTrue(any("Ora Esatta" in name for name in field_names))
        self.assertTrue(any("Data Odierna" in name for name in field_names))
        self.assertTrue(any("Fuso Orario" in name for name in field_names))


# ==============================================================================
# 2. TEST METEO IN TEMPO REALE VIA OPEN-METEO
# ==============================================================================

class TestOpenMeteoWeather(unittest.IsolatedAsyncioTestCase):
    """Test del modulo meteo, estrazione città e gestione fallback."""

    def test_extract_city_from_query(self):
        test_queries = [
            ("che tempo fa a Milano?", "Milano"),
            ("che tempo fa a Roma stasera", "Roma"),
            ("com'è il meteo a Napoli", "Napoli"),
            ("previsioni meteo per Firenze", "Firenze"),
            ("piove oggi a Torino", "Torino"),
            ("che tempo fa?", "Roma"),  # Default
            ("meteo Bologna", "Bologna"),
            ("dimmi il meteo di Napoli", "Napoli"),
            ("previsioni del tempo a Bari", "Bari"),
            ("che tempo farà a Roma", "Roma"),
            ("com'è il tempo a Bologna", "Bologna"),
            ("fa freddo a Londra per favore", "Londra"),
            ("franchino dimmi il meteo a Verona stasera", "Verona"),
        ]
        for query, expected_city in test_queries:
            extracted = extract_city_from_query(query)
            self.assertEqual(extracted.lower(), expected_city.lower(), f"Fallito per '{query}'")

    async def test_get_weather_info_success_mock(self):
        """Simula la risposta dell'API Open-Meteo per una città valida."""
        mock_geo = {
            "results": [{
                "name": "Firenze",
                "country": "Italia",
                "latitude": 43.7696,
                "longitude": 11.2558,
            }]
        }
        mock_forecast = {
            "current": {
                "temperature_2m": 20.4,
                "apparent_temperature": 19.8,
                "relative_humidity_2m": 58,
                "precipitation": 0.0,
                "weather_code": 0,
                "wind_speed_10m": 8.5,
            },
            "daily": {
                "temperature_2m_max": [22.0],
                "temperature_2m_min": [12.0],
            }
        }

        class MockResponse:
            def __init__(self, data, status=200):
                self._data = data
                self.status = status

            async def json(self):
                return self._data

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        class MockSession:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, headers=None):
                if "geocoding-api" in url:
                    return MockResponse(mock_geo)
                return MockResponse(mock_forecast)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("aiohttp.ClientSession", side_effect=MockSession):
            res = await get_weather_info("Firenze")
            self.assertTrue(res["success"])
            self.assertEqual(res["city"], "Firenze")
            self.assertEqual(res["temperature"], 20.4)
            self.assertIn("sereno", res["condition"].lower())
            self.assertIn("Magia!", res["voice_reply"])
            self.assertIsInstance(res["embed"], discord.Embed)

    async def test_get_weather_city_not_found(self):
        """Simula città inesistente nei sensori geocoding."""
        class MockSessionNotFound:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url, headers=None):
                class MockResp:
                    status = 200
                    async def json(self):
                        return {"results": []}
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, exc_type, exc_val, exc_tb):
                        pass
                return MockResp()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("aiohttp.ClientSession", side_effect=MockSessionNotFound):
            res = await get_weather_info("CittaImpossibile12345")
            self.assertFalse(res["success"])
            self.assertIn("Non ho trovato", res["voice_reply"])
            self.assertIsInstance(res["embed"], discord.Embed)

    async def test_get_weather_network_error_fallback(self):
        """Simula errore di rete o timeout."""
        with patch("aiohttp.ClientSession", side_effect=Exception("Timeout simulato")):
            res = await get_weather_info("Roma")
            self.assertFalse(res["success"])
            self.assertIn("radar", res["voice_reply"].lower())


# ==============================================================================
# 3. TEST TIMER & PROMEMORIA ASINCRONI IN BACKGROUND
# ==============================================================================

class TestTimerAndReminders(unittest.IsolatedAsyncioTestCase):
    """Test del parser dei timer, formattazione e gestione del ciclo di vita del timer."""

    def test_format_duration_italian(self):
        self.assertEqual(format_duration_italian(30), "30 secondi")
        self.assertEqual(format_duration_italian(60), "1 minuto")
        self.assertEqual(format_duration_italian(300), "5 minuti")
        self.assertEqual(format_duration_italian(3600), "1 ora")
        self.assertEqual(format_duration_italian(3660), "1 ora e 1 minuto")
        self.assertEqual(format_duration_italian(4800), "1 ora e 20 minuti")

    def test_parse_timer_request_durations_and_labels(self):
        cases = [
            ("metti un timer di 5 minuti per la pasta", 300, "pasta"),
            ("timer di 30 secondi per la pizza", 30, "pizza"),
            ("avvertimi tra 10 minuti di uscire", 600, "uscire"),
            ("metti un timer da 1 ora e 20 minuti per il forno", 4800, "forno"),
            ("timer 15 min", 900, "Timer"),
            ("timer da 45 secondi", 45, "Timer"),
            ("timer di due minuti", 120, "Timer"),
            ("timer di cinque minuti per il te", 300, "te"),
            ("timer di dieci minuti", 600, "Timer"),
            ("timer di un quarto d'ora per la torta", 900, "torta"),
            ("timer di un'ora e mezza per il forno", 5400, "forno"),
            ("timer di mezz’ora", 1800, "Timer"),
            ("1h30m", 5400, "Timer"),
            ("5m30s", 330, "Timer"),
        ]
        for text, exp_sec, exp_label in cases:
            sec, label = parse_timer_request(text)
            self.assertEqual(sec, exp_sec, f"Durata errata per '{text}'")
            self.assertIn(exp_label, label, f"Label errata per '{text}': {label}")

    def test_is_timer_status_and_cancel_queries(self):
        self.assertTrue(is_timer_status_query("quanto manca al timer?"))
        self.assertTrue(is_timer_status_query("a che punto è il timer"))
        self.assertTrue(is_timer_status_query("quanto manca alla pasta?"))
        self.assertTrue(is_timer_status_query("quanto tempo rimane?"))
        self.assertTrue(is_timer_cancel_query("cancella il timer"))
        self.assertTrue(is_timer_cancel_query("stop timer"))
        self.assertTrue(is_timer_cancel_query("ferma la sveglia"))
        self.assertTrue(is_timer_cancel_query("cancella la sveglia"))
        self.assertFalse(is_timer_cancel_query("metti un timer di 5 minuti"))

    async def test_timer_manager_lifecycle_and_callback(self):
        """Avvia un timer ultra-rapido (0.05s) e verifica l'esecuzione del callback e dell'allarme."""
        tm = TimerManager()
        guild_id = 99999
        channel = MagicMock()
        channel.send = AsyncMock()
        user = MagicMock()
        user.id = 12345
        user.display_name = "Mario"

        player = MagicMock()
        player.voice_client = MagicMock()
        player.voice_client.is_connected.return_value = True
        player.play_sound_effect = AsyncMock()
        player.speak_tts = AsyncMock()

        # Avvio timer di 1 secondo (poi cancelliamo per testare cancel)
        t_cancel = tm.start_timer(guild_id, channel, user, 10, "prova cancel", player=player)
        self.assertEqual(len(tm.get_timers(guild_id)), 1)
        cancelled = tm.cancel_timers(guild_id)
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(len(tm.get_timers(guild_id)), 0)

        # Avvio timer brevissimo con esecuzione completa del worker
        t_run = tm.start_timer(guild_id, channel, user, 0.05, "pasta", player=player)
        await asyncio.sleep(0.15)

        # Verifica che il channel abbia ricevuto la notifica embed con 'DRIIIIN!'
        channel.send.assert_awaited()
        call_kwargs = channel.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        embed = call_kwargs["embed"]
        self.assertIn("DRIIIIN", embed.title)
        self.assertIn("pasta", embed.description)

        # Verifica che player abbia suonato l'effetto alarm con resume=False e abbia parlato
        player.play_sound_effect.assert_awaited_with("alarm", resume=False)
        player.speak_tts.assert_called()


# ==============================================================================
# 4. TEST CALCOLATRICE MATEMATICA RAPIDA & SICURA (AST PARSER)
# ==============================================================================

class TestSafeMathCalculator(unittest.TestCase):
    """Test del motore di calcolo sicuro con AST."""

    def test_basic_arithmetic(self):
        cases = [
            ("quanto fa 25 per 4?", "100"),
            ("150 diviso 3", "50"),
            ("calcola 12 più 8", "20"),
            ("quanto fa 100 meno 35", "65"),
            ("2 alla 8", "256"),
            ("radice di 16", "4"),
            ("calcola la radice quadrata di 81", "9"),
            ("calcola la radice di 16", "4"),
            ("quanto fa 10 piu 5", "15"),
            ("quanto fa 10,5 piu 4,5", "15"),
            ("quanto fa venti per cinque", "100"),
            ("quanto fa cento diviso due", "50"),
            ("50 * 2", "100"),
            ("10 / 4", "2.5"),
        ]
        for query, expected_res in cases:
            res = evaluate_math_expression(query)
            self.assertTrue(res["success"], f"Calcolo fallito per '{query}': {res.get('error')}")
            self.assertEqual(res["result"], expected_res, f"Risultato errato per '{query}'")
            self.assertIn("Magia!", res["voice_reply"])

    def test_division_by_zero_handling(self):
        res = evaluate_math_expression("quanto fa 10 diviso 0")
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "Divisione per zero")
        self.assertIn("zero", res["voice_reply"])

    def test_ast_security_injection_rejected(self):
        """Verifica che codice Python arbitrario venga rifiutato categoricamente."""
        malicious_inputs = [
            "__import__('os').system('ls')",
            "open('/etc/passwd').read()",
            "eval('2+2')",
            "exec('x = 1')",
        ]
        for mal in malicious_inputs:
            res = evaluate_math_expression(mal)
            self.assertFalse(res["success"])


# ==============================================================================
# 5. TEST SCELTA CASUALE, TESTA O CROCE, DADO
# ==============================================================================

class TestRandomChoiceAndDice(unittest.TestCase):
    """Test delle utility di sorte e lancio dadi."""

    def test_flip_coin(self):
        res = flip_coin()
        self.assertIn(res["result"], ("Testa", "Croce"))
        self.assertIn("moneta", res["voice_reply"].lower())
        self.assertIsInstance(res["embed"], discord.Embed)

    def test_roll_dice_d6_and_d20(self):
        res_d6 = roll_dice(6)
        self.assertGreaterEqual(res_d6["result"], 1)
        self.assertLessEqual(res_d6["result"], 6)

        res_d20 = roll_dice(20)
        self.assertGreaterEqual(res_d20["result"], 1)
        self.assertLessEqual(res_d20["result"], 20)

    def test_choose_option(self):
        res = choose_option("scegli tra pizza o hamburger")
        self.assertIn(res["chosen"].lower(), ("pizza", "hamburger"))
        self.assertIn("scelgo", res["voice_reply"].lower())


# ==============================================================================
# 6. TEST BARZELLETTE E BATTUTE DA VOCALIST
# ==============================================================================

class TestVocalistJokes(unittest.TestCase):
    """Test delle battute comiche e freddure da vocalist."""

    def test_get_vocalist_joke(self):
        joke = get_vocalist_joke()
        self.assertTrue(len(joke["question"]) > 5)
        self.assertTrue(len(joke["punchline"]) > 5)
        self.assertIn("Magia!", joke["voice_reply"])
        self.assertIsInstance(joke["embed"], discord.Embed)


# ==============================================================================
# 7. TEST EFFETTO SONORO ALARM IN SOUND_EFFECTS
# ==============================================================================

class TestSoundEffectsAlarm(unittest.TestCase):
    """Verifica la sintesi e registrazione dell'effetto sonoro sveglia 'alarm'."""

    def test_alarm_registered_in_soundboard(self):
        self.assertIn("alarm", SOUNDBOARD_EFFECTS)
        self.assertEqual(SOUNDBOARD_EFFECTS["alarm"]["emoji"], "⏰")

    def test_alarm_wav_file_synthesis(self):
        path = ensure_sfx_file("alarm")
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 1000)


# ==============================================================================
# 8. TEST CLASSIFICAZIONE JACK MATH ENGINE PER SMART ASSISTANT
# ==============================================================================

class TestJackMathSmartAssistantClassification(unittest.TestCase):
    """Verifica che il motore analitico Jack riconosca le richieste stile Alexa."""

    def test_smart_assistant_intent_in_jack_intents(self):
        self.assertIn("smart_assistant", INTENTS)

    def test_jack_classification_of_smart_queries(self):
        queries = [
            ("Franchino che ore sono?", "time"),
            ("Franchino che giorno è oggi?", "time"),
            ("Franchino che tempo fa a Milano?", "weather"),
            ("Franchino metti un timer di 5 minuti per la pasta", "timer"),
            ("Franchino quanto fa 25 per 4?", "math"),
            ("Franchino lancia un dado", "random"),
            ("Franchino testa o croce", "random"),
            ("Franchino lancia una moneta", "random"),
            ("Franchino quanto manca al timer", "timer"),
            ("Franchino ferma la sveglia", "timer"),
            ("Franchino dimmi l'ora", "time"),
            ("Franchino com'è il tempo oggi", "weather"),
            ("Franchino raccontami una barzelletta", "joke"),
        ]
        for q, expected_sub in queries:
            res = jack_engine.classify(q)
            self.assertEqual(
                res.intent,
                "smart_assistant",
                f"Query '{q}' classificata erroneamente come {res.intent}"
            )
            self.assertGreaterEqual(res.probability, 0.25)
            self.assertEqual(res.extracted_entities.get("subintent"), expected_sub)


# ==============================================================================
# 9. TEST FALLBACK CERVELLO GEMMA IN BRAIN.PY & DISPATCH VOCALE
# ==============================================================================

class TestBrainSmartAssistantIntegration(unittest.IsolatedAsyncioTestCase):
    """Verifica l'integrazione di smart_assistant in FranchinoBrain e nel dispatch vocale."""

    def test_brain_fallback_time_query(self):
        decision = brain._fallback_heuristic_intent("Franchino che ore sono?", "Marco")
        self.assertEqual(decision["intent"], "smart_assistant")
        self.assertEqual(decision["subintent"], "time")
        self.assertIn("console", decision["voice_reply"].lower())

    def test_brain_fallback_weather_query(self):
        decision = brain._fallback_heuristic_intent("Franchino che tempo fa a Firenze?", "Luca")
        self.assertEqual(decision["intent"], "smart_assistant")
        self.assertEqual(decision["subintent"], "weather")
        self.assertEqual(decision["city"].lower(), "firenze")

    def test_brain_fallback_timer_query(self):
        decision = brain._fallback_heuristic_intent("Franchino metti un timer di 5 minuti per la pasta", "Ema")
        self.assertEqual(decision["intent"], "smart_assistant")
        self.assertEqual(decision["subintent"], "timer_set")
        self.assertEqual(decision["duration_seconds"], 300)

    def test_brain_fallback_timer_status_and_cancel_queries(self):
        dec_status = brain._fallback_heuristic_intent("Franchino quanto manca al timer?", "Ema")
        self.assertEqual(dec_status["intent"], "smart_assistant")
        self.assertEqual(dec_status["subintent"], "timer_status")

        dec_cancel = brain._fallback_heuristic_intent("Franchino cancella il timer", "Ema")
        self.assertEqual(dec_cancel["intent"], "smart_assistant")
        self.assertEqual(dec_cancel["subintent"], "timer_cancel")

    def test_brain_fallback_math_query(self):
        decision = brain._fallback_heuristic_intent("Franchino quanto fa 150 diviso 3?", "Sara")
        self.assertEqual(decision["intent"], "smart_assistant")
        self.assertEqual(decision["subintent"], "math")
        self.assertIn("50", decision["voice_reply"])

    def test_brain_fallback_joke_query(self):
        decision = brain._fallback_heuristic_intent("Franchino raccontami una barzelletta", "Giulia")
        self.assertEqual(decision["intent"], "smart_assistant")
        self.assertEqual(decision["subintent"], "joke")

    async def test_dispatch_brain_decision_timer_status_and_cancel(self):
        """Verifica che _dispatch_brain_decision gestisca timer_status e timer_cancel correttamente."""
        from voice_commands import FranchinoVoiceSink
        sink = FranchinoVoiceSink.__new__(FranchinoVoiceSink)
        sink._buffers = {}
        sink._audio_data = {}
        player = MagicMock()
        player.guild = MagicMock()
        player.guild.id = 88888
        player.text_channel = MagicMock()
        player.speak_tts = AsyncMock()
        sink.player = player
        sink._notify_channel = AsyncMock()

        user = MagicMock()
        user.id = 999
        user.display_name = "Matteo"

        # Inizializza un timer
        from smart_assistant import timer_manager
        timer_manager.start_timer(88888, player.text_channel, user, 60, "risotto", player=player)

        # 1. Test status dispatch
        status_dec = {"intent": "smart_assistant", "subintent": "timer_status"}
        res_status = await sink._dispatch_brain_decision(user, status_dec, "quanto manca al timer?")
        self.assertTrue(res_status)
        player.speak_tts.assert_called()

        # 2. Test cancel dispatch
        cancel_dec = {"intent": "smart_assistant", "subintent": "timer_cancel"}
        res_cancel = await sink._dispatch_brain_decision(user, cancel_dec, "cancella il timer")
        self.assertTrue(res_cancel)
        self.assertEqual(len(timer_manager.get_timers(88888)), 0)


# ==============================================================================
# 10. TEST COMANDI SLASH IN MAIN.PY & RIMOZIONE DI /jack
# ==============================================================================

class TestMainSlashCommands(unittest.IsolatedAsyncioTestCase):
    """Verifica la rimozione di /jack e il corretto funzionamento dei nuovi comandi slash."""

    def test_jack_command_removed_from_bot(self):
        """Verifica che il comando /jack sia stato rimosso dai comandi di bot."""
        self.assertIsNone(bot.get_command("jack"), "Il comando /jack non dovrebbe più essere registrato!")

    def test_new_slash_commands_registered(self):
        """Verifica la presenza dei nuovi comandi slash."""
        for cmd_name in ("ora", "meteo", "timer", "calcola", "lancia", "barzelletta"):
            self.assertIsNotNone(bot.get_command(cmd_name), f"Comando /{cmd_name} mancante!")

    async def test_slash_ora_command(self):
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        await ora_cmd.callback(ctx)
        ctx.defer.assert_awaited_once()
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        self.assertIn("Orologio", call_kwargs["embed"].title)

    async def test_slash_meteo_command(self):
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        await meteo_cmd.callback(ctx, citta="Milano")
        ctx.defer.assert_awaited_once()
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        self.assertIn("Meteo", call_kwargs["embed"].title)

    async def test_slash_timer_command(self):
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()
        ctx.guild.id = 77777
        ctx.channel = MagicMock()
        ctx.author = MagicMock()
        ctx.author.id = 11111
        ctx.author.display_name = "Chef"

        # 1. Imposta timer
        await timer_cmd.callback(ctx, durata="5m", motivo="risotto")
        ctx.send.assert_awaited()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        self.assertIn("Timer Impostato", call_kwargs["embed"].title)

        # 2. Stato timer
        ctx.send.reset_mock()
        await timer_cmd.callback(ctx, durata="status")
        ctx.send.assert_awaited()

        # 3. Cancella timer
        ctx.send.reset_mock()
        await timer_cmd.callback(ctx, durata="cancella")
        ctx.send.assert_awaited()

    async def test_slash_calcola_command(self):
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        await calcola_cmd.callback(ctx, operazione="25 * 4")
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        self.assertIn("100", [f.value for f in call_kwargs["embed"].fields if f.name == "📤 Risultato"][0])

    async def test_slash_lancia_command(self):
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        # Dado
        await lancia_cmd.callback(ctx, tipo="d20")
        ctx.send.assert_awaited()

        # Moneta
        ctx.send.reset_mock()
        await lancia_cmd.callback(ctx, tipo="moneta")
        ctx.send.assert_awaited()

    async def test_slash_barzelletta_command(self):
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        await barzelletta_cmd.callback(ctx)
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        self.assertIn("Battuta", call_kwargs["embed"].title)

    async def test_boost_jack_mode_remains_functional(self):
        """Verifica che /boost jack mantenga la telemetria e la formula Jack intatte."""
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        await boost_cmd.callback(ctx, modo="jack")
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        embed = call_kwargs["embed"]
        self.assertIn("Formula Matematica Jack", embed.title)
