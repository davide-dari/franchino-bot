#!/usr/bin/env python3
"""
build_voice_dataset.py - Costruisce un dataset vocale pulito per addestrare un TTS
=================================================================================
Prende uno o più video/audio (es. le serate di Franchino) e produce un dataset
pronto per l'addestramento di un modello text-to-speech:

  1. ESTRAZIONE   audio mono ad alta qualità con ffmpeg
  2. VOCE         (opzionale) separazione voce/musica con demucs
  3. SEGMENTAZIONE rileva le pause reali (silencedetect) e taglia solo il parlato
  4. QUALITÀ      scarta clipping, silenzio, rumore e pezzi troppo corti/lunghi
  5. LIVELLO      normalizza ogni clip a un RMS target (gain statico, niente pumping)
  6. EXPORT       tre formati in un colpo: master 44.1k, Piper 22.05k, GPT-SoVITS 32k
  7. REPORT       JSON + testo leggibile con quanto materiale utilisable hai

Nessuna dipendenza Python: usa solo `ffmpeg` (già presente nell'immagine Docker).
`demucs` e `faster-whisper` sono opzionali e servono solo se li attivi.

USO
---
  python scripts/build_voice_dataset.py --input video.mp4 --out dataset
  python scripts/build_voice_dataset.py --input cartella_video/ --out dataset
  python scripts/build_voice_dataset.py --input v.mp4 --out ds --separate-vocals
  python scripts/build_voice_dataset.py --input v.mp4 --out ds --transcribe

AVVERTENZA
-----------
Il dataset contiene la voce di una persona reale: NON va distribuito né
committato. Tienilo fuori dal repository (la cartella dataset/ è in .gitignore).
L'addestramento richiede una GPU: sul server Oracle (2 vCPU) si può solo
inferire, non addestrare. Vedi docs/VOCE_ADDESTRAMENTO.md.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts",
             ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".wma"}

# Soglie indicative di materiale utile per modello (secondi di parlato pulito)
CONSIGLI_SECONDI = {
    "gpt_sovits": (60, "1-10 minuti bastano per un fine-tuning zero-shot"),
    "piper": (1800, "Piper chiede ~30-60 minuti per una voce decente"),
    "rvc": (600, "RVC vuole ~10-15 minuti di audio pulito"),
}


# =============================================================================
# Utility ffmpeg
# =============================================================================
def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("❌ ffmpeg non trovato nel PATH: serve per tutto il processo.")
    return exe


def run_ffmpeg(args: List[str], timeout: int = 3600) -> str:
    """Esegue ffmpeg e restituisce lo stderr (dove ffmpeg scrive i log)."""
    cmd = [_ffmpeg(), "-hide_banner", "-nostdin", "-y"] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg in timeout dopo {timeout}s")
    if proc.returncode != 0:
        coda = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"ffmpeg è fallito:\n{coda}")
    return proc.stderr


def probe_duration(path: str) -> float:
    """Durata in secondi, ricavata dai log di ffmpeg (nessuna dipendenza da ffprobe)."""
    cmd = [_ffmpeg(), "-hide_banner", "-nostdin", "-i", path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if not match:
        raise RuntimeError(f"Durata non rilevabile per {path}")
    ore, minuti, secondi = match.groups()
    return int(ore) * 3600 + int(minuti) * 60 + float(secondi)


def probe_sample_rate(path: str) -> Optional[int]:
    proc = subprocess.run([_ffmpeg(), "-hide_banner", "-nostdin", "-i", path],
                          capture_output=True, text=True)
    match = re.search(r"Audio:.*?(\d+)\s*Hz", proc.stderr)
    return int(match.group(1)) if match else None


# =============================================================================
# Fasi della pipeline
# =============================================================================
def extract_audio(src: str, dst_wav: str, sample_rate: int = 44100) -> None:
    """Estrae l'audio mono. Se il file ha più tracce, ffmpeg prende la prima."""
    run_ffmpeg(["-i", src, "-vn", "-ac", "1", "-ar", str(sample_rate),
                "-acodec", "pcm_s16le", dst_wav])


