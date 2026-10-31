"""Test di scripts/analyze_voice.py — lo strumento che misura pitch e spettro.

Se lo strumento sbaglia, sbaglia anche la taratura della voce: questi test usano
segnali sintetici con frequenza nota, così il risultato è verificabile a priori.
"""

import math
import wave

import pytest

np = pytest.importorskip("numpy", reason="serve numpy per l'analisi spettrale")


def scrivi_tono(path, frequenza: float, durata: float = 2.0, rate: int = 44100, ampiezza: float = 0.5):
    """Scrive un WAV mono con un tono puro a frequenza nota."""
    t = np.arange(int(durata * rate)) / rate
    segnale = (ampiezza * np.sin(2 * math.pi * frequenza * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(segnale.tobytes())
    return str(path)


def scrivi_silenzio(path, durata: float = 1.0, rate: int = 44100):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(durata * rate))
    return str(path)


# ===========================================================================
# Pitch (F0)
# ===========================================================================
class TestStimaF0:
    @pytest.mark.parametrize("frequenza", [90.0, 120.0, 155.0, 197.0, 250.0])
    def test_riconosce_la_frequenza_fondamentale(self, tmp_path, frequenza):
        import scripts.analyze_voice as av

        path = scrivi_tono(tmp_path / "tono.wav", frequenza)
        misura = av.analizza(path)
        assert misura["f0_mediana"] == pytest.approx(frequenza, rel=0.04), (
            f"atteso ~{frequenza} Hz, misurato {misura['f0_mediana']}"
        )

    def test_distingue_una_voce_bassa_da_una_alta(self, tmp_path):
        """È il confronto che ha guidato la scelta della voce TTS."""
        import scripts.analyze_voice as av

        bassa = av.analizza(scrivi_tono(tmp_path / "bassa.wav", 155.0))["f0_mediana"]
        alta = av.analizza(scrivi_tono(tmp_path / "alta.wav", 197.0))["f0_mediana"]
        assert alta - bassa == pytest.approx(42.0, abs=6.0)

    def test_silenzio_non_produce_pitch(self, tmp_path):
        import scripts.analyze_voice as av

        misura = av.analizza(scrivi_silenzio(tmp_path / "muto.wav"))
        assert misura["f0_mediana"] is None
        assert misura["frame_con_voce"] == 0


# ===========================================================================
# Bilanciamento spettrale (LTAS)
# ===========================================================================
class TestLtas:
    @pytest.mark.parametrize("centro", [250, 500, 1000, 2000, 4000])
    def test_la_banda_del_tono_e_la_piu_forte(self, tmp_path, centro):
        import scripts.analyze_voice as av

        misura = av.analizza(scrivi_tono(tmp_path / "tono.wav", centro))
        curva = misura["ltas_db"]
        assert curva[centro] == pytest.approx(0.0, abs=0.01), "la banda del tono è il riferimento (0 dB)"
        altre = [v for b, v in curva.items() if b != centro]
        assert all(v < -6 for v in altre), f"le altre bande devono essere molto più basse: {curva}"

    def test_curva_normalizzata_sulla_banda_piu_forte(self, tmp_path):
        import scripts.analyze_voice as av

        curva = av.analizza(scrivi_tono(tmp_path / "t.wav", 1000))["ltas_db"]
        assert max(curva.values()) == pytest.approx(0.0, abs=0.01)

    def test_distanza_fra_curve_identiche_e_zero(self, tmp_path):
        import scripts.analyze_voice as av

        misura = av.analizza(scrivi_tono(tmp_path / "t.wav", 500))
        assert av.distanza_ltas(misura["ltas_db"], misura["ltas_db"]) == 0.0

    def test_distanza_calcola_la_media_delle_differenze(self):
        import scripts.analyze_voice as av

        a = {b: 0.0 for b in av.BANDE}
        b = {banda: (-2.0 if banda in av.BANDE_AFFIDABILI else 0.0) for banda in av.BANDE}
        # Solo le bande affidabili: 5 bande, ognuna con 2 dB di scarto
        assert av.distanza_ltas(a, b, av.BANDE_AFFIDABILI) == pytest.approx(2.0)

    def test_le_bande_non_affidabili_sono_escluse(self):
        """63/125 Hz li svuota demucs, 8 kHz lo taglia l'mp3: non vanno usati."""
        import scripts.analyze_voice as av

        assert 63 not in av.BANDE_AFFIDABILI
        assert 125 not in av.BANDE_AFFIDABILI
        assert 8000 not in av.BANDE_AFFIDABILI
        assert set(av.BANDE_AFFIDABILI) <= set(av.BANDE)
        assert av.BANDE_AFFIDABILI == (250, 500, 1000, 2000, 4000)


# ===========================================================================
# Interfaccia a riga di comando
# ===========================================================================
class TestCli:
    def test_analisi_di_un_file(self, tmp_path, capsys):
        import scripts.analyze_voice as av

        path = scrivi_tono(tmp_path / "tono.wav", 197.0)
        assert av.main([path]) == 0
        uscita = capsys.readouterr().out
        assert "PITCH" in uscita
        assert "LTAS" in uscita

    def test_confronto_fra_due_file(self, tmp_path, capsys):
        import scripts.analyze_voice as av

        rif = scrivi_tono(tmp_path / "rif.wav", 197.0)
        alt = scrivi_tono(tmp_path / "alt.wav", 155.0)
        assert av.main([rif, alt, "--compare"]) == 0
        uscita = capsys.readouterr().out
        assert "affidabile" in uscita
        assert "semitoni" in uscita

    def test_output_json(self, tmp_path, capsys):
        import json

        import scripts.analyze_voice as av

        path = scrivi_tono(tmp_path / "tono.wav", 250.0)
        assert av.main([path, "--json"]) == 0
        dati = json.loads(capsys.readouterr().out)
        assert dati[0]["f0_mediana"] == pytest.approx(250.0, rel=0.05)
        assert "ltas_db" in dati[0]

    def test_file_inesistente_non_crasha(self, tmp_path, capsys):
        import scripts.analyze_voice as av

        esito = av.main([str(tmp_path / "non_esiste.wav")])
        assert esito == 1
        assert "⚠️" in capsys.readouterr().out


# ===========================================================================
# Formati non supportati: messaggio chiaro invece di un errore criptico
# ===========================================================================
def scrivi_wav_float(path, frequenza: float = 200.0, durata: float = 0.5, rate: int = 16000):
    """WAV IEEE float (formato 3): torchaudio lo produce di default."""
    import struct

    n = int(durata * rate)
    dati = np.sin(2 * math.pi * frequenza * np.arange(n) / rate).astype("<f4").tobytes()
    with open(str(path), "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + len(dati)) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32))
        fh.write(b"data" + struct.pack("<I", len(dati)) + dati)
    return str(path)


class TestFormatiNonSupportati:
    def test_wav_float_da_errore_spiegato(self, tmp_path):
        """Il WAV float è quello che scrive torchaudio: va detto come convertirlo."""
        import scripts.analyze_voice as av

        path = scrivi_wav_float(tmp_path / "float.wav")
        with pytest.raises(ValueError) as err:
            av.analizza(path)
        messaggio = str(err.value)
        assert "pcm_s16le" in messaggio
        assert "float" in messaggio.lower()

    def test_il_cli_segnala_il_file_illeggibile(self, tmp_path, capsys):
        import scripts.analyze_voice as av

        path = scrivi_wav_float(tmp_path / "float.wav")
        assert av.main([path]) == 1
        assert "⚠️" in capsys.readouterr().out
