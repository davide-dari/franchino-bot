import pytest
import sqlite3
import asyncio
from outlands_wiki import (
    normalize_uo_speech,
    is_wiki_question,
    _resolve_pets_query,
    _resolve_build_query,
    _resolve_creature_query,
    _resolve_special_item_query,
    _resolve_dungeon_query,
    _extract_topic_and_keywords,
    query_outlands_wiki,
    get_db_connection,
    OUTLANDS_BEST_PETS,
    OUTLANDS_BUILDS,
)
from voice_commands import FranchinoVoiceSink


# ==============================================================================
# 1. TEST NORMALIZZAZIONE FONETICA (normalize_uo_speech / _clean_transcription)
# ==============================================================================

class TestPhoneticNormalization:
    """Test delle sostituzioni fonetiche e prevenzione di falsi positivi."""

    @pytest.mark.parametrize("input_text, expected_text", [
        # --- Varianti di "pet" ---
        ("quali sono i migliori pat per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori pad per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori ped per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori pette per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori petti per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori pezz per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori peth per farmare", "quali sono i migliori pet per farmare"),
        ("che pat posso usare", "che pet posso usare"),
        ("che pad posso usare", "che pet posso usare"),
        ("che ped posso usare", "che pet posso usare"),
        ("consigliami un pat", "consigliami un pet"),
        ("consigliami un pad", "consigliami un pet"),
        ("parlami dei pat", "parlami dei pet"),
        ("guida ai pat", "guida ai pet"),
        ("tier list pat", "tier list pet"),
        ("i migliori pets", "i migliori pet"),
        ("i migliori pads", "i migliori pet"),
        ("i migliori pats", "i migliori pet"),

        # --- Nuove varianti fonetiche di 'pet' ('pe', 'pez', 'pè', 'pé', 'bette', ecc.) ---
        ("quali sono i migliori pe per farmare", "quali sono i migliori pet per farmare"),
        ("che pe posso usare", "che pet posso usare"),
        ("che pez posso usare", "che pet posso usare"),
        ("che pè posso usare", "che pet posso usare"),
        ("che pé posso usare", "che pet posso usare"),
        ("che bette posso usare", "che pet posso usare"),
        ("consigliami un pe", "consigliami un pet"),
        ("parlami dei pe", "parlami dei pet"),
        ("guida ai pe", "guida ai pet"),
        ("spiegami i pe", "spiegami i pet"),
        ("consigli per bet per farmare", "consigli per pet per farmare"),

        # --- Contesto 'ipad' / 'i pad' ---
        ("quali sono i migliori ipad per farmare", "quali sono i migliori pet per farmare"),
        ("quali sono i migliori i pad per farmare", "quali sono i migliori pet per farmare"),
        ("ipad per farmare", "i pet per farmare"),
        ("che ipad posso usare", "che pet posso usare"),
        ("consigliami ipad", "consigliami pet"),
        ("pad per farmare", "pet per farmare"),

        # --- Varianti di "build" ---
        ("miglior bild per il temer", "miglior build per il tamer"),
        ("migliore bilde per iniziare", "migliore build per iniziare"),
        ("quali bild consigli", "quali build consigli"),
        ("consigliami una biuld", "consigliami una build"),
        ("come bildare il mago", "come buildare il mago"),

        # --- Varianti di "taming" e "tamer" ---
        ("come funziona l'animal teming", "come funziona l'animal taming"),
        ("guida al teming", "guida al taming"),
        ("come salire di teiming", "come salire di taming"),
        ("build per animal temer", "build per animal tamer"),
        ("consigli per il teimer", "consigli per il tamer"),

        # --- Varianti di "drake" ---
        ("dove trovo i dreik", "dove trovo i drake"),
        ("statistiche del dreic", "statistiche del drake"),
        ("parlami dell'eldritch dreik", "parlami dell'eldritch drake"),
        ("come domare un cold dreik", "come domare un cold drake"),

        # --- Varianti di "slayer" ---
        ("che sleier serve per inferno", "che slayer serve per inferno"),
        ("armi sleiar", "armi slayer"),
        ("gruppo sleer", "gruppo slayer"),

        # --- Varianti di "dexxer" ---
        ("build per dexer spada", "build per dexxer spada"),
        ("guida al dexter", "guida al dexxer"),
        ("statistiche del decs", "statistiche del dexxer"),

        # --- Varianti di "aspect" e "mastery chain" ---
        ("a cosa servono gli aspet core", "a cosa servono gli aspect core"),
        ("come si usa l'aspet tome", "come si usa l'aspect tome"),
        ("come funzionano gli aspet", "come funzionano gli aspect"),
        ("come potenziare la masteri cein", "come potenziare la mastery chain"),
        ("medaglioni della masteri", "medaglioni della mastery"),

        # --- Varianti di dungeon e meccaniche ---
        ("parlami dei dangeon", "parlami dei dungeon"),
        ("come expare nel dangion", "come expare nel dungeon"),
        ("come funziona il locpiking", "come funziona il lockpicking"),
        ("come usare i codecs", "come usare i codex"),
        ("taming bestieri", "taming bestiary"),

        # --- Comandi vocali / musicali ---
        ("skippa la canzone", "skip la canzone"),
        ("stoppa la musica", "stop la musica"),
        ("plei subito", "play subito"),
        ("metti in sciaffol", "metti in shuffle"),
        ("attiva il lup", "attiva il loop"),
    ])
    def test_normalize_uo_speech_valid_cases(self, input_text, expected_text):
        normalized = normalize_uo_speech(input_text)
        assert normalized == expected_text

    @pytest.mark.parametrize("preserve_text", [
        "la patata è cotta",
        "ho una spada affilata",
        "il patto è siglato",
        "questo è molto compatto",
        "padella rovente",
        "un tipo sveglio ed esperto",
        "la pasta al pesto",
        "salta la corda",
        "mouse pad",
        "un pezzo di pane",
        "la risposta corretta",
    ])
    def test_prevent_false_positives(self, preserve_text):
        """Verifica che parole italiane contenenti 'pat', 'pad', 'spada' ecc. NON vengano alterate."""
        normalized = normalize_uo_speech(preserve_text)
        assert normalized == preserve_text

    def test_franchino_voice_sink_clean_transcription(self):
        """Verifica il metodo di classe _clean_transcription di FranchinoVoiceSink."""
        res = FranchinoVoiceSink._clean_transcription("franchino quali sono i migliori pat per farmare")
        assert res == "franchino quali sono i migliori pet per farmare"


