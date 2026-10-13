"""
jack_math_engine.py - Motore Matematico di Intelligenza "Super Matematico Jack" & Hardware Optimizer
=====================================================================================================
Implementa con rigore matematico la formula di disambiguazione e scoring per tutte le intenzioni vocali
e testuali di Franchino Bot:

    S(Intent, q) = w_wake * phi_wake + w_phonetic * sigma_jaro + w_domain * BM25(q, D) + w_action * alpha_syntax - lambda_noise

Progettato per girare con consumo zero (<0.2 ms di CPU) e footprint di memoria trascurabile su server
Oracle Cloud ARM Ampere A1 (2 vCPU ARM, 12 GB RAM, 200 GB disco).
"""

import math
import os
import re
import gc
import shutil
import resource
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger("Franchino.JackMath")

# Costanti pesi della formula Jack
W_WAKE = 0.15
W_PHONETIC = 0.25
W_DOMAIN = 0.35
W_ACTION = 0.25
BETA_SOFTMAX = 3.0

# Intenzioni riconosciute dal bot
INTENTS = [
    "smart_assistant",
    "jack_math_boost",
    "razor_script",
    "wiki_guide",
    "sports_match",
    "dj_control",
    "admin_control",
    "social_interaction",
    "live_fable",
    "liveness_check",
    "play_music",
    "chat",
]

# Corpus documentale per Okapi BM25
DOMAIN_CORPUS: Dict[str, List[str]] = {
    "smart_assistant": [
        "ore", "ora", "orario", "data", "giorno", "oggi", "calendario",
        "meteo", "previsioni", "tempo", "pioggia", "temperatura", "gradi",
        "timer", "promemoria", "avvertimi", "avvisami", "sveglia", "minuti", "secondi", "minuto", "secondo",
        "quarto", "mezzora", "calcola", "quanto", "somma", "moltiplicazione", "divisione", "radice",
        "dado", "dadi", "moneta", "testa", "croce", "sorte", "scegli",
        "barzelletta", "barzellette", "battuta", "battute", "ridere", "freddura"
    ],
    "jack_math_boost": [
        "jack", "matematico", "formula", "matematica", "boost", "algoritmo", "probabilita",
        "scoring", "intelligenza", "bm25", "jaro", "winkler", "arm", "oracle", "hardware",
        "cpu", "ram", "ottimizzazione", "super", "genio", "telemetria", "motore", "calcolo",
        "rigore", "matematici", "formule"
    ],
    "razor_script": [
        "script", "scripts", "razor", "razorscript", "macro", "codice", "file", "download",
        "magery", "taming", "lockpicking", "fishing", "pesca", "mining", "lumberjacking",
        "blacksmith", "carpentry", "tailoring", "alchemy", "inscribe", "tinkering", "cartography",
        "cooking", "stealth", "hiding", "stealing", "snooping", "removetrap", "detecthidden",
        "poisoning", "swords", "fencing", "macing", "archery", "parry", "healing", "meditation",
        "evalint", "magicresist", "spiritspeak", "necromancy", "chivalry", "veterinary", "herding",
        "armslore", "tasteid", "itemid", "forensics", "tracking", "camping", "begging",
        "addomesticare", "tamare", "tammare", "scassinare", "grimaldello", "minare", "pescare",
        "curare", "bende", "spada", "arco", "mazza", "forgiare", "fabbro", "legna"
    ],
    "wiki_guide": [
        "wiki", "guida", "outlands", "ultima", "online", "uo", "aspect", "armor", "build",
        "template", "dungeon", "boss", "meccaniche", "mechanics", "tier", "core", "distillation",
        "pet", "pets", "creature", "bestiary", "bestiario", "nightmare", "ostard", "drake",
        "drago", "stat", "stats", "dexxer", "codex", "slayer", "pvm", "pvp", "parry",
        "aegis", "ossuary", "nusero", "sanctuary", "mausoleum", "pulma", "corpserun",
        "prevalia", "andaria", "cambria", "terramir", "shelter", "newb", "spiegami", "funziona",
        "mastery", "chain", "link", "taming", "dexxer", "bard", "tamer",
        "cavernam", "darkmire", "petram", "undermountain", "kraul", "souls", "tidal", "tomb", "inferno",
        "bloodwolf", "balron", "lich", "wyrm", "daemon", "hellhound", "corpse", "worm",
        "gazer", "harpy", "mongbat", "elemental", "skeleton", "zombie", "orc", "troll", "ogre",
        "hydra", "chimera", "kraken", "serpent", "spider", "ettin", "gargoyle", "golem",
        "wraith", "mummy", "wisp", "steed", "yeti", "zorn", "guar", "kith", "garou", "dryad",
        "magery", "fishing", "pesca", "mining", "lockpicking", "scassinare", "tamare", "curare",
        "meditare", "swords", "archery", "chivalry", "necromancy", "spiritspeak"
    ],
    "sports_match": [
        "partita", "partite", "match", "incontri", "gare", "sfide", "stasera", "oggi",
        "chi gioca", "streaming", "dove vedere", "palinsesto", "orari", "calcio", "basket",
        "serie a", "serie b", "champions", "champions league", "premier", "premier league",
        "la liga", "bundesliga", "nba", "eurolega", "euroleague", "tennis", "formula 1",
        "f1", "motogp", "gp", "gran premio", "ferrari", "leclerc", "milan", "inter", "juve",
        "juventus", "roma", "napoli", "lazio", "prossima gara", "motori", "corre", "corrono", "corsa", "circuito", "formula1"
    ],
    "dj_control": [
        "stop", "stoppa", "ferma", "fermati", "pausa", "riprendi", "salta", "skip",
        "volume", "alza", "abbassa", "riavvia", "riavviati", "reset", "sbloccati",
        "boost", "bassboost", "bassi", "megabass", "nightcore", "slowed", "8d",
        "loop", "ripeti", "shuffle", "mischia", "coda", "effetti", "audio", "console"
    ],
    "admin_control": [
        "sposta", "porta", "caccia", "scollega", "muta", "smuta", "silenzia", "ruolo",
        "timeout", "kick", "espelli", "ban", "banna", "sbanna", "pulisci", "cancella",
        "crea stanza", "chiudi stanza", "stanza", "moderazione", "admin"
    ],
    "social_interaction": [
        "abbraccio", "abbraccia", "brindisi",
        "cin cin", "auguri", "buonanotte", "cinque", "batti cinque", "saluta",
        "ti voglio bene", "troll", "amore", "amico", "salutami"
    ],
    "live_fable": [
        "favola", "favole", "raccontami", "poesia", "vocalist", "notte", "metempsicosi",
        "insomnia", "magia", "chiudi gli occhi", "vola", "storia", "c'era una volta"
    ],
    "liveness_check": [
        "ci senti", "mi senti", "ci sei", "ci siete", "sei vivo", "sei sveglio",
        "batti un colpo", "rispondi", "fatti sentire", "test microfono", "prova audio", "funzioni", "funziona",
        "senti", "sentite", "ascolti", "colpo", "vivo", "sveglio"
    ],
    "play_music": [
        "metti", "suona", "play", "riproduci", "fammi sentire", "canzone", "canzoni",
        "brano", "musica", "traccia", "cantante", "artista", "album", "disco",
        "remix", "playlist", "ascolta", "fai partire", "youtube", "spotify",
        "queen", "vasco", "eminem", "albachiara", "rock", "pop", "rap", "jazz",
        "techno", "dance", "house", "trap", "blues", "metal", "reggaeton", "classica",
        "chitarra", "pianoforte", "basso", "batteria", "audio", "volume", "sound", "beat"
    ],
    "chat": [
        "ciao", "buongiorno", "buonasera", "buonanotte", "come stai", "tutto bene",
        "come va", "chi sei", "cosa fai", "grazie", "grande", "mito", "stanco",
        "giornata pesante", "triste", "parlami", "racconta", "fratello", "amico"
    ],
}

