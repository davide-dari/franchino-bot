#!/usr/bin/env python3
"""
mine_phrases.py - Estrae le frasi iconiche di Franchino da registrazioni lunghe
==============================================================================
Trascrive uno o più file (idealmente la traccia voce separata con demucs),
cerca le frasi simbolo ("magia", "chiudi gli occhi", "figli della musica"...),
ritaglia i momenti migliori e li masterizza come singoli campioni audio.

Perché così
-----------
Per le frasi fisse il TTS è un ripiego: la registrazione vera è la qualità
massima possibile. Questo strumento costruisce un "phrase pack": tanti "Magia!"
delle tonalità realmente pronunciate, pronti da usare come effetto sonoro o
come risposta vocale a latenza zero.

Cosa fa, in ordine
------------------
1. TRASCRIZIONE con faster-whisper (timestamp a livello di parola, italiano)
2. RICERCA delle frasi chiave nel flusso di parole (accenti/punteggiatura ignorati)
3. RITAGLIO con margini calibrati sulla sillaba (attacco e coda inclusi)
4. PUNTEGGIO di pulizia: distanza fra banda intera e bassi <120 Hz, che misura
   quanta musica è rimasta; più alta = più pulito
5. MASTERIZZAZIONE: taglio dei silenzi ai bordi, normalizzazione di picco,
   fade anti-click, esportazione WAV 44.1 kHz mono + anteprima mp3
6. ETICHETTA DI TONALITÀ: misura la F0 di ogni clip e la classifica
   (grave / media / acuta), così si scegli quella giusta per il momento
7. MANIFEST JSON con tutto: file, testo, tempi, punteggio, F0

Uso
---
    python scripts/mine_phrases.py --input voce.wav --out frasi/
    python scripts/mine_phrases.py --input a.wav b.wav --out frasi/ --min-score 12
    python scripts/mine_phrases.py --input voce.wav --out frasi/ --model medium

Dipendenze: faster-whisper, ffmpeg, numpy (per la F0).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

# =============================================================================
# Le frasi simbolo di Franchino (confronto su testo normalizzato)
# =============================================================================
FRASI_CHIAVE: Dict[str, Tuple[str, ...]] = {
    # chiave -> varianti attese nel parlato
    "magia": ("magia", "magiaaa", "magiaa"),
    "chiudi_gli_occhi": ("chiudi gli occhi", "chiudete gli occhi"),
    "vola_con_me": ("vola con me", "volare con me", "vola insieme a me"),
    "favola": ("una favola", "favola", "vivere una favola"),
    "da_stasera": ("da stasera",),
    "niente_sara_come_prima": ("niente sara piu come prima", "niente sara come prima"),
    "figli_della_musica": ("figli della musica", "figli della notte", "figli delle stelle"),
    "si_decolla": ("si decolla", "decollare", "decolliamo"),
    "metempsicosi": ("metempsicosi",),
    "insomnia": ("insomnia", "insonia"),
    "morena": ("morena",),
    "battito_novanta": ("battito novanta", "battito 90"),
    "signore_e_signori": ("signore e signori",),
    "ragazzi_della_notte": ("ragazzi della notte", "ragazzi della notte"),
    "alza_il_volume": ("alza il volume", "alzate il volume"),
    "musica_ti_porta": ("la musica ti porta", "musica ti porta fuori"),
    "buonanotte": ("buonanotte", "buona notte"),
    "grazie": ("grazie", "grazie mille"),
    "andiamo": ("andiamo", "si parte", "si parte"),
}

# Parole troppo comuni da sole: servono solo come parte di una frase più lunga
# (una clip di 0.3 s con "grazie" non è un campione utile)

MIN_DURATA = 0.55
MAX_DURATA = 9.0


@dataclass
class Candidato:
    """Una frase trovata, con tutto quello che serve per valutarla."""

    frase: str
    testo: str
    sorgente: str
    inizio: float
    fine: float
    durata: float
    punteggio_pulizia: float = 0.0
    f0_hz: Optional[float] = None
    tonalita: str = ""
    file_wav: str = ""
    file_mp3: str = ""

    @property
    def ok(self) -> bool:
        return MIN_DURATA <= self.durata <= MAX_DURATA


# =============================================================================
# Utilità
# =============================================================================
def normalizza(testo: str) -> str:
    t = unicodedata.normalize("NFKD", (testo or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("❌ ffmpeg non trovato nel PATH.")
    return exe


def esegui_ffmpeg(args: List[str]) -> str:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y"] + args,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("\n".join(proc.stderr.strip().splitlines()[-4:]))
    return proc.stderr


def distanza_bassi(path: str) -> float:
    """Distanza (dB) fra energia totale e quella sotto 120 Hz: misura la musica residua."""
    def rms(filtro: str) -> Optional[float]:
        af = f"{filtro}astats=metadata=0:reset=0" if filtro else "astats=metadata=0:reset=0"
        stderr = esegui_ffmpeg(["-i", path, "-af", af, "-f", "null", "-"])
        blocco = stderr.split("Overall")[-1] if "Overall" in stderr else stderr
        m = re.search(r"RMS level dB:\s*(-?(?:inf|\d+\.?\d*))", blocco)
        if not m or m.group(1) == "-inf":
            return None
        return float(m.group(1))

    totale = rms("")
    bassi = rms("lowpass=f=120,")
    if totale is None or bassi is None:
        return 0.0
    return round(totale - bassi, 1)


def stima_f0(path: str) -> Optional[float]:
    """F0 mediana via scripts/analyze_voice.py (numpy). None se non disponibile."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import analyze_voice  # type: ignore

        return analyze_voice.analizza(path)["f0_mediana"]
    except Exception:
        return None