def separate_vocals(src: str, out_dir: str) -> str:
    """Separa la voce dalla musica con demucs (opzionale). Ritorna il wav della voce."""
    demucs = shutil.which("demucs")
    if not demucs:
        raise RuntimeError(
            "demucs non installato. Installa con: pip install demucs\n"
            "Serve quando Franchino parla sopra la musica; per i monologhi a cappella "
            "puoi evitare questa fase (è la più lenta)."
        )
    run_ffmpeg(["-i", src, "-vn", "-ac", "2", os.path.join(out_dir, "demucs_in.wav")])
    proc = subprocess.run(
        [demucs, "--two-stems", "vocals", "-n", "htdemucs",
         "-o", out_dir, os.path.join(out_dir, "demucs_in.wav")],
        capture_output=True, text=True, timeout=7200,
    )
    if proc.returncode != 0:
        coda = "\n".join(proc.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"demucs è fallito:\n{coda}")
    for radice, _, files in os.walk(out_dir):
        for f in files:
            if f == "vocals.wav":
                return os.path.join(radice, f)
    raise RuntimeError("demucs non ha prodotto vocals.wav")


def detect_speech_segments(audio: str, noise_db: float, min_silence: float) -> List[Tuple[float, float]]:
    """Trova gli intervalli di parlato usando le pause rilevate da silencedetect."""
    durata = probe_duration(audio)
    stderr = run_ffmpeg([
        "-i", audio, "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f", "null", "-",
    ])
    silenzi: List[Tuple[float, float]] = []
    inizio: Optional[float] = None
    for riga in stderr.splitlines():
        m_start = re.search(r"silence_start:\s*(-?\d+\.?\d*)", riga)
        if m_start:
            inizio = max(0.0, float(m_start.group(1)))
            continue
        m_end = re.search(r"silence_end:\s*(-?\d+\.?\d*)", riga)
        if m_end and inizio is not None:
            silenzi.append((inizio, min(durata, float(m_end.group(1)))))
            inizio = None
    if inizio is not None:
        silenzi.append((inizio, durata))

    # Il parlato è il complemento dei silenzi
    parlato: List[Tuple[float, float]] = []
    cursore = 0.0
    for s_start, s_end in silenzi:
        if s_start > cursore + 0.05:
            parlato.append((cursore, s_start))
        cursore = max(cursore, s_end)
    if durata > cursore + 0.05:
        parlato.append((cursore, durata))
    return parlato


