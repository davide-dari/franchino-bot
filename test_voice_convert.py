"""Test di scripts/voice_convert.py — conversione voce zero-shot (kNN-VC).

Non caricano i modelli veri (1,2 GB): verificano la logica dello script con un
`torch` e un modello finti, così girano senza torch installato.
"""

import sys
import types

import pytest


# ===========================================================================
# Raccolta dei file
# ===========================================================================
class TestRaccoltaFile:
    def test_cartella_ricorsiva(self, tmp_path):
        import scripts.voice_convert as vc

        (tmp_path / "sub").mkdir()
        (tmp_path / "a.wav").write_bytes(b"x")
        (tmp_path / "sub" / "b.mp3").write_bytes(b"x")
        (tmp_path / "note.txt").write_text("non audio")
        trovati = vc.raccogli_audio([str(tmp_path)])
        assert len(trovati) == 2
        assert all(p.endswith((".wav", ".mp3")) for p in trovati)

    def test_file_singolo(self, tmp_path):
        import scripts.voice_convert as vc

        f = tmp_path / "uno.WAV"
        f.write_bytes(b"x")  # estensione maiuscola: deve contare
        assert vc.raccogli_audio([str(f)]) == [str(f)]

    def test_percorso_inesistente(self, tmp_path):
        import scripts.voice_convert as vc

        with pytest.raises(FileNotFoundError):
            vc.raccogli_audio([str(tmp_path / "manca.wav")])

    def test_cartella_senza_audio(self, tmp_path):
        import scripts.voice_convert as vc

        (tmp_path / "vuota").mkdir()
        with pytest.raises(ValueError):
            vc.raccogli_audio([str(tmp_path / "vuota")])


# ===========================================================================
# Matching set resistente
# ===========================================================================
@pytest.fixture
def torch_finto(monkeypatch):
    """Sostituisce torch: allo script serve solo concat per questo test."""

    class TensoreFinto(dict):
        def cpu(self):
            return self

    modulo = types.ModuleType("torch")

    def concat(frames, dim=0):
        return TensoreFinto(frames=list(frames), dim=dim)

    modulo.concat = concat
    monkeypatch.setitem(sys.modules, "torch", modulo)
    return modulo


class ModelloFinto:
    weighting = object()

    def __init__(self, guaste=()):
        self.guaste = set(guaste)
        self.chieste = []

    def get_features(self, path, weights=None):
        self.chieste.append(path)
        if path in self.guaste:
            raise RuntimeError("cannot reshape tensor of 0 elements (VAD vuoto)")
        return f"feat:{path}"

    def match(self, query, matching, topk=4):
        return {"query": query, "matching": matching, "topk": topk}


class TestMatchingRobusto:
    def test_una_clip_guasta_non_butta_via_le_altre(self, torch_finto):
        """Era il bug reale: una clip senza parlato rilevabile faceva fallire
        l'intero riferimento, buttando via centinaia di clip buone."""
        import scripts.voice_convert as vc

        modello = ModelloFinto(guaste={"rotta.wav"})
        risultato = vc.get_matching_set_robusto(
            modello, ["buona1.wav", "rotta.wav", "buona2.wav"]
        )
        assert modello.chieste == ["buona1.wav", "rotta.wav", "buona2.wav"]
        assert risultato["frames"] == ["feat:buona1.wav", "feat:buona2.wav"]
        assert risultato["dim"] == 0

    def test_tutte_guaste_segnala_errore_esplicito(self, torch_finto):
        import scripts.voice_convert as vc

        modello = ModelloFinto(guaste={"a.wav", "b.wav"})
        with pytest.raises(RuntimeError) as err:
            vc.get_matching_set_robusto(modello, ["a.wav", "b.wav"])
        assert "riferimento" in str(err.value).lower()

    def test_passa_i_pesi_del_modello(self, torch_finto):
        import scripts.voice_convert as vc

        raccolti = {}

        class Modello(ModelloFinto):
            def get_features(self, path, weights=None):
                raccolti["pesi"] = weights
                return "f"

        vc.get_matching_set_robusto(Modello(), ["x.wav"])
        assert raccolti["pesi"] is Modello.weighting

    def test_converti_usa_il_matching_robusto(self, torch_finto):
        import scripts.voice_convert as vc

        modello = ModelloFinto(guaste={"rotta.wav"})
        esito = vc.converti(modello, ["src.wav"], ["ok.wav", "rotta.wav"], topk=6)
        assert esito["topk"] == 6
        assert esito["query"] == "feat:src.wav"
        assert esito["matching"]["frames"] == ["feat:ok.wav"]


# ===========================================================================
# Prerequisiti
# ===========================================================================
class TestPrerequisiti:
    def test_repo_knn_vc_mancante_spiega_come_scaricarlo(self, tmp_path):
        import scripts.voice_convert as vc

        with pytest.raises(FileNotFoundError) as err:
            vc.carica_modello(tmp_path / "non_esiste", "cpu")
        messaggio = str(err.value)
        assert "git clone" in messaggio
        assert "knn-vc" in messaggio

    def test_modulo_iniettabile(self):
        import scripts.voice_convert as vc

        finto = object()
        assert vc.carica_modello("/percorso/qualsiasi", "cpu", knn_vc_module=finto) is finto
