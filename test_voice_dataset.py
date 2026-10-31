"""Test del builder del dataset vocale (scripts/build_voice_dataset.py).

Girano **senza rete**: l'audio di prova è generato con ffmpeg (toni separati da
silenzi), così il test verifica segmentazione, filtri, normalizzazione ed export
in modo deterministico.
"""

import json
import os
import shutil
import subprocess

import pytest

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg non disponibile"
)

# Durata totale del materiale sintetico
DURATA = 13.0
# Tre frammenti "parlati": 4s, 4s, 1s separati da 0.6s di silenzio
FRAMMENTI = [(0.4, 4.4), (5.0, 9.0), (9.6, 10.6)]


@pytest.fixture(scope="module")
def audio_prova(tmp_path_factory):
    """WAV sintetico con tre frammenti separati da silenzio (nessuna rete)."""
    cartella = tmp_path_factory.mktemp("voce")
    path = cartella / "prova.wav"
    espressione = "+".join(f"between(t,{a},{b})" for a, b in FRAMMENTI)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=180:duration={DURATA}:sample_rate=44100",
            "-af", f"volume=0.30,volume='if({espressione},1,0)':eval=frame",
            "-ac", "1", str(path),
        ],
        check=True, capture_output=True,
    )
    return str(path)


@pytest.fixture(scope="module")
def audio_quasi_muto(tmp_path_factory):
    """Audio a volume bassissimo: deve essere scartato dal filtro sul RMS."""
    cartella = tmp_path_factory.mktemp("voce_muta")
    path = cartella / "muto.wav"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=180:duration={DURATA}:sample_rate=44100",
            "-af", "volume=0.02",
            "-ac", "1", str(path),
        ],
        check=True, capture_output=True,
    )
    return str(path)


# ===========================================================================
# Funzioni di base
# ===========================================================================
@ffmpeg_required
class TestFunzioniBase:
    def test_durata_senza_ffprobe(self, audio_prova):
        """ffprobe non è detto che ci sia: la durata si ricava dai log di ffmpeg."""
        import scripts.build_voice_dataset as bvd

        assert bvd.probe_duration(audio_prova) == pytest.approx(DURATA, abs=0.2)

    def test_rileva_i_frammenti_di_parlato(self, audio_prova):
        import scripts.build_voice_dataset as bvd

        segmenti = bvd.detect_speech_segments(audio_prova, noise_db=-32.0, min_silence=0.35)
        assert len(segmenti) == len(FRAMMENTI), f"attesi {len(FRAMMENTI)} frammenti, trovati {len(segmenti)}"
        for (start, end), (atteso_start, atteso_end) in zip(segmenti, FRAMMENTI):
            assert start == pytest.approx(atteso_start, abs=0.25)
            assert end == pytest.approx(atteso_end, abs=0.25)

    def test_i_tratti_lunghi_vengono_spezzati(self):
        import scripts.build_voice_dataset as bvd

        pezzi = bvd.split_long([(0.0, 30.0)], max_len=12.0)
        assert len(pezzi) == 3
        assert all(0 < (b - a) <= 12.0 for a, b in pezzi)
        assert pezzi[0][0] == 0.0
        assert pezzi[-1][1] == pytest.approx(30.0)

    def test_misura_rms_e_picco(self, audio_prova):
        import scripts.build_voice_dataset as bvd

        misure = bvd.measure_clip(audio_prova)
        assert misure["campioni"] > 0
        assert -90 < misure["rms_db"] < 0
        assert misure["rms_db"] <= misure["peak_db"]

    def test_trova_i_file_in_una_cartella(self, audio_prova, tmp_path):
        import scripts.build_voice_dataset as bvd

        (tmp_path / "sottocartella").mkdir()
        shutil.copy(audio_prova, tmp_path / "sottocartella" / "uno.wav")
        (tmp_path / "ignorami.txt").write_text("non è audio")
        trovati = bvd.trova_sorgenti(str(tmp_path))
        assert len(trovati) == 1 and trovati[0].endswith("uno.wav")