def split_long(segmenti: List[Tuple[float, float]], max_len: float) -> List[Tuple[float, float]]:
    """Spezza i tratti troppo lunghi (senza pause) in pezzi di durata simile."""
    out: List[Tuple[float, float]] = []
    for start, end in segmenti:
        lunghezza = end - start
        if lunghezza <= max_len:
            out.append((start, end))
            continue
        pezzi = int(lunghezza // max_len) + 1
        passo = lunghezza / pezzi
        for i in range(pezzi):
            out.append((start + i * passo, start + (i + 1) * passo))
    return out


def measure_clip(path: str) -> Dict[str, float]:
    """Misura RMS, picco, campioni e flat factor di una clip (via astats)."""
    stderr = run_ffmpeg(["-i", path, "-af", "astats=metadata=0:reset=0", "-f", "null", "-"])
    blocco = stderr.split("Overall")[-1] if "Overall" in stderr else stderr
    misure: Dict[str, float] = {}

    def _num(pattern: str) -> Optional[float]:
        m = re.search(pattern + r":\s*(-?(?:inf|\d+\.?\d*))", blocco)
        if not m:
            return None
        valore = m.group(1)
        return float("-inf") if valore == "-inf" else float(valore)

    rms = _num("RMS level dB")
    picco = _num("Peak level dB")
    flat = _num("Flat factor")
    campioni = _num("Number of samples")
    misure["rms_db"] = rms if rms is not None else -120.0
    misure["peak_db"] = picco if picco is not None else -120.0
    misure["flat_factor"] = flat if flat is not None else 0.0
    misure["campioni"] = campioni if campioni is not None else 0.0
    return misure


def render_clip(src: str, dst: str, start: float, end: float, sample_rate: int,
                target_rms_db: float, peak_ceiling_db: float) -> None:
    """Taglia la clip, applica un gain statico per centrare il RMS target e limita i picchi."""
    durata = max(0.05, end - start)
    # Prima passata: misura la clip grezza per calcolare il gain static
    tmp = dst + ".tmp.wav"
    run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{durata:.3f}", "-i", src,
                "-ac", "1", "-ar", str(sample_rate), "-acodec", "pcm_s16le", tmp])
    misure = measure_clip(tmp)
    gain = target_rms_db - misure["rms_db"]
    if misure["peak_db"] > -120:
        gain_max = peak_ceiling_db - misure["peak_db"]
        gain = min(gain, gain_max)
    gain = max(-24.0, min(24.0, gain))  # niente amplificazioni assurde sul rumore

    af = f"volume={gain:.2f}dB"
    if misure["peak_db"] + gain > peak_ceiling_db:
        af += f",alimiter=limit={10 ** (peak_ceiling_db / 20):.4f}:level=false"
    run_ffmpeg(["-i", tmp, "-af", af, "-ac", "1", "-ar", str(sample_rate),
                "-acodec", "pcm_s16le", dst])
    try:
        os.remove(tmp)
    except OSError:
        pass


def resample(src: str, dst: str, sample_rate: int) -> None:
    run_ffmpeg(["-i", src, "-ac", "1", "-ar", str(sample_rate), "-acodec", "pcm_s16le", dst])


