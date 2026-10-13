"""
test_jack_math_and_razor_scripts.py
===================================
Test Suite approfondita per:
1. Ripristino completo e verifica avanzata degli script UO Outlands (razor_scripts.py, scrape_razor_scripts.py, /script).
2. Motore Matematico di Intelligenza "Super Matematico Jack" (JackIntelligenceFormula, BM25, Jaro-Winkler, Softmax).
3. Ottimizzazione delle risorse hardware per Oracle Cloud ARM (2 vCPU ARM, 12 GB RAM, 200 GB disco).
"""

import asyncio
import io
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import pytest

from razor_scripts import (
    resolve_skill_from_text,
    is_script_question,
    get_scripts_for_skill,
    get_script_by_id,
    build_razor_scripts_response,
    build_razor_skills_list_response,
    RazorScriptView,
    RazorScriptSelect,
    RazorSkillPickerView,
    SKILL_MAP,
    SKILL_CATEGORIES,
)
from jack_math_engine import (
    jack_engine,
    hardware_optimizer,
    JackIntelligenceFormula,
    HardwareOptimizer,
    BM25DomainIndex,
    jaro_winkler_distance,
    W_WAKE,
    W_PHONETIC,
    W_DOMAIN,
    W_ACTION,
    BETA_SOFTMAX,
)
from scripts.scrape_razor_scripts import (
    verify_catalog_integrity,
    export_db_to_cache_json,
    import_cache_json_to_db,
    sync_razor_scripts,
)


# ==============================================================================
# 1. TEST UO OUTLANDS RAZOR SCRIPTS: SINONIMI, VERBI & RICHIESTE NATURALI
# ==============================================================================

class TestRazorScriptsAdvancedSkillResolution:
    """Test della risoluzione per tutti i verbi e sinonimi italiani aggiunti."""

    @pytest.mark.parametrize("query, expected_slug", [
        ("meditare", "meditation"),
        ("tammare", "animaltaming"),
        ("tamare", "animaltaming"),
        ("domare", "animaltaming"),
        ("curare", "healing"),
        ("curarsi", "healing"),
        ("pescare", "fishing"),
        ("minare", "mining"),
        ("scavare", "mining"),
        ("scassinare", "lockpicking"),
        ("nascondersi", "hiding"),
        ("nascondere", "hiding"),
        ("rubare", "stealing"),
        ("borseggiare", "snooping"),
        ("avvelenare", "poisoning"),
        ("cucinare", "cooking"),
        ("forgiare", "blacksmith"),
        ("cucire", "tailoring"),
        ("attrezzi", "tinkering"),
        ("iscrivere", "inscribe"),
        ("suonare", "musicianship"),
        ("provocare", "provocation"),
        ("calmare", "peacemaking"),
        ("tracciare", "tracking"),
        ("identificare", "itemid"),
        ("campeggiare", "camping"),
        ("spada", "swords"),
        ("spade", "swords"),
        ("scherma", "fencing"),
        ("mazza", "macing"),
        ("arco", "archery"),
        ("parata", "parry"),
        ("lotta", "wrestling"),
        ("resistere", "magicresist"),
        ("spiriti", "spiritspeak"),
        ("paladino", "chivalry"),
    ])
    def test_italian_verbs_and_synonyms(self, query, expected_slug):
        res = resolve_skill_from_text(query)
        assert res is not None, f"Impossibile risolvere skill per: {query}"
        assert res[1] == expected_slug