def classifica_tonalita(f0: Optional[float], riferimento: float = 197.0) -> str:
    """Grave / media / acuta rispetto alla voce parlata di riferimento."""
    if not f0:
        return "n/d"
    if f0 < riferimento * 0.88:
        return "grave"
    if f0 > riferimento * 1.12:
        return "acuta"
    return "media"


# =============================================================================
# 1-2. Trascrizione e ricerca
# =============================================================================
def trascina_parole(path: str, modello) -> List[dict]:
    """Trascrive e restituisce l'elenco piatto delle parole con i tempi."""
    segmenti, _ = modello.transcribe(
        path,
        language="it",
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )
    parole: List[dict] = []
    for segmento in segmenti:
        for w in (segmento.words or []):
            testo = normalizza(w.word)
            if testo:
                parole.append({"testo": testo, "inizio": float(w.start), "fine": float(w.end)})
    return parole


def cerca_frasi(parole: List[dict]) -> List[Tuple[str, str, float, float]]:
    """Trova le frasi chiave nel flusso di parole. Ritorna (chiave, testo, inizio, fine)."""
    trovate: List[Tuple[str, str, float, float]] = []
    testi = [p["testo"] for p in parole]

    for chiave, varianti in FRASI_CHIAVE.items():
        for variante in varianti:
            tokens = normalizza(variante).split()
            if not tokens:
                continue
            lunghezza = len(tokens)
            for i in range(len(testi) - lunghezza + 1):
                if testi[i:i + lunghezza] == tokens:
                    inizio = parole[i]["inizio"]
                    fine = parole[i + lunghezza - 1]["fine"]
                    originale = " ".join(testi[i:i + lunghezza])
                    trovate.append((chiave, originale, inizio, fine))
    return trovate


# =============================================================================
# 3-5. Ritaglio, punteggio, masterizzazione
# =============================================================================
def ritaglia_e_masterizza(sorgente: str, inizio: float, fine: float, destinazione: str,
                          margine_iniziale: float = 0.22, margine_finale: float = 0.40) -> str:
    """Taglia la frase, toglie i silenzi ai bordi, normalizza e applica i fade."""
    start = max(0.0, inizio - margine_iniziale)
    durata = max(0.15, (fine + margine_finale) - start)
    af = (
        "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
        "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
        "afade=t=in:st=0:d=0.012,"
        # picco a -1 dB: i campioni si mixano con la musica, meglio non saturare
        "dynaudnorm=f=200:g=5:p=0.9,"
        "alimiter=limit=0.89:level=false"
    )
    esegui_ffmpeg([
        "-ss", f"{start:.3f}", "-t", f"{durata:.3f}", "-i", sorgente,
        "-af", af, "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le", destinazione,
    ])
    return destinazione


def durata_file(path: str) -> float:
    stderr = esegui_ffmpeg(["-i", path, "-f", "null", "-"])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# =============================================================================