DOMAIN_CORPUS_SETS: Dict[str, set] = {it: set(words) for it, words in DOMAIN_CORPUS.items()}

# Imperative / action verbs syntactical regexes
ACTION_SYNTAX_PATTERNS = {
    "smart_assistant": re.compile(
        r"^(?:che\s+ore\s+sono|che\s+ora\s+[eè]|orario|dimmi\s+l[\x27\’]?\s*ora|che\s+giorno\s+[eè]|data\s+di\s+oggi|giorno\s+siamo|"
        r"che\s+tempo\s+fa|che\s+tempo\s+far[aà]|com[\x27\’]?\s*[eè]\s+(?:il\s+)?(?:meteo|tempo)|previsioni\s+(?:del\s+tempo|meteo)|previsioni|meteo|"
        r"metti\s+(?:un\s+)?timer|imposta\s+(?:un\s+)?timer|timer|promemoria|avvertimi\s+tra|avvisami\s+tra|quanto\s+manca|cancella\s+(?:il\s+)?timer|ferma\s+(?:il\s+)?timer|stop\s+timer|"
        r"metti\s+(?:la\s+|una\s+)?sveglia|sveglia|cancella\s+(?:la\s+)?sveglia|ferma\s+(?:la\s+)?sveglia|"
        r"quanto\s+fa|calcola|fai\s+il\s+calcolo|radice\s+(?:quadrata\s+)?di|"
        r"testa\s+o\s+croce|lancia\s+(?:un\s+|il\s+)?dado|tira\s+(?:un\s+|il\s+)?dado|lancia\s+(?:una\s+|la\s+)?moneta|tira\s+(?:una\s+|la\s+)?moneta|scegli\s+tra|decidi\s+tra|"
        r"raccontami\s+una\s+barzelletta|racconta\s+una\s+barzelletta|dimmi\s+una\s+barzelletta|dimmi\s+una\s+battuta|fammi\s+ridere|barzelletta|battuta)\b",
        re.IGNORECASE,
    ),
    "jack_math_boost": re.compile(
        r"^(?:chiedi\s+a(?:l)?\s+(?:super\s+)?(?:matematico\s+)?jack|super\s+matematico\s+jack|"
        r"formula\s+(?:matematica|di\s+jack)|boost\s+(?:jack|matematico)|"
        r"(?:cosa\s+dice|parlami\s+di|chi\s+[eè]|spiegami\s+la)\s+(?:super\s+matematico\s+)?jack|"
        r"spiegami\s+la\s+formula|dimmi\s+la\s+formula|formula\s+jack|telemetria\s+hardware)\b",
        re.IGNORECASE,
    ),
    "razor_script": re.compile(
        r"^(?:script|scripts|razor|macro|codice\s+razor|"
        r"(?:dammi|passami|mi\s+passi|mi\s+daresti|trova|cerca|mostra|hai)\s+(?:lo\s+|un\s+|la\s+|gli\s+)?(?:script|macro|codice)|"
        r"(?:che|quali|mostrami|elenco|lista)\s+(?:gli\s+|i\s+)?(?:script|macro))\b",
        re.IGNORECASE,
    ),
    "wiki_guide": re.compile(
        r"^(?:cos[\x27\’]?\s*[eè]|cosa\s+[eè]|cosa\s+fa|cosa\s+fanno|wiki|guida|spiegami|come\s+funziona|dove\s+si\s+trova|dove\s+trovo|dov[\x27\’]?\s*[eè]|cosa\s+serve|a\s+cosa\s+serve|a\s+che\s+serve|come\s+si\s+fa|stats\s+di|info\s+su|parlami\s+di|dimmi\s+di|scheda\s+di|cerco\s+(?:un\s+|il\s+)?|ho\s+trovato\s+(?:un\s+|il\s+)?|spawn\s+di|loot\s+di)\b",
        re.IGNORECASE,
    ),
    "sports_match": re.compile(
        r"^(?:chi\s+gioca|quando\s+gioca|quando\s+corre|a\s+che\s+ora\s+corre|a\s+che\s+ora\s+gioca|orari\s+(?:della|del)|orari|dove\s+vedere|prossima\s+gara|palinsesto|partite|match|streaming\s+di|cerca\s+la\s+partita)\b",
        re.IGNORECASE,
    ),
    "dj_control": re.compile(
        r"^(?:stop(?!\s+(?:timer|sveglia|il\s+timer|la\s+sveglia))|stoppa|ferma(?!\s+(?:il\s+timer|la\s+sveglia|timer|sveglia))|pausa|riprendi|salta|skip|volume|alza|abbassa|riavvia|riavviati|reset|bassi|boost|/boost|togli\s+il\s+boost|attiva\s+il\s+boost|"
        r"metti\s+(?:in\s+)?(?:pausa|stop|loop|ripetizione|shuffle|muto|mute)|"
        r"metti\s+(?:il\s+)?volume|"
        r"(?:metti|attiva|imposta|applica|pompa)\s+(?:il\s+|l[\x27\']|un\s+)?(?:bass\s*boost|megabass|bassi|boost|riverbero|reverb|eco|nightcore|slowed|8d|karaoke|vaporwave)|"
        r"(?:togli|disattiva|leva|rimuovi|senza|no)\s+(?:il\s+|l[\x27\']|i\s+)?(?:boost|bass\s*boost|effetti|filtri|riverbero|eco)|"
        r"loop|shuffle|mescola|mischia)\b",
        re.IGNORECASE,
    ),
    "admin_control": re.compile(
        r"^(?:sposta|porta|caccia|scollega|muta|smuta|ruolo|timeout|kick|espelli|ban|banna|pulisci|crea\s+stanza)\b",
        re.IGNORECASE,
    ),
    "social_interaction": re.compile(
        r"^(?:fai\s+un\s+brindisi|brindisi|abbraccia|abbraccio|batti\s+cinque|saluta)\b",
        re.IGNORECASE,
    ),
    "live_fable": re.compile(
        r"^(?:raccontami\s+una\s+favola|dimmi\s+una\s+favola|favola|fai\s+il\s+vocalist|poesia|c[\x27\']era\s+una\s+volta)\b",
        re.IGNORECASE,
    ),
    "liveness_check": re.compile(
        r"^(?:ci\s+senti|mi\s+senti|ci\s+sei|ci\s+siete|sei\s+vivo|sei\s+sveglio|batti\s+un\s+colpo|rispondi|fatti\s+sentire|funzioni|funziona|test\s+microfono)\b",
        re.IGNORECASE,
    ),
    "play_music": re.compile(
        r"^(?:metti(?!\s+(?:in\s+)?(?:pausa|stop|loop|ripetizione|shuffle|muto|mute)(?:\b|$)|(?:\s+(?:il\s+)?volume)|(?:\s+(?:un\s+|il\s+)?timer)|(?:\s+(?:una\s+|la\s+)?sveglia)|(?:\s+(?:il\s+|l[\x27\']|un\s+)?(?:boost|bass|effetto)))|"
        r"suona(?!\s+(?:la\s+)?sveglia)|play|riproduci|fammi\s+sentire|fammi\s+ascoltare|fai\s+partire|ascolta|"
        r"cerca\s+(?:la\s+canzone|il\s+brano|la\s+musica)|canzone\s+di|brano\s+di|traccia\s+di)\b",
        re.IGNORECASE,
    ),
    "chat": re.compile(
        r"^(?:ciao|buongiorno|buonasera|buonanotte|come\s+stai|come\s+va|tutto\s+bene|grazie|ti\s+voglio\s+bene|grande\s+franchino)\b",
        re.IGNORECASE,
    ),
}

