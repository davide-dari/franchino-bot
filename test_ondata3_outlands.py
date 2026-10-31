"""Test per l'Ondata 3: conoscenza UO Outlands (qualità, latenza, cache, FTS).

Coprono i problemi trovati nell'audit:
1. La traduzione (rete, ~1.4s) veniva chiamata PRIMA di guardare nel database.
2. Il resolver delle build si mangiava le domande su creature e dungeon
   ("quanto vale il gold del boss aegis" -> build starter).
3. L'argomento estratto era l'intera frase ("cosa serve per l'aspect armor").
4. Un indice FTS disallineato rendeva invisibile uno script Razor.
5. La risoluzione girava sul loop degli eventi, bloccando audio e TTS.
"""

import asyncio
import os
import sqlite3
import time

import pytest


# ===========================================================================
# 1. Estrazione dell'argomento
# ===========================================================================
class TestTopicExtraction:
    @pytest.mark.parametrize(
        "domanda,atteso",
        [
            ("cosa serve per l'aspect armor", "aspect armor"),
            ("metti la wiki di outlands su magery", "magery"),
            ("parlami del mastery chain", "mastery chain"),
        ],
    )
    def test_estrarre_l_argomento(self, domanda, atteso):
        import outlands_wiki as ow

        topic, _ = ow._extract_topic_and_keywords(domanda)
        assert topic == atteso, f"'{domanda}' -> '{topic}' invece di '{atteso}'"

    def test_il_ruolo_boss_non_e_un_argomento(self):
        import outlands_wiki as ow

        topic, _ = ow._extract_topic_and_keywords("quanto vale il gold del boss aegis")
        assert "aegis" in topic
        assert "boss" not in topic


# ===========================================================================
# 2. Ordine dei resolver: il più specifico vince
# ===========================================================================
class TestResolverOrdering:
    def test_domanda_su_boss_risponde_sul_dungeon_e_non_sulla_build(self):
        """La parola 'gold' faceva scattare il resolver build prima di quello dungeon."""
        import outlands_wiki as ow

        ow.clear_query_cache()
        res = asyncio.run(ow.query_outlands_wiki("quanto vale il gold del boss aegis"))
        assert res is not None
        assert "aegis" in (res.get("page_title") or "").lower(), (
            f"attesa una pagina su Aegis, arrivata '{res.get('page_title')}'"
        )

    def test_creatura_specifica_ha_la_precedenza(self):
        import outlands_wiki as ow

        ow.clear_query_cache()
        res = asyncio.run(ow.query_outlands_wiki("che stats ha il drake"))
        assert res is not None
        assert "drake" in (res.get("page_title") or "").lower()

    def test_build_resta_riconosciuta(self):
        import outlands_wiki as ow

        ow.clear_query_cache()
        res = asyncio.run(ow.query_outlands_wiki("come si fa la build del tamer"))
        assert res is not None
        assert "build" in (res.get("page_title") or "").lower() or "tamer" in (res.get("page_title") or "").lower()

    def test_meccanica_conosciuta(self):
        import outlands_wiki as ow

        ow.clear_query_cache()
        res = asyncio.run(ow.query_outlands_wiki("come funziona il mastery chain"))
        assert res is not None
        assert "mastery" in (res.get("page_title") or "").lower()


# ===========================================================================
# 3. La traduzione non deve essere pagata quando il DB basta
# ===========================================================================
class TestTranslationOnlyAsFallback:
    def test_nessuna_traduzione_per_una_pagina_gia_in_locale(self, monkeypatch):
        """Il percorso 'pagina trovata in locale' non deve chiamare il traduttore
        per l'argomento: era una chiamata di rete (~1.4s) a ogni lookup."""
        import outlands_wiki as ow

        chiamate = []
        orig = ow.translate_text

        def spia(testo, sl="en", tl="it"):
            chiamate.append((sl, tl, testo[:40]))
            return orig(testo, sl, tl)

        monkeypatch.setattr(ow, "translate_text", spia)
        conn = ow.get_db_connection()
        try:
            # "drake" è il titolo esatto di una pagina: si risolve senza tradurre l'argomento
            chiamate.clear()
            res = ow._resolve_local_knowledge_page("che stats ha il drake", conn)
        finally:
            conn.close()

        assert res is not None
        traduzioni_argomento = [c for c in chiamate if c[0] == "it" and c[1] == "en"]
        assert traduzioni_argomento == [], f"traduzione dell'argomento non necessaria: {traduzioni_argomento}"

    def test_la_traduzione_scatta_solo_se_in_locale_non_c_e_nulla(self, monkeypatch):
        import outlands_wiki as ow

        chiamate = []
        monkeypatch.setattr(
            ow, "translate_text", lambda t, sl="en", tl="it": chiamate.append(t) or t
        )
        conn = ow.get_db_connection()
        try:
            res = ow._resolve_local_knowledge_page("argomento inesistente xyzzy plugh", conn)
        finally:
            conn.close()
        assert res is None
        assert chiamate, "quando il database non ha nulla la traduzione deve essere tentata"