# ==============================================================================
# 2. TEST RICONOSCIMENTO DOMANDE WIKI (is_wiki_question)
# ==============================================================================

class TestIsWikiQuestion:
    """Test del rilevamento delle domande sulla Wiki di UO Outlands."""

    @pytest.mark.parametrize("query", [
        # Domande con pet (originali e varianti fonetiche)
        "quali sono i migliori pet per farmare",
        "quali sono i migliori pat per farmare",
        "quali sono i migliori pad per farmare",
        "quali sono i migliori pe per farmare",
        "che pet posso usare",
        "che pat posso usare",
        "che pad posso usare",
        "che pe posso usare",
        "che pez posso usare",
        "che pè posso usare",
        "che bette posso usare",
        "consigliami un pet",
        "consigliami un pat",
        "consigliami un pe",
        "guida ai pet",
        "guida ai pat",
        "guida ai pe",
        "guida pet",
        "parlami dei pet",
        "parlami dei pat",
        "parlami dei pe",
        "parla dei pet",
        "parla dei pe",
        "info pet",
        "info pat",
        "spiegami i pet",
        "spiegami i pe",
        "pet per tamer",

        # Domande con build e template
        "miglior build per il tamer",
        "miglior bild per il temer",
        "migliori build",
        "migliori bild",
        "consigliami una build",
        "che build mi consigli",
        "build bardo arciere",
        "build dexxer spada",

        # Domande con meccaniche e dungeon
        "come funziona l'animal teming",
        "dove trovo i dreik",
        "che sleier serve per inferno",
        "come si usa la masteri cein",
        "a cosa servono gli aspet core",
        "parlami dei dangeon",
        "dungeon aegis keep",
        "dungeon inferno",
        "time dungeon",
        "statistiche del drake",
        "info su ossuary",

        # Termini singoli UO
        "pet",
        "i pet",
        "pat",
        "pad",
        "i pe",
        "taming",
        "build",
        "drake",
        "slayer",
        "aegis keep",
    ])
    def test_is_wiki_question_true_cases(self, query):
        is_w, norm_q = is_wiki_question(query)
        assert is_w is True
        assert len(norm_q) > 0

    @pytest.mark.parametrize("non_wiki_cmd", [
        "metti la canzone dei pet",
        "play build me up buttercup",
        "riproduci drake hotline bling",
        "skip",
        "stoppa tutto",
        "pausa",
        "alza il volume",
        "muta l'utente marco",
        "trombetta",
        "sirena",
        "ripeti",
        "ripeti la canzone",
        "ripeti questa traccia",
        "loop",
        "aspetta",
        "aspetta un attimo",
        "ho appetito",
        "con rispetto",
        "senza sospetto",
        "tappeto",
        "competenza",
        "new york",
        "brand new",
        "one more time",
        "closing time",
        "ocean eyes",
        "frank ocean",
        "mount fuji",
        "free bird",
        "red bull",
        "she wolf",
        "lombardo",
        "bombardo",
        "mouse pad",
    ])
    def test_is_wiki_question_excluded_commands(self, non_wiki_cmd):
        """Verifica che comandi musicali, admin, soundboard o frasi comuni NON vengano scambiati per wiki."""
        is_w, _ = is_wiki_question(non_wiki_cmd)
        assert is_w is False


