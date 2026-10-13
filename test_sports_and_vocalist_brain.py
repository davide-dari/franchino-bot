"""
test_sports_and_vocalist_brain.py - Test Suite per Motore Multi-Sport, Palinsesto & Vocalist Brain
===================================================================================================
Testa in modo approfondito:
1. Scraping e consultazione di tutti gli sport (Calcio, Serie A, Champions, NBA, Tennis, F1, MotoGP)
2. Recupero del palinsesto di stasera ("mi dici i match di stasera?", "chi gioca stasera?")
3. Generazione delle risposte vocali ed embed da Vocalist (Franchino persona)
4. Integrazione nel flusso comandi vocali (voice_commands.py)
5. Separazione netta tra quesiti sportivi e UO Outlands Wiki
6. Comandi slash Discord /partita e /partite in main.py
"""

import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sports_scraper import sports_scraper, SportsScraper, _clean_title, _parse_protobuf_wire
from brain import brain, FranchinoBrain
from outlands_wiki import is_wiki_question
from voice_commands import FranchinoVoiceSink


# ==============================================================================
# 1. TEST PULIZIA E PARSING PROTOBUF / TITOLI SPORTIVI
# ==============================================================================
class TestSportsProtobufAndCleaning(unittest.TestCase):

    def test_clean_title_removes_unwanted_characters(self):
        """Verifica che la pulizia titoli rimuova artefatti senza danneggiare i nomi reali, preservando cifre legittime (es. 76ers, 1860)."""
        self_tests = [
            ("+Wolverhampton Wanderers vs Bolton Wanderers", "Wolverhampton Wanderers vs Bolton Wanderers"),
            ("-Winterthur F.C. Barcelona vs Maccabi Tel Aviv", "Winterthur F.C. Barcelona vs Maccabi Tel Aviv"),
            ("\"Olimpia Milano vs Real Madrid Teka", "Olimpia Milano vs Real Madrid Teka"),
            ("0VICTOR Denmark Open 2026", "VICTOR Denmark Open 2026"),
            ("$Birmingham City vs Preston North End", "Birmingham City vs Preston North End"),
            ("Belize U16 vs Nicaragua U16", "Belize U16 vs Nicaragua U16"),
            ("76ers vs Celtics", "76ers vs Celtics"),
            ("1860 Munich vs Dortmund", "1860 Munich vs Dortmund"),
            ("1. FC Koln vs Bayern Munich", "1. FC Koln vs Bayern Munich"),
        ]
        for raw, expected in self_tests:
            self.assertEqual(_clean_title(raw), expected)

    def test_parse_protobuf_wire_varint_and_bytes(self):
        """Verifica che il parser wire protobuf decodifichi campi varint e stringhe delimitate."""
        raw = bytes([0x08, 0x2A, 0x12, 0x05]) + b"Milan"
        parsed = _parse_protobuf_wire(raw)
        self.assertIn(1, parsed)
        self.assertEqual(parsed[1], [42])
        self.assertIn(2, parsed)
        self.assertEqual(parsed[2], [b"Milan"])