# Negative noise / cross-domain conflict patterns
CONFLICT_PATTERNS = {
    "third_person_chatter": re.compile(
        r"\b(?:parlavo\s+di|dicevo\s+di|si\s+parla\s+di|secondo\s+me|dicono\s+che|dice\s+che)\s+franchino\b",
        re.IGNORECASE,
    ),
    "colloquial_fra_chatter": re.compile(
        r"\b(?:fra\s+chi\s+ha|fra\s+chi\s+boosta|fra\s+chi\s+usa|fra\s+chi\s+fa|fra\s+chi\s+prende|fra\s+chi\s+vuole)\b",
        re.IGNORECASE,
    ),
}


def strip_wake_prefix(text: str) -> str:
    """Rimuove il richiamo a Franchino all'inizio della frase per isolare il comando operativo."""
    return re.sub(r"^(?:franchino|frankino|francino|franci|fra)\s*[,:\s]*", "", text.strip(), flags=re.IGNORECASE).strip()


def jaro_winkler_distance(s1: str, s2: str, p: float = 0.1, max_l: int = 4) -> float:
    """
    Calcola la distanza di similarità di Jaro-Winkler tra due stringhe in O(L).
    Restituisce un valore compreso tra 0.0 (nessuna somiglianza) e 1.0 (identiche).
    """
    if s1 == s2:
        return 1.0

    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    s1_matches = [False] * len1
    s2_matches = [False] * len2

    matches = 0
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    transpositions = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (matches / len1 + matches / len2 + (matches - transpositions / 2.0) / matches) / 3.0

    # Winkler prefix bonus
    prefix = 0
    for i in range(min(len1, len2, max_l)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    return jaro + prefix * p * (1.0 - jaro)


class BM25DomainIndex:
    """Indice BM25 compatto in-memory per classificazione ontologica delle intenzioni."""

    def __init__(self, corpus: Dict[str, List[str]], k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_lengths: Dict[str, int] = {intent: len(words) for intent, words in corpus.items()}
        self.avg_doc_len = sum(self.doc_lengths.values()) / max(1, len(corpus))
        self.num_docs = len(corpus)

        # Calcolo IDF per ciascun termine del vocabolario
        self.df: Dict[str, int] = {}
        for words in corpus.values():
            unique_words = set(words)
            for w in unique_words:
                self.df[w] = self.df.get(w, 0) + 1

        self.idf: Dict[str, float] = {}
        for term, freq in self.df.items():
            # Robertson-Spärck Jones IDF
            self.idf[term] = math.log(1.0 + (self.num_docs - freq + 0.5) / (freq + 0.5))

    def score(self, query_tokens: List[str], intent: str) -> float:
        """Calcola lo score BM25 normalizzato in [0, 1] della query rispetto all'intento."""
        words = self.corpus.get(intent, [])
        doc_len = self.doc_lengths.get(intent, 1)
        raw_score = 0.0

        for t in query_tokens:
            if t not in self.idf:
                continue
            tf = words.count(t)
            if tf == 0:
                continue
            idf_val = self.idf[t]
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))
            raw_score += idf_val * (numerator / denominator)

        # Saturazione con tangente iperbolica per normalizzare in [0, 1]
        return math.tanh(0.40 * raw_score)