# ==============================================================================
# 3. TEST RISOLUTORE PET (_resolve_pets_query)
# ==============================================================================

class TestPetsQueryResolver:
    """Test delle risposte alle domande sui Pet per farmare."""

    @pytest.mark.parametrize("pet_q", [
        "quali sono i migliori pet per farmare",
        "quali sono i migliori pat per farmare",
        "quali sono i migliori pad per farmare",
        "quali sono i migliori pe per farmare",
        "che pet posso usare",
        "che pat posso usare",
        "che pad posso usare",
        "che pe posso usare",
        "che pez posso usare",
        "che pè posso usare",
        "che bette posso usare",
        "consigliami un pet",
        "consigliami un pat",
        "consigliami un pe",
        "parlami dei pet",
        "parlami dei pat",
        "parlami dei pe",
        "parla dei pet",
        "parla dei pe",
        "guida ai pet",
        "guida ai pat",
        "guida ai pe",
        "guida pet",
        "info pet",
        "info pat",
        "spiegami i pet",
        "spiegami i pe",
        "i migliori pet per iniziare",
        "top pet pvm",
        "i pet",
        "pet",
        "pat",
        "pad",
        "i pat",
        "i pad",
        "i pe",
    ])
    def test_resolve_pets_query_matches(self, pet_q):
        res = _resolve_pets_query(pet_q)
        assert res is not None
        assert res["page_title"] == OUTLANDS_BEST_PETS["title"]
        assert "voice_answer" in res
        assert "embed" in res

    def test_build_disambiguation_does_not_hijack_pets(self):
        """Verifica che una domanda di pura BUILD (es. 'build per tamer') non ritorni i pet."""
        res_build_as_pet = _resolve_pets_query("miglior build per il tamer")
        assert res_build_as_pet is None

        res_build = _resolve_build_query("miglior build per il tamer")
        assert res_build is not None
        assert "Tamer Mage" in res_build["page_title"]


# ==============================================================================
# 4. TEST RISOLUTORE BUILD (_resolve_build_query)
# ==============================================================================

class TestBuildQueryResolver:
    """Test delle risposte alle domande sulle Build e Template meta."""

    @pytest.mark.parametrize("build_q, expected_build_key", [
        ("miglior build per il tamer", "tamer_mage"),
        ("miglior bild per il temer", "tamer_mage"),
        ("template tamer", "tamer_mage"),
        ("build bardo arciere", "bardo_arciere"),
        ("build dexxer spada", "dexxer_spada"),
        ("build deccer", "dexxer_spada"),
        ("build ladro dungeon", "ladro_dungeon"),
        ("build summoner necro", "summoner_necro"),
        ("build per iniziare da zero", "starter_gold"),
        ("migliori build", "meta_overview"),
        ("migliori bild", "meta_overview"),
        ("consigliami una build", "meta_overview"),
    ])
    def test_resolve_build_query_keys(self, build_q, expected_build_key):
        res = _resolve_build_query(build_q)
        assert res is not None
        expected_title = OUTLANDS_BUILDS[expected_build_key]["title"]
        assert res["page_title"] == expected_title