# ==============================================================================
# 2. TEST MOTORE SPORT: PALINSESTO DI STASERA & TUTTE LE LEGHE
# ==============================================================================
class TestSportsScraperEngine(unittest.IsolatedAsyncioTestCase):

    async def test_get_live_matches_returns_structured_events(self):
        """Verifica che lo scraper restituisca eventi strutturati con streaming URL."""
        matches = await sports_scraper.get_live_matches()
        self.assertIsInstance(matches, list)
        if matches:
            first = matches[0]
            self.assertIn("title", first)
            self.assertIn("sport_name", first)
            self.assertIn("time", first)
            self.assertIn("url", first)
            self.assertTrue(first["url"].startswith("https://web01.madplay77.ru/it/seth/livestream?mdata="))

    async def test_get_tonight_matches_general(self):
        """'mi dici i match di stasera?' deve raccogliere gli eventi di oggi con voce da Vocalist."""
        res = await sports_scraper.get_tonight_matches()
        self.assertTrue(res["found"])
        self.assertGreater(res["count"], 0)
        self.assertGreater(len(res["matches"]), 0)
        self.assertIn("voice_summary", res)
        # Deve contenere il calore di Franchino
        self.assertTrue(any(k in res["voice_summary"].lower() for k in ("ragazzi", "fuoco", "magia", "volare", "favola", "decolla")))

    async def test_get_tonight_matches_filtered_by_sport(self):
        """Verifica il filtro su basket e calcio."""
        res_calcio = await sports_scraper.get_tonight_matches(sport_filter="calcio")
        self.assertTrue(res_calcio["found"])
        for m in res_calcio["matches"][:5]:
            sport_l = (m.get("sport") or "").lower()
            self.assertTrue(any(k in sport_l for k in ("calcio", "football", "amichevoli", "champions", "league", "revelacao")))

    async def test_search_event_natural_queries(self):
        """Verifica le interrogazioni con linguaggio naturale richieste dall'utente."""
        queries = [
            "mi dici i match di stasera?",
            "chi gioca stasera?",
            "chi gioca in serie a stasera?",
            "partite di champions stasera",
            "prossima gara f1",
            "quando corre la motogp",
            "chi gioca a basket stasera",
        ]
        for q in queries:
            res = await sports_scraper.search_event(q)
            self.assertTrue(res.get("found"), f"Fallita ricerca naturale per: '{q}'")
            self.assertIn("voice_summary", res, f"Manca voice_summary per: '{q}'")
            self.assertGreater(len(res["voice_summary"]), 10)

    async def test_search_serie_a_returns_serie_a_fixtures(self):
        """'chi gioca in serie a stasera?' deve restituire incontri di Serie A (anche il prossimo turno se oggi riposa)."""
        res = await sports_scraper.search_event("chi gioca in serie a stasera?")
        self.assertTrue(res.get("found"))
        self.assertGreater(len(res.get("matches", [])), 0)
        first = res["matches"][0]
        self.assertIn("Serie A", first.get("league", ""))

    async def test_search_euroleague_matches(self):
        """'chi gioca in eurolega stasera' deve trovare i match di Euroleague con sintesi per il parquet europeo."""
        res = await sports_scraper.search_event("chi gioca in eurolega stasera")
        self.assertTrue(res.get("found"))
        self.assertGreater(len(res.get("matches", [])), 0)
        self.assertTrue(any("Euroleague" in m.get("league", "") for m in res["matches"]))
        voice = res.get("voice_summary", "").lower()
        self.assertTrue(any(k in voice for k in ("eurolega", "parquet", "canestri", "basket", "fuoco")))

    async def test_motorsport_ferrari_query(self):
        """'quando corre la ferrari?' deve essere ricondotto alla Formula 1, non a sport casuali."""
        res = await sports_scraper.search_event("quando corre la ferrari?")
        self.assertTrue(res.get("found"))
        self.assertEqual(res.get("type"), "motorsport")
        self.assertEqual(res.get("subtype"), "f1")
        self.assertIn("Formula 1", res.get("voice_summary", ""))

    async def test_inter_search_does_not_match_winterthur(self):
        """'streaming inter' non deve matchare 'Winterthur' per semplice sottostringa."""
        res = await sports_scraper.search_event("streaming inter")
        self.assertTrue(res.get("found"))
        title = res.get("best_match", {}).get("title", "")
        self.assertNotIn("Winterthur", title)
        self.assertTrue(any(k in title.lower() for k in ("inter", "internazionale")))

    async def test_motorsport_f1_and_motogp(self):
        """Verifica il recupero informazioni su F1 e MotoGP."""
        f1 = await sports_scraper.get_f1_info()
        self.assertIn("name", f1)
        self.assertIn("circuit", f1)

        mgp = await sports_scraper.get_motogp_info()
        self.assertIn("name", mgp)
        self.assertIn("circuit", mgp)

    def test_build_sports_embed_and_view(self):
        """Verifica che il builder dell'embed Discord crei view con pulsanti e colori adeguati."""
        fake_res = {
            "found": True,
            "type": "schedule",
            "matches": [
                {
                    "title": "Inter vs Juventus",
                    "league": "Serie A",
                    "sport_emoji": "⚽",
                    "time": "20:45",
                    "score": "",
                    "url": "https://web01.madplay77.ru/it",
                    "stream_url": "https://web01.madplay77.ru/it/seth/livestream?mdata=123",
                }
            ],
            "voice_summary": "Stasera in campo c'è il fuoco ragazzi!",
            "portal_url": "https://web01.madplay77.ru/it",
        }
        embed, view, voice = sports_scraper.build_sports_embed(fake_res)
        self.assertIsNotNone(embed.title)
        self.assertTrue("Palinsesto" in embed.title or "Match" in embed.title)
        self.assertGreaterEqual(len(view.children), 1)
        self.assertIn("ragazzi", voice.lower())