class TestRazorScriptsIntentDetection(unittest.TestCase):
    """Test del rilevamento vocale per tutte le forme di richiesta script e macro."""

    def test_natural_speech_voice_queries(self):
        queries = [
            ("script per magery", True, "magery"),
            ("mi passi lo script di taming", True, "animaltaming"),
            ("dammi lo script per pesca", True, "fishing"),
            ("passami lo script di lockpicking", True, "lockpicking"),
            ("script di taming", True, "animaltaming"),
            ("hai uno script per mining?", True, "mining"),
            ("script per meditare", True, "meditation"),
            ("script per pescare", True, "fishing"),
            ("script per scassinare", True, "lockpicking"),
            ("script per tammare", True, "animaltaming"),
            ("macro per magery", True, "magery"),
            ("macro di taming", True, "animaltaming"),
            ("macro per pesca", True, "fishing"),
        ]
        for q, exp_is_script, exp_slug in queries:
            is_sc, slug = is_script_question(q)
            self.assertEqual(is_sc, exp_is_script, f"Errore booleano per '{q}'")
            self.assertEqual(slug, exp_slug, f"Errore slug per '{q}' (ottenuto: {slug})")

    def test_general_script_catalog_queries(self):
        general_queries = [
            "che script hai?",
            "quali script hai?",
            "lista degli script",
            "script outlands",
            "script uo",
            "script per outlands",
            "uo scripts",
            "script per uo outlands",
            "dammi lo script per outlands",
            "lista script",
            "tutti gli script",
        ]
        for q in general_queries:
            is_sc, slug = is_script_question(q)
            self.assertTrue(is_sc, f"Fallito riconoscimento generale per '{q}'")
            self.assertEqual(slug, "lista", f"Atteso 'lista' per query generale '{q}', ottenuto '{slug}'")

    def test_non_script_questions_negative(self):
        non_scripts = [
            "metti la musica di vasco",
            "chi gioca stasera?",
            "come funziona il dungeon aegis",
            "alza il volume",
            "stop",
            "raccontami una favola",
        ]
        for q in non_scripts:
            is_sc, _ = is_script_question(q)
            self.assertFalse(is_sc, f"Falso positivo script per '{q}'")


class TestRazorScriptsDatabaseAndCacheFallback(unittest.TestCase):
    """Verifica il fallback trasparente tra SQLite e la cache JSON data/razor_scripts_cache.json."""

    def test_cache_file_exists_and_valid(self):
        cache_path = os.path.join(os.path.dirname(__file__), "data", "razor_scripts_cache.json")
        self.assertTrue(os.path.isfile(cache_path), "File data/razor_scripts_cache.json assente!")
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("scripts", data)
        self.assertGreaterEqual(len(data["scripts"]), 600)

    def test_fallback_when_db_missing(self):
        import razor_scripts
        orig_db = razor_scripts.DB_PATH
        try:
            razor_scripts.DB_PATH = "/tmp/fake_non_existent_knowledge_db.db"
            scripts = get_scripts_for_skill("magery")
            self.assertGreaterEqual(len(scripts), 50, "Il fallback su JSON deve caricare gli script")
            first = scripts[0]
            self.assertIn("title", first)
            self.assertIn("code", first)

            # Test recupero per ID tramite fallback cache
            single = get_script_by_id(first["script_id"])
            self.assertIsNotNone(single)
            self.assertEqual(single["title"], first["title"])
        finally:
            razor_scripts.DB_PATH = orig_db

    def test_build_skills_catalog_response(self):
        embed, view, file, voice, code = build_razor_skills_list_response()
        self.assertIsNotNone(embed)
        self.assertIn("Catalogo Script Razor", embed.title)
        self.assertIsNotNone(view)
        self.assertIsInstance(view, RazorSkillPickerView)
        self.assertIn("Magia", voice)
        self.assertIn("600", voice)


class TestScraperModuleAndSync(unittest.TestCase):
    """Verifica il funzionamento del modulo di scraping e sincronizzazione scripts/scrape_razor_scripts.py."""

    def test_verify_catalog_integrity(self):
        st = verify_catalog_integrity()
        self.assertTrue(st["cache_exists"])
        self.assertGreaterEqual(st["total_scripts"], 600)
        self.assertGreaterEqual(st["scripts_with_code"], 600)
        self.assertGreaterEqual(st["skills_count"], 51)
        self.assertIn("Magery", st["skills_summary"])

    def test_sync_razor_scripts_idempotency(self):
        st = sync_razor_scripts()
        self.assertTrue(st["cache_exists"])
        self.assertGreaterEqual(st["total_scripts"], 600)


# ==============================================================================
# 2. TEST FORMULA MATEMATICA JACK (SUPER MATEMATICO JACK)
# ==============================================================================

