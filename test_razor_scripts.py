"""
Test automatizzati per il modulo razor_scripts e l'integrazione di guide approfondite UO Outlands.
"""

import pytest
import asyncio
import discord
from razor_scripts import (
    resolve_skill_from_text,
    is_script_question,
    get_scripts_for_skill,
    get_script_by_id,
    build_razor_scripts_response,
    RazorScriptView,
    RazorScriptSelect,
    SKILL_MAP,
)
from outlands_wiki import query_outlands_wiki, is_wiki_question


# ==============================================================================
# 1. TEST RISOLUZIONE DELLE SKILL (ITALIANO / INGLESE / SLANG)
# ==============================================================================

class TestSkillResolution:
    """Test della risoluzione dei nomi delle 52 skill in italiano e inglese."""

    @pytest.mark.parametrize("query, expected_name, expected_slug", [
        ("magery", "Magery", "magery"),
        ("magia", "Magery", "magery"),
        ("mago", "Magery", "magery"),
        ("taming", "Animal Taming", "animaltaming"),
        ("animal taming", "Animal Taming", "animaltaming"),
        ("addomesticare", "Animal Taming", "animaltaming"),
        ("addomesticamento", "Animal Taming", "animaltaming"),
        ("lockpicking", "Lockpicking", "lockpicking"),
        ("lockpick", "Lockpicking", "lockpicking"),
        ("scassinare", "Lockpicking", "lockpicking"),
        ("grimaldello", "Lockpicking", "lockpicking"),
        ("fishing", "Fishing", "fishing"),
        ("pesca", "Fishing", "fishing"),
        ("pescare", "Fishing", "fishing"),
        ("mining", "Mining", "mining"),
        ("minare", "Mining", "mining"),
        ("estrazione", "Mining", "mining"),
        ("lumberjacking", "Lumberjacking", "lumberjacking"),
        ("taglialegna", "Lumberjacking", "lumberjacking"),
        ("legna", "Lumberjacking", "lumberjacking"),
        ("blacksmith", "Blacksmithy", "blacksmith"),
        ("fabbro", "Blacksmithy", "blacksmith"),
        ("carpentry", "Carpentry", "carpentry"),
        ("falegname", "Carpentry", "carpentry"),
        ("tailoring", "Tailoring", "tailoring"),
        ("sarto", "Tailoring", "tailoring"),
        ("alchemy", "Alchemy", "alchemy"),
        ("alchimia", "Alchemy", "alchemy"),
        ("pozioni", "Alchemy", "alchemy"),
        ("chivalry", "Chivalry", "chivalry"),
        ("cavalleria", "Chivalry", "chivalry"),
        ("paladino", "Chivalry", "chivalry"),
        ("necromancy", "Necromancy", "necromancy"),
        ("negromanzia", "Necromancy", "necromancy"),
        ("stealth", "Stealth", "stealth"),
        ("hiding", "Hiding", "hiding"),
        ("nascondersi", "Hiding", "hiding"),
        ("stealing", "Stealing", "stealing"),
        ("rubare", "Stealing", "stealing"),
        ("snooping", "Snooping", "snooping"),
        ("sbirciare", "Snooping", "snooping"),
        ("poisoning", "Poisoning", "poisoning"),
        ("veleno", "Poisoning", "poisoning"),
        ("cartography", "Cartography", "cartography"),
        ("cartografia", "Cartography", "cartography"),
        ("inscribe", "Inscription", "inscribe"),
        ("iscrizione", "Inscription", "inscribe"),
        ("tinkering", "Tinkering", "tinkering"),
        ("attrezzi", "Tinkering", "tinkering"),
        ("veterinary", "Veterinary", "veterinary"),
        ("veterinaria", "Veterinary", "veterinary"),
        ("healing", "Healing", "healing"),
        ("curare", "Healing", "healing"),
        ("bende", "Healing", "healing"),
        ("parry", "Parrying", "parry"),
        ("scudo", "Parrying", "parry"),
        ("archery", "Archery", "archery"),
        ("arco", "Archery", "archery"),
        ("swords", "Swordsmanship", "swords"),
        ("spada", "Swordsmanship", "swords"),
        ("fencing", "Fencing", "fencing"),
        ("scherma", "Fencing", "fencing"),
        ("macing", "Mace Fighting", "macing"),
        ("mazza", "Mace Fighting", "macing"),
    ])
    def test_resolve_skill_from_text(self, query, expected_name, expected_slug):
        res = resolve_skill_from_text(query)
        assert res is not None, f"Skill non risolta per: {query}"
        name, slug = res
        assert name == expected_name
        assert slug == expected_slug


# ==============================================================================
# 2. TEST RICONOSCIMENTO VOCALE INTENTO SCRIPT RAZOR (is_script_question)
# ==============================================================================