# ==============================================================================
# 3. TEST PERSONALITÀ E VOCALIST CARISMA IN BRAIN.PY
# ==============================================================================
class TestFranchinoBrainPersona(unittest.IsolatedAsyncioTestCase):

    async def test_brain_offline_heuristic_chat_warmth(self):
        """Verifica che le risposte chat abbiano calore umano, empatia e gergo da Vocalist."""
        test_inputs = [
            ("ciao Franchino!", "chat", ["benvenuto", "magia", "fratello", "notte"]),
            ("sono stanco oggi", "chat", ["respira", "occhi", "musica", "notte", "magia"]),
            ("sei un mito", "chat", ["magia", "famiglia", "cuore", "notte", "bene"]),
            ("come stai?", "chat", ["console", "cuore", "musica", "carico", "ritmo"]),
        ]
        for text, expected_intent, expected_keywords in test_inputs:
            decision = await brain.process_user_speech("Marco", "101", text)
            self.assertEqual(decision.get("intent"), expected_intent)
            voice = decision.get("voice_reply", "").lower()
            self.assertTrue(any(kw in voice for kw in expected_keywords), f"Keyword mancante in: '{voice}'")

    async def test_brain_offline_sports_recognition(self):
        """Verifica il riconoscimento intent sportivo da parte di Brain."""
        sport_questions = [
            "mi dici i match di stasera?",
            "chi gioca stasera?",
            "chi gioca in serie a stasera?",
            "prossima gara f1",
            "partite di champions stasera",
        ]
        for sq in sport_questions:
            decision = await brain.process_user_speech("Luca", "102", sq)
            self.assertEqual(decision.get("intent"), "sports_match")
            self.assertIn("voice_reply", decision)
            self.assertTrue(any(k in decision["voice_reply"].lower() for k in ("ragazzi", "magia", "fuoco", "motori", "chat", "stelle")))

    async def test_generate_live_fable_returns_poetic_quote(self):
        """Verifica che generate_live_fable restituisca favole poetiche e mistiche."""
        fable = await brain.generate_live_fable("Andrea")
        self.assertGreater(len(fable), 30)
        self.assertTrue(any(k in fable for k in ("Magia", "Metempsicosi", "stelle", "notte", "favola", "battito")))


# ==============================================================================
# 4. TEST SEPARAZIONE SPORT VS OUTLANDS WIKI
# ==============================================================================
class TestWikiVsSportsSeparation(unittest.TestCase):

    def test_sports_queries_do_not_trigger_outlands_wiki(self):
        """Le domande su partite e sport NON devono essere intercettate dalla Wiki di Outlands."""
        sports_queries = [
            "mi dici i match di stasera?",
            "chi gioca stasera?",
            "quali partite ci sono stasera a calcio?",
            "chi gioca in serie a stasera?",
            "chi gioca stasera a calcio nella serie a",
            "partite di champions stasera",
            "prossima gara f1",
            "quando corre la motogp",
            "match di tennis oggi",
            "partite di stasera",
        ]
        for sq in sports_queries:
            is_w, _ = is_wiki_question(sq)
            self.assertFalse(is_w, f"La domanda sportiva '{sq}' è stata erroneamente marcata come Wiki Outlands!")

    def test_genuine_outlands_queries_still_trigger_wiki(self):
        """Le vere domande su UO Outlands devono continuare ad attivare la wiki."""
        outlands_queries = [
            "quali sono i migliori pet?",
            "come funziona il taming?",
            "parlami di aegis keep",
            "statistiche del drake",
            "come si usa la mastery chain",
            "miglior build per dexxer",
        ]
        for oq in outlands_queries:
            is_w, _ = is_wiki_question(oq)
            self.assertTrue(is_w, f"La domanda Outlands '{oq}' non è stata riconosciuta dalla Wiki!")