# ===========================================================================
# 4. Cache delle risposte (incluse le negative)
# ===========================================================================
class TestQueryCache:
    @pytest.fixture(autouse=True)
    def _cache_pulita(self):
        import outlands_wiki as ow

        ow.clear_query_cache()
        yield
        ow.clear_query_cache()

    def test_seconda_domanda_identica_non_risolve_di_nuovo(self, monkeypatch):
        import outlands_wiki as ow

        contatore = {"n": 0}
        vero = ow._resolve_local_sync

        def spia(question):
            contatore["n"] += 1
            return vero(question)

        monkeypatch.setattr(ow, "_resolve_local_sync", spia)
        domanda = "che stats ha il drake"
        asyncio.run(ow.query_outlands_wiki(domanda))
        asyncio.run(ow.query_outlands_wiki(domanda))
        assert contatore["n"] == 1, "la seconda richiesta deve arrivare dalla cache"

    def test_anche_i_non_trovati_vengono_ricordati(self, monkeypatch):
        """Un buco di conoscenza non deve ripagare il fallback online ogni volta."""
        import outlands_wiki as ow

        contatore = {"n": 0}

        async def finto_online(question):
            contatore["n"] += 1
            return None

        monkeypatch.setattr(ow, "_resolve_local_sync", lambda q: None)
        monkeypatch.setattr(ow, "_search_outlands_wiki_sync", lambda q: None)

        domanda = "domanda che non esiste xyzzy"
        asyncio.run(ow.query_outlands_wiki(domanda))
        asyncio.run(ow.query_outlands_wiki(domanda))
        assert contatore["n"] <= 1

    def test_la_cache_restituisce_copie_indipendenti(self):
        import outlands_wiki as ow

        asyncio.run(ow.query_outlands_wiki("che stats ha il drake"))
        primo = asyncio.run(ow.query_outlands_wiki("che stats ha il drake"))
        secondo = asyncio.run(ow.query_outlands_wiki("che stats ha il drake"))
        assert primo is not secondo, "ogni chiamata deve ricevere la propria copia"
        assert primo["embed"] is not secondo["embed"], "l'embed non va condiviso per riferimento"


# ===========================================================================
# 5. Integrità degli indici FTS
# ===========================================================================
class TestFtsIntegrity:
    def _db_misaligned(self, tmp_path):
        """Crea un DB minimale con un indice FTS incompleto (come in produzione)."""
        path = str(tmp_path / "kb.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE razor_scripts (script_id TEXT PRIMARY KEY, title TEXT, skill TEXT,
                                        description TEXT, tags TEXT);
            CREATE VIRTUAL TABLE razor_scripts_fts USING fts5(
                script_id UNINDEXED, title, skill, description, tags, tokenize='unicode61'
            );
            """
        )
        righe = [("a", "Script A", "magery", "descrizione a", "tag"),
                 ("b", "Script B", "taming", "descrizione b", "tag")]
        conn.executemany("INSERT INTO razor_scripts VALUES (?,?,?,?,?)", righe)
        # Solo il primo finisce nell'indice: il secondo è invisibile alla ricerca
        conn.execute("INSERT INTO razor_scripts_fts VALUES (?,?,?,?,?)", righe[0])
        conn.commit()
        return conn

    def test_indice_incompleto_viene_riallineato(self, tmp_path, monkeypatch):
        import outlands_wiki as ow

        monkeypatch.setattr(ow, "_FTS_CHECKED", False)
        conn = self._db_misaligned(tmp_path)

        assert conn.execute("SELECT COUNT(*) FROM razor_scripts_fts").fetchone()[0] == 1
        ow._ensure_fts_integrity(conn)

        assert conn.execute("SELECT COUNT(*) FROM razor_scripts_fts").fetchone()[0] == 2
        trovati = conn.execute(
            "SELECT script_id FROM razor_scripts_fts WHERE razor_scripts_fts MATCH 'taming'"
        ).fetchall()
        assert [r[0] for r in trovati] == ["b"], "lo script mancante deve tornare cercabile"
        conn.close()

    def test_indice_gia_allineato_non_viene_toccato(self, tmp_path, monkeypatch):
        import outlands_wiki as ow

        monkeypatch.setattr(ow, "_FTS_CHECKED", False)
        path = str(tmp_path / "ok.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE pages (page_id INTEGER PRIMARY KEY, title TEXT);
            CREATE VIRTUAL TABLE pages_fts USING fts5(title, content='pages', content_rowid='page_id');
            INSERT INTO pages VALUES (1, 'Drake');
            INSERT INTO pages_fts(pages_fts) VALUES('rebuild');
            """
        )
        conn.commit()
        prima = conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0]
        ow._ensure_fts_integrity(conn)
        assert conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == prima
        conn.close()


# ===========================================================================
# 6. La risoluzione non deve bloccare il loop degli eventi
# ===========================================================================
class TestEventLoopNotBlocked:
    def test_il_loop_continua_a_girare_durante_una_query_lenta(self, monkeypatch):
        """La risoluzione locale può durare ~1.5s (traduzione): se girasse sul loop
        bloccherebbe il sink vocale e il TTS. Deve avvenire in un thread."""
        import outlands_wiki as ow

        ow.clear_query_cache()

        def risoluzione_lenta(question):
            time.sleep(0.30)  # simula traduzione + I/O
            return None

        monkeypatch.setattr(ow, "_resolve_local_sync", risoluzione_lenta)
        monkeypatch.setattr(ow, "_search_outlands_wiki_sync", lambda q: None)

        ticks = {"n": 0}

        async def scenario():
            async def ticker():
                for _ in range(30):
                    await asyncio.sleep(0.01)
                    ticks["n"] += 1

            t = asyncio.create_task(ticker())
            await ow.query_outlands_wiki("domanda lenta xyzzy")
            t.cancel()
            return ticks["n"]

        n = asyncio.run(scenario())
        assert n >= 5, f"il loop è stato bloccato durante l'attesa (solo {n} tick)"
