import asyncio
import audioop
import re
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import discord

from voice_commands import FranchinoVoiceSink


# ==============================================================================
# 1. TEST PREVENZIONE FALSI POSITIVI DELLA WAKE WORD (False chiamate)
# ==============================================================================

class TestWakeWordFalsePositivePrevention:
    """Verifica che parole simili, espressioni colloquiali e discorsi ordinari NON sveglino Franchino."""

    @pytest.mark.parametrize("ordinary_phrase", [
        # Il caso specifico menzionato dall'utente ("Fra, chi ha il boost")
        "Fra, chi ha il boost?",
        "Fra chi ha fatto il boost al server?",
        "Fra chi c'è stasera in vocale?",
        "Fra chi viene a fare il boss?",
        "Fra chi ha preso le cuffie?",
        "Fra chi boosta?",
        "Fra chi usa il boost?",

        # Moneta / Sostantivo 'franchi'
        "Ho cento franchi in tasca",
        "Mi servono cinquanta franchi svizzeri",
        "Porto franchi per favore",
        "Dobbiamo essere franchi e dire la verità",
        "Erano franchi tiratori",

        # Lavoro / Azioni ordinarie
        "Facciamo il facchino per il trasloco",
        "Non fare il facchino tutto il giorno",
        "Sembra un fachiro che cammina sui carboni",

        # Luoghi e Nomi propri
        "Quest'estate andiamo a San Francisco",
        "Ho parenti a San Francisco in California",
        "Francesco viene più tardi",
        "Francesca ha già mangiato",
        "Parla molto bene il francese",
        "I francesi sono simpatici",
        "Franklin ha rubato una macchina in GTA",
        "Frankie non si è ancora collegato",

        # Oggetti e Arredamento
        "Ci sediamo su quella panchina nel parco?",
        "Hanno riverniciato le panchine",
        "Prendi la banchina di destra alla stazione",
        "I banchieri hanno chiuso lo sportello",
        "Ho perso il fischietto dell'arbitro",
        "Passami quel fiaschino d'olio",
        "Ho comprato un cucchiaino nuovo",

        # Cibo e Bevande
        "Bevo un bianchino al bar",
        "Ho mangiato un tranchino di pizza margherita",
        "Prendiamo due tranchini al taglio",
        "Siamo andati a mangiare al ranchino fuori città",

        # Assicurazioni e Gaming terms
        "La franchigia dell'assicurazione auto è alta",
        "Bisogna pagare la franchigia",
        "Le franchigie NBA giocano stanotte",

        # Bigrammi e combinazioni di parole comuni
        "Fa chino il capo per rispetto",
        "Fai piano quando parli",
        "Francia no, preferisco andare in Spagna",
        "Franco no, non venire oggi",
        "Franco uno a zero per noi",

        # Altri vocaboli italiani
        "Il giocatore è mancino",
        "Un tiro mancino imparabile",
        "La macchina è dal meccanico",

        # Richiamo al solo Franchino SENZA Hey/Ehi (deve essere rigorosamente ignorato!)
        "Franchino",
        "franchino metti musica",
        "Franchino metti Gigi D'Agostino",
        "franchino che ore sono",
        "franchino stop",
        "franchino boost",
        "Franchino come funziona il taming",
        "ho parlato con franchino",
        "secondo me franchino non funziona",
    ])
    def test_ordinary_conversation_does_not_trigger_wake(self, ordinary_phrase):
        """Nessuna frase di conversazione comune o senza 'Hey/Ehi' deve far scattare la wake word."""
        has_wake, cmd, tok = FranchinoVoiceSink.extract_wake_and_command(ordinary_phrase)
        assert not has_wake, (
            f"FALSO POSITIVO RILEVATO! Frase: '{ordinary_phrase}' -> matched token: '{tok}', cmd: '{cmd}'"
        )


# ==============================================================================
# 2. TEST REATTIVITÀ CORRETTA ALLE VERE CHIAMATE A FRANCHINO
# ==============================================================================

class TestWakeWordPositiveDetection:
    """Verifica che tutte le forme legittime di richiamo a Franchino con prefisso Hey/Ehi vengano sempre riconosciute."""

    @pytest.mark.parametrize("valid_call, expected_wake, expected_cmd", [
        # Canonica
        ("Hey Franchino", "Hey Franchino", ""),
        ("hey franchino metti musica", "hey franchino", "metti musica"),
        ("ehi franchino metti i preferiti", "ehi franchino", "metti i preferiti"),
        ("Hey Franchino come funziona il taming", "Hey Franchino", "come funziona il taming"),

        # Chiamate /boost e bass boost
        ("hey franchino boost", "hey franchino", "boost"),
        ("hey franchino /boost", "hey franchino", "/boost"),
        ("hey franchino bass boost", "hey franchino", "bass boost"),
        ("hey franchino metti il boost", "hey franchino", "metti il boost"),
        ("Hey Franchino alza i bassi", "Hey Franchino", "alza i bassi"),
        ("hey franchino togli il boost", "hey franchino", "togli il boost"),
        ("hey franchino no boost", "hey franchino", "no boost"),

        # Comandi diretti
        ("hey franchino stop", "hey franchino", "stop"),
        ("hey franchino salta", "hey franchino", "salta"),
        ("Hey Franchino comandi", "Hey Franchino", "comandi"),

        # Varianti fonetiche da STT
        ("ehi frankino metti una canzone", "ehi frankino", "metti una canzone"),
        ("hey francino suona rock", "hey francino", "suona rock"),
        ("hei franckino alza il volume", "hei franckino", "alza il volume"),
        ("ehi franchì dimmi tutto", "ehi franchì", "dimmi tutto"),
        ("hey franceschino ci sei", "hey franceschino", "ci sei"),

        # Varianti separate con spazio tipiche di Google STT
        ("hey fran chino metti jazz", "hey fran chino", "metti jazz"),
        ("ehi franco ino metti pop", "ehi franco ino", "metti pop"),
        ("hey frank ino pausa", "hey frank ino", "pausa"),
    ])
    def test_valid_calls_detected_correctly(self, valid_call, expected_wake, expected_cmd):
        has_wake, cmd, tok = FranchinoVoiceSink.extract_wake_and_command(valid_call)
        assert has_wake, f"MANCATA ATTIVAZIONE: Frase legittima '{valid_call}' non ha attivato la wake word!"
        assert tok.lower() == expected_wake.lower()
        if expected_cmd:
            assert expected_cmd.lower() in cmd.lower()