# ==============================================================================
# 5. TEST PIPELINE VOCALE (VOICE_COMMANDS.PY)
# ==============================================================================
class TestVoiceCommandsSportsDispatch(unittest.IsolatedAsyncioTestCase):

    async def test_voice_sink_dispatches_tonight_matches(self):
        """Verifica che FranchinoVoiceSink invochi il motore sport e legga il vocal summary."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.speak_tts = AsyncMock()
        mock_player.current = None
        mock_player.text_channel = MagicMock()
        mock_player.guild = MagicMock()
        mock_player.voice_client = MagicMock()

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = 777
        mock_user.display_name = "Matteo"

        # Esecuzione comando vocale "franchino mi dici i match di stasera?"
        await sink._handle_transcribed_text(
            mock_user,
            "franchino mi dici i match di stasera?",
            detected_cmd="mi dici i match di stasera?"
        )

        # Deve aver inviato notifica in canale e TTS a voce
        sink._notify_channel.assert_awaited()
        call_kwargs = sink._notify_channel.call_args[1]
        self.assertIn("embed", call_kwargs)
        self.assertIn("view", call_kwargs)
        self.assertIn("Franchino Sports", call_kwargs.get("message", ""))

        # Verifica che il TTS sia stato schedulato con una frase da vocalist
        await asyncio.sleep(0.05)
        mock_player.speak_tts.assert_called()
        spoken_text = mock_player.speak_tts.call_args[0][0]
        self.assertTrue(any(k in spoken_text.lower() for k in ("ragazzi", "fuoco", "magia", "chat", "partite", "decolla")))


# ==============================================================================
# 6. TEST SLASH COMMANDS IN MAIN.PY
# ==============================================================================
class TestSlashCommandsInMain(unittest.TestCase):

    def test_partita_and_partite_commands_registered(self):
        """Verifica che i comandi /partita, /partite e /sport siano registrati nel bot con i relativi alias."""
        from main import bot
        partita_cmd = bot.get_command("partita")
        partite_cmd = bot.get_command("partite")
        sport_cmd = bot.get_command("sport")

        self.assertIsNotNone(partita_cmd)
        self.assertIsNotNone(partite_cmd)
        self.assertIsNotNone(sport_cmd)
        self.assertEqual(sport_cmd.name, "sport")
        self.assertEqual(partite_cmd.name, "partite")
        self.assertIn("match", partite_cmd.aliases)
        self.assertIn("palinsesto", sport_cmd.aliases)


# ==============================================================================
# 7. TEST DISPATCHER DECISIONI BRAIN (VOICE_COMMANDS)
# ==============================================================================
class TestVoiceSinkBrainDispatcher(unittest.IsolatedAsyncioTestCase):

    async def test_dispatch_brain_sports_intent(self):
        """Verifica che un intento sports_match da Brain venga instradato allo scraper."""
        mock_player = MagicMock()
        mock_player.speak_tts = AsyncMock()
        def _dummy_create_task(coro):
            coro.close()
            return MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=_dummy_create_task)
        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = 888
        mock_user.display_name = "Simone"

        decision = {
            "intent": "sports_match",
            "sport_query": "chi gioca in serie a stasera?",
            "voice_reply": "Il grande calcio italiano!",
            "chat_reply": "Cerco la Serie A!",
        }

        handled = await sink._dispatch_brain_decision(mock_user, decision, "chi gioca in serie a stasera?")
        self.assertTrue(handled)
        sink._notify_channel.assert_awaited()
        mock_player.speak_tts.assert_called()


if __name__ == "__main__":
    unittest.main()

