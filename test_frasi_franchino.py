"""Test dell'estrazione frasi e della soundboard con la voce vera di Franchino.

Nessun modello whisper e nessun file audio reale: si verificano la logica di
ricerca, la classificazione delle tonalità e la scoperta dei clip su disco
(la cartella sounds/franchino/ è dell'utente, non del repository).
"""

import os

import pytest


# ===========================================================================
# 1. Estrazione delle frasi (logica, senza whisper)
# ===========================================================================
class TestRicercaFrasi:
    def _parole(self, testo: str, passo: float = 0.35):
        """Costruisce una sequenza di parole con tempi finti."""
        return [
            {"testo": w, "inizio": i * passo, "fine": i * passo + passo * 0.8}
            for i, w in enumerate(testo.split())
        ]

    def test_normalizza_accenti_e_punteggiatura(self):
        import scripts.mine_phrases as mp

        assert mp.normalizza("Magìa!!!") == "magia"
        assert mp.normalizza("  niente sarà più come prima  ") == "niente sara piu come prima"
        assert mp.normalizza("Battito 90") == "battito 90"

    def test_trova_una_parola_simbolo(self):
        import scripts.mine_phrases as mp

        parole = self._parole("ragazzi adesso magia tutti insieme")
        trovate = mp.cerca_frasi(parole)
        assert len(trovate) == 1
        chiave, testo, inizio, fine = trovate[0]
        assert chiave == "magia"
        assert testo == "magia"
        assert inizio == pytest.approx(0.70)   # terza parola
        assert fine > inizio

    def test_trova_una_frase_di_piu_parole(self):
        import scripts.mine_phrases as mp

        parole = self._parole("chiudi gli occhi e vola con me")
        chiavi = {t[0] for t in mp.cerca_frasi(parole)}
        assert "chiudi_gli_occhi" in chiavi
        assert "vola_con_me" in chiavi

    def test_ignora_il_maiuscolo_e_la_punteggiatura(self):
        import scripts.mine_phrases as mp

        parole = [
            {"testo": "magia", "inizio": 0.0, "fine": 0.5},
            {"testo": "insomnia", "inizio": 1.0, "fine": 1.5},
        ]
        chiavi = {t[0] for t in mp.cerca_frasi(parole)}
        assert chiavi == {"magia", "insomnia"}

    def test_nessuna_frase_restituisce_lista_vuota(self):
        import scripts.mine_phrases as mp

        assert mp.cerca_frasi(self._parole("parole qualsiasi senza senso")) == []

    @pytest.mark.parametrize(
        "f0,atteso",
        [(120.0, "grave"), (197.0, "media"), (260.0, "acuta"), (None, "n/d")],
    )
    def test_classifica_la_tonalita(self, f0, atteso):
        import scripts.mine_phrases as mp

        assert mp.classifica_tonalita(f0) == atteso

    def test_le_frasi_chiave_sono_normalizzate(self):
        """Un refuso con accenti o maiuscole renderebbe la ricerca muta."""
        import scripts.mine_phrases as mp

        for chiave, varianti in mp.FRASI_CHIAVE.items():
            for variante in varianti:
                assert variante == mp.normalizza(variante), (
                    f"la variante '{variante}' di '{chiave}' non è normalizzata"
                )


# ===========================================================================
# 2. Soundboard: scoperta dei clip di voce vera
# ===========================================================================
class TestVoceVeraSoundboard:
    @pytest.fixture
    def cartella_voce(self, tmp_path, monkeypatch):
        """Cartella sounds/franchino/ finta con qualche clip."""
        import sound_effects

        voce = tmp_path / "franchino"
        voce.mkdir()
        (voce / "magia_01.mp3").write_bytes(b"x" * 2048)
        (voce / "magia_02.wav").write_bytes(b"x" * 2048)
        (voce / "favola_01.mp3").write_bytes(b"x" * 2048)
        (voce / "note.txt").write_text("non è audio")
        monkeypatch.setattr(sound_effects, "FRANCHINO_VOICE_DIR", str(voce))
        return voce

    @pytest.fixture
    def cartella_vuota(self, tmp_path, monkeypatch):
        import sound_effects

        monkeypatch.setattr(sound_effects, "FRANCHINO_VOICE_DIR", str(tmp_path / "assente"))
        return tmp_path / "assente"

    def test_trova_i_clip_di_una_categoria(self, cartella_voce):
        import sound_effects as se

        magia = se.franchino_voice_files("franchino_magia")
        assert len(magia) == 2
        assert all(os.path.basename(p).startswith("magia_") for p in magia)

    def test_ignora_i_file_che_non_sono_audio(self, cartella_voce):
        import sound_effects as se

        trovati = se.franchino_voice_files("franchino_magia")
        assert not any(p.endswith(".txt") for p in trovati)

    def test_categoria_senza_clip(self, cartella_voce):
        import sound_effects as se

        assert se.franchino_voice_files("franchino_grazie") == []
        assert se.franchino_voice_file("franchino_grazie") is None

    def test_sceglie_a_caso_fra_le_varianti(self, cartella_voce):
        import sound_effects as se

        scelte = {se.franchino_voice_file("franchino_magia") for _ in range(40)}
        assert len(scelte) == 2, "con più varianti deve alternarle, non usarne una sola"

    def test_senza_cartella_non_esplode(self, cartella_vuota):
        import sound_effects as se

        assert se.franchino_voice_files("franchino_magia") == []
        assert se.franchino_voice_file("franchino_magia") is None

    def test_la_categoria_compare_solo_se_ci_sono_i_file(self, cartella_voce):
        import sound_effects as se

        effetti = se.soundboard_effects()
        assert "franchino_magia" in effetti
        assert "franchino_favola" in effetti
        assert "franchino_grazie" not in effetti, "senza clip non deve comparire"
        assert "airhorn" in effetti, "gli effetti sintetizzati restano disponibili"

    def test_senza_file_non_compare(self, cartella_vuota):
        import sound_effects as se

        effetti = se.soundboard_effects()
        assert not any(k.startswith("franchino_") for k in effetti)

    def test_il_file_usato_e_quello_vero_non_sintetizzato(self, cartella_voce):
        import sound_effects as se

        path = se.ensure_sfx_file("franchino_magia")
        assert path in se.franchino_voice_files("franchino_magia")

    def test_senza_registrazioni_ripiega_sulla_sintesi(self, cartella_vuota):
        import sound_effects as se

        path = se.ensure_sfx_file("franchino_magia")
        assert path.endswith(".wav"), "senza clip si usa l'effetto sintetizzato"
        assert "franchino_magia" not in os.path.basename(path)