class TestJackIntelligenceFormula(unittest.TestCase):
    """Test analitico dei componenti e della formula S(Intent, q) e P(Intent | q)."""

    def test_formula_weights_sum_to_one(self):
        """I pesi positivi della formula devono sommare esattamente a 1.0."""
        pos_sum = W_WAKE + W_PHONETIC + W_DOMAIN + W_ACTION
        self.assertAlmostEqual(pos_sum, 1.0, places=5)

    def test_jaro_winkler_phonetic_distance(self):
        """Verifica la robustezza contro errori di trascrizione vocale STT."""
        # Errori realistici di trascrizione STT
        self.assertGreaterEqual(jaro_winkler_distance("teiming", "taming"), 0.80)
        self.assertGreaterEqual(jaro_winkler_distance("lokpiking", "lockpicking"), 0.90)
        self.assertGreaterEqual(jaro_winkler_distance("partida", "partita"), 0.90)
        self.assertEqual(jaro_winkler_distance("identical", "identical"), 1.0)
        self.assertEqual(jaro_winkler_distance("", "test"), 0.0)

    def test_bm25_domain_saturation(self):
        """Verifica che lo score BM25 sia compreso in [0, 1] e sia monotonicamente crescente con le occorrenze."""
        bm25 = jack_engine.bm25
        score_magery = bm25.score(["magery", "script"], "razor_script")
        score_unrelated = bm25.score(["motogp", "ferrari"], "razor_script")
        self.assertGreaterEqual(score_magery, 0.0)
        self.assertLessEqual(score_magery, 1.0)
        self.assertGreater(score_magery, score_unrelated)

    def test_wake_detection_and_anti_wake_filter(self):
        """Verifica phi_wake e l'abbattimento della confidenza per false attivazioni colloquiali."""
        phi_direct = jack_engine.calculate_wake("Franchino dammi lo script per pesca")
        self.assertEqual(phi_direct, 1.0)

        phi_semi = jack_engine.calculate_wake("Ehi Franchino dimmi la favola")
        self.assertGreaterEqual(phi_semi, 0.80)

        phi_chatter = jack_engine.calculate_wake("Fra chi ha il boost al server?")
        self.assertLessEqual(phi_chatter, 0.10)

        phi_third_person = jack_engine.calculate_wake("Secondo me Franchino lagga un po'")
        self.assertLessEqual(phi_third_person, 0.15)

    def test_all_ten_intents_mathematical_classification(self):
        """Tutti i 10 domini di intenzione devono essere classificati con largo margine di confidenza."""
        test_cases = [
            ("Franchino script per magery", "razor_script"),
            ("Franchino mi passi lo script di taming", "razor_script"),
            ("Franchino dammi lo script per pesca", "razor_script"),
            ("Franchino che script hai?", "razor_script"),
            ("Franchino come funziona aspect armor su outlands?", "wiki_guide"),
            ("Franchino chi gioca stasera?", "sports_match"),
            ("Franchino cerca la partita del Milan", "sports_match"),
            ("Franchino metti Gigi D Agostino", "play_music"),
            ("Franchino alza il volume al massimo", "dj_control"),
            ("Franchino stop", "dj_control"),
            ("Franchino raccontami una favola", "live_fable"),
            ("Franchino ci senti?", "liveness_check"),
            ("Franchino come stai fratello?", "chat"),
            ("Franchino sposta Marco in Stanza Relax", "admin_control"),
            ("Franchino fai un brindisi per Giulia", "social_interaction"),
        ]
        for query, expected_intent in test_cases:
            res = jack_engine.classify(query)
            self.assertEqual(
                res.intent,
                expected_intent,
                f"Classificazione errata per '{query}': atteso {expected_intent}, ottenuto {res.intent} ({res.to_math_summary()})"
            )
            self.assertGreater(res.confidence_margin, 0.10, f"Margine troppo basso per '{query}': {res.confidence_margin}")
            self.assertGreater(res.probability, 0.25, f"Probabilità troppo bassa per '{query}': {res.probability}")

    def test_probabilities_sum_to_one(self):
        """La distribuzione di probabilità Softmax deve sommare esattamente a 1.0."""
        res = jack_engine.classify("Franchino script per magery")
        prob_sum = sum(res.probabilities_by_intent.values())
        self.assertAlmostEqual(prob_sum, 1.0, places=4)


