#!/usr/bin/env python3
"""
analyze_voice.py - Misura il timbro di una voce (pitch + bilanciamento spettrale)
================================================================================
Serve a tarare la catena DSP di Franchino **sui dati**, invece che a orecchio:
si misura la voce reale (separata con demucs) e la si confronta con quella
prodotta dal TTS + filtri attuali.

Cosa misura
-----------
1. **F0 (pitch)**: frequenza fondamentale per ogni frame voiced, con
   autocorrelazione. Riporta mediana e distribuzione (p10-p90): è il parametro
   che decide se la voce "sembra" quella persona.
2. **LTAS** (Long-Term Average Spectrum): energia media per banda d'ottava.
   È la curva che un equalizzatore deve riprodurre: se le due curve coincidono,
   il timbro percepito è lo stesso.
3. **Picchi formantici** approssimati (F1/F2/F3) fra 200 e 3500 Hz.

Uso
---
    python scripts/analyze_voice.py voce.wav tts.wav [--json]
    python scripts/analyze_voice.py --compare target.wav output.wav

Richiede `numpy` (unica dipendenza). Legge WAV PCM 8/16/32 bit con la stdlib.
"""

import argparse
import json
import math
import os
import sys
import wave
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    sys.exit(
        "❌ Serve numpy per l'analisi spettrale.\n"
        "   pip install numpy\n"
        "(è l'unica dipendenza di questo script)"
    )

# Bande d'ottava (centro in Hz) usate per la curva LTAS
BANDE = (63, 125, 250, 500, 1000, 2000, 4000, 8000)

# Bande su cui il confronto è affidabile: 63/125 Hz vengono svuotate dalla
# separazione vocale (demucs toglie i bassi) e 8 kHz è tagliato dal codec mp3
# della sorgente. In mezzo la misura è solida e non va falsata.
BANDE_AFFIDABILI = (250, 500, 1000, 2000, 4000)

# Intervallo plausibile per una voce umana
F0_MIN = 60.0
F0_MAX = 400.0

FRAME_SEC = 0.040     # 40 ms
HOP_SEC = 0.020       # 20 ms