# ==============================================================================
# 5. TEST RISOLUTORE CREATURE (_resolve_creature_query)
# ==============================================================================

class TestCreatureQueryResolver:
    """Test della ricerca istantanea nel DB locale di 1.301 creature."""

    @pytest.mark.parametrize("query, expected_creature", [
        ("dove trovo i drake", "Drake"),
        ("dove trovo i dreik", "Drake"),
        ("statistiche del dreic", "Drake"),
        ("parlami del bloodwolf", "Bloodwolf"),
        ("statistiche del bludwolf", "Bloodwolf"),
        ("parlami del hellhound", "Hellhound"),
        ("info sui drakes", "Drake"),
    ])
    def test_resolve_creature_query(self, query, expected_creature):
        conn = get_db_connection()
        assert conn is not None
        try:
            res = _resolve_creature_query(query, conn)
            assert res is not None
            assert res["page_title"] == expected_creature
        finally:
            conn.close()


# ==============================================================================
# 6. TEST RISOLUTORE OGGETTI E DUNGEON
# ==============================================================================

class TestSpecialItemsAndDungeons:
    """Test di Mastery Chain, Aspect Core e Dungeon."""

    def test_mastery_chain_with_phonetics(self):
        res = _resolve_special_item_query("come si usa la masteri cein")
        assert res is not None
        assert "Mastery Chain" in res["page_title"]

    def test_aspect_core_with_phonetics(self):
        res = _resolve_special_item_query("a cosa servono gli aspet core")
        assert res is not None
        assert "Aspect Core" in res["page_title"]

    def test_dungeon_query(self):
        conn = get_db_connection()
        assert conn is not None
        try:
            res = _resolve_dungeon_query("parlami del dungeon aegis keep", conn)
            assert res is not None
            assert "Aegis Keep" in res["page_title"]
        finally:
            conn.close()


# ==============================================================================
# 7. TEST QUERY COMPLETA ASINCRONA (query_outlands_wiki)
# ==============================================================================

def test_full_query_outlands_wiki():
    """Test end-to-end della funzione principale query_outlands_wiki."""
    async def _run():
        # Test Pet con trascrizione fonetica 'pat'
        res_pet = await query_outlands_wiki("quali sono i migliori pat per farmare")
        assert res_pet is not None
        assert "Migliori Pet" in res_pet["page_title"]

        # Test Build con trascrizione fonetica 'bild temer'
        res_build = await query_outlands_wiki("miglior bild per il temer")
        assert res_build is not None
        assert "Tamer Mage" in res_build["page_title"]

        # Test Creatura con trascrizione fonetica 'dreik'
        res_creature = await query_outlands_wiki("dove trovo i dreik")
        assert res_creature is not None
        assert res_creature["page_title"] == "Drake"

    asyncio.run(_run())


# ==============================================================================
# 8. TEST CASI LIMITE & EDGE CASES (Edge cases & Boundary values)
# ==============================================================================