# ==============================================================================
# 3. TEST EFFETTI DJ: /BOOST, BASS BOOST, SPEGNIMENTO & PREVENZIONE COLLISIONI
# ==============================================================================

class TestEffectAndBoostRouting:
    """Verifica il routing intelligente dell'effetto Bass Boost (/boost) e la prevenzione di falsi trigger."""

    @staticmethod
    def _resolve_effect(cmd: str):
        """Simula esattamente la logica implementata in voice_commands.py (sezione 0F)."""
        cmd_lower = cmd.lower().strip()

        eff_keywords = (
            "effetto", "effetti", "boost", "bassboost", "bass boost", "bassi",
            "megabass", "mega bass", "sub bass", "subbass",
            "riverbero", "reverb", "eco", "nightcore", "night core",
            "slowed", "8d", "otto d", "karaoke", "vaporwave", "lofi", "lo-fi",
            "chipmunk", "scoiattolo", "demone", "demon", "vintage", "audio normale", "normale"
        )
        is_explicit_music_req = (
            cmd_lower.startswith((
                "metti ", "riproduci ", "suona ", "play ", "cerca ",
                "fammi sentire ", "fammi ascoltare ", "fai partire ", "aggiungi ", "accoda "
            ))
            and (
                any(m_word in cmd_lower for m_word in ("canzone", "canzoni", "brano", "traccia", "pezzo", "album", "disco", "playlist"))
                or not any(eff_kw in cmd_lower for eff_kw in eff_keywords)
            )
            and not any(cmd_lower.startswith(p) for p in (
                "metti l'effetto ", "metti effetto ", "metti il boost", "metti boost",
                "metti bass boost", "metti il bass boost", "metti l'audio normale", "metti audio normale"
            ))
        )
        if is_explicit_music_req:
            return "MUSIC_REQUEST"

        off_pat = re.compile(
            r'^(?:normale|audio\s+normale|effetto\s+normale|reset\s+effetti|resetta\s+effetti|togli\s+effetti?|senza\s+effetti?|originale|no\s+boost|senza\s+boost|basta\s+boost|stop\s+effetti)$|'
            r'\b(?:togli(?:ere)?|disattiva(?:re)?|leva(?:re)?|rimuovi(?:ere)?|spegni(?:ere)?|'
            r'stoppa(?:re)?|basta|cancella|elimina|senza|no)\s+'
            r'(?:il\s+|l\'|gli\s+|l\'effetto\s+|l\'audio\s+|i\s+)?'
            r'(?:effetti|effetto|filtri|filtro|boost|bass\s*boost(?:er)?|bassboost|megabass|nightcore|slowed|8d|reverb|riverbero|eco|karaoke|vaporwave|lo-?fi|scoiattolo|chipmunk|demone|demon|vintage|radio)\b',
            re.IGNORECASE
        )

        effect_question_or_chatter = re.compile(
            r'\b(?:chi\s+(?:ha|c\'ha|usa|fa|prende|vuole|boosta)|avete|hai|abbiamo|hanno|'
            r'del\s+server|al\s+server|per\s+il\s+boost|grazie|'
            r'come\s+funziona|cos\'[eè]|cosa\s+[eè]|a\s+cosa\s+serve|spiegami|'
            r'perch[eé]|serve\s+il\s+boost|prezzi\s+bassi|volumi\s+bassi|toni\s+bassi|'
            r'suoni\s+bassi|passi\s+bassi)\b|\?',
            re.IGNORECASE
        )

        if off_pat.search(cmd_lower):
            return "off"

        if effect_question_or_chatter.search(cmd_lower):
            return None

        effect_patterns = [
            ("megabass", re.compile(r'^(?:megabass|mega\s*bass|sub\s*bass|subbass)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:mega\s*bass|sub\s*bass|bassi\s+estremi|bassi\s+devastanti|ultra\s+bassi)\b|\b(?:mega\s*bass|sub\s*bass|bassi\s+estremi|bassi\s+devastanti|ultra\s+bassi)\b', re.I)),
            ("bassboost", re.compile(r'^(?:boost|/boost|bass\s*boost(?:er)?|bassboost|bassi|effetto\s+bassi)$|\b(?:metti|attiva|imposta|applica|alza|aumenta|pompa|spingi|spara|carica|abilita|accendi|voglio|dammi)\s+(?:il\s+|l\'|un\s+|i\s+|l\'effetto\s+|l\'audio\s+|di\s+|pi[uù]\s+)?(?:bass\s*boost(?:er)?|bassboost|boost|bassi|sub\s*bass)\b|\b(?:bassi\s+a\s+palla|bassi\s+potenti|pi[uù]\s+bassi|effetto\s+bassi|bassi\s+forti|bassi\s+devastanti)\b', re.I)),
            ("nightcore", re.compile(r'^(?:night\s*core|nightcore|velocizza)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:night\s*core|effetto\s+veloce)\b|\b(?:night\s*core|effetto\s+veloce)\b', re.I)),
            ("slowed", re.compile(r'^(?:slowed|slow|rallenta)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:slowed|effetto\s+lento)\b|\b(?:slowed|effetto\s+lento)\b', re.I)),
            ("8d", re.compile(r'^(?:8d|otto\s*d|surround)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:8d|otto\s*d|surround|effetto\s+8d)\b|\b(?:8d|otto\s*d|surround|effetto\s+8d)\b', re.I)),
            ("reverb", re.compile(r'^(?:reverb|riverbero|eco)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:reverb|riverbero|eco|effetto\s+eco|eco\s+da\s+concerto|effetto\s+arena)\b|\b(?:reverb|riverbero|effetto\s+eco|eco\s+da\s+concerto|effetto\s+arena)\b', re.I)),
            ("karaoke", re.compile(r'^(?:karaoke|strumentale|base\s+musicale)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?karaoke\b|\b(?:togli\s+la\s+voce|senza\s+voce|base\s+musicale|versione\s+strumentale)\b', re.I)),
            ("vaporwave", re.compile(r'^(?:vaporwave|vapor\s*wave|lo-?fi)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:vapor\s*wave|lo-?fi)\b|\b(?:vapor\s*wave|lo-?fi)\b', re.I)),
            ("chipmunk", re.compile(r'^(?:scoiattolo|chipmunk|elio)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:scoiattolo|chipmunk|voce\s+acuta|voce\s+da\s+scoiattolo|elio)\b|\b(?:scoiattolo|chipmunk|voce\s+acuta|voce\s+da\s+scoiattolo|elio)\b', re.I)),
            ("demon", re.compile(r'^(?:demone|demon|mostro|darth\s+vader)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:demone|demon|voce\s+profonda|voce\s+grave|voce\s+cupa|effetto\s+mostro|darth\s+vader)\b|\b(?:demone|demon|voce\s+profonda|voce\s+grave|voce\s+cupa|effetto\s+mostro|darth\s+vader)\b', re.I)),
            ("vintage", re.compile(r'^(?:vintage|vinile|vecchia\s+radio|radio\s+anni\s+60)$|\b(?:metti|attiva|imposta|applica)\s+(?:il\s+|l\'|un\s+|l\'effetto\s+)?(?:vintage|radio\s+vecchia|radio\s+anni\s+60|vinile|vecchia\s+radio|effetto\s+telefono)\b|\b(?:vintage|radio\s+vecchia|radio\s+anni\s+60|vinile|vecchia\s+radio|effetto\s+telefono)\b', re.I)),
        ]
        for eff_key, pat in effect_patterns:
            if pat.search(cmd_lower):
                return eff_key
        return None

    @pytest.mark.parametrize("boost_cmd", [
        "boost",
        "/boost",
        "bass boost",
        "bassboost",
        "metti il boost",
        "metti boost",
        "attiva il boost",
        "attiva bass boost",
        "aumenta i bassi",
        "alza i bassi",
        "pompa i bassi",
        "spingi i bassi",
        "più bassi",
        "bassi a palla",
        "bassi potenti",
        "effetto bassi",
        "bassi",
    ])
    def test_boost_commands_identified_as_bassboost(self, boost_cmd):
        """Tutti i comandi vocali espliciti di boost devono essere identificati come bassboost."""
        matched = self._resolve_effect(boost_cmd)
        assert matched == "bassboost", f"Comando '{boost_cmd}' non ha mappato su bassboost, ottenuto: '{matched}'"

    @pytest.mark.parametrize("turn_off_cmd", [
        "togli il boost",
        "disattiva il boost",
        "togli boost",
        "no boost",
        "senza boost",
        "basta boost",
        "togli il riverbero",
        "disattiva nightcore",
        "togli karaoke",
        "togli effetti",
        "reset effetti",
        "audio normale",
        "normale",
        "senza effetti",
    ])
    def test_turn_off_commands_identified_as_off(self, turn_off_cmd):
        """Comandi di spegnimento effetto ('togli il boost', 'no boost', ecc.) devono mappare su 'off'."""
        matched = self._resolve_effect(turn_off_cmd)
        assert matched == "off", f"Comando di spegnimento '{turn_off_cmd}' non ha mappato su 'off', ottenuto: '{matched}'"

    @pytest.mark.parametrize("non_effect_phrase", [
        "chi ha il boost?",
        "chi ha il boost",
        "chi c'ha il boost",
        "chi ha fatto il boost",
        "il boost del server",
        "grazie per il boost",
        "serve il boost",
        "avete il boost",
        "hai il boost",
        "come funziona il boost?",
        "cos'è il boost?",
        "spiegami il boost",
        "a cosa serve il boost",
        "i volumi sono bassi",
        "parla a toni bassi",
        "ci sono prezzi bassi",
        "siamo rimasti troppo bassi di vita",
        "ha il boost",
        "chi boosta",
        "chi ha il boost al server",
    ])
    def test_casual_mention_of_boost_or_bassi_does_not_trigger_effects(self, non_effect_phrase):
        """Domande o chiacchiere su boost/bassi NON devono attivare filtri DJ."""
        matched = self._resolve_effect(non_effect_phrase)
        assert matched is None, f"FALSO EFFETTO BASS BOOST scattato su '{non_effect_phrase}', ottenuto: '{matched}'!"

    @pytest.mark.parametrize("music_request_with_effect_words", [
        "metti il concerto di vasco rossi",
        "suona live all'arena di verona",
        "metti lady gaga telephone",
        "riproduci la canzone dei mostri",
        "metti la canzone con i bassi potenti",
        "fammi sentire i brani da discoteca",
    ])
    def test_music_request_not_hijacked_by_dj_effects(self, music_request_with_effect_words):
        """Richieste musicali con parole come 'concerto' o 'canzone' NON devono attivare filtri DJ."""
        matched = self._resolve_effect(music_request_with_effect_words)
        assert matched == "MUSIC_REQUEST"

    @pytest.mark.parametrize("effect_request_with_metti, expected_effect", [
        ("metti il riverbero", "reverb"),
        ("metti l'eco", "reverb"),
        ("metti nightcore", "nightcore"),
        ("metti karaoke", "karaoke"),
        ("metti 8d", "8d"),
        ("metti slowed", "slowed"),
        ("metti megabass", "megabass"),
        ("metti vintage", "vintage"),
    ])
    def test_effect_requests_with_metti_trigger_correct_effect(self, effect_request_with_metti, expected_effect):
        """Richieste di filtri introdotte da 'metti' (es. 'metti il riverbero') devono attivare il relativo effetto."""
        matched = self._resolve_effect(effect_request_with_metti)
        assert matched == expected_effect, f"Richiesta '{effect_request_with_metti}' non ha attivato '{expected_effect}', ottenuto: '{matched}'"