class TestIsScriptQuestion:
    """Test del rilevamento di richieste vocali o testuali di script."""

    @pytest.mark.parametrize("query, expected_slug", [
        ("script per magery", "magery"),
        ("script magery", "magery"),
        ("script lockpicking", "lockpicking"),
        ("dammi lo script di lockpicking", "lockpicking"),
        ("hai uno script per taming", "animaltaming"),
        ("mostra lo script per pesca", "fishing"),
        ("script per allenare la pesca", "fishing"),
        ("codice razor per mining", "mining"),
        ("script taglialegna", "lumberjacking"),
        ("script chivalry", "chivalry"),
        ("script per cavalleria", "chivalry"),
        ("script rubare", "stealing"),
        ("script stealth", "stealth"),
        ("script bende", "healing"),
        ("script bardo", "musicianship"),
    ])
    def test_is_script_question_positive(self, query, expected_slug):
        is_sc, slug = is_script_question(query)
        assert is_sc is True
        assert slug == expected_slug

    @pytest.mark.parametrize("non_script_query", [
        "metti la canzone di drake",
        "play build me up buttercup",
        "skip",
        "pausa",
        "cosa puoi fare su outlands",
        "quali sono i migliori pet per farmare",
        "come funziona lockpicking",
        "dimmi tutto su aegis keep",
    ])
    def test_is_script_question_negative(self, non_script_query):
        is_sc, _ = is_script_question(non_script_query)
        assert is_sc is False


# ==============================================================================
# 3. TEST QUERY SCRIPT DA DATABASE SQLITE
# ==============================================================================

class TestScriptDatabaseRetrieval:
    """Test di lettura dati reali da razor_scripts in outlands_knowledge.db."""

    def test_magery_scripts_retrieval(self):
        scripts = get_scripts_for_skill("magery")
        assert len(scripts) >= 50, f"Attesi almeno 50 script per magery, trovati {len(scripts)}"
        first = scripts[0]
        assert "title" in first
        assert "author" in first
        assert "code" in first
        assert len(first["code"]) > 10, "Il codice dello script non deve essere vuoto"
        assert first["url"].startswith("https://outlands.uorazorscripts.com")

    def test_lockpicking_scripts_retrieval(self):
        scripts = get_scripts_for_skill("lockpicking")
        assert len(scripts) >= 20
        # Tutti gli script devono avere un ID valido e un titolo
        for s in scripts:
            assert len(s["script_id"]) > 10
            assert len(s["title"]) > 0

    def test_animal_taming_scripts_retrieval(self):
        scripts = get_scripts_for_skill("animaltaming")
        assert len(scripts) >= 20
        # Almeno uno script ha codice
        with_code = [s for s in scripts if s.get("code") and len(s["code"]) > 10]
        assert len(with_code) > 0


# ==============================================================================
# 4. TEST COSTRUZIONE RISPOSTA COMPLETA DISCORD (Embed, Select View, File .razor)
# ==============================================================================

class TestBuildRazorScriptsResponse:
    """Verifica la generazione dell'embed con tutti gli script, della view e del file."""

    def test_multiple_scripts_response(self):
        embed, view, script_file, voice_ans, code = build_razor_scripts_response("lockpicking")
        assert embed is not None
        assert isinstance(embed, discord.Embed)
        assert "Lockpicking" in embed.title
        assert "disponibili" in embed.title

        # Deve elencare gli script
        field_names = [f.name for f in embed.fields]
        assert any("Tutti gli Script" in fn for fn in field_names)
        assert any("Script Consigliato" in fn for fn in field_names)

        # Deve avere la View con il Select Menu
        assert view is not None
        assert isinstance(view, RazorScriptView)
        assert len(view.children) >= 1
        select = view.children[0]
        assert isinstance(select, RazorScriptSelect)
        assert len(select.options) > 5

        # Deve avere il file .razor pronto da scaricare
        assert script_file is not None
        assert script_file.filename.endswith(".razor")

        # Deve avere la risposta vocale di Franchino
        assert "Magia" in voice_ans or "Lockpicking" in voice_ans
        assert len(code) > 0

    def test_single_script_index_selection(self):
        # Richiede il secondo script di Magery
        embed, view, script_file, voice_ans, code = build_razor_scripts_response("magery", requested_idx=2)
        assert embed is not None
        scripts = get_scripts_for_skill("magery")
        second_script = scripts[1]
        assert second_script["title"] in embed.fields[1].name or second_script["title"] in embed.fields[1].value


# ==============================================================================
# 5. TEST GUIDE APPROFONDITE OUTLANDS (Non superficiali)
# ==============================================================================

class TestDeepOutlandsWikiGuides:
    """Verifica che le risposte della wiki siano approfondite e contengano più sezioni e riferimenti agli script."""

    def test_magery_deep_guide(self):
        res = asyncio.run(query_outlands_wiki("magery"))
        assert res is not None
        embed = res["embed"]
        assert isinstance(embed, discord.Embed)
        assert "Magery" in res["page_title"]
        # Verifica che il titolo sia di tipo Guida Approfondita
        assert "Guida Approfondita" in embed.title

        field_names = [f.name for f in embed.fields]
        # Deve contenere o Training o Meccaniche o Script Razor
        assert any("Script Razor" in fn for fn in field_names), "Dovrebbe contenere il riferimento agli script Razor"
        assert any("Sezione Ufficiale" in fn for fn in field_names)

        # La risposta vocale deve rimandare brevemente alla chat
        voice_ans = res["voice_answer"]
        assert "chat" in voice_ans.lower()
        assert "Magery" in voice_ans

    def test_lockpicking_deep_guide(self):
        res = asyncio.run(query_outlands_wiki("lockpicking"))
        assert res is not None
        embed = res["embed"]
        field_names = [f.name for f in embed.fields]
        assert any("Script Razor" in fn for fn in field_names)
        assert any("Training" in fn or "Allenamento" in fn or "Meccaniche" in fn or "Sezione" in fn for fn in field_names)
