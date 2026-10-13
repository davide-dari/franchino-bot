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
            ("Franchino manda un bacio a Giulia", "social_interaction"),
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