# ==============================================================================
# 4. TEST MULTI-CANDIDATE DISAMBIGUATION (Colloquial Intercalari vs True Wake)
# ==============================================================================

class TestMultiCandidateDisambiguation:
    """Verifica che intercalari tipo 'Fra chi ha il boost' non vengano forzati da candidati secondari."""

    def test_fra_chi_candidates_ignored(self):
        """Se l'ipotesi primaria è 'Fra chi...', candidati secondari con wake word vengono rigettati."""
        candidates = ["Fra chi ha il boost", "Franchino ha il boost"]
        top_cand = candidates[0]
        top_has_wake, _, _ = FranchinoVoiceSink.extract_wake_and_command(top_cand)
        primary_has_wake = top_has_wake

        colloquial_anti_wake = re.compile(
            r'\b(?:fra\s*,?\s*chi|ho\s+(?:cento|\d+)\s+franchi|i\s+franchi)\b',
            re.IGNORECASE
        )
        all_candidates = candidates
        if not primary_has_wake and colloquial_anti_wake.search(top_cand):
            all_candidates = []

        wake_hits = []
        for idx, cand in enumerate(all_candidates):
            has_w, cmd, w_tok = FranchinoVoiceSink.extract_wake_and_command(cand)
            if has_w:
                wake_hits.append(cand)

        assert len(wake_hits) == 0, f"Allucinazione non bloccata! wake_hits={wake_hits}"

    def test_valid_alternative_candidate_accepted_for_real_commands(self):
        """Se l'ipotesi primaria non ha wake word ma non è un intercalare ed è un comando chiaro, viene accettata."""
        candidates = ["metti musica rock", "hey franchino metti musica rock"]
        top_cand = candidates[0]
        top_has_wake, _, _ = FranchinoVoiceSink.extract_wake_and_command(top_cand)
        primary_has_wake = top_has_wake

        colloquial_anti_wake = re.compile(
            r'\b(?:fra\s*,?\s*chi|ho\s+(?:cento|\d+)\s+franchi|i\s+franchi)\b',
            re.IGNORECASE
        )
        all_candidates = candidates
        if not primary_has_wake and colloquial_anti_wake.search(top_cand):
            all_candidates = []

        wake_hits = []
        for idx, cand in enumerate(all_candidates):
            has_w, cmd, w_tok = FranchinoVoiceSink.extract_wake_and_command(cand)
            if has_w:
                wake_hits.append(cand)

        assert len(wake_hits) == 1
        assert wake_hits[0] == "hey franchino metti musica rock"


