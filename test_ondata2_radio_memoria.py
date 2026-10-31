"""Test per l'Ondata 2: radio italiane, memoria dei comportamenti scorretti,
preferenza YouTube/Spotify sul motore musicale.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# 1. Radio: riconoscimento dell'emittente
# ===========================================================================
class TestRadioRecognition:
    @pytest.mark.parametrize(
        "frase,atteso",
        [
            ("metti m2o", "m2o"),
            ("hey franchino metti m2o", "m2o"),
            ("metti m due o", "m2o"),
            ("metti emme due o", "m2o"),
            ("metti la radio deejay", "deejay"),
            ("metti deejay", "deejay"),
            ("metti rtl 102.5", "rtl1025"),
            ("metti rtl", "rtl1025"),
            ("metti radio italia", "radioitalia"),
            ("metti 105", "radio105"),
            ("metti radio 105", "radio105"),
            ("metti virgin radio", "virgin"),
            ("metti radio capital", "capital"),
            ("metti radio kiss kiss", "kisskiss"),
            ("metti rds", "rds"),
            ("metti radio monte carlo", "rmc"),
            ("metti r101", "r101"),
            ("metti radio zeta", "zeta"),
            ("metti discoradio", "discoradio"),
            ("metti radio 24", "radio24"),
        ],
    )
    def test_riconosce_le_radio(self, frase, atteso):
        import radio

        st = radio.find_radio(frase)
        assert st is not None, f"'{frase}' non ha riconosciuto nessuna radio"
        assert st.key == atteso, f"'{frase}' ha riconosciuto {st.key} invece di {atteso}"

    @pytest.mark.parametrize(
        "frase",
        ["metti caparezza", "metti una canzone di vasco", "ferma la musica", "che ore sono"],
    )
    def test_non_scambia_brani_per_radio(self, frase):
        import radio

        assert radio.find_radio(frase) is None

    def test_alias_piu_lungo_vince(self):
        import radio

        # "radio kiss kiss" non deve collidere con "kiss"
        assert radio.find_radio("metti kiss kiss").key == "kisskiss"
        assert radio.find_radio("metti kiss").key == "kisskiss"

    def test_richiesta_generica_di_radio(self):
        import radio

        assert radio.wants_generic_radio("franchino metti una radio") is True
        assert radio.wants_generic_radio("che radio ci sono") is True
        assert radio.wants_generic_radio("metti m2o") is False

    def test_nome_radio_fuori_catalogo(self):
        import radio

        assert radio.extract_radio_name("metti radio gamma") == "gamma"
        assert radio.extract_radio_name("metti radio accesa") is None
        assert radio.extract_radio_name("metti caparezza") is None

    def test_normalizzazione_accenti_e_punteggiatura(self):
        import radio

        assert radio.normalize("Radio Città") == "radio citta"
        assert radio.normalize("RTL 102.5") == "rtl 102 5"
        assert radio.normalize("Rock & Roll") == "rock e roll"


# ===========================================================================
# 2. Radio: risoluzione del flusso (senza rete, con probe/API simulate)
# ===========================================================================
class TestRadioStreamResolution:
    @pytest.fixture(autouse=True)
    def _cache_isolata(self, monkeypatch, tmp_path):
        """Isola la cache su disco: un vecchio radio_streams.json non deve
        influenzare i test (e viceversa i test non devono sporcare data/)."""
        import radio

        monkeypatch.setattr(radio, "CACHE_PATH", str(tmp_path / "radio_streams.json"))
        monkeypatch.setattr(radio, "_cache", radio._RadioCache())

    def test_catalogo_usa_gli_url_verificati(self, monkeypatch):
        import radio

        monkeypatch.setattr(radio, "_cache", radio._RadioCache())
        chiamate = []

        async def fake_probe(url):
            chiamate.append(url)
            return url

        monkeypatch.setattr(radio, "_probe", fake_probe)
        st = radio.CATALOG_BY_KEY["m2o"]
        stream = asyncio.run(radio.resolve_stream(st))

        assert stream is not None
        assert stream.url == st.hints[0]
        assert stream.hls is True
        assert chiamate == [st.hints[0]]

    def test_fallback_api_quando_il_catalogo_e_morto(self, monkeypatch):
        import radio

        monkeypatch.setattr(radio, "_cache", radio._RadioCache())

        async def fake_probe(url):
            return None  # tutti gli URL del catalogo sono morti

        async def fake_api(key, name):
            return {
                "url": "https://example.invalid/live.m3u8",
                "name": name,
                "codec": "AAC",
                "bitrate": 128,
                "hls": True,
                "homepage": "https://example.invalid",
                "favicon": "",
            }

        monkeypatch.setattr(radio, "_probe", fake_probe)
        monkeypatch.setattr(radio, "_resolve_from_api", fake_api)

        st = radio.CATALOG_BY_KEY["rtl1025"]
        stream = asyncio.run(radio.resolve_stream(st))
        assert stream is not None
        assert stream.url == "https://example.invalid/live.m3u8"
        assert stream.codec == "AAC"

    def test_radio_fuori_catalogo_per_nome(self, monkeypatch):
        import radio

        monkeypatch.setattr(radio, "_cache", radio._RadioCache())

        async def fake_api(key, name):
            return {"url": "https://example.invalid/gamma.aac", "name": name, "codec": "MP3",
                    "bitrate": 96, "hls": False, "homepage": "", "favicon": ""}

        monkeypatch.setattr(radio, "_resolve_from_api", fake_api)
        stream = asyncio.run(radio.resolve_stream(name="gamma"))
        assert stream is not None and "gamma" in stream.url

    def test_nessun_flusso_restituisce_none(self, monkeypatch):
        import radio

        monkeypatch.setattr(radio, "_cache", radio._RadioCache())

        async def dead_probe(url):
            return None

        async def no_api(key, name):
            return None

        monkeypatch.setattr(radio, "_probe", dead_probe)
        monkeypatch.setattr(radio, "_resolve_from_api", no_api)
        assert asyncio.run(radio.resolve_stream(radio.CATALOG_BY_KEY["m2o"])) is None

    def test_cache_evita_lookups_ripetuti(self, monkeypatch):
        import radio

        monkeypatch.setattr(radio, "_cache", radio._RadioCache())
        contatore = {"probe": 0}

        async def fake_probe(url):
            contatore["probe"] += 1
            return url

        monkeypatch.setattr(radio, "_probe", fake_probe)
        st = radio.CATALOG_BY_KEY["m2o"]

        async def due_volte():
            await radio.resolve_stream(st)
            await radio.resolve_stream(st)

        asyncio.run(due_volte())
        assert contatore["probe"] == 1, "il secondo resolve deve arrivare dalla cache"

    def test_playlist_pls_viene_espansa(self, monkeypatch):
        """Un .pls non è audio: va espanso al primo URL http contenuto."""
        import radio

        pls = "[playlist]\nNumberOfEntries=1\nFile1=http://stream.example.invalid/live.mp3\nTitle1=Test\n"

        class FakeContent:
            async def read(self, n=-1):
                return pls.encode()

        class FakeResp:
            status = 200
            url = "http://example.invalid/listen.pls"

            def __init__(self):
                self.content = FakeContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class FakeSession:
            def get(self, *a, **k):
                return FakeResp()

        monkeypatch.setattr(radio, "_get_session", lambda: FakeSession())
        assert asyncio.run(radio._probe("http://example.invalid/listen.pls")) == "http://stream.example.invalid/live.mp3"

    def test_m3u8_non_viene_espanso(self, monkeypatch):
        """Un .m3u8 è un flusso HLS: ffmpeg lo consuma direttamente."""
        import radio

        class FakeContent:
            async def read(self, n=-1):
                return b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nchunklist.m3u8\n"

        class FakeResp:
            status = 200
            url = "https://example.invalid/live/master.m3u8"

            def __init__(self):
                self.content = FakeContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        class FakeSession:
            def get(self, *a, **k):
                return FakeResp()

        monkeypatch.setattr(radio, "_get_session", lambda: FakeSession())
        url = asyncio.run(radio._probe("https://example.invalid/live/master.m3u8"))
        assert url == "https://example.invalid/live/master.m3u8"

    def test_catalogo_non_ha_chiavi_duplicate(self):
        import radio

        chiavi = [st.key for st in radio.RADIO_CATALOG]
        assert len(chiavi) == len(set(chiavi))
        assert len(chiavi) >= 15
        for st in radio.RADIO_CATALOG:
            assert st.name and st.homepage and st.genre and st.emoji


# ===========================================================================
# 3. Memoria: riconoscimento delle offese
# ===========================================================================
class TestOffenseDetection:
    @pytest.mark.parametrize(
        "frase",
        [
            "sei un coglione",
            "Franchino sei un cretino",
            "vaffanculo franchino",
            "fai schifo",
            "non capisci niente",
            "sei una pippa",
            "ti ammazzo",
            "vattene",
            "esci",
            "sparisci",
        ],
    )
    def test_riconosce_le_offese(self, frase):
        import memoria

        off = memoria.detect_offense(frase)
        assert off is not None, f"'{frase}' non riconosciuta come offesa"
        assert off.kind in (
            memoria.KIND_INSULTO,
            memoria.KIND_MINACCIA,
            memoria.KIND_SFOTTO,
            memoria.KIND_CONGEDO,
        )

    @pytest.mark.parametrize(
        "frase",
        [
            "metti m2o",
            "ciao franchino",
            "sei un grande",
            "sei il migliore franchino",
            "che ore sono",
            "metti caparezza",
            "stop",
            "pausa",
            "grazie mille",
            "come stai",
        ],
    )
    def test_non_riconosce_frasi_legittime(self, frase):
        import memoria

        assert memoria.detect_offense(frase) is None, f"'{frase}' scambiata per offesa"

    def test_complimento_non_e_offesa_anche_con_parole_simili(self):
        import memoria

        # "inutile" è nella lista insulti, ma dentro un complimento non conta
        assert memoria.detect_offense("sei un grande, non sei inutile") is None

    def test_pesi_per_categoria(self):
        import memoria

        assert memoria.detect_offense("vaffanculo").weight == memoria.PESI[memoria.KIND_INSULTO]
        assert memoria.detect_offense("vattene").weight == memoria.PESI[memoria.KIND_CONGEDO]
        assert memoria.detect_offense("fai schifo").weight == memoria.PESI[memoria.KIND_SFOTTO]


# ===========================================================================
# 4. Memoria: profilo, livelli, decadimento, persistenza, risposte
# ===========================================================================
class TestMemoriaProfile:
    @pytest.fixture(autouse=True)
    def _store_isolato(self, tmp_path):
        import memoria

        self.path = str(tmp_path / "franchino_memoria.json")
        self.m = memoria.Memoria(path=self.path)
        yield
        memoria.memoria  # istanza globale intatta

    def _offesa(self, testo):
        import memoria

        off = memoria.detect_offense(testo)
        assert off is not None, f"'{testo}' non riconosciuta"
        return off

    def test_utente_sconosciuto_non_ha_storico(self):
        assert self.m.get("999") is None
        assert self.m.level("999") == 0
        assert self.m.prompt_note("999") == ""

    def test_congedi_ripetuti_fanno_scattare_il_livello(self):
        uid = "1"
        # Un solo "esci" è leggero: non basta per il livello 1
        self.m.record(uid, "Marco", self._offesa("esci"))
        assert self.m.level(uid) == 0
        self.m.record(uid, "Marco", self._offesa("vattene"))
        assert self.m.get(uid).points >= 2.0
        assert self.m.level(uid) >= 1

    def test_insulto_porta_subito_al_livello_alto(self):
        uid = "2"
        for _ in range(3):
            self.m.record(uid, "Luca", self._offesa("sei un coglione"))
        rec = self.m.get(uid)
        assert rec.count == 3
        assert rec.points >= 8.0
        assert rec.level == 3

    def test_il_profilo_ricorda_chi_e_la_frase(self):
        uid = "3"
        self.m.record(uid, "Sara", self._offesa("sei un cretino"))
        rec = self.m.get(uid)
        assert rec.name == "Sara"
        assert rec.last_kind == "insulto"
        assert rec.examples and "cretino" in rec.examples[0]
        assert rec.kinds["insulto"] == 1

    def test_il_decadimento_dimezza_i_punti_in_24h(self):
        import time as _time

        uid = "4"
        self.m.record(uid, "Anna", self._offesa("sei un coglione"))
        punti_iniziali = self.m.get(uid).points
        # Simula 24 ore di silenzio riscrivendo il timestamp dell'ultima offesa
        self.m._data[uid]["last"] = _time.time() - 24 * 3600
        rec = self.m.get(uid)
        assert rec is not None
        assert abs(rec.points - punti_iniziali / 2) < 0.2, "dopo 24h i punti devono dimezzarsi"

    def test_persistenza_su_disco(self):
        import memoria

        uid = "5"
        self.m.record(uid, "Paolo", self._offesa("fai schifo"))
        altro = memoria.Memoria(path=self.path)
        rec = altro.get(uid)
        assert rec is not None and rec.name == "Paolo" and rec.count == 1

    def test_scuse_cancellano_lo_storico(self):
        uid = "6"
        self.m.record(uid, "Elena", self._offesa("sei un idiota"))
        assert self.m.forget(uid) is True
        assert self.m.get(uid) is None
        assert self.m.forget(uid) is False

    def test_risposta_a_tono_scala_con_il_livello(self):
        uid = "7"
        chat1, voce1 = self.m.reply(self.m.record(uid, "Marco", self._offesa("sei un cretino")))
        for _ in range(4):
            rec = self.m.record(uid, "Marco", self._offesa("sei un cretino"))
        chat3, voce3 = self.m.reply(rec)
        assert chat1 and voce1 and chat3 and voce3
        assert chat1 != chat3, "il tono deve cambiare con il livello"
        assert len(voce3) < 200, "la replica vocale deve restare breve per il TTS"
        assert "Marco" in chat3

    def test_nota_prompt_passa_il_contesto_al_cervello(self):
        uid = "8"
        self.m.record(uid, "Gino", self._offesa("sei un coglione"))
        nota = self.m.prompt_note(uid)
        assert "MEMORIA" in nota and "1 volte" in nota


# ===========================================================================
# 5. Motore musicale: fast-fail YouTube, PO token, Spotify, radio
# ===========================================================================
BOT_CHECK = "ERROR: [youtube] xyz: Sign in to confirm you're not a bot. Use --cookies"


def _song(titolo="Traccia", source_type="youtube", stream="http://stream.example/a.mp3"):
    from sources import SongInfo

    return SongInfo(
        title=titolo,
        webpage_url="https://example.invalid/watch",
        stream_url=stream,
        duration=100,
        thumbnail=None,
        uploader=None,
        requester=None,
        source_type=source_type,
        is_direct_stream=True,
    )


class TestYoutubeFastFail:
    def _manager(self):
        import sources

        sm = sources.SourceManager()
        sm._cache_song = lambda *a, **k: None
        return sm

    def test_bot_check_sui_cookie_prova_i_client_mobili_poi_fallisce_rapido(self, monkeypatch):
        """Auto-heal: cookie bloccati → client mobili. Ma niente download né ricerca flat.

        Prima si facevano 4 tentativi per lo stesso errore (22.5s): ora sono 2,
        perché il download locale e la ricerca diretta non possono aggirare un
        bot-check che ha già bloccato i client.
        """
        import sources

        sm = self._manager()
        tentativi = []

        def fake_extract(target, requester, opts, download=False):
            tentativi.append({"download": download, "cookie": "cookiefile" in opts})
            raise RuntimeError(BOT_CHECK)

        monkeypatch.setattr(sm, "_do_extract_single", fake_extract)
        monkeypatch.setattr(sm, "_extract_soundcloud_fallback", lambda *a, **k: _song("SC", "soundcloud"))

        song = sm._extract_youtube_single("ytsearch1:caparezza", None)

        assert song.source_type == "soundcloud"
        assert len(tentativi) == 2, f"attesi 2 tentativi (cookie + client), fatti {len(tentativi)}"
        assert tentativi[0]["cookie"] is True
        assert tentativi[1]["cookie"] is False, "il secondo tentativo deve usare i client senza cookie"
        assert not any(t["download"] for t in tentativi), "il download locale non deve essere tentato"
        assert sm._youtube_blocked_until > 0
        assert sm._youtube_bot_hits == 1

    def test_dopo_due_bot_check_nessun_tentativo(self, monkeypatch):
        """Al secondo giro bloccato scatta il blocco duro: si va diretti al fallback."""
        sm = self._manager()
        chiamate = []

        def fake_extract(target, requester, opts, download=False):
            chiamate.append(1)
            raise RuntimeError(BOT_CHECK)

        monkeypatch.setattr(sm, "_do_extract_single", fake_extract)
        monkeypatch.setattr(sm, "_extract_soundcloud_fallback", lambda *a, **k: _song("SC", "soundcloud"))

        sm._extract_youtube_single("ytsearch1:uno", None)      # cookie + client = 2
        assert len(chiamate) == 2
        sm._extract_youtube_single("ytsearch1:due", None)      # in blocco: solo 1
        assert len(chiamate) == 3
        # Terza chiamata: blocco duro, YouTube non viene nemmeno interrogato
        sm._extract_youtube_single("ytsearch1:tre", None)
        assert len(chiamate) == 3, "con il blocco duro non deve esserci nessun nuovo tentativo YouTube"

    def test_successo_azzera_il_blocco(self, monkeypatch):
        sm = self._manager()
        sm._youtube_blocked_until = 1.0  # finestra già scaduta
        sm._youtube_bot_hits = 1
        monkeypatch.setattr(sm, "_do_extract_single", lambda *a, **k: _song("OK"))

        song = sm._extract_youtube_single("ytsearch1:vasco", None)
        assert song.title == "OK"
        assert sm._youtube_bot_hits == 0

    def test_soundcloud_disabilitato_fallisce_invece_di_ripiegare(self, monkeypatch):
        import sources

        sm = self._manager()
        monkeypatch.setattr(sm, "_do_extract_single", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(BOT_CHECK)))
        monkeypatch.setattr(sources, "ALLOW_SOUNDCLOUD_FALLBACK", False)
        sc_chiamato = []
        monkeypatch.setattr(sm, "_extract_soundcloud_fallback", lambda *a, **k: sc_chiamato.append(1))

        with pytest.raises(RuntimeError):
            sm._extract_youtube_single("ytsearch1:x", None)
        assert sc_chiamato == [], "con MUSIC_ALLOW_SOUNDCLOUD=0 SoundCloud non va usato"

    def test_po_token_provider_sopravvive_al_cambio_di_client(self, monkeypatch):
        import sources

        sm = self._manager()
        monkeypatch.setattr(sources, "POT_PROVIDER_URL", "http://bgutil-provider:4416")
        opts = sm._get_mobile_extractor_opts(sm.ydl_opts_stream)
        pot = opts["extractor_args"].get("youtubepot-bgutilhttp")
        assert pot and pot["base_url"] == ["http://bgutil-provider:4416/"]

        monkeypatch.setattr(sources, "POT_PROVIDER_URL", "")
        opts_senza = sm._get_mobile_extractor_opts(sm.ydl_opts_stream)
        assert "youtubepot-bgutilhttp" not in opts_senza["extractor_args"]


class TestSpotifyEnrichment:
    def test_metadati_spotify_arricchiscono_il_brano(self):
        import sources

        song = _song("titolo youtube", stream=None)
        song.uploader = None
        meta = {
            "title": "Vengo dalla luna", "artist": "Caparezza",
            "url": "https://open.spotify.com/track/abc",
            "thumbnail": "https://img.example/cover.jpg",
            "search_query": "Caparezza Vengo dalla luna",
        }
        sources.SourceManager._enrich_with_spotify(song, meta)
        assert song.title == "Caparezza - Vengo dalla luna"
        assert song.uploader == "Caparezza"
        assert song.spotify_url == "https://open.spotify.com/track/abc"
        assert song.thumbnail == "https://img.example/cover.jpg"
        assert song.search_query == "Caparezza Vengo dalla luna"

    def test_senza_metadati_il_brano_resta_intatto(self):
        import sources

        song = _song("titolo originale")
        sources.SourceManager._enrich_with_spotify(song, None)
        assert song.title == "titolo originale"

    def test_spotify_non_configurato_non_chiama_la_rete(self):
        import sources

        sm = sources.SourceManager()
        sm.sp = None
        assert asyncio.run(sm._spotify_lookup("caparezza")) is None


class TestRadioEnsureStream:
    def test_radio_usa_subito_lo_stream_live(self):
        import sources

        sm = sources.SourceManager()
        song = _song("📻 Radio m2o", "radio", "https://example.invalid/m2o.m3u8")
        assert asyncio.run(sm.ensure_stream_url(song)) == "https://example.invalid/m2o.m3u8"

    def test_radio_senza_stream_lo_risolve(self, monkeypatch):
        import radio
        import sources

        sm = sources.SourceManager()
        song = _song("📻 Radio m2o", "radio", None)
        song.search_query = "m2o"

        async def fake_resolve(station=None, name=None, force_refresh=False):
            return radio.RadioStream(url="https://example.invalid/rinnovato.m3u8", station="Radio m2o")

        monkeypatch.setattr(radio, "resolve_stream", fake_resolve)
        url = asyncio.run(sm.ensure_stream_url(song))
        assert url == "https://example.invalid/rinnovato.m3u8"
        assert song.is_direct_stream is True

    def test_radio_irrisolvibile_solleva(self, monkeypatch):
        import radio
        import sources

        sm = sources.SourceManager()
        song = _song("📻 Radio fantasma", "radio", None)

        async def fake_resolve(station=None, name=None, force_refresh=False):
            return None

        monkeypatch.setattr(radio, "resolve_stream", fake_resolve)
        with pytest.raises(RuntimeError):
            asyncio.run(sm.ensure_stream_url(song))


# ===========================================================================
# 6. Innesti nella pipeline vocale: radio e memoria
# ===========================================================================
def _sink(player=None):
    """Crea un FranchinoVoiceSink con player finto (pattern dei test esistenti)."""
    from unittest.mock import MagicMock, patch

    from voice_commands import FranchinoVoiceSink

    p = player or MagicMock()
    p.bot = MagicMock()
    p.bot.loop = MagicMock()
    p.bot.loop.create_task = MagicMock(return_value=MagicMock())
    p.speak_tts = AsyncMock()
    p.play_now = AsyncMock()
    p.disconnect = AsyncMock()
    p.voice_client = MagicMock(is_connected=MagicMock(return_value=True))
    p.voice_client.channel = MagicMock(name="musica")
    p.text_channel = MagicMock(name="musica")
    p.guild = MagicMock()
    p.guild.name = "Server Test"
    p.current = MagicMock(title="Brano")
    with patch.object(FranchinoVoiceSink, "_monitor_speech_loop", new=lambda self: None):
        sink = FranchinoVoiceSink(p)
    sink._notify_channel = AsyncMock()
    return sink, p


class TestRadioHook:
    def _user(self):
        from unittest.mock import MagicMock

        u = MagicMock()
        u.id = 42
        u.display_name = "Marco"
        u.bot = False
        return u

    def test_metti_m2o_manda_in_onda_la_radio(self, monkeypatch):
        import radio

        sink, player = _sink()
        sent = {}

        async def fake_resolve(station=None, name=None, force_refresh=False):
            sent["station"] = station
            return radio.RadioStream(
                url="https://example.invalid/m2o.m3u8", station="Radio m2o",
                codec="AAC+", bitrate=122, hls=True, homepage="https://www.m2o.it/", genre="Dance",
            )

        monkeypatch.setattr(radio, "resolve_stream", fake_resolve)
        ok = asyncio.run(sink._play_radio(self._user(), radio.CATALOG_BY_KEY["m2o"], None))

        assert ok is True
        assert sent["station"].key == "m2o"
        player.play_now.assert_awaited_once()
        song = player.play_now.await_args[0][0][0]
        assert song.source_type == "radio"
        assert song.is_direct_stream is True
        assert song.duration == 0
        assert song.search_query == "m2o"      # serve a ri-risolvere lo stream se cade
        assert "m2o" in song.title

    def test_radio_senza_flusso_avvisa_e_non_suona(self, monkeypatch):
        import radio

        sink, player = _sink()

        async def fake_resolve(**kwargs):
            return None

        monkeypatch.setattr(radio, "resolve_stream", fake_resolve)
        ok = asyncio.run(sink._play_radio(self._user(), radio.CATALOG_BY_KEY["m2o"], None))

        assert ok is False
        player.play_now.assert_not_awaited()
        messaggio = sink._notify_channel.await_args[0][0]
        assert "Non trovo un flusso attivo" in messaggio

    def test_radio_sconosciuta_fallisce_in_silenzio(self, monkeypatch):
        """Nome fuori catalogo: se non è una radio, la richiesta prosegue come musica."""
        import radio

        sink, player = _sink()

        async def fake_resolve(**kwargs):
            return None

        monkeypatch.setattr(radio, "resolve_stream", fake_resolve)
        ok = asyncio.run(
            sink._play_radio(self._user(), None, None, radio_name="ga ga", announce_missing=False)
        )
        assert ok is False
        sink._notify_channel.assert_not_awaited()

    def test_comando_vocale_metti_m2o_instrada_sulla_radio(self, monkeypatch):
        """'Hey Franchino metti m2o' deve finire su _play_radio, non su YouTube."""
        sink, player = _sink()
        chiamate = {}

        async def fake_play_radio(self, user, station, requester_arg, radio_name=None, announce_missing=True):
            chiamate["station"] = station
            return True

        monkeypatch.setattr(type(sink), "_play_radio", fake_play_radio)
        asyncio.run(sink._handle_transcribed_text(self._user(), "Hey Franchino metti m2o", detected_cmd="metti m2o"))

        assert chiamate.get("station") is not None
        assert chiamate["station"].key == "m2o"
        player.play_now.assert_not_awaited()  # ci pensa _play_radio (qui sostituito)

    def test_richiesta_generica_elenca_le_radio(self, monkeypatch):
        sink, _ = _sink()
        chiamate = {}

        async def fake_list(self, user):
            chiamate["lista"] = True

        monkeypatch.setattr(type(sink), "_send_radio_list", fake_list)
        asyncio.run(
            sink._handle_transcribed_text(self._user(), "Hey Franchino metti una radio", detected_cmd="metti una radio")
        )
        assert chiamate.get("lista") is True


class TestMemoryHook:
    def _user(self, uid=101, nome="Luca"):
        from unittest.mock import MagicMock

        u = MagicMock()
        u.id = uid
        u.display_name = nome
        u.bot = False
        return u

    @pytest.fixture(autouse=True)
    def _memoria_isolata(self, tmp_path, monkeypatch):
        import memoria

        m = memoria.Memoria(path=str(tmp_path / "memoria.json"))
        monkeypatch.setattr(memoria, "memoria", m)
        self.m = m

    def test_frase_legittima_non_registra_nulla(self):
        sink, _ = _sink()
        gestito = asyncio.run(sink._check_offense_and_reply(self._user(), "metti caparezza"))
        assert gestito is False
        assert self.m.get(101) is None

    def test_primo_congedo_resta_gentile(self):
        """Un solo 'esci' non deve far partire la ramanzina (ma viene ricordato)."""
        sink, player = _sink()
        gestito = asyncio.run(sink._check_offense_and_reply(self._user(), "esci"))
        assert gestito is False
        assert self.m.get(101) is not None, "l'offesa va comunque registrata"

    def test_congedi_ripetuti_cambiano_il_tono(self):
        sink, player = _sink()
        asyncio.run(sink._check_offense_and_reply(self._user(), "esci"))
        gestito = asyncio.run(sink._check_offense_and_reply(self._user(), "vattene"))

        assert gestito is True
        assert sink._notify_channel.await_count >= 1
        risposta = sink._notify_channel.await_args[0][0]
        assert "Luca" in risposta
        player.disconnect.assert_awaited_once()  # il congedo resta un comando valido

    def test_insulto_risponde_subito_e_non_prosegue(self):
        sink, player = _sink()
        gestito = asyncio.run(sink._check_offense_and_reply(self._user(), "sei un coglione"))
        assert gestito is True
        player.disconnect.assert_not_awaited()
        player.speak_tts.assert_called_once()  # replica anche a voce

    def test_insulto_in_endpoint_vocale_non_diventa_ricerca_musicale(self, monkeypatch):
        """Un insulto non deve mai far partire una ricerca YouTube."""
        sink, player = _sink()
        chiamate = []

        async def fake_play_radio(*a, **k):
            chiamate.append("radio")
            return True

        monkeypatch.setattr(type(sink), "_play_radio", fake_play_radio)
        monkeypatch.setattr(
            type(sink), "_dispatch_brain_decision", AsyncMock(return_value=True)
        )
        asyncio.run(
            sink._handle_transcribed_text(
                self._user(), "Hey Franchino sei un cretino", detected_cmd="sei un cretino"
            )
        )
        assert chiamate == []
        assert sink._notify_channel.await_count >= 1