# ==============================================================================
# 3. TEST OTTIMIZZAZIONE RISORSE SERVER ARM (2 vCPU, 12 GB RAM, 200 GB Disco)
# ==============================================================================

class TestARMHardwareOptimization(unittest.TestCase):
    """Test delle metriche e dell'efficienza per server Oracle Cloud ARM."""

    def test_hardware_telemetry_metrics(self):
        telem = hardware_optimizer.get_hardware_telemetry()
        self.assertIn("ARM64", telem["server_arch"])
        self.assertEqual(telem["cpu_cores_target"], 2)
        self.assertEqual(telem["system_ram_gb_total"], 12.0)
        self.assertGreater(telem["process_ram_mb"], 0.0)
        self.assertLess(telem["process_ram_mb"], 400.0, "Il consumo RAM deve essere contenuto (<400 MB)")
        self.assertGreater(telem["disk_total_gb"], 10.0)

    def test_garbage_collection_call(self):
        collected = hardware_optimizer.run_garbage_collection()
        self.assertIsInstance(collected, int)

    def test_classification_latency_efficiency(self):
        """Il motore Jack deve completare la classificazione in meno di 2 millisecondi."""
        import time
        t0 = time.perf_counter()
        for _ in range(50):
            jack_engine.classify("Franchino dammi lo script per pesca")
        t_elapsed = (time.perf_counter() - t0) / 50.0
        self.assertLess(t_elapsed, 0.003, f"Latenza di classificazione troppo alta: {t_elapsed*1000:.2f} ms")


# ==============================================================================
# 4. TEST COMANDI SLASH /script E /boost IN main.py
# ==============================================================================

class TestSlashCommandsIntegration(unittest.IsolatedAsyncioTestCase):
    """Verifica che i comandi /script e /boost gestiscano tutti gli argomenti senza eccezioni."""

    async def test_slash_script_command_default_lista(self):
        from main import script_cmd

        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.author.display_name = "Davide"
        ctx.guild = MagicMock()

        # Invocazione senza argomenti (default skill="lista")
        await script_cmd.callback(ctx, skill="lista", numero=None)
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        embed = call_kwargs["embed"]
        self.assertIn("Catalogo Script Razor", embed.title)

    async def test_slash_script_command_specific_skill(self):
        from main import script_cmd

        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.author.display_name = "Davide"
        ctx.guild = MagicMock()

        await script_cmd.callback(ctx, skill="lockpicking", numero=1)
        ctx.send.assert_awaited_once()
        call_kwargs = ctx.send.call_args.kwargs
        self.assertIn("embed", call_kwargs)
        self.assertIn("file", call_kwargs)
        self.assertTrue(call_kwargs["file"].filename.endswith(".razor"))

    async def test_slash_boost_command_jack_formula(self):
        from main import boost_cmd

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
        self.assertTrue(any("Hardware Server" in f.name for f in embed.fields))


# ==============================================================================
# 5. TEST ROBUSTEZZA SKILL SCONOSCIUTE, SELECT MULTIPLI & PARSER LIVE
# ==============================================================================

class TestRazorScriptsRobustnessAndUI(unittest.TestCase):
    """Test approfondito dei casi limite: skill sconosciute, menu paginati e parsing."""

    def test_unknown_skill_returns_helpful_catalog_embed(self):
        """Una skill inesistente non deve mai ritornare embed=None ma il catalogo completo."""
        embed, view, file, voice, code = build_razor_scripts_response("pirateria_fantasma")
        self.assertIsNotNone(embed)
        self.assertIn("Nessuno Script", embed.title)
        self.assertIsNotNone(view)
        self.assertIsNone(file)
        self.assertIsNone(code)
        self.assertIn("Non ho trovato script Razor per Pirateria_Fantasma", voice)

    def test_multi_select_view_for_large_skills(self):
        """Skill con più di 25 script (es. Magery con 96) devono presentare 2 Select Menu."""
        scripts = get_scripts_for_skill("magery")
        self.assertGreater(len(scripts), 25)
        view = RazorScriptView(scripts, "Magery")
        self.assertEqual(len(view.children), 2, "Devono essere presenti 2 Select Menu per oltre 25 script")
        self.assertEqual(len(view.children[0].options), 25)
        self.assertEqual(len(view.children[1].options), 25)

    def test_popular_and_category_selects_in_picker(self):
        """La View generale del catalogo deve contenere sia le Top Skill dirette sia le Categorie."""
        view = RazorSkillPickerView()
        self.assertEqual(len(view.children), 2)
        from razor_scripts import RazorPopularSkillSelect, RazorSkillCategorySelect
        self.assertIsInstance(view.children[0], RazorPopularSkillSelect)
        self.assertIsInstance(view.children[1], RazorSkillCategorySelect)