# ===========================================================================
# Pipeline completa
# ===========================================================================
@ffmpeg_required
class TestPipelineCompleta:
    def test_dataset_completo(self, audio_prova, tmp_path):
        import scripts.build_voice_dataset as bvd

        out = tmp_path / "dataset"
        esito = bvd.main([
            "--input", audio_prova,
            "--out", str(out),
            "--min-len", "3.0",
            "--max-len", "12.0",
        ])
        assert esito == 0

        # 3 segmenti: i due da 4s passano, quello da 1s è troppo corto
        wavs = sorted((out / "wavs").glob("*.wav"))
        assert len(wavs) == 2, f"attese 2 clip, trovate {len(wavs)}"

        # Le tre esportazioni con i sample rate giusti
        assert bvd.probe_sample_rate(str(out / "wavs" / wavs[0].name)) == 44100
        piper = sorted((out / "piper" / "wavs").glob("*.wav"))
        assert len(piper) == 2
        assert bvd.probe_sample_rate(str(piper[0])) == 22050
        gs = sorted((out / "gpt_sovits").glob("*.wav"))
        assert len(gs) == 2
        assert bvd.probe_sample_rate(str(gs[0])) == 32000

        # Normalizzazione: il gain statico deve centrare il target
        for w in wavs:
            misure = bvd.measure_clip(str(w))
            assert misure["rms_db"] == pytest.approx(-20.0, abs=1.0), f"{w.name}: RMS {misure['rms_db']}"

        # Nessun file di lavoro lasciato in giro
        assert not (out / "_work").exists(), "la cartella temporanea va rimossa"

    def test_report_contiene_i_conteggi(self, audio_prova, tmp_path):
        import scripts.build_voice_dataset as bvd

        out = tmp_path / "dataset"
        bvd.main(["--input", audio_prova, "--out", str(out), "--min-len", "3.0"])

        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert report["segmenti_trovati"] == 3
        assert report["clip_accettate"] == 2
        assert report["scarti"]["troppo_corta"] == 1
        assert report["durata_accettata_sec"] == pytest.approx(8.6, abs=0.8)
        assert "gpt_sovits_32k" in report["percorsi"]
        assert "piper_22k" in report["percorsi"]
        assert (out / "report.txt").exists()
        # Con 9s di audio il report deve avvisare che per Piper non basta
        assert any("piper" in a for a in report["avvisi"])

    def test_audio_troppo_basso_viene_scartato(self, audio_quasi_muto, tmp_path):
        """Con --noise-db basso i frammenti vengono rilevati ma il filtro RMS li scarta.

        (Con la soglia di silenzio di default un audio così basso non produrrebbe
        nemmeno segmenti: il filtro sul volume va esercitato apposta.)
        """
        import scripts.build_voice_dataset as bvd

        out = tmp_path / "dataset_muto"
        esito = bvd.main([
            "--input", audio_quasi_muto,
            "--out", str(out),
            "--noise-db", "-65",
            "--min-rms", "-45",
        ])
        assert esito == 1, "senza materiale utile il builder deve segnalarlo"
        assert sorted((out / "wavs").glob("*.wav")) == []
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert report["clip_accettate"] == 0
        assert report["scarti"].get("troppo_bassa"), "il report deve dire perché ha scartato"

    def test_sorgente_inesistente(self, tmp_path):
        import scripts.build_voice_dataset as bvd

        with pytest.raises(FileNotFoundError):
            bvd.trova_sorgenti(str(tmp_path / "non_esiste.mp4"))

    def test_trascrizione_senza_faster_whisper_spiega_come_installarla(self, monkeypatch):
        import scripts.build_voice_dataset as bvd

        # Simula faster-whisper assente
        import builtins
        vero_import = builtins.__import__

        def finto_import(name, *args, **kwargs):
            if name.startswith("faster_whisper"):
                raise ImportError("modulo assente")
            return vero_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", finto_import)
        with pytest.raises(RuntimeError) as err:
            bvd.transcribe(["x.wav"], "small", "it")
        assert "faster-whisper" in str(err.value)
        assert "pip install" in str(err.value)