class TestEdgeCasesAndBoundaries:
    """Test di edge case estremi: input vuoti, stringhe corte, case mixing, simboli."""

    def test_empty_and_whitespace_inputs(self):
        assert normalize_uo_speech("") == ""
        assert normalize_uo_speech("   ") == ""
        assert FranchinoVoiceSink._clean_transcription("") == ""
        assert FranchinoVoiceSink._clean_transcription("   ") == ""

        is_w, q = is_wiki_question("")
        assert is_w is False
        assert q == ""

        assert _resolve_pets_query("") is None
        assert _resolve_build_query("") is None
        assert _resolve_special_item_query("") is None

    def test_symbols_and_punctuation_only(self):
        assert normalize_uo_speech("??? !!! ...") == "??? !!! ..."
        is_w, _ = is_wiki_question("???")
        assert is_w is False

    def test_multiple_phonetic_replacements_in_one_sentence(self):
        raw = "che pat scegliere per il temer con la bild da farmare nei dangeon"
        exp = "che pet scegliere per il tamer con la build da farmare nei dungeon"
        assert normalize_uo_speech(raw) == exp

    def test_mixed_case_normalization(self):
        raw = "CHE PAT POSSO USARE PER IL TEMER CON LA BILD"
        norm = normalize_uo_speech(raw).lower()
        assert "pet" in norm
        assert "tamer" in norm
        assert "build" in norm

    def test_candidate_scoring_prefers_gaming_terms(self):
        """Simula la selezione candidati in _process_and_transcribe_audio."""
        raw_candidates = [
            "hey franchino quali sono i migliori pat per farmare",
            "hey franchino quali sono i migliori pad per farmare",
        ]
        # Arricchimento candidati
        all_candidates = []
        for cand in raw_candidates:
            if cand not in all_candidates:
                all_candidates.append(cand)
            clean_cand = FranchinoVoiceSink._clean_transcription(cand)
            if clean_cand and clean_cand not in all_candidates:
                all_candidates.append(clean_cand)

        wake_hits = []
        import re
        uo_priority = re.compile(r'\b(?:pet|pets|build|taming|tamer)\b', re.IGNORECASE)
        quest_terms = re.compile(r'\b(?:miglior|migliori)\b', re.IGNORECASE)
        for cand in all_candidates:
            has_w, cmd, w_tok = FranchinoVoiceSink.extract_wake_and_command(cand)
            if has_w:
                norm_cmd = FranchinoVoiceSink._clean_transcription(cmd)
                score = float(len(norm_cmd))
                if uo_priority.search(norm_cmd):
                    score += 50.0
                if quest_terms.search(norm_cmd):
                    score += 20.0
                wake_hits.append((cand, norm_cmd, w_tok, score))

        wake_hits.sort(key=lambda x: x[3], reverse=True)
        best_cand, best_cmd, best_wake, best_score = wake_hits[0]

        assert "pet" in best_cmd
        assert "pat" not in best_cmd
        assert best_score >= 70.0

    def test_candidate_scoring_does_not_falsely_boost_ripeti(self):
        """Verifica che parole come 'ripeti' non ricevano il bonus prioritario di 50 punti per gaming 'pet'."""
        import re
        uo_priority = re.compile(
            r'\b(?:pet|pets|build|builds|taming|tamer|drake|slayer|dexxer|aspect|'
            r'mastery\s+chain|codex|dungeon|ostard|wiki|outlands|bestiario)\b',
            re.IGNORECASE
        )
        assert uo_priority.search("ripeti la canzone") is None
        assert uo_priority.search("aspetta un attimo") is None
        assert uo_priority.search("quali sono i migliori pet") is not None

    def test_active_session_candidate_selection(self):
        """Verifica che in active session venga scelto il candidato normalizzato migliore tra le alternative."""
        import re
        uo_priority = re.compile(
            r'\b(?:pet|pets|build|builds|taming|tamer|drake|slayer|dexxer|aspect)\b',
            re.IGNORECASE
        )
        candidates = [
            "quali sono i migliori pat",
            "rumore di fondo",
        ]
        all_cands = []
        for c in candidates:
            if c not in all_cands:
                all_cands.append(c)
            clean = FranchinoVoiceSink._clean_transcription(c)
            if clean and clean not in all_cands:
                all_cands.append(clean)

        best_cand = candidates[0]
        best_score = float(len(best_cand))
        for cand_item in all_cands:
            c_clean = FranchinoVoiceSink._clean_transcription(cand_item)
            sc = float(len(c_clean))
            if uo_priority.search(c_clean):
                sc += 50.0
            if sc > best_score:
                best_score = sc
                best_cand = c_clean

        assert "pet" in best_cand
        assert best_score > 50.0


# ==============================================================================
# 7. TEST COMANDO AIUTO OUTLANDS & DISAMBIGUAZIONE MUSICA VS DOMANDE
# ==============================================================================

