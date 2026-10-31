#!/usr/bin/env python3
"""
voice_convert.py - Converte una voce qualsiasi nel timbro di Franchino (zero-shot)
==================================================================================
Usa **kNN-VC** (Baas et al., Interspeech 2023): prende le feature self-supervised
(WavLM) dell'audio sorgente e le sostituisce con quelle più vicine prese dalla
voce di riferimento. Non addestra nulla: bastano 10-60 secondi di riferimento.

Perché serve
------------
Equalizzare e abbassare il pitch **non cambia chi sta parlando**: cambia la curva
spettrale, non l'identità. Questa è l'unica strada praticabile con poco materiale
per far suonare la voce del TTS come quella di Franchino.

Uso
---
    # sorgente = audio del TTS, riferimento = clip pulite della voce vera
    python scripts/voice_convert.py \
        --reference dataset/_prova_90s/wavs \
        --source tts_diego.wav \
        --out franchino.wav

    # più materiale di riferimento = qualità migliore (fino a ~5 minuti)
    python scripts/voice_convert.py --reference cartella_favole/ --source tts.wav --out out.wav

Opzioni utili:
    --topk 4           quanti vicini usare (più alto = più vicino al riferimento)
    --upsample 44100   ricampiona l'output (il vocoder lavora a 16 kHz)
    --device cpu|cuda  default: cuda se disponibile

Prerequisiti (una volta sola)
-----------------------------
    pip install torch torchaudio numpy
    git clone --depth 1 https://github.com/bshall/knn-vc.git vendor/knn-vc
    # il repo richiede Python >= 3.10; con Python 3.9 aggiungere in testa a
    # vendor/knn-vc/matcher.py la riga:  from __future__ import annotations

I pesi (WavLM-Large ~1.2 GB + HiFi-GAN) si scaricano da soli al primo avvio in
~/.cache/torch. Il primo caricamento richiede ~1 minuto; le conversioni successive
nello stesso processo costano ~0.15x il tempo reale (misurato su Apple Silicon).

⚠️ La voce di una persona reale: l'output non va distribuito. La cartella dataset/
   è già in .gitignore.
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac"}
SR_MODEL = 16000  # kNN-VC lavora a 16 kHz
DEFAULT_REPO = Path(__file__).resolve().parent.parent / "vendor" / "knn-vc"


# =============================================================================
# Input: raccolta file e ricampionamento
# =============================================================================
def raccogli_audio(percorsi: List[str]) -> List[str]:
    """Espande cartelle e tiene solo i file audio."""
    trovati: List[str] = []
    for p in percorsi:
        path = Path(p)
        if path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.suffix.lower() in AUDIO_EXT:
                    trovati.append(str(f))
        elif path.is_file():
            trovati.append(str(path))
        else:
            raise FileNotFoundError(f"Non trovato: {p}")
    if not trovati:
        raise ValueError("Nessun file audio trovato nei percorsi indicati.")
    return trovati


def prepara_16k(paths: List[str], destinazione: Path, torchaudio) -> List[str]:
    """Porta ogni file a 16 kHz mono: kNN-VC non accetta altro sample rate."""
    destinazione.mkdir(parents=True, exist_ok=True)
    pronti: List[str] = []
    for i, path in enumerate(paths):
        wav, sr = torchaudio.load(path)
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        if sr != SR_MODEL:
            wav = torchaudio.functional.resample(wav, sr, SR_MODEL)
        uscita = destinazione / f"{i:03d}.wav"
        torchaudio.save(str(uscita), wav, SR_MODEL)
        pronti.append(str(uscita))
    return pronti


def durata_totale(paths: List[str], torchaudio) -> float:
    totale = 0.0
    for path in paths:
        info = torchaudio.info(path)
        totale += info.num_frames / info.sample_rate
    return totale


# =============================================================================
# Modello
# =============================================================================
def carica_modello(repo: Path, device: str, knn_vc_module=None):
    """Carica kNN-VC (WavLM-Large + HiFi-GAN). Ritorna il modello pronto."""
    if knn_vc_module is not None:  # iniettabile nei test
        return knn_vc_module

    if not repo.is_dir():
        raise FileNotFoundError(
            f"Repo kNN-VC non trovato in {repo}.\n"
            "Scaricalo con:\n"
            "  git clone --depth 1 https://github.com/bshall/knn-vc.git vendor/knn-vc"
        )
    sys.path.insert(0, str(repo))
    try:
        import hubconf  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            f"Impossibile importare kNN-VC da {repo}: {e}\n"
            "Serve Python >= 3.10, oppure aggiungi in testa a "
            "vendor/knn-vc/matcher.py:  from __future__ import annotations"
        ) from e

    return hubconf.knn_vc(pretrained=True, progress=False, prematched=True, device=device)


# =============================================================================
# Conversione
# =============================================================================
def get_matching_set_robusto(modello, riferimenti: List[str]):
    """Come `get_matching_set`, ma una clip problematica non fa fallire tutto.

    kNN-VC applica un VAD a ogni riferimento: se in una clip non rileva parlato
    restituisce un tensore vuoto e `torch.concat` esplode, buttando via anche le
    altre cento clip buone. Qui si filtra una per una.
    """
    import torch

    frames = []
    scartate = 0
    for path in riferimenti:
        try:
            frames.append(modello.get_features(path, weights=modello.weighting))
        except Exception as e:
            scartate += 1
            if scartate <= 3:
                print(f"   ⚠️  clip scartata ({Path(path).name}): {str(e)[:70]}", flush=True)
    if not frames:
        raise RuntimeError("Nessuna clip di riferimento utilizzabile (VAD senza parlato).")
    if scartate:
        print(f"   ℹ️  {scartate} clip scartate su {len(riferimenti)}", flush=True)
    return torch.concat(frames, dim=0).cpu()


def converti(modello, sorgente: List[str], riferimento: List[str], topk: int = 4) -> "object":
    """Esegue la conversione e restituisce il waveform (tensor 1D a 16 kHz)."""
    query = modello.get_features(sorgente[0])
    matching = get_matching_set_robusto(modello, riferimento)
    return modello.match(query, matching, topk=topk)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Converte un audio nel timbro di Franchino con kNN-VC (zero-shot).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--reference", nargs="+", required=True,
                    help="file o cartelle con la voce di riferimento (10 s - 5 min)")
    ap.add_argument("--source", nargs="+", required=True,
                    help="audio da convertire (es. la voce del TTS)")
    ap.add_argument("--out", required=True, help="file WAV di destinazione")
    ap.add_argument("--topk", type=int, default=4, help="vicini usati nel matching (default 4)")
    ap.add_argument("--upsample", type=int, default=0,
                    help="ricampiona l'output a questo rate (es. 44100)")
    ap.add_argument("--device", default="cpu", help="cpu o cuda")
    ap.add_argument("--repo", default=str(DEFAULT_REPO), help="percorso del repo kNN-VC")
    args = ap.parse_args(argv)

    try:
        import torch
        import torchaudio
    except ImportError as e:
        print(f"❌ Serve torch + torchaudio: {e}", file=sys.stderr)
        print("   pip install torch torchaudio numpy", file=sys.stderr)
        return 2

    try:
        rif_paths = raccogli_audio(args.reference)
        src_paths = raccogli_audio(args.source)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="franchino_vc_") as tmp:
        tmp_dir = Path(tmp)
        print(f"📁 Preparazione: {len(rif_paths)} riferimenti, {len(src_paths)} sorgenti", flush=True)
        rif_16k = prepara_16k(rif_paths, tmp_dir / "ref", torchaudio)
        src_16k = prepara_16k(src_paths, tmp_dir / "src", torchaudio)

        secondi_rif = durata_totale(rif_16k, torchaudio)
        print(f"🎯 Riferimento: {secondi_rif:.1f}s di voce target", flush=True)
        if secondi_rif < 10:
            print("   ⚠️  meno di 10 s: la qualità sarà bassa", flush=True)
        elif secondi_rif < 60:
            print("   ℹ️  con 1-5 minuti il risultato migliora molto", flush=True)

        t0 = time.perf_counter()
        print("⏳ Caricamento modelli (la prima volta scarica ~1.2 GB)...", flush=True)
        modello = carica_modello(Path(args.repo), args.device)
        print(f"   modelli pronti in {time.perf_counter() - t0:.1f}s", flush=True)

        t1 = time.perf_counter()
        audio = converti(modello, src_16k, rif_16k, topk=args.topk)
        tempo = time.perf_counter() - t1

        if args.upsample and args.upsample != SR_MODEL:
            audio = torchaudio.functional.resample(audio.unsqueeze(0), SR_MODEL, args.upsample)
            rate_uscita = args.upsample
        else:
            audio = audio.unsqueeze(0)
            rate_uscita = SR_MODEL

        uscita = Path(args.out)
        uscita.parent.mkdir(parents=True, exist_ok=True)
        # 16 bit PCM: il float32 non è leggibile dalla libreria standard `wave`
        # né da buona parte degli strumenti audio che si usano a valle.
        torchaudio.save(str(uscita), audio.cpu(), rate_uscita,
                        encoding="PCM_S", bits_per_sample=16)

        durata = audio.shape[-1] / rate_uscita
        print(f"✅ Salvato: {uscita} ({durata:.1f}s a {rate_uscita} Hz)", flush=True)
        print(f"⏱️  Conversione: {tempo:.1f}s per {durata:.1f}s di parlato "
              f"(x{tempo / max(durata, 0.001):.2f} tempo reale, modelli esclusi)", flush=True)
        if rate_uscita <= SR_MODEL:
            print("   ℹ️  Il vocoder di kNN-VC lavora a 16 kHz: usa --upsample 44100 "
                  "se ti serve la banda intera (non aggiunge dettaglio, evita solo "
                  "problemi con gli effetti a valle).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