class TestJackMathEngineIntegration(unittest.TestCase):
    """Verifica l'integrazione del motore Jack con brain.py e le richieste vocali."""

    def test_jack_math_boost_intent_queries(self):
        queries = [
            "Franchino chiedi al super matematico Jack la formula",
            "Franchino super matematico Jack",
            "Franchino boost matematico",
            "Franchino formula matematica di Jack",
            "Franchino spiegami la formula matematica",
            "Franchino boost jack",
            "Franchino cosa dice jack",
        ]
        for q in queries:
            res = jack_engine.classify(q)
            self.assertEqual(
                res.intent,
                "jack_math_boost",
                f"Query '{q}' non classificata come jack_math_boost: ottenuta {res.intent}"
            )
            self.assertGreater(res.probability, 0.30)
            self.assertIn("formula", res.extracted_entities)
            self.assertIn("telemetry", res.extracted_entities)

    def test_brain_fallback_integrates_jack_engine(self):
        """Il fallback di brain.py deve riconoscere sia Jack sia gli script Razor tramite formula."""
        from brain import brain
        res_jack = brain._fallback_heuristic_intent("Franchino chiedi al super matematico Jack la formula")
        self.assertEqual(res_jack["intent"], "jack_math_boost")
        self.assertIn("Formula", res_jack["chat_reply"])

        res_script = brain._fallback_heuristic_intent("Franchino dammi lo script per pesca")
        self.assertEqual(res_script["intent"], "razor_script")
        self.assertEqual(res_script["skill_slug"], "fishing")

    def test_hardware_optimizer_configure_for_arm(self):
        """Verifica la configurazione runtime per 2 core ARM."""
        conf = hardware_optimizer.configure_for_arm()
        self.assertTrue(conf["configured"])
        self.assertEqual(conf["target_vcpu"], 2)
        self.assertEqual(conf["target_ram_gb"], 12.0)
        self.assertEqual(conf["gc_thresholds"], (700, 10, 10))

    def test_turbo_stream_parser_live_payload(self):
        """Verifica la funzione di decodifica turbo-stream in scrape_razor_scripts.py."""
        from scripts.scrape_razor_scripts import parse_turbo_stream_script
        mock_payload = json.dumps([
            {"_1": 2},
            "root",
            {"_3": 4},
            "data",
            {"_5": 6, "_7": 8, "_9": 10, "_11": 12, "_13": 14, "_15": 16, "_17": 18, "_19": 20},
            "id",
            "12345678-1234-1234-1234-123456789abc",
            "name",
            "Test Script Magery",
            "category",
            "Magery",
            "content",
            "say 'Heal'",
            "description",
            "Script di test per cura",
            "tags",
            "pvp, heal",
            "version",
            "v1.0",
            "displayName",
            "DavideTester",
        ])
        res = parse_turbo_stream_script(mock_payload)
        self.assertIsNotNone(res)
        self.assertEqual(res["script_id"], "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(res["title"], "Test Script Magery")
        self.assertEqual(res["skill_slug"], "magery")
        self.assertEqual(res["code"], "say 'Heal'")
        self.assertEqual(res["author"], "DavideTester")


# ==============================================================================
# 5. TEST DISAMBIGUAZIONE AUTOMATICA SOTTO IL COFANO (SENZA BOOST)
# ==============================================================================

class TestTransparentDomainDisambiguation(unittest.TestCase):
    """
    Test suite per verificare che Franchino distingua automaticamente e silenziosamente tra:
    1. Ultima Online Outlands (creature, dungeon, aspetti, skill, build, item, script)
    2. Musica (richieste di riproduzione YouTube/Spotify, brani, artisti, generi)
    3. Altre funzionalità (smart assistant, sport, vocalist chat)
    SENZA che l'utente debba pronunciare la parola 'boost' o invocare formule.
    """

    def test_outlands_queries_without_boost(self):
        """Verifica che query naturali e brevi su UO Outlands siano identificate come wiki_guide."""
        from outlands_wiki import is_wiki_question, is_outlands_entity
        from brain import brain

        test_cases = [
            "bloodwolf",
            "aegis keep",
            "drake",
            "magery",
            "pesca",
            "cosa fa il fire aspect",
            "dove trovo la mastery chain",
            "dimmi tutto su cavernam",
            "quanta skill serve per un ostard",
        ]

        for query in test_cases:
            is_ent, ent_name, ent_type = is_outlands_entity(query)
            self.assertTrue(
                is_ent,
                f"is_outlands_entity ha fallito per la query Outlands: '{query}'"
            )

            is_w, w_q = is_wiki_question(query)
            self.assertTrue(
                is_w,
                f"is_wiki_question ha fallito per la query Outlands: '{query}'"
            )

            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "wiki_guide",
                f"Jack Math ha classificato erroneamente '{query}' come {jack_res.intent} (prob={jack_res.probability:.2f})"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "wiki_guide",
                f"Brain decide_intent ha classificato erroneamente '{query}' come {decision['intent']}"
            )
            self.assertIn("wiki_topic", decision)

    def test_music_queries_route_to_playback(self):
        """Verifica che le richieste musicali vengano classificate come play_music e NON wiki."""
        from outlands_wiki import is_wiki_question
        from brain import brain

        music_queries = [
            "metti albachiara",
            "suona i queen",
            "riproduci eminem",
            "play bohemian rhapsody",
            "fammi sentire i coldplay",
        ]

        for query in music_queries:
            is_w, _ = is_wiki_question(query)
            self.assertFalse(
                is_w,
                f"is_wiki_question ha erroneamente scambiato la richiesta musicale '{query}' per wiki!"
            )

            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "play_music",
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di play_music"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "play_music",
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']} invece di play_music"
            )
            self.assertTrue(bool(decision.get("music_query")))

    def test_razor_script_queries_route_correctly(self):
        """Verifica che le richieste di script Razor vengano riconosciute come razor_script."""
        from brain import brain

        script_queries = [
            ("script per magery", "magery"),
            ("dammi lo script per pesca", "fishing"),
            ("macro lockpicking", "lockpicking"),
            ("script animal taming", "animaltaming"),
        ]

        for query, expected_slug in script_queries:
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "razor_script",
                f"Jack Math non ha classificato '{query}' come razor_script (got {jack_res.intent})"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "razor_script",
                f"Brain non ha classificato '{query}' come razor_script (got {decision['intent']})"
            )
            self.assertEqual(decision["skill_slug"], expected_slug)

    def test_smart_assistant_queries_without_boost(self):
        """Verifica che i comandi smart assistant non vengano intercettati da wiki o musica."""
        from brain import brain

        smart_queries = [
            "Franchino che ore sono?",
            "Franchino che tempo fa a Milano?",
            "Franchino lancia un dado",
            "Franchino testa o croce",
            "Franchino quanto fa 50 diviso 2?",
        ]

        for query in smart_queries:
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "smart_assistant",
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di smart_assistant"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "smart_assistant",
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']} invece di smart_assistant"
            )

    def test_sports_queries_route_to_sports(self):
        """Verifica che le richieste su partite e gare sportive vadano a sports_match."""
        from brain import brain

        sport_queries = [
            "chi gioca stasera",
            "quali partite ci sono oggi",
            "quando corre la formula 1",
            "orari della motogp",
        ]

        for query in sport_queries:
            jack_res = jack_engine.classify(query)
            self.assertIn(
                jack_res.intent,
                ("sports_match", "sports_schedule"),
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di sport"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "sports_match",
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']}"
            )

    def test_blended_music_and_outlands_queries(self):
        """Verifica che query con verbi musicali e nomi sovrapposti (drake, pet shop boys) vadano a play_music."""
        from brain import brain

        blended_cases = [
            ("suona la canzone del drake", "play_music"),
            ("suona drake", "play_music"),
            ("suona i pet shop boys", "play_music"),
            ("metti pet shop boys", "play_music"),
            ("metti drake", "play_music"),
            ("metti balron", "play_music"),
        ]

        for query, expected_intent in blended_cases:
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                expected_intent,
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di {expected_intent}"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                expected_intent,
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']} invece di {expected_intent}"
            )

    def test_dj_console_control_queries(self):
        """Verifica che i comandi di controllo DJ (stop, pausa, volume, loop, shuffle, bass boost) vadano a dj_control e non a musica o boost matematico."""
        from brain import brain

        dj_cases = [
            "stop",
            "stoppa la musica",
            "ferma la musica",
            "metti in pausa",
            "pausa",
            "riprendi la musica",
            "salta la traccia",
            "skip",
            "alza il volume",
            "abbassa il volume",
            "metti in loop",
            "metti in shuffle",
            "attiva il bass boost",
            "togli il boost",
        ]

        for query in dj_cases:
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "dj_control",
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di dj_control"
            )

            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "dj_control",
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']} invece di dj_control"
            )

    def test_generic_dungeons_with_context_and_songs(self):
        """Verifica la disambiguazione tra dungeon generici con contesto (inferno, ocean) e canzoni/artisti omonimi (ocean eyes, frank ocean)."""
        from outlands_wiki import is_wiki_question, is_outlands_entity
        from brain import brain

        # Richieste dungeon con contesto -> wiki_guide
        dungeon_context_queries = [
            "cerca la guida di inferno",
            "boss del dungeon inferno",
            "guida dungeon ocean",
            "guida a inferno",
        ]
        for query in dungeon_context_queries:
            is_ent, ent_name, ent_type = is_outlands_entity(query)
            self.assertTrue(
                is_ent,
                f"is_outlands_entity non ha riconosciuto il contesto dungeon per '{query}'"
            )
            is_w, _ = is_wiki_question(query)
            self.assertTrue(
                is_w,
                f"is_wiki_question non ha riconosciuto '{query}' come domanda wiki"
            )
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "wiki_guide",
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di wiki_guide"
            )

        # Brani o artisti omonimi -> play_music, NON wiki
        music_disambig_queries = [
            "ocean eyes",
            "frank ocean",
            "metti ocean eyes",
            "suona frank ocean",
        ]
        for query in music_disambig_queries:
            is_w, _ = is_wiki_question(query)
            self.assertFalse(
                is_w,
                f"is_wiki_question ha erroneamente scambiato '{query}' per wiki!"
            )
            jack_res = jack_engine.classify(query)
            self.assertIn(
                jack_res.intent,
                ("play_music", "chat"),
                f"Jack Math ha classificato '{query}' erroneamente come {jack_res.intent}"
            )

    def test_liveness_and_social_queries(self):
        """Verifica che controlli di presenza (ci senti, sei vivo) e interazioni sociali (brindisi) non vadano a musica o wiki."""
        from brain import brain

        liveness_queries = ["ci senti", "sei vivo", "ci sei"]
        for query in liveness_queries:
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "liveness_check",
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di liveness_check"
            )
            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "liveness_check",
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']}"
            )

        social_queries = ["fai un brindisi", "batti cinque", "abbraccia marco"]
        for query in social_queries:
            jack_res = jack_engine.classify(query)
            self.assertEqual(
                jack_res.intent,
                "social_interaction",
                f"Jack Math ha classificato '{query}' come {jack_res.intent} invece di social_interaction"
            )
            decision = brain.decide_intent(query)
            self.assertEqual(
                decision["intent"],
                "social_interaction",
                f"Brain decide_intent ha classificato '{query}' come {decision['intent']}"
            )