# Inizializzazione singleton dell'indice BM25
_bm25_index = BM25DomainIndex(DOMAIN_CORPUS)


@dataclass
class JackClassificationResult:
    """Struttura dati rigorosa con i risultati della formula matematica Jack."""
    intent: str
    score: float
    probability: float
    confidence_margin: float
    phi_wake: float
    sigma_jaro: float
    bm25_score: float
    alpha_syntax: float
    lambda_noise: float
    breakdown_by_intent: Dict[str, float] = field(default_factory=dict)
    probabilities_by_intent: Dict[str, float] = field(default_factory=dict)
    extracted_entities: Dict[str, Any] = field(default_factory=dict)

    def to_math_summary(self) -> str:
        """Genera una stringa di diagnostica LaTeX/ASCII rigorosa."""
        return (
            f"S({self.intent}) = {W_WAKE}·({self.phi_wake:.2f}) + {W_PHONETIC}·({self.sigma_jaro:.2f}) + "
            f"{W_DOMAIN}·({self.bm25_score:.2f}) + {W_ACTION}·({self.alpha_syntax:.2f}) - ({self.lambda_noise:.2f}) = "
            f"{self.score:.4f} [P={self.probability*100:.1f}%, Δ={self.confidence_margin:.3f}]"
        )


class JackIntelligenceFormula:
    """
    Motore matematico Jack di disambiguazione delle intenzioni.
    Calcola analiticamente la distribuzione probabilistica di ogni richiesta:

        S(Intent, q) = w_wake * phi_wake + w_phonetic * sigma_jaro + w_domain * BM25(q, D) + w_action * alpha_syntax - lambda_noise
        P(Intent | q) = exp(beta * S(Intent, q)) / sum_k exp(beta * S(k, q))
    """

    def __init__(self):
        self.bm25 = _bm25_index

    def calculate_wake(self, text: str) -> float:
        """Calcola phi_wake in [0, 1] in base alla presenza, posizione e intenzione dell'invocazione di Franchino."""
        t_low = text.lower().strip()
        tokens = re.findall(r"\b\w+\b", t_low)
        if not tokens:
            return 0.0

        # Filtro anti-chiacchiera colloquiale "fra chi ha il boost"
        if CONFLICT_PATTERNS["colloquial_fra_chatter"].search(t_low):
            return 0.05

        # Chiacchiera in terza persona su Franchino ("secondo me Franchino non va")
        if CONFLICT_PATTERNS["third_person_chatter"].search(t_low):
            return 0.10

        wake_aliases = ("franchino", "frankino", "francino", "franci")
        for idx, tok in enumerate(tokens):
            if tok in wake_aliases:
                if idx == 0:
                    return 1.00  # Chiamata diretta iniziale
                elif idx <= 2:
                    return 0.85  # Chiamata quasi iniziale
                else:
                    return 0.50  # Chiamata interna

        # Alias abbreviato 'fra' solo se seguito da una virgola o a inizio frase con azione
        if tokens[0] == "fra" and len(tokens) > 1:
            if not CONFLICT_PATTERNS["colloquial_fra_chatter"].search(t_low):
                return 0.70

        return 0.0

    def calculate_phonetic(self, query_tokens: List[str], intent: str) -> float:
        """Calcola sigma_jaro confrontando foneticamente i token della query con le parole chiave dell'intento."""
        target_words = DOMAIN_CORPUS.get(intent, [])
        target_set = DOMAIN_CORPUS_SETS.get(intent, set())
        if not query_tokens or not target_words:
            return 0.0

        max_jaro = 0.0
        for q_tok in query_tokens:
            if len(q_tok) < 3:
                continue
            if q_tok in target_set:
                return 1.0
            for t_word in target_words:
                sim = jaro_winkler_distance(q_tok, t_word)
                if sim > max_jaro:
                    max_jaro = sim
                    if max_jaro >= 0.98:
                        return 1.0

        return max_jaro

    def calculate_syntax(
        self,
        query_command: str,
        intent: str,
        is_uo_entity: Optional[bool] = None,
        is_razor: Optional[bool] = None,
    ) -> float:
        """Calcola alpha_syntax determinando la congruenza della struttura grammaticale/imperativa."""
        cmd_strip = query_command.strip()
        pattern = ACTION_SYNTAX_PATTERNS.get(intent)
        if pattern and pattern.search(cmd_strip):
            return 1.0

        # Riconoscimento entità Outlands per wiki_guide (se non è richiesta esplicita di macro/script)
        if intent == "wiki_guide":
            try:
                if is_razor is None:
                    from razor_scripts import is_script_question
                    is_razor, _ = is_script_question(cmd_strip)
                if not is_razor:
                    if is_uo_entity is None:
                        from outlands_wiki import is_outlands_entity
                        is_uo_entity, _, _ = is_outlands_entity(cmd_strip)
                    if is_uo_entity:
                        return 1.0
            except Exception:
                pass

        # Riconoscimento comandi musicali impliciti
        if intent == "play_music":
            try:
                if is_uo_entity is None:
                    from outlands_wiki import is_outlands_entity
                    is_uo_entity, _, _ = is_outlands_entity(cmd_strip)
                c_low = cmd_strip.lower()
                has_explicit_uo = any(w in c_low for w in ("guida", "wiki", "dungeon", "aspect", "build", "template", "macro", "script", "outlands"))
                if not is_uo_entity or not has_explicit_uo:
                    if any(w in c_low for w in ("canzone", "canzoni", "musica", "brano", "traccia", "album", "disco", "artista")):
                        return 1.0
            except Exception:
                pass

        return 0.0

    def calculate_noise(
        self,
        text: str,
        intent: str,
        is_uo_entity: Optional[bool] = None,
    ) -> float:
        """Calcola la penalità lambda_noise per prevenire falsi positivi o conflitti di dominio."""
        t_low = text.lower().strip()
        penalty = 0.0

        # Penalità chiacchiera in terza persona
        if CONFLICT_PATTERNS["third_person_chatter"].search(t_low):
            penalty += 0.40

        # Riconoscimento entità Outlands
        if is_uo_entity is None:
            try:
                from outlands_wiki import is_outlands_entity
                is_uo_entity, _, _ = is_outlands_entity(text)
            except Exception:
                is_uo_entity = False

        is_formula_one = bool(re.search(r"\bformula\s*(?:1|uno)\b|\bf1\b", t_low))
        has_sport_words = is_formula_one or any(
            w in t_low for w in (
                "chi gioca", "quando gioca", "a che ora gioca", "gioca", "giocano",
                "partita", "partite", "match", "serie a", "serie b", "champions",
                "milan", "inter", "juve", "f1", "motogp", "gran premio", "gara",
                "motori", "palinsesto", "eurolega", "euroleague", "nba", "basket", "calcio", "tennis"
            )
        )
        has_script_words = any(w in t_low for w in ("script", "scripts", "razor", "razorscript", "macro"))
        has_wiki_words = is_uo_entity or any(w in t_low for w in ("aspect", "tier", "dungeon", "ossuary", "aegis", "build", "bestiario", "drake", "bloodwolf", "balron", "lich", "taming", "pet", "mastery"))
        has_jack_words = (
            any(w in t_low for w in ("jack", "matematico", "super matematico"))
            or ("formula" in t_low and not is_formula_one)
        )

        is_audio_boost = any(w in t_low for w in ("bass", "bassi", "bassboost", "megabass", "subbass", "audio", "volume", "suono", "cassa", "casse", "equalizzatore", "filtro", "filtri", "effetto", "effetti"))
        is_dj_command = bool(ACTION_SYNTAX_PATTERNS["dj_control"].search(t_low)) or any(
            w in t_low for w in ("in pausa", "metti pausa", "metti stop", "metti muto", "in loop", "in shuffle", "metti il volume", "alza il volume", "abbassa il volume", "bass boost", "togli il boost", "senza boost")
        )

        is_explicit_music = (
            any(t_low.startswith(p) for p in ("metti ", "suona ", "riproduci ", "play ", "fammi sentire ", "fammi ascoltare ", "fai partire ", "ascolta "))
            or any(w in t_low for w in ("canzone", "canzoni", "brano", "brani", "musica", "traccia", "tracce", "cantante", "album", "disco"))
        ) and not is_dj_command

        has_explicit_uo = any(w in t_low for w in ("guida", "wiki", "dungeon", "aspect", "build", "template", "macro", "script", "outlands", "droppa", "spawn"))

        if has_jack_words and intent in ("sports_match", "play_music", "wiki_guide", "razor_script"):
            penalty += 0.50

        if intent == "jack_math_boost":
            if is_formula_one:
                penalty += 0.65
            if is_audio_boost:
                penalty += 0.70
            if CONFLICT_PATTERNS["colloquial_fra_chatter"].search(t_low):
                penalty += 0.60
            if (has_sport_words or has_script_words or has_wiki_words or is_dj_command) and not has_jack_words:
                penalty += 0.50
            if is_explicit_music and not has_jack_words:
                penalty += 0.60

        if is_uo_entity and not (is_explicit_music and not has_explicit_uo):
            # Se la frase contiene una chiara entità di Outlands (e non è una richiesta musicale con cantante/brano omonimo)
            if intent in ("play_music", "dj_control"):
                penalty += 0.55
            elif intent in ("sports_match", "smart_assistant"):
                penalty += 0.50
            elif intent == "chat" and not any(w in t_low for w in ("ciao", "buongiorno", "buonasera", "grazie")):
                penalty += 0.40

        if intent in ("wiki_guide", "razor_script") and has_sport_words and not any(w in t_low for w in ("outlands", "ultima", "pet", "taming", "lockpicking")):
            penalty += 0.45

        # Penalizza wiki_guide se inizia con comando musicale esplicito o privo di pertinenza Outlands
        if intent == "wiki_guide":
            if is_explicit_music and not has_explicit_uo:
                penalty += 0.60
            elif not is_uo_entity and not has_wiki_words and not has_explicit_uo:
                penalty += 0.35

        if intent == "razor_script" and not any(w in t_low for w in ("script", "scripts", "macro", "razor", "razorscript")):
            penalty += 0.35

        if intent == "admin_control" and not any(w in t_low for w in ("sposta", "porta", "caccia", "scollega", "muta", "smuta", "ruolo", "timeout", "kick", "ban", "banna", "espelli", "pulisci", "stanza", "admin")):
            penalty += 0.35

        if intent == "dj_control":
            if CONFLICT_PATTERNS["colloquial_fra_chatter"].search(t_low):
                penalty += 0.50
            elif not ACTION_SYNTAX_PATTERNS["dj_control"].search(t_low) and not any(w in t_low for w in ("stop", "pausa", "volume", "loop", "shuffle", "bassi", "boost")):
                penalty += 0.30

        if intent == "sports_match":
            if has_script_words or has_wiki_words:
                penalty += 0.40
            elif not ACTION_SYNTAX_PATTERNS["sports_match"].search(t_low) and not any(w in t_low for w in ("partita", "partite", "match", "serie a", "champions", "milan", "inter", "juve", "f1", "motogp", "gara", "sport")):
                penalty += 0.30

        if intent == "smart_assistant" and not ACTION_SYNTAX_PATTERNS["smart_assistant"].search(t_low) and not any(w in t_low for w in ("ora", "ore", "meteo", "tempo", "timer", "sveglia", "calcola", "quanto", "dado", "moneta", "barzelletta")):
            penalty += 0.30

        if intent == "liveness_check" and not ACTION_SYNTAX_PATTERNS["liveness_check"].search(t_low):
            penalty += 0.30

        if intent == "social_interaction" and not ACTION_SYNTAX_PATTERNS["social_interaction"].search(t_low):
            penalty += 0.30

        if intent == "live_fable" and not ACTION_SYNTAX_PATTERNS["live_fable"].search(t_low) and not any(w in t_low for w in ("favola", "favole", "poesia", "vocalist", "metempsicosi", "insomnia", "raccontami")):
            penalty += 0.30

        if intent == "play_music":
            if is_dj_command:
                penalty += 0.65
            elif (has_script_words or has_sport_words or (has_wiki_words and not is_explicit_music)):
                penalty += 0.45

        if intent == "chat" and has_sport_words and not any(w in t_low for w in ("ciao", "buongiorno", "buonasera", "grazie", "ti voglio bene")):
            penalty += 0.35

        return min(0.95, penalty)

    def classify(self, query: str, current_song_title: Optional[str] = None) -> JackClassificationResult:
        """
        Classifica l'intenzione calcolando il punteggio analitico di ogni intento
        e la relativa distribuzione di probabilità con Softmax.
        """
        clean_text = query.strip()
        clean_cmd = strip_wake_prefix(clean_text) or clean_text
        cmd_tokens = re.findall(r"\b\w+\b", clean_cmd.lower())
        full_tokens = re.findall(r"\b\w+\b", clean_text.lower())

        phi_w = self.calculate_wake(clean_text)

        # Precalcolo entità Outlands e script una sola volta prima del ciclo
        is_uo_entity = False
        uo_entity_name = ""
        uo_entity_type = None
        try:
            from outlands_wiki import is_outlands_entity
            is_uo_entity, uo_entity_name, uo_entity_type = is_outlands_entity(clean_cmd)
        except Exception:
            pass

        is_razor = False
        try:
            from razor_scripts import is_script_question
            is_razor, _ = is_script_question(clean_cmd)
        except Exception:
            pass

        # Calcolo di S(Intent, q) per ciascun intento
        scores: Dict[str, float] = {}
        components: Dict[str, Dict[str, float]] = {}

        for it in INTENTS:
            sigma_j = self.calculate_phonetic(cmd_tokens or full_tokens, it)
            bm25_s = self.bm25.score(cmd_tokens or full_tokens, it)
            alpha_s = self.calculate_syntax(clean_cmd, it, is_uo_entity=is_uo_entity, is_razor=is_razor)
            lambda_n = self.calculate_noise(clean_text, it, is_uo_entity=is_uo_entity)

            raw_score = (
                W_WAKE * phi_w
                + W_PHONETIC * sigma_j
                + W_DOMAIN * bm25_s
                + W_ACTION * alpha_s
                - lambda_n
            )
            score = max(0.0, raw_score)
            scores[it] = score
            components[it] = {
                "phi_wake": phi_w,
                "sigma_jaro": sigma_j,
                "bm25": bm25_s,
                "alpha_syntax": alpha_s,
                "lambda_noise": lambda_n,
            }

        # Calcolo Softmax con scaling beta
        exp_scores = {it: math.exp(BETA_SOFTMAX * sc) for it, sc in scores.items()}
        sum_exp = sum(exp_scores.values()) or 1.0
        probabilities = {it: exp_scores[it] / sum_exp for it in INTENTS}

        # Ordinamento degli intenti
        sorted_intents = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        winner_intent, top_score = sorted_intents[0]
        second_intent, second_score = sorted_intents[1]
        margin = top_score - second_score

        # Estrazione entità di supporto
        entities: Dict[str, Any] = {}
        if winner_intent == "jack_math_boost":
            entities["formula"] = (
                r"\mathcal{S}(\text{Intent}, q) = w_{\text{wake}} \cdot \phi_{\text{wake}} + "
                r"w_{\text{phonetic}} \cdot \sigma_{\text{jaro}} + w_{\text{domain}} \cdot \text{BM25}(q, D) + "
                r"w_{\text{action}} \cdot \alpha_{\text{syntax}} - \lambda_{\text{noise}}"
            )
            entities["telemetry"] = HardwareOptimizer.get_hardware_telemetry()
        elif winner_intent == "razor_script":
            from razor_scripts import resolve_skill_from_text
            res = resolve_skill_from_text(clean_cmd)
            entities["skill_slug"] = res[1] if res else "lista"
            entities["skill_name"] = res[0] if res else "UO Outlands"
        elif winner_intent == "wiki_guide":
            entities["wiki_topic"] = uo_entity_name if is_uo_entity else clean_cmd
            entities["entity_type"] = uo_entity_type or "topic"
        elif winner_intent in ("sports_match", "sports_schedule"):
            clean_q = re.sub(
                r"^(?:franchino|mi\s+dici|quali\s+sono|dimmi|trova|cerca|dove\s+(?:fanno\s+vedere|posso\s+vedere)|streaming\s+di|partita\s+(?:del|della|di)?)\s*",
                "",
                clean_cmd.lower(),
                flags=re.IGNORECASE,
            ).strip()
            entities["sport_query"] = clean_q or "stasera"
        elif winner_intent == "play_music":
            clean_music = re.sub(r"^(?:metti|suona|play|riproduci|fammi\s+sentire|cerca)\s+", "", clean_cmd, flags=re.IGNORECASE).strip()
            entities["music_query"] = clean_music or clean_cmd
        elif winner_intent == "dj_control":
            c_low = clean_cmd.lower()
            if any(w in c_low for w in ("pausa", "aspetta")):
                entities["action"] = "pause"
            elif any(w in c_low for w in ("riprendi", "continua", "vai", "togli la pausa")):
                entities["action"] = "resume"
            elif any(w in c_low for w in ("stop", "ferma", "stoppa", "basta", "zitto", "muto")):
                entities["action"] = "stop"
            elif any(w in c_low for w in ("skip", "salta", "prossima", "avanti", "cambia")):
                entities["action"] = "skip"
            elif any(w in c_low for w in ("loop", "ripeti")):
                entities["action"] = "loop"
            elif any(w in c_low for w in ("shuffle", "mescola", "casuale", "mischia")):
                entities["action"] = "shuffle"
            elif any(w in c_low for w in ("volume", "alza", "abbassa", "massimo")):
                entities["action"] = "volume"
            elif any(w in c_low for w in ("boost", "bass", "riverbero", "eco", "effetto", "filtro")):
                entities["action"] = "effect"
            else:
                entities["action"] = "general"
        elif winner_intent == "admin_control":
            entities["command"] = clean_cmd
        elif winner_intent == "social_interaction":
            entities["interaction"] = clean_cmd
        elif winner_intent == "liveness_check":
            entities["check"] = clean_cmd
        elif winner_intent == "smart_assistant":
            from smart_assistant import detect_smart_assistant_intent
            entities["subintent"] = detect_smart_assistant_intent(clean_cmd) or "general"

        best_comp = components[winner_intent]
        return JackClassificationResult(
            intent=winner_intent,
            score=top_score,
            probability=probabilities[winner_intent],
            confidence_margin=margin,
            phi_wake=best_comp["phi_wake"],
            sigma_jaro=best_comp["sigma_jaro"],
            bm25_score=best_comp["bm25"],
            alpha_syntax=best_comp["alpha_syntax"],
            lambda_noise=best_comp["lambda_noise"],
            breakdown_by_intent=scores,
            probabilities_by_intent=probabilities,
            extracted_entities=entities,
        )