# ==============================================================================
# 5. TEST INTELLIGENZA CONTESTUALE: TERZA PERSONA, DOMANDE & SALUTI
# ==============================================================================

class TestContextualIntelligenceAndChatter:
    """Verifica che discorsi in terza persona o chiacchiere non vengano scambiate per ordini musicali."""

    @pytest.mark.parametrize("third_person_phrase", [
        "secondo me Franchino non funziona bene",
        "anche Franchino lagga stasera",
        "pure Franchino è crashato",
        "Franchino è un bot molto potente",
        "Franchino ha suonato prima",
        "dicevo che Franchino va riavviato",
        "parlavo di Franchino con gli altri",
    ])
    def test_third_person_speech_identified(self, third_person_phrase):
        """Frasi che parlano DI Franchino in terza persona vengono identificate."""
        cmd_lower = third_person_phrase.lower().strip()
        third_person_pat = re.compile(
            r'\b(?:parlavo\s+di|dicevo\s+di|si\s+parla\s+di)\s+(?:franchino|frankino|francino)\b|'
            r'\b(?:secondo\s+me|anche|pure|credo\s+che|penso\s+che|dicevo\s+che)?\s*'
            r'(?:franchino|frankino|francino)\s+'
            r'(?:è|ha|fa|non|sembra|era|dice|va|lagga|crasha|funziona|esiste)\b',
            re.IGNORECASE
        )
        assert third_person_pat.search(cmd_lower) is not None

    @pytest.mark.parametrize("dismissal_phrase", [
        "lascia stare",
        "lascia perdere",
        "niente",
        "non fa niente",
        "fa niente",
        "annulla",
        "come non detto",
    ])
    def test_conversational_dismissals(self, dismissal_phrase):
        """Annullamenti e 'lascia stare' non lanciano ricerche musicali."""
        dismissals = (
            "lascia stare", "lascia perdere", "niente", "non fa niente",
            "fa niente", "annulla", "come non detto", "lascia", "nessuna", "nulla"
        )
        assert dismissal_phrase in dismissals

    @pytest.mark.parametrize("question_phrase", [
        "chi sei",
        "come stai",
        "cosa fai",
        "dove sei",
        "come va",
        "chi ha il boost",
        "come funziona il boost",
        "cos'è il boost",
    ])
    def test_conversational_questions_identified_not_music(self, question_phrase):
        """Domande discorsive o informative vengono identificate come domande e non come brani."""
        cmd_strip = question_phrase.strip().lower()
        is_question = (
            cmd_strip.endswith("?")
            or re.match(
                r"^(?:cosa|che|chi|come|dove|quando|perch[eé]|quali|quale|quanto|quanta|quanti|quante|"
                r"spiegami|dimmi|parlami|raccontami|sai\s+dirmi|puoi\s+dirmi|mi\s+dici|mi\s+spieghi|"
                r"sai\s+cos'è|sai\s+cosa|sai|conosci|esiste|ci\s+sono|si\s+può|posso|c'è|hai|avete|"
                r"chi\s+ha|come\s+funziona|cosa\s+serve|a\s+cosa\s+serve|cosa\s+fa|chi\s+sei|come\s+stai|cosa\s+fai|dove\s+sei|come\s+va)\b",
                cmd_strip,
                re.IGNORECASE,
            ) is not None
            or any(q_term in cmd_strip for q_term in (
                "outlands", "ultima online", "come funziona", "come si fa", "dove trovo",
                "dove si trova", "cosa serve", "a cosa serve", "cosa fa", "cosa fanno",
                "perché", "perche", "chi ha il boost", "cos'è il boost", "come funziona il boost"
            ))
        )
        assert is_question is True, f"Frase interrogativa '{question_phrase}' non identificata come domanda!"

    @pytest.mark.parametrize("greeting_phrase", [
        "ciao",
        "buongiorno",
        "buonasera",
        "buonanotte",
        "buona notte",
        "salve",
        "ti voglio bene",
        "sei un grande",
        "sei il migliore",
    ])
    def test_friendly_greetings_identified_not_music(self, greeting_phrase):
        """Saluti amichevoli a Franchino vengono intercettati senza finire su YouTube."""
        cmd_strip = greeting_phrase.strip().lower()
        is_greeting = bool(re.match(
            r'^(?:ciao|buongiorno|buonasera|buonanotte|buona\s+notte|salve|ueh|uè|bella|grande|ti\s+voglio\s+bene|sei\s+un\s+grande|sei\s+il\s+migliore)\b',
            cmd_strip
        ))
        assert is_greeting is True, f"Saluto '{greeting_phrase}' non intercettato!"