# =============================================================================
# Lettura audio
# =============================================================================
def read_wav(path: str) -> Tuple["np.ndarray", int]:
    """Legge un WAV PCM (8/16/32 bit) e lo restituisce come float mono in [-1, 1]."""
    with wave.open(path, "rb") as wf:
        canali = wf.getnchannels()
        larghezza = wf.getsampwidth()
        rate = wf.getframerate()
        grezzo = wf.readframes(wf.getnframes())

    if larghezza == 1:
        dati = (np.frombuffer(grezzo, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif larghezza == 2:
        dati = np.frombuffer(grezzo, dtype="<i2").astype(np.float32) / 32768.0
    elif larghezza == 4:
        dati = np.frombuffer(grezzo, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Profondità non supportata: {larghezza * 8} bit")

    if canali > 1:
        dati = dati.reshape(-1, canali).mean(axis=1)
    return dati, rate


# =============================================================================
# Analisi
# =============================================================================
def stima_f0(segnale: "np.ndarray", rate: int) -> List[float]:
    """F0 per frame (autocorrelazione), tenendo solo i frame con voce."""
    lun = int(FRAME_SEC * rate)
    hop = int(HOP_SEC * rate)
    if len(segnale) < lun:
        return []

    valori: List[float] = []
    soglia_energia = max(1e-4, float(np.sqrt(np.mean(segnale ** 2))) * 0.35)
    min_lag = int(rate / F0_MAX)
    max_lag = int(rate / F0_MIN)

    for inizio in range(0, len(segnale) - lun, hop):
        frame = segnale[inizio:inizio + lun]
        energia = float(np.sqrt(np.mean(frame ** 2)))
        if energia < soglia_energia:
            continue

        frame = frame - frame.mean()
        corr = np.correlate(frame, frame, mode="full")[lun - 1:]
        if corr[0] <= 0:
            continue
        corr = corr / corr[0]

        finestra = corr[min_lag:min(max_lag, len(corr))]
        if len(finestra) < 3:
            continue
        picco = int(np.argmax(finestra)) + min_lag
        # Confidenza: l'autocorrelazione deve avere un picco netto
        if corr[picco] < 0.30:
            continue
        valori.append(rate / picco)

    return valori


def ltas(segnale: "np.ndarray", rate: int) -> Dict[int, float]:
    """Energia media per banda d'ottava (dBFS relativa alla banda più forte)."""
    lun = int(FRAME_SEC * rate)
    hop = int(HOP_SEC * rate)
    if len(segnale) < lun:
        return {b: -120.0 for b in BANDE}

    finestra = np.hanning(lun)
    accumulo: Optional["np.ndarray"] = None
    frames = 0

    for inizio in range(0, len(segnale) - lun, hop):
        frame = segnale[inizio:inizio + lun] * finestra
        spettro = np.abs(np.fft.rfft(frame)) ** 2
        accumulo = spettro if accumulo is None else accumulo + spettro
        frames += 1

    if accumulo is None or frames == 0:
        return {b: -120.0 for b in BANDE}

    spettro_medio = accumulo / frames
    frequenze = np.fft.rfftfreq(lun, 1.0 / rate)

    bande: Dict[int, float] = {}
    for centro in BANDE:
        basso, alto = centro / math.sqrt(2), centro * math.sqrt(2)
        maschera = (frequenze >= basso) & (frequenze < alto)
        if not np.any(maschera):
            bande[centro] = -120.0
            continue
        energia = float(np.sum(spettro_medio[maschera]))
        bande[centro] = 10.0 * math.log10(energia + 1e-20)

    # Normalizza sulla banda più energica: interessa la forma, non il volume
    massimo = max(bande.values()) if bande else 0.0
    return {b: round(v - massimo, 1) for b, v in bande.items()}


def picchi_formantici(segnale: "np.ndarray", rate: int, quanti: int = 3) -> List[float]:
    """Stima grezza di F1/F2/F3: massimi locali dello spettro medio fra 200 e 3500 Hz."""
    lun = int(0.040 * rate)
    hop = int(0.020 * rate)
    if len(segnale) < lun:
        return []

    finestra = np.hanning(lun)
    accumulo: Optional["np.ndarray"] = None
    frames = 0
    for inizio in range(0, len(segnale) - lun, hop):
        spettro = np.abs(np.fft.rfft(segnale[inizio:inizio + lun] * finestra))
        accumulo = spettro if accumulo is None else accumulo + spettro
        frames += 1
    if accumulo is None or frames == 0:
        return []

    medio = accumulo / frames
    frequenze = np.fft.rfftfreq(lun, 1.0 / rate)
    # Smorzamento per non catturare singole armoniche
    nucleo = np.ones(9) / 9.0
    medio = np.convolve(medio, nucleo, mode="same")

    intervallo = (frequenze >= 200) & (frequenze <= 3500)
    idx = np.where(intervallo)[0]
    if len(idx) < 3:
        return []

    candidati: List[Tuple[float, float]] = []
    for i in idx[1:-1]:
        if medio[i] > medio[i - 1] and medio[i] > medio[i + 1]:
            candidati.append((float(medio[i]), float(frequenze[i])))
    candidati.sort(reverse=True)
    picchi = sorted(f for _, f in candidati[:quanti])
    return [round(f) for f in picchi]


def analizza(path: str) -> dict:
    segnale, rate = read_wav(path)
    if len(segnale) == 0:
        raise ValueError(f"{path}: file vuoto")
    f0 = stima_f0(segnale, rate)
    durata = len(segnale) / rate
    return {
        "file": os.path.basename(path),
        "durata_sec": round(durata, 2),
        "rate": rate,
        "f0_mediana": round(float(np.median(f0)), 1) if f0 else None,
        "f0_media": round(float(np.mean(f0)), 1) if f0 else None,
        "f0_p10": round(float(np.percentile(f0, 10)), 1) if f0 else None,
        "f0_p90": round(float(np.percentile(f0, 90)), 1) if f0 else None,
        "frame_con_voce": len(f0),
        "ltas_db": ltas(segnale, rate),
        "formanti_hz": picchi_formantici(segnale, rate),
    }


# =============================================================================
# Confronto e output
# =============================================================================
def distanza_ltas(a: dict, b: dict, bande=None) -> float:
    """Distanza media (dB) fra le due curve spettrali: più bassa = timbro più simile."""
    bande = [x for x in (bande or BANDE) if x in a and x in b]
    if not bande:
        return float("nan")
    diff = [abs(a[x] - b[x]) for x in bande]
    return round(sum(diff) / len(diff), 2)


def stampa(riferimento: dict, altra: Optional[dict] = None) -> None:
    def riga(m: dict) -> str:
        f0 = m["f0_mediana"]
        f0_txt = f"{f0:.0f} Hz" if f0 else "n/d"
        rng = f"{m['f0_p10']:.0f}-{m['f0_p90']:.0f} Hz" if f0 else "n/d"
        form = " / ".join(str(f) for f in m["formanti_hz"]) or "n/d"
        return f"{f0_txt:>9} {rng:>15} {form:>16}"

    print("\n=== PITCH e FORMANTI ===")
    print(f"{'file':32s} {'F0 mediana':>9} {'F0 p10-p90':>15} {'F1/F2/F3':>16}")
    print(f"{riferimento['file'][:32]:32s} {riga(riferimento)}")
    if altra:
        print(f"{altra['file'][:32]:32s} {riga(altra)}")

    print("\n=== BILANCIAMENTO SPETTRALE (LTAS, dB relativi) ===")
    intest = "".join(f"{b:>8}" for b in BANDE)
    print(f"{'file':32s}{intest}")
    for m in (riferimento, altra) if altra else (riferimento,):
        valori = "".join(f"{m['ltas_db'].get(b, float('nan')):>8.1f}" for b in BANDE)
        print(f"{m['file'][:32]:32s}{valori}")

    if altra:
        delta = "".join(
            f"{altra['ltas_db'].get(b, float('nan')) - riferimento['ltas_db'].get(b, float('nan')):>+8.1f}"
            for b in BANDE
        )
        print(f"{'  DIFFERENZA (dB)':32s}{delta}")
        print(f"\n  Distanza media (tutte le bande)          : {distanza_ltas(riferimento['ltas_db'], altra['ltas_db']):.2f} dB")
        print(f"  Distanza media (250-4000 Hz, affidabile) : {distanza_ltas(riferimento['ltas_db'], altra['ltas_db'], BANDE_AFFIDABILI):.2f} dB")
        if riferimento["f0_mediana"] and altra["f0_mediana"]:
            rapporto = altra["f0_mediana"] / riferimento["f0_mediana"]
            semitoni = 12 * math.log2(rapporto)
            print(
                f"  Pitch: {altra['f0_mediana']:.0f} Hz vs {riferimento['f0_mediana']:.0f} Hz "
                f"→ {semitoni:+.1f} semitoni (per allinearsi serve un fattore {rapporto:.3f}x)"
            )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Misura pitch e bilanciamento spettrale di una voce.")
    ap.add_argument("file", nargs="+", help="uno o più WAV da analizzare")
    ap.add_argument("--compare", action="store_true",
                    help="confronta il primo file (riferimento) con gli altri")
    ap.add_argument("--json", action="store_true", help="stampa i risultati in JSON")
    args = ap.parse_args(argv)

    misure = []
    for path in args.file:
        try:
            misure.append(analizza(path))
        except Exception as e:
            print(f"⚠️  {path}: {e}")

    if not misure:
        return 1

    if args.json:
        print(json.dumps(misure, ensure_ascii=False, indent=2))
        return 0

    if args.compare and len(misure) >= 2:
        for altra in misure[1:]:
            stampa(misure[0], altra)
    else:
        for m in misure:
            stampa(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