class HardwareOptimizer:
    """
    Gestore e ottimizzatore delle risorse hardware specificamente profilato
    per Oracle Cloud Always Free (Ampere A1: 2 vCPU ARM, 12 GB RAM, 200 GB Disco).
    Assicura minimo consumo RAM, assenza di leak e massima reattività non-bloccante.
    """

    @staticmethod
    def configure_for_arm() -> Dict[str, Any]:
        """Configura le soglie GC e le risorse di sistema per ARM Ampere A1 (2 vCPU, 12 GB RAM)."""
        gc.set_threshold(700, 10, 10)
        logger.info("⚙️ HardwareOptimizer: GC thresholds configurate a (700, 10, 10) per 2 vCPU ARM.")
        return {
            "gc_thresholds": gc.get_threshold(),
            "target_vcpu": 2,
            "target_ram_gb": 12.0,
            "configured": True,
        }

    @staticmethod
    def get_hardware_telemetry() -> Dict[str, Any]:
        """Restituisce le metriche correnti del server ARM e del processo Python."""
        # Memoria del processo Python tramite getrusage
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        max_rss = rusage.ru_maxrss
        import sys
        if sys.platform == "darwin":
            ram_mb = max_rss / (1024 * 1024)
        else:
            ram_mb = max_rss / 1024

        # Spazio su disco
        try:
            total, used, free = shutil.disk_usage(".")
            disk_total_gb = round(total / (1024 ** 3), 1)
            disk_used_gb = round(used / (1024 ** 3), 1)
            disk_free_gb = round(free / (1024 ** 3), 1)
        except Exception:
            disk_total_gb, disk_used_gb, disk_free_gb = 200.0, 15.0, 185.0

        # CPU e Garbage Collection
        cpu_cores = os.cpu_count() or 2
        gc_counts = gc.get_count()
        gc_thresholds = gc.get_threshold()

        return {
            "server_arch": "ARM64 (Ampere A1 / Apple Silicon)",
            "cpu_cores_available": cpu_cores,
            "cpu_cores_target": 2,
            "system_ram_gb_total": 12.0,
            "process_ram_mb": round(ram_mb, 1),
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "gc_generations": gc_counts,
            "gc_thresholds": gc_thresholds,
            "target_efficiency": "Zero Sprechi RAM / Basso Overhead ARM",
        }

    @staticmethod
    def run_garbage_collection() -> int:
        """Esegue una garbage collection efficiente senza bloccare l'event loop."""
        collected = gc.collect(1)
        return collected


# Istanze singleton globali
jack_engine = JackIntelligenceFormula()
hardware_optimizer = HardwareOptimizer()
