"""Test di non-regressione per l'Ondata 1 di ottimizzazione.

Riferimento: docs/AUDIT_PERFORMANCE_E_INTELLIGENZA.md
Coprono: fix playlist YouTube, token budget/streaming del cervello AI, cache dello
status Ollama e telemetria di latenza per stadio.
"""

import asyncio
import json
import time
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 1. Playlist YouTube: il blocco di costruzione SongInfo era irraggiungibile
# ---------------------------------------------------------------------------

class TestYoutubePlaylistFix:
    def _manager(self):
        import sources
        sm = sources.SourceManager.__new__(sources.SourceManager)
        sm.ydl_opts_flat = {"extract_flat": True}
        sm.ydl_opts_stream = {}
        return sm

    def test_playlist_restituisce_i_brani(self):
        import sources
        sm = self._manager()
        fake_info = {
            "entries": [
                {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "Brano A", "duration": 100},
                {"id": "bbbbbbbbbbb", "title": "Brano B", "duration": 200},
                None,  # entries private/eliminate
            ]
        }
        fake_ydl = MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = fake_info
        with patch.object(sources.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            songs = sm._extract_youtube_playlist("https://www.youtube.com/playlist?list=XYZ", None)

        assert songs is not None, "la playlist non deve più restituire None"
        assert len(songs) == 2
        assert songs[0].title == "Brano A"
        assert songs[0].webpage_url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
        assert songs[0].stream_url is None  # estratto on demand
        assert songs[1].webpage_url == "https://www.youtube.com/watch?v=bbbbbbbbbbb"

    def test_playlist_senza_info_solleva(self):
        import sources
        sm = self._manager()
        fake_ydl = MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = None
        with patch.object(sources.yt_dlp, "YoutubeDL", return_value=fake_ydl):
            with pytest.raises(Exception):
                sm._extract_youtube_playlist("https://www.youtube.com/playlist?list=XYZ", None)


# ---------------------------------------------------------------------------
# 2. Cervello AI: budget token, streaming con uscita anticipata, cache status
# ---------------------------------------------------------------------------

class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    def iter_any(self):
        async def _gen():
            for c in self._chunks:
                if isinstance(c, Exception):
                    raise c
                yield c
        return _gen()


class _FakeResp:
    def __init__(self, status=200, payload=None, chunks=None):
        self.status = status
        self._payload = payload or {}
        self.content = _FakeContent(chunks or [])

    async def json(self):
        return self._payload


class _FakePost:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.posts = 0
        self.gets = 0

    def post(self, *a, **k):
        self.posts += 1
        return _FakePost(self.resp)

    def get(self, *a, **k):
        self.gets += 1
        return _FakePost(self.resp)


def _ndjson(content_piece: str, done: bool = False) -> bytes:
    return (json.dumps({"message": {"content": content_piece}, "done": done}) + "\n").encode()


class TestBrainTokenBudget:
    def test_budget_allineato_al_target_arm(self):
        import brain as brain_mod
        assert brain_mod.BRAIN_NUM_PREDICT <= 160, "num_predict deve stare nel budget di generazione"
        assert brain_mod.BRAIN_NUM_CTX <= 4096, "num_ctx alto = KV cache grande e prompt eval lento"
        assert brain_mod.BRAIN_CHAT_TIMEOUT >= 8.0, "il JSON completo non arriva in 4.5s su 2 vCPU"
        assert brain_mod.BRAIN_STATUS_TTL > 0, "lo status di Ollama deve essere in cache"

    def test_payload_usa_streaming_e_num_ctx(self):
        """process_user_speech deve inviare stream=True, num_ctx esplicito e keep_alive."""
        brain_mod = pytest.importorskip("brain")
        b = brain_mod.FranchinoBrain()
        captured = {}

        async def _fake_stream(payload, text, user_name):
            captured.update(payload)
            return {"intent": "chat", "chat_reply": "ok", "voice_reply": ""}

        b._check_ollama_status = AsyncMock(return_value=True)
        b._stream_decision = _fake_stream
        with patch.object(brain_mod.hub, "archive_interaction", new=AsyncMock()):
            decision = asyncio.run(b.process_user_speech("Marco", "1", "che ore sono"))

        assert decision["intent"] == "chat"
        assert captured.get("stream") is True
        opts = captured.get("options", {})
        assert opts.get("num_ctx") == brain_mod.BRAIN_NUM_CTX
        assert opts.get("num_predict") == brain_mod.BRAIN_NUM_PREDICT
        assert captured.get("keep_alive")

    def test_json_troncato_ricade_sull_euristica(self):
        """Se il modello non chiude il JSON, la decisione resta quella deterministica."""
        brain_mod = pytest.importorskip("brain")
        b = brain_mod.FranchinoBrain()
        b._check_ollama_status = AsyncMock(return_value=True)
        b._stream_decision = AsyncMock(return_value=None)
        with patch.object(brain_mod.hub, "archive_interaction", new=AsyncMock()):
            decision = asyncio.run(b.process_user_speech("Marco", "1", "che ore sono"))

        expected = brain_mod.brain._fallback_heuristic_intent("che ore sono", "Marco")
        assert decision.get("intent") == expected.get("intent")


class TestBrainStreaming:
    def test_esce_appena_il_json_e_completo(self):
        """Non deve attendere la fine della generazione: la coda non va consumata."""
        brain_mod = pytest.importorskip("brain")
        b = brain_mod.FranchinoBrain()
        # Il JSON viene chiuso nel terzo chunk; il quarto chunk solleva se letto.
        chunks = [
            _ndjson('{"intent": "play_music", "music_'),
            _ndjson('query": "caparezza", "chat_reply": "ok"'),
            _ndjson('}'),
            RuntimeError("la lettura doveva fermarsi prima di questo chunk"),
        ]
        b._get_session = lambda: _FakeSession(_FakeResp(chunks=chunks))

        async def _run():
            return await b._stream_decision({"model": b.model}, "metti caparezza", "Marco")

        decision = asyncio.run(_run())
        assert decision is not None
        assert decision["intent"] == "play_music"
        assert decision["music_query"] == "caparezza"

    def test_json_troncato_restituisce_none(self):
        """Generazione interrotta => None, così il chiamante usa l'euristica deterministica."""
        brain_mod = pytest.importorskip("brain")
        b = brain_mod.FranchinoBrain()
        chunks = [_ndjson('{"intent": "chat", "chat_re'), _ndjson("", done=True)]
        b._get_session = lambda: _FakeSession(_FakeResp(chunks=chunks))

        async def _run():
            return await b._stream_decision({"model": b.model}, "ciao", "Marco")

        assert asyncio.run(_run()) is None


class TestOllamaStatusCache:
    def test_status_in_cache_evita_il_round_trip(self):
        brain_mod = pytest.importorskip("brain")
        b = brain_mod.FranchinoBrain()
        fake = _FakeSession(_FakeResp(payload={"models": []}))
        b._get_session = lambda: fake

        async def _run():
            first = await b._check_ollama_status()
            second = await b._check_ollama_status()
            third = await b._check_ollama_status()
            forced = await b._check_ollama_status(force=True)
            return first, second, third, forced

        first, second, third, forced = asyncio.run(_run())
        assert (first, second, third, forced) == (True, True, True, True)
        assert fake.gets == 2, "solo il primo controllo e quello forzato devono fare GET"


# ---------------------------------------------------------------------------
# 3. Endpointing vocale: meno silenzio atteso prima di trascrivere
# ---------------------------------------------------------------------------

class TestEndpointing:
    def _costante(self, nome):
        import ast
        import os
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_commands.py")
        tree = ast.parse(open(src, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "FranchinoVoiceSink":
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign) and getattr(stmt.targets[0], "id", "") == nome:
                        return ast.literal_eval(stmt.value)
        raise AssertionError(f"{nome} non trovato in FranchinoVoiceSink")

    def test_silenzio_di_fine_frase_entro_500ms(self):
        assert self._costante("SILENCE_THRESHOLD_SEC") <= 0.50

    def test_non_taglia_le_frasi_brevi(self):
        # Il pre-roll (500ms) e la soglia minima di frase devono restare invariati
        assert self._costante("PREROLL_CHUNKS") >= 25
        assert self._costante("MIN_PHRASE_SEC") <= 0.50


# ---------------------------------------------------------------------------
# 4. docker-compose: i limiti CPU non devono superare i vCPU reali
# ---------------------------------------------------------------------------

class TestComposeCpuBudget:
    def _compose(self):
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docker-compose.yml")
        return open(path, encoding="utf-8").read()

    def test_limiti_cpu_entro_i_2_vcpu_reali(self):
        text = self._compose()
        limiti = []
        in_limits = False
        limits_indent = 0
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "limits:":
                in_limits = True
                limits_indent = len(line) - len(line.lstrip())
                continue
            if in_limits:
                indent = len(line) - len(line.lstrip())
                if stripped and indent <= limits_indent:
                    in_limits = False
                elif stripped.startswith("cpus:"):
                    limiti.append(float(stripped.split(":", 1)[1].strip().strip('"')))
        assert len(limiti) == 3, f"attesi 3 limiti cpu (db, ollama, bot), trovati {limiti}"
        assert sum(limiti) <= 2.0 + 1e-9, f"somma dei limiti CPU {sum(limiti)} > 2 vCPU: oversubscription"

    def test_ollama_tuning_arm(self):
        text = self._compose()
        for chiave in (
            "OLLAMA_FLASH_ATTENTION=1",
            "OLLAMA_KV_CACHE_TYPE=q8_0",
            "OLLAMA_NUM_THREADS=2",
            "OLLAMA_KEEP_ALIVE=24h",
        ):
            assert chiave in text, f"manca {chiave} nel servizio ollama"


# ---------------------------------------------------------------------------
# 5. TTS: cache distinta per motore e preriscaldamento delle frasi canoniche
# ---------------------------------------------------------------------------

class TestTtsCacheWarm:
    def test_edge_e_gtts_non_condividono_il_file(self, tmp_path, monkeypatch):
        """Un file gTTS ripreso dalla cache deve restare riconoscibile come gTTS.

        Prima esisteva un solo nome per entrambi i motori: al secondo ascolto il
        pitch-shift di correzione (applicato solo ai file gTTS) veniva perso.
        """
        player_mod = pytest.importorskip("player")
        monkeypatch.setattr(player_mod, "TTS_CACHE_DIR", str(tmp_path))

        edge = player_mod.tts_cache_path("Ciao ragazzi", False, gtts=False)
        gtts = player_mod.tts_cache_path("Ciao ragazzi", False, gtts=True)
        assert edge != gtts

        with open(gtts, "wb") as fh:
            fh.write(b"\0" * 4096)

        found, used_gtts = player_mod.find_cached_tts("Ciao ragazzi", False)
        assert found == gtts
        assert used_gtts is True

        # La chiave è normalizzata (maiuscole/spazi): stessa cache
        found2, used2 = player_mod.find_cached_tts("  ciao RAGAZZI ", False)
        assert found2 == gtts and used2 is True

    def test_warm_salta_le_frasi_gia_generate(self, tmp_path, monkeypatch):
        player_mod = pytest.importorskip("player")
        monkeypatch.setattr(player_mod, "TTS_CACHE_DIR", str(tmp_path))

        chiamate = []

        async def _fake_generate(text, dramatic_fable=False):
            chiamate.append(text)
            path = player_mod.tts_cache_path(text, dramatic_fable)
            with open(path, "wb") as fh:
                fh.write(b"\0" * 4096)
            return path, False

        monkeypatch.setattr(player_mod, "generate_tts_file", _fake_generate)

        gia_pronta = player_mod.tts_cache_path("Magia! Dimmi tutto, sono qui in console!")
        with open(gia_pronta, "wb") as fh:
            fh.write(b"\0" * 4096)

        generati = asyncio.run(
            player_mod.warm_tts_cache(("Magia! Dimmi tutto, sono qui in console!", "Dimmi il titolo!"))
        )
        assert generati == 1
        assert chiamate == ["Dimmi il titolo!"]

    def test_frasi_canoniche_coperte_dal_warm(self):
        player_mod = pytest.importorskip("player")
        assert len(player_mod.WARM_TTS_PHRASES) >= 3
        assert all(isinstance(p, str) and p.strip() for p in player_mod.WARM_TTS_PHRASES)


# ---------------------------------------------------------------------------
# 6. Telemetria di latenza per stadio
# ---------------------------------------------------------------------------

class TestLatencyTelemetry:
    def test_riga_riassuntiva_con_tutti_gli_stadi(self, caplog):
        import logging
        from latency import LatencyTelemetry

        lat = LatencyTelemetry()
        trace = lat.start(src="voice", user="Marco")
        trace.mark("stt")
        lat.mark("jack")
        lat.set_intent("smart_assistant")
        lat.note("engine", "gemma2:2b")

        with caplog.at_level(logging.INFO, logger="Franchino.Latency"):
            riga = lat.finish()

        assert "src=voice" in riga
        assert "user=Marco" in riga
        assert "intent=smart_assistant" in riga
        assert "stt=" in riga and "jack=" in riga
        assert "engine=gemma2:2b" in riga
        assert "total=" in riga
        assert any("req=" in r.message for r in caplog.records)

    def test_no_op_senza_traccia_attiva(self):
        from latency import LatencyTelemetry

        lat = LatencyTelemetry()
        lat.mark("stt")
        lat.set_intent("chat")
        lat.note("engine", "x")
        assert lat.finish() is None

    def test_stadio_tardo_arriva_dopo_la_chiusura(self, caplog):
        import logging
        from latency import LatencyTelemetry

        lat = LatencyTelemetry()
        trace = lat.start(src="voice")
        trace.mark("stt")
        lat.finish()

        with caplog.at_level(logging.INFO, logger="Franchino.Latency"):
            trace.mark("first_audio")  # il TTS parte in background, dopo la chiusura
        assert any("late first_audio=" in r.message for r in caplog.records)

    def test_stadio_lento_finisce_in_warning(self, caplog):
        import logging
        import time as _time
        from latency import LatencyTelemetry

        lat = LatencyTelemetry()
        trace = lat.start(src="voice")
        _time.sleep(0.02)
        trace.mark("jack")  # soglia jack = 50 ms: 20 ms non basta -> nessun warning
        with caplog.at_level(logging.INFO, logger="Franchino.Latency"):
            lat.finish()
        assert not any("STADI LENTI" in r.message for r in caplog.records)