def transcribe(paths: List[str], model_size: str, language: str) -> Dict[str, str]:
    """Trascrive le clip (opzionale). Ritorna {path: testo}."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Trascrizione richiesta ma faster-whisper non è installato:\n"
            "  pip install faster-whisper\n"
            "Senza trascrizioni GPT-SoVITS e Piper non possono essere addestrati "
            "(RVC invece non ne ha bisogno)."
        )
    modello = WhisperModel(model_size, device="cpu", compute_type="int8")
    risultati: Dict[str, str] = {}
    for i, path in enumerate(paths, 1):
        segmenti, _ = modello.transcribe(path, language=language, vad_filter=True)
        testo = " ".join(s.text.strip() for s in segmenti).strip()
        risultati[path] = testo
        print(f"   🎙️ {i}/{len(paths)} trascritta", end="\r")
    print()
    return risultati


# =============================================================================
# Programma principale
# =============================================================================
@dataclass
class Riepilogo:
    sorgenti: List[str] = field(default_factory=list)
    durata_sorgenti: float = 0.0
    segmenti_trovati: int = 0
    accettate: int = 0
    scarti: Dict[str, int] = field(default_factory=dict)
    durata_accettata: float = 0.0
    percorsi: Dict[str, str] = field(default_factory=dict)
    avvisi: List[str] = field(default_factory=list)

    def scarto(self, motivo: str) -> None:
        self.scarti[motivo] = self.scarti.get(motivo, 0) + 1


def trova_sorgenti(percorso: str) -> List[str]:
    if os.path.isfile(percorso):
        return [percorso]
    if os.path.isdir(percorso):
        trovati = []
        for radice, _, files in os.walk(percorso):
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() in MEDIA_EXT:
                    trovati.append(os.path.join(radice, f))
        return trovati
    raise FileNotFoundError(f"Sorgente non trovata: {percorso}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Costruisce un dataset vocale pulito per addestrare un TTS della voce di Franchino.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", action="append", required=True,
                    help="file video/audio o cartella (ripetibile)")
    ap.add_argument("--out", required=True, help="cartella di destinazione del dataset")
    ap.add_argument("--min-len", type=float, default=3.0, help="durata minima clip in secondi")
    ap.add_argument("--max-len", type=float, default=12.0, help="durata massima clip in secondi")
    ap.add_argument("--pad", type=float, default=0.15, help="margine attorno al parlato (s)")
    ap.add_argument("--noise-db", type=float, default=-32.0, help="soglia silenzio per silencedetect")
    ap.add_argument("--min-silence", type=float, default=0.35, help="pausa minima per separare (s)")
    ap.add_argument("--target-rms", type=float, default=-20.0, help="RMS target in dBFS")
    ap.add_argument("--peak-ceiling", type=float, default=-1.0, help="picco massimo in dBFS")
    ap.add_argument("--min-rms", type=float, default=-45.0, help="sotto questo RMS la clip è rumore")
    ap.add_argument("--max-flat", type=float, default=0.0005, help="soglia flat factor (clipping)")
    ap.add_argument("--separate-vocals", action="store_true", help="separa la voce con demucs")
    ap.add_argument("--transcribe", action="store_true", help="trascrive le clip (faster-whisper)")
    ap.add_argument("--whisper-model", default="small", help="modello whisper (tiny/base/small/medium)")
    ap.add_argument("--language", default="it", help="lingua della trascrizione")
    ap.add_argument("--speaker", default="franchino", help="nome speaker per GPT-SoVITS")
    ap.add_argument("--master-rate", type=int, default=44100, help="sample rate del master")
    args = ap.parse_args(argv)

    avvio = time.time()
    out = os.path.abspath(args.out)
    lavoro = os.path.join(out, "_work")
    wavs = os.path.join(out, "wavs")
    for d in (out, lavoro, wavs):
        os.makedirs(d, exist_ok=True)

    riepilogo = Riepilogo()
    sorgenti: List[str] = []
    for voce in args.input:
        sorgenti.extend(trova_sorgenti(voce))
    if not sorgenti:
        print("❌ Nessun file video/audio trovato negli input indicati.")
        return 2

    print(f"\n🎬 Sorgenti: {len(sorgenti)} file\n")

    # ---------------------------------------------------------------- 1-3
    clip_ok: List[Tuple[str, float]] = []
    for idx, src in enumerate(sorgenti, 1):
        nome = os.path.basename(src)
        print(f"[{idx}/{len(sorgenti)}] {nome}")
        try:
            durata = probe_duration(src)
        except Exception as e:
            riepilogo.scarto(f"illeggibile")
            print(f"   ⚠️  saltato: {e}")
            continue
        riepilogo.sorgenti.append(src)
        riepilogo.durata_sorgenti += durata

        estratto = os.path.join(lavoro, f"src_{idx:03d}.wav")
        try:
            extract_audio(src, estratto, args.master_rate)
        except Exception as e:
            riepilogo.scarto("estrazione_fallita")
            print(f"   ⚠️  estrazione fallita: {e}")
            continue

        if args.separate_vocals:
            print("   🎚️  separazione voce (demucs)...")
            try:
                estratto = separate_vocals(estratto, os.path.join(lavoro, f"demucs_{idx:03d}"))
            except Exception as e:
                riepilogo.avvisi.append(f"demucs non usato per {nome}: {e}")
                print(f"   ⚠️  {e}")

        try:
            segmenti = detect_speech_segments(estratto, args.noise_db, args.min_silence)
            segmenti = split_long(segmenti, args.max_len)
        except Exception as e:
            riepilogo.scarto("segmentazione_fallita")
            print(f"   ⚠️  segmentazione fallita: {e}")
            continue

        riepilogo.segmenti_trovati += len(segmenti)
        print(f"   ✂️  {len(segmenti)} segmenti di parlato rilevati")

        for start, end in segmenti:
            start = max(0.0, start - args.pad)
            end = min(durata, end + args.pad)
            lunghezza = end - start
            if lunghezza < args.min_len:
                riepilogo.scarto("troppo_corta")
                continue
            if lunghezza > args.max_len + 1.0:
                riepilogo.scarto("troppo_lunga")
                continue
            clip_ok.append((estratto, start, end))

    # ---------------------------------------------------------------- 4-6
    print(f"\n🎚️  Filtro qualità e normalizzazione ({len(clip_ok)} candidate)\n")
    accettate: List[Tuple[str, str, float]] = []  # (path_master, id, durata)
    for i, (sorgente, start, end) in enumerate(clip_ok, 1):
        cid = f"{len(accettate) + 1:05d}"
        dst = os.path.join(wavs, f"{cid}.wav")
        try:
            durata = end - start
            # misura veloce su un ritaglio temporaneo prima di accettare
            tmp = os.path.join(lavoro, "probe.wav")
            run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{durata:.3f}", "-i", sorgente,
                        "-ac", "1", "-ar", str(args.master_rate), "-acodec", "pcm_s16le", tmp])
            misure = measure_clip(tmp)
            os.remove(tmp)
        except Exception as e:
            riepilogo.scarto("misura_fallita")
            print(f"   ⚠️  clip {i}: {e}")
            continue

        if misure["rms_db"] < args.min_rms:
            riepilogo.scarto("troppo_bassa")
            print(f"⏭️  clip {i}: troppo bassa (RMS {misure['rms_db']:.1f} dB < {args.min_rms:.0f} dB)")
            continue
        # Clipping reale: campioni che toccano il fondo scala
        if misure["peak_db"] >= -0.2:
            riepilogo.scarto("clipping")
            print(f"⏭️  clip {i}: saturata (picco {misure['peak_db']:.1f} dB)")
            continue
        # "Flat factor" di astats non è 0-1 (una sinusoide pura vale ~8): conta solo
        # se accompagnato da un picco già quasi a fondo scala.
        if misure["flat_factor"] > args.max_flat and misure["peak_db"] > args.peak_ceiling:
            riepilogo.scarto("clipping_prolungato")
            print(f"⏭️  clip {i}: clipping prolungato (flat {misure['flat_factor']:.1f}, picco {misure['peak_db']:.1f} dB)")
            continue

        try:
            render_clip(sorgente, dst, start, end, args.master_rate,
                        args.target_rms, args.peak_ceiling)
        except Exception as e:
            riepilogo.scarto("render_fallito")
            print(f"   ⚠️  clip {i}: {e}")
            continue

        accettate.append((dst, cid, durata))
        riepilogo.durata_accettata += durata
        print(f"   ✅ {cid}  {durata:5.2f}s  RMS {misure['rms_db']:6.1f} dB  picco {misure['peak_db']:6.1f} dB", end="\r")

    print()
    riepilogo.accettate = len(accettate)

    if not accettate:
        riepilogo.avvisi.append("Nessuna clip utilizzabile: controlla --noise-db e i volumi del video.")
        _scrivi_report(out, riepilogo, time.time() - avvio)
        print("\n❌ Nessuna clip utilizzabile. Report salvato in report.txt")
        return 1

    # Trascrizioni (opzionale)
    testi: Dict[str, str] = {}
    if args.transcribe:
        print("🎙️  Trascrizione in corso (faster-whisper)...")
        testi = transcribe([p for p, _, _ in accettate], args.whisper_model, args.language)
        vuote = [p for p, t in testi.items() if not t.strip()]
        if vuote:
            riepilogo.avvisi.append(f"{len(vuote)} clip senza trascrizione: verranno saltate negli export testuali")

    # Export per modello
    piper_dir = os.path.join(out, "piper")
    gs_dir = os.path.join(out, "gpt_sovits")
    os.makedirs(os.path.join(piper_dir, "wavs"), exist_ok=True)
    os.makedirs(gs_dir, exist_ok=True)

    righe_piper: List[str] = []
    righe_gs: List[str] = []
    for path_master, cid, _ in accettate:
        testo = testi.get(path_master, "").strip()
        if testo and not testo.endswith((".", "!", "?")):
            testo += "."
        p_piper = os.path.join(piper_dir, "wavs", f"{cid}.wav")
        resample(path_master, p_piper, 22050)
        if testo:
            righe_piper.append(f"wavs/{cid}.wav|{testo}")

        p_gs = os.path.join(gs_dir, f"{cid}.wav")
        resample(path_master, p_gs, 32000)
        if testo:
            righe_gs.append(f"{p_gs}|{args.speaker}|{args.language.upper()}|{testo}")

    with open(os.path.join(piper_dir, "metadata.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(righe_piper) + ("\n" if righe_piper else ""))
    with open(os.path.join(gs_dir, "dataset.list"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(righe_gs) + ("\n" if righe_gs else ""))

    riepilogo.percorsi = {
        "master_44k": wavs,
        "piper_22k": piper_dir,
        "gpt_sovits_32k": gs_dir,
        "report_json": os.path.join(out, "report.json"),
    }

    # Pulizia: gli estratti servivano solo per ritagliare le clip
    shutil.rmtree(lavoro, ignore_errors=True)

    # Suggerimenti sulla quantità
    for modello, (minimo, nota) in CONSIGLI_SECONDI.items():
        if riepilogo.durata_accettata < minimo:
            riepilogo.avvisi.append(
                f"{modello}: hai {riepilogo.durata_accettata:.0f}s di audio pulito, "
                f"ne servono ~{minimo}s ({nota})."
            )

    _scrivi_report(out, riepilogo, time.time() - avvio)

    print(f"\n🎉 Dataset pronto in: {out}")
    print(f"   Clip accettate: {riepilogo.accettate}  ({riepilogo.durata_accettata:.1f}s di parlato pulito)")
    for motivo, n in sorted(riepilogo.scarti.items(), key=lambda kv: -kv[1]):
        print(f"   Scartate per {motivo}: {n}")
    for avviso in riepilogo.avvisi:
        print(f"   ⚠️  {avviso}")
    print(f"   Report: {os.path.join(out, 'report.txt')}\n")
    return 0


def _scrivi_report(out: str, r: Riepilogo, secondi: float) -> None:
    dati = {
        "sorgenti": r.sorgenti,
        "durata_sorgenti_sec": round(r.durata_sorgenti, 1),
        "segmenti_trovati": r.segmenti_trovati,
        "clip_accettate": r.accettate,
        "durata_accettata_sec": round(r.durata_accettata, 1),
        "scarti": r.scarti,
        "percorsi": r.percorsi,
        "avvisi": r.avvisi,
        "tempo_elaborazione_sec": round(secondi, 1),
    }
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(dati, fh, ensure_ascii=False, indent=2)

    righe = [
        "=== REPORT DATASET VOCALE ===",
        f"File analizzati     : {len(r.sorgenti)} ({r.durata_sorgenti:.1f}s totali)",
        f"Segmenti rilevati   : {r.segmenti_trovati}",
        f"Clip accettate      : {r.accettate} ({r.durata_accettata:.1f}s di parlato pulito)",
        "",
        "Scarti:",
    ]
    if r.scarti:
        for motivo, n in sorted(r.scarti.items(), key=lambda kv: -kv[1]):
            righe.append(f"  - {motivo}: {n}")
    else:
        righe.append("  - nessuno")
    if r.percorsi:
        righe += ["", "Export:"]
        for nome, percorso in r.percorsi.items():
            righe.append(f"  - {nome}: {percorso}")
    if r.avvisi:
        righe += ["", "Avvisi:"]
        righe += [f"  - {a}" for a in r.avvisi]
    righe += [
        "",
        "Prossimo passo: addestramento su GPU (vedi docs/VOCE_ADDESTRAMENTO.md).",
        "Questa cartella contiene la voce di una persona reale: non distribuirla.",
    ]
    with open(os.path.join(out, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(righe) + "\n")


if __name__ == "__main__":
    sys.exit(main())