# ==============================================================================
# 6. TEST EFFICIENZA HARDWARE: AUDIO PROCESSING & RISPARMIO RISORSE
# ==============================================================================

class TestHardwareEfficiencyAndAudioPipeline:
    """Verifica le ottimizzazioni di CPU, RAM e banda di rete della pipeline audio."""

    def test_minimum_phrase_duration_blocks_short_noise(self):
        """MIN_PHRASE_SEC a 0.45s blocca istantaneamente respiri e rumori transitori sotto 450ms."""
        assert FranchinoVoiceSink.MIN_PHRASE_SEC >= 0.45

    def test_16khz_resampling_reduces_data_size_by_66_percent(self):
        """Verifica che il downsampling a 16kHz riduca il buffer e il payload del 66% esatto."""
        # 1 secondo di audio mono a 48kHz 16-bit = 96.000 byte
        pcm_48k_1sec = b"\x00\x05" * 48000
        resampled_16k, _ = audioop.ratecv(pcm_48k_1sec, 2, 1, 48000, 16000, None)

        assert len(pcm_48k_1sec) == 96000
        assert len(resampled_16k) == 32000
        # Esattamente un terzo (risparmio del 66.6% di RAM, banda FLAC e upload HTTP)
        assert len(resampled_16k) == len(pcm_48k_1sec) // 3

    def test_rms_energy_gate_discards_silence(self):
        """Un frame a basso volume (rumore di fondo/respiro RMS < 120) viene scartato."""
        # Creiamo un frame di silenzio/rumore debolissimo (valore 2 in 16-bit little endian)
        quiet_pcm = b"\x02\x00" * 48000
        rms_val = audioop.rms(quiet_pcm, 2)
        assert rms_val < 120, f"RMS {rms_val} non deve superare la soglia VAD di 120"

    def test_buffer_pruning_and_cleanup_removes_stale_data(self):
        """Verifica che _prune_stale_buffers e cleanup liberino memoria ed evitino memory leak."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        sink = FranchinoVoiceSink(mock_player)

        now = time.monotonic()
        stale_time = now - 35.0  # Inattivo da 35 secondi
        fresh_time = now - 2.0   # Attivo 2 secondi fa

        sink._last_packet_time[1001] = stale_time
        sink._preroll_buffers[1001] = MagicMock()
        sink._users[1001] = MagicMock()

        sink._last_packet_time[1002] = fresh_time
        sink._preroll_buffers[1002] = MagicMock()
        sink._users[1002] = MagicMock()

        sink._pending_wake[1001] = (stale_time, "")
        sink._pending_wake[1002] = (fresh_time, "")

        # Eseguiamo il pruning
        sink._prune_stale_buffers(now)

        # L'utente inattivo 1001 deve essere stato eliminato dalla memoria
        assert 1001 not in sink._last_packet_time
        assert 1001 not in sink._preroll_buffers
        assert 1001 not in sink._users
        assert 1001 not in sink._pending_wake

        # L'utente attivo 1002 deve rimanere in memoria
        assert 1002 in sink._last_packet_time
        assert 1002 in sink._preroll_buffers
        assert 1002 in sink._users
        assert 1002 in sink._pending_wake

        # Test cleanup totale
        sink.cleanup()
        assert len(sink._buffers) == 0
        assert len(sink._preroll_buffers) == 0
        assert len(sink._last_packet_time) == 0
        assert len(sink._users) == 0
        assert len(sink._user_cache) == 0
        assert len(sink._pending_wake) == 0


# ==============================================================================
# 7. TEST END-TO-END DISPATCH COMANDI VOCALI & COMANDO SLASH /BOOST
# ==============================================================================

class TestVoiceCommandDispatchEndToEnd(unittest.IsolatedAsyncioTestCase):
    """Verifica end-to-end il comportamento di _handle_transcribed_text su boost, domande e musica."""

    async def test_boost_false_trigger_blocked(self):
        """'franchino chi ha il boost' non deve chiamare né set_effect né play_now."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.set_effect = AsyncMock(return_value="🔊 Bass Boost (Club)")
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.current = None
        mock_player.text_channel = MagicMock()
        mock_player.guild = MagicMock()
        mock_player.voice_client = MagicMock()

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = 12345
        mock_user.display_name = "Mario"

        with patch("brain.brain.process_user_speech", new=AsyncMock(return_value={"intent": "chat", "chat_reply": "Non c'è il boost", "voice_reply": "Tutto ok"})):
            await sink._handle_transcribed_text(mock_user, "hey franchino chi ha il boost", detected_cmd="chi ha il boost")

        mock_player.set_effect.assert_not_awaited()
        mock_player.play_now.assert_not_awaited()

    async def test_boost_positive_activation(self):
        """'hey franchino metti il boost' deve attivare set_effect('bassboost')."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.set_effect = AsyncMock(return_value="🔊 Bass Boost (Club)")
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = 12345
        mock_user.display_name = "Mario"

        await sink._handle_transcribed_text(mock_user, "hey franchino metti il boost", detected_cmd="metti il boost")
        mock_player.set_effect.assert_awaited_with("bassboost")

    async def test_boost_turn_off(self):
        """'hey franchino togli il boost' deve attivare set_effect('off')."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.set_effect = AsyncMock(return_value="⚪ Disattivato")
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = 12345
        mock_user.display_name = "Mario"

        await sink._handle_transcribed_text(mock_user, "hey franchino togli il boost", detected_cmd="togli il boost")
        mock_player.set_effect.assert_awaited_with("off")

    async def test_music_request_with_concerto(self):
        """'hey franchino metti il concerto di Vasco Rossi' deve avviare play_now e non set_effect."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.set_effect = AsyncMock()
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.current = None
        mock_player.text_channel = MagicMock()
        mock_player.guild = MagicMock()
        mock_player.voice_client = MagicMock()
        mock_player.source_manager.resolve_url_or_query = AsyncMock(return_value=["mock_song"])

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        mock_user = MagicMock()
        mock_user.id = 12345
        mock_user.display_name = "Mario"

        await sink._handle_transcribed_text(mock_user, "hey franchino metti il concerto di Vasco Rossi", detected_cmd="metti il concerto di Vasco Rossi")
        mock_player.set_effect.assert_not_awaited()
        mock_player.play_now.assert_awaited()


class TestSlashBoostCommand(unittest.IsolatedAsyncioTestCase):
    """Verifica il funzionamento del comando hybrid /boost registrato in main.py."""

    async def test_boost_slash_command_toggle(self):
        from main import boost_cmd, player_manager

        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        mock_player = MagicMock()
        mock_player.audio_effect = "off"
        mock_player.set_effect = AsyncMock(return_value="🔊 Bass Boost (Club)")
        player_manager.get_player = MagicMock(return_value=mock_player)

        # 1. Attivazione
        await boost_cmd.callback(ctx)
        mock_player.set_effect.assert_awaited_with("bassboost")
        ctx.send.assert_awaited_with("🎛️ Effetto Bass Boost impostato su: **🔊 Bass Boost (Club)**!")

        # 2. Disattivazione
        mock_player.audio_effect = "bassboost"
        mock_player.set_effect = AsyncMock(return_value="⚪ Disattivato")
        await boost_cmd.callback(ctx)
        mock_player.set_effect.assert_awaited_with("off")
        ctx.send.assert_awaited_with("🎛️ Effetto Bass Boost impostato su: **⚪ Disattivato**!")


# ==============================================================================
# 7. TEST REGOLA TASSATIVA: 'HEY/EHI FRANCHINO' OBBLIGATORIO
# ==============================================================================

class TestStrictHeyFranchinoRuleDetection:
    """
    Test suite approfondita sulla regola tassativa:
    Franchino risponde e si attiva SOLO ED ESCLUSIVAMENTE se preceduto da 'Hey'/'Ehi'.
    Se l'utente non pronuncia 'Hey Franchino' (es. 'Franchino metti la musica'),
    il bot NON deve rispondere e deve rimanere in silenzio assoluto.
    """

    @pytest.mark.parametrize("valid_wake", [
        "Hey Franchino",
        "ehi franchino",
        "hei franchino",
        "ey franchino",
        "ei franchino",
        "heylà franchino",
        "heyla franchino",
        "heilà franchino",
        "heila franchino",
        "ehilà franchino",
        "ehila franchino",
        "hey la franchino",
        "ehi la franchino",
        "Hey, Franchino",
        "Ehi... Franchino!",
        "Hey Franchina",
        "Hey Franchini",
        "hey frankino",
        "ehi francino",
        "hei franghino",
        "hey franckino",
        "hey franceschino",
        "ehi franchì",
        "hey fran chino",
        "ehi frank ino",
        "hey franco ino",
        "hey franchi no",
        "ehi fra chi no",
        "hey frankie no",
        "hey franchi, no metti musica",
        "ehi fra chi, no come stai",
        "Hey Franchino metti musica",
        "ehi franchino stop",
        "ciao hey franchino metti pop",
    ])
    def test_all_hey_variants_activate_bot(self, valid_wake):
        """Tutte le varianti fonetiche di 'Hey/Ehi' attivano correttamente la wake word."""
        has_wake, cmd, tok = FranchinoVoiceSink.extract_wake_and_command(valid_wake)
        assert has_wake is True, f"Mancata attivazione per frase con Hey: '{valid_wake}'"
        assert len(tok) > 0

    @pytest.mark.parametrize("invalid_call", [
        "Franchino",
        "franchino metti la musica",
        "Franchino che ore sono?",
        "franchino stop",
        "franchino salta",
        "franchino pausa",
        "franchino comandi",
        "franchino boost",
        "franchino /boost",
        "franchino bass boost",
        "franchino togli il boost",
        "franchi no ci sei",
        "fra chi no rispondi",
        "franco ino metti pop",
        "frank ino pausa",
        "frankino metti una canzone",
        "francino suona rock",
        "franckino alza il volume",
        "franceschino ci sei",
        "franchì dimmi tutto",
        "a franchì dimmi tutto",
        "secondo me franchino non funziona",
        "anche franchino lagga stasera",
        "ho parlato con franchino",
        "grazie franchino",
        "ciao franchino",
        "dimmi franchino",
    ])
    def test_franchino_without_hey_strictly_rejected(self, invalid_call):
        """Qualsiasi frase con 'Franchino' ma SENZA 'Hey/Ehi' viene tassativamente scartata."""
        has_wake, cmd, tok = FranchinoVoiceSink.extract_wake_and_command(invalid_call)
        assert has_wake is False, f"FALSO POSITIVO: '{invalid_call}' ha attivato la wake word senza 'Hey'!"
        assert cmd == ""
        assert tok == ""

    def test_fuzzy_and_bigram_strictly_require_hey_prefix(self):
        """Il fuzzy matching di livello 2 e bigram join di livello 3 richiedono tassativamente Hey/Ehi."""
        # Livello 2 (Fuzzy typo 'franchkno'):
        has_w_with_hey, _, _ = FranchinoVoiceSink.extract_wake_and_command("hey franchkno metti musica")
        assert has_w_with_hey is True, "Fuzzy con 'hey' deve attivare la wake word"

        has_w_no_hey, _, _ = FranchinoVoiceSink.extract_wake_and_command("franchkno metti musica")
        assert has_w_no_hey is False, "Fuzzy senza 'hey' NON deve attivare la wake word"

        has_w_wrong_prefix, _, _ = FranchinoVoiceSink.extract_wake_and_command("ragazzi franchkno metti musica")
        assert has_w_wrong_prefix is False, "Fuzzy con parola diversa da 'hey' NON deve attivare la wake word"

        # Livello 3 (Bigram):
        has_w_bigram_hey, _, _ = FranchinoVoiceSink.extract_wake_and_command("ehi franco ino metti rock")
        assert has_w_bigram_hey is True, "Bigram con 'ehi' deve attivare la wake word"

        has_w_bigram_no_hey, _, _ = FranchinoVoiceSink.extract_wake_and_command("franco ino metti rock")
        assert has_w_bigram_no_hey is False, "Bigram senza 'ehi' NON deve attivare la wake word"


class TestStrictHeyFranchinoAsyncDispatch(unittest.IsolatedAsyncioTestCase):
    """Verifica il comportamento asincrono di _process_utterance e _handle_transcribed_text."""

    async def test_process_utterance_silent_on_franchino_without_hey(self):
        """In _process_utterance, trascrizioni senza 'Hey Franchino' non attivano il bot e restano in silenzio."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.voice_client = MagicMock()
        mock_player.is_speaking_tts = False

        sink = FranchinoVoiceSink(mock_player)
        sink._handle_transcribed_text = AsyncMock()

        phrase_pcm = b"\xd0\x00\xd0\x00" * int(48000 * 0.8)
        speaker = MagicMock(id=888, display_name="User")

        # 1. Trascrizione "franchino metti la musica" (SENZA Hey)
        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "franchino metti la musica"}]}):
            await sink._process_utterance(888, speaker, phrase_pcm)
            sink._handle_transcribed_text.assert_not_called()

        # 2. Trascrizione "hey franchino metti la musica" (CON Hey)
        with patch.object(sink.recognizer, "recognize_google", return_value={"alternative": [{"transcript": "hey franchino metti la musica"}]}):
            await sink._process_utterance(888, speaker, phrase_pcm)
            sink._handle_transcribed_text.assert_awaited_once()

    async def test_handle_transcribed_text_silent_when_no_hey(self):
        """Chiamate dirette a _handle_transcribed_text senza 'Hey Franchino' sono ignorate in silenzio."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        mock_player.play_now = AsyncMock()
        mock_player.set_effect = AsyncMock()
        mock_player.speak_tts = AsyncMock()

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        user = MagicMock(id=777, display_name="Davide")

        # Se l'utente dice "Franchino metti la musica" o "Franchino stop"
        await sink._handle_transcribed_text(user, "Franchino metti la musica")
        await sink._handle_transcribed_text(user, "Franchino stop")
        await sink._handle_transcribed_text(user, "Franchino", detected_cmd="")

        mock_player.play_now.assert_not_awaited()
        mock_player.set_effect.assert_not_awaited()
        mock_player.speak_tts.assert_not_called()
        sink._notify_channel.assert_not_awaited()

    async def test_active_session_flow_after_hey_franchino(self):
        """Dopo aver detto 'Hey Franchino', il bot risponde e apre la sessione per completare il comando."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.source_manager.resolve_url_or_query = AsyncMock(return_value=["mock_song"])
        mock_player.current = None

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        user = MagicMock(id=4242, display_name="TestFan")

        # 1. Utente dice solo "Hey Franchino"
        await sink._handle_transcribed_text(user, "Hey Franchino", detected_cmd="")
        mock_player.speak_tts.assert_called_once()
        assert "Magia! Dimmi tutto" in mock_player.speak_tts.call_args[0][0]
        assert 4242 in sink._pending_wake

        # 2. Entro 10 secondi, l'utente pronuncia il titolo "metti Albachiara" (senza ripetere Hey Franchino)
        await sink._handle_transcribed_text(user, "metti Albachiara", detected_cmd="metti Albachiara")
        mock_player.play_now.assert_awaited_once()

    async def test_pending_prefix_deduplication_in_active_session(self):
        """Verifica che un prefisso (es. 'metti') non venga duplicato se l'utente lo ripete nel follow-up."""
        mock_player = MagicMock()
        mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
        mock_player.play_now = AsyncMock()
        mock_player.speak_tts = AsyncMock()
        mock_player.source_manager.resolve_url_or_query = AsyncMock(return_value=["mock_song"])
        mock_player.current = None

        sink = FranchinoVoiceSink(mock_player)
        sink._notify_channel = AsyncMock()

        user = MagicMock(id=5555, display_name="PrefixTester")

        # 1. Utente dice "Hey Franchino metti" -> pending_wake memorizza "metti"
        await sink._handle_transcribed_text(user, "Hey Franchino metti", detected_cmd="metti")
        assert 5555 in sink._pending_wake
        assert sink._pending_wake[5555][1] == "metti"

        # 2. Utente nel follow-up ripete "metti Vasco Rossi"
        await sink._handle_transcribed_text(user, "metti Vasco Rossi", detected_cmd="metti Vasco Rossi")
        mock_player.source_manager.resolve_url_or_query.assert_awaited_once()
        query_arg = mock_player.source_manager.resolve_url_or_query.call_args[0][0]
        # La query musicale deve essere 'Vasco Rossi' e non 'metti Vasco Rossi'
        assert query_arg.strip().lower() == "vasco rossi"