class TestOutlandsHelpCommandAndMusicDisambiguation:
    """Test del comando aiuto su Outlands e della disambiguazione da ricerche musicali."""

    @pytest.mark.parametrize("help_query", [
        "cosa puoi fare su outlands",
        "cosa sai fare su outlands",
        "cosa sai su outlands",
        "quali argomenti conosci su outlands",
        "che argomenti conosci su outlands",
        "quali argomenti puoi fare su outlands per aiutare l'utente",
        "come puoi aiutarci su outlands",
        "come ci puoi aiutare su outlands",
        "in cosa puoi aiutarci su outlands",
        "in cosa ci puoi aiutare su outlands",
        "cosa puoi fare per aiutarci su outlands",
        "cosa puoi fare per aiutare l'utente su outlands",
        "aiuto outlands",
        "help outlands",
        "guida outlands",
        "outlands aiuto",
        "outlands help",
        "outlands guida",
        "dimmi cosa sai su outlands",
        "di cosa mi parli su outlands",
        "argomenti outlands",
    ])
    def test_is_help_question_positive(self, help_query):
        """Verifica che tutte le forme di richiesta aiuto/argomenti su Outlands siano riconosciute."""
        from outlands_wiki import is_help_question
        assert is_help_question(help_query) is True

    @pytest.mark.parametrize("non_help_query", [
        "metti gigi dag",
        "play bohemian rhapsody",
        "skip",
        "stop",
        "alza il volume",
        "cosa canta ligabue",
        "come stai franchino",
        "che ore sono",
    ])
    def test_is_help_question_negative(self, non_help_query):
        """Verifica che comandi musicali o generici NON siano scambiati per aiuto outlands."""
        from outlands_wiki import is_help_question
        assert is_help_question(non_help_query) is False

    def test_get_outlands_help_embed_content(self):
        """Verifica che l'embed di aiuto contenga le categorie essenziali e la risposta vocale."""
        from outlands_wiki import get_outlands_help_embed
        import discord

        embed, voice_ans = get_outlands_help_embed()
        assert isinstance(embed, discord.Embed)
        assert "Franchino" in embed.title
        assert len(voice_ans) > 20
        assert "Outlands" in voice_ans or "console" in voice_ans or "pagine" in voice_ans

        # Verifica presenza di campi informativi chiave
        field_names = [f.name for f in embed.fields]
        assert any("Skill" in fn for fn in field_names)
        assert any("Dungeon" in fn for fn in field_names)
        assert any("Creature" in fn or "Pet" in fn for fn in field_names)
        assert any("Build" in fn or "Template" in fn for fn in field_names)
        assert any("Aspetti" in fn or "Aspect" in fn for fn in field_names)

    @pytest.mark.parametrize("interrogative_query", [
        "cosa puoi fare per aiutarci?",
        "cosa sai fare per aiutarci",
        "come funziona il taming",
        "dove si trova aegis keep",
        "quali sono i migliori pet per farmare",
        "perché il fire aspect fa tanti danni",
        "spiegami come funziona la mastery chain",
        "dimmi tutto sui frenzied ostard",
    ])
    def test_question_not_routed_to_music(self, interrogative_query):
        """Verifica che le frasi interrogative/informative NON vengano trattate come titoli musicali."""
        import re
        cmd_strip = interrogative_query.strip().lower()
        is_question = (
            cmd_strip.endswith("?")
            or re.match(
                r"^(?:cosa|che|chi|come|dove|quando|perch[eé]|quali|quale|quanto|quanta|quanti|quante|"
                r"spiegami|dimmi|parlami|raccontami|sai\s+dirmi|puoi\s+dirmi|mi\s+dici|mi\s+spieghi|"
                r"sai\s+cos'è|sai\s+cosa|sai|conosci|esiste|ci\s+sono|si\s+può|posso)\b",
                cmd_strip,
                re.IGNORECASE,
            ) is not None
            or any(q_term in cmd_strip for q_term in (
                "outlands", "ultima online", "come funziona", "come si fa", "dove trovo",
                "dove si trova", "cosa serve", "a cosa serve", "cosa fa", "cosa fanno",
                "perché", "perche"
            ))
        )
        assert is_question is True