# Programma principale
# =============================================================================
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Estrae le frasi iconiche di Franchino da registrazioni lunghe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", nargs="+", required=True,
                    help="file vocali (idealmente la traccia voce separata con demucs)")
    ap.add_argument("--out", required=True, help="cartella del phrase pack")
    ap.add_argument("--model", default="small", help="modello whisper (tiny/base/small/medium/large-v3)")
    ap.add_argument("--min-score", type=float, default=6.0,
                    help="distanza minima bassi/banda per accettare la clip (dB)")
    ap.add_argument("--max-per-frase", type=int, default=6,
                    help="quante varianti tenere per ogni frase")
    ap.add_argument("--mp3", action="store_true", default=True, help="crea anche l'anteprima mp3")

    args = ap.parse_args(argv)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("❌ Serve faster-whisper:  pip install faster-whisper", file=sys.stderr)
        return 2

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    print(f"🧠 Carico whisper '{args.model}' (la prima volta scarica il modello)...", flush=True)
    modello = WhisperModel(args.model, device="cpu", compute_type="int8")

    candidati: List[Candidato] = []
    for sorgente in args.input:
        nome = os.path.basename(sorgente)
        print(f"\n🎧 {nome}", flush=True)
        try:
            parole = trascina_parole(sorgente, modello)
        except Exception as e:
            print(f"   ⚠️  trascrizione fallita: {e}", flush=True)
            continue
        print(f"   📝 {len(parole)} parole trascritte", flush=True)

        trovate = cerca_frasi(parole)
        print(f"   🎯 {len(trovate)} occorrenze di frasi chiave", flush=True)

        for chiave, testo, inizio, fine in trovate:
            durata_parlato = fine - inizio
            if durata_parlato + 0.6 < MIN_DURATA or durata_parlato > MAX_DURATA:
                continue
            candidati.append(Candidato(
                frase=chiave, testo=testo, sorgente=nome,
                inizio=round(inizio, 2), fine=round(fine, 2),
                durata=round(durata_parlato, 2),
            ))

    if not candidati:
        print("\n❌ Nessuna frase chiave trovata. Prova con --model medium.", file=sys.stderr)
        return 1

    # Ritaglio + valutazione
    print(f"\n✂️  Valuto {len(candidati)} candidati...\n", flush=True)
    tmp = os.path.join(out, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    per_frase: Dict[str, List[Candidato]] = {}

    for i, cand in enumerate(candidati):
        provvisorio = os.path.join(tmp, f"cand_{i:04d}.wav")
        try:
            sorgente = next(p for p in args.input if os.path.basename(p) == cand.sorgente)
            ritaglia_e_masterizza(sorgente, cand.inizio, cand.fine, provvisorio)
            cand.durata = round(durata_file(provvisorio), 2)
            cand.punteggio_pulizia = distanza_bassi(provvisorio)
        except Exception as e:
            print(f"   ⚠️  candidato {i} scartato: {e}", flush=True)
            continue
        if not cand.ok or cand.punteggio_pulizia < args.min_score:
            continue
        per_frase.setdefault(cand.frase, []).append(cand)

    # Selezione dei migliori per frase
    finali: List[Candidato] = []
    for frase, elenco in per_frase.items():
        elenco.sort(key=lambda c: c.punteggio_pulizia, reverse=True)
        finali.extend(elenco[: args.max_per_frase])

    if not finali:
        print("❌ Nessun candidato ha superato il filtro di pulizia. "
              "Abbassa --min-score o separa meglio la voce.", file=sys.stderr)
        return 1

    # Rinomina definitiva, tonalità e manifest
    contatori: Dict[str, int] = {}
    manifest = []
    for cand in sorted(finali, key=lambda c: (c.frase, c.inizio)):
        contatori[cand.frase] = contatori.get(cand.frase, 0) + 1
        indice = contatori[cand.frase]
        base = f"{cand.frase}_{indice:02d}"
        wav_path = os.path.join(out, base + ".wav")
        sorgente = next(p for p in args.input if os.path.basename(p) == cand.sorgente)
        ritaglia_e_masterizza(sorgente, cand.inizio, cand.fine, wav_path)
        cand.file_wav = wav_path
        cand.f0_hz = stima_f0(wav_path)
        cand.tonalita = classifica_tonalita(cand.f0_hz)
        if args.mp3:
            mp3_path = os.path.join(out, base + ".mp3")
            try:
                esegui_ffmpeg(["-i", wav_path, "-codec:a", "libmp3lame", "-b:a", "192k", mp3_path])
                cand.file_mp3 = mp3_path
            except Exception:
                pass
        manifest.append(asdict(cand))
        print(f"   ✅ {base:28s} {cand.durata:4.2f}s  pulizia {cand.punteggio_pulizia:5.1f} dB  "
              f"F0 {cand.f0_hz or 0:5.1f} Hz ({cand.tonalita})  «{cand.testo}»", flush=True)

    shutil.rmtree(tmp, ignore_errors=True)

    riepilogo = {
        "cartella": out,
        "clip": len(manifest),
        "frasi_trovate": sorted(per_frase.keys()),
        "per_frase": {f: len(v) for f, v in per_frase.items()},
        "candidati_totali": len(candidati),
        "manifest": manifest,
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(riepilogo, fh, ensure_ascii=False, indent=2)

    print(f"\n🎁 Phrase pack pronto: {out}")
    print(f"   {len(manifest)} clip da {len(per_frase)} frasi diverse")
    for frase, elenco in sorted(per_frase.items(), key=lambda kv: -len(kv[1])):
        tonalita = sorted({c.tonalita for c in elenco})
        print(f"   - {frase}: {len(elenco)} varianti ({', '.join(tonalita)})")
    print(f"   Manifest: {os.path.join(out, 'manifest.json')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
