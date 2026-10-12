import math
import os
import random
import struct
import tempfile
import wave
from typing import Dict

SFX_DIR = os.path.join(tempfile.gettempdir(), "franchino_sfx")

SOUNDBOARD_EFFECTS: Dict[str, Dict[str, str]] = {
    "airhorn": {
        "label": "Trombetta DJ (Airhorn)",
        "emoji": "📯",
        "description": "Classica tripla trombetta da stadio / DJ hype",
    },
    "siren": {
        "label": "Sirena Rave Club",
        "emoji": "🚨",
        "description": "Sirena elettronica da discoteca",
    },
    "drop": {
        "label": "Sub-Bass Drop",
        "emoji": "💣",
        "description": "Esplosione di bassi profondi stile festival EDM",
    },
    "drumroll": {
        "label": "Rullo di Tamburi (Ba-Dum-Tss)",
        "emoji": "🥁",
        "description": "Rullo di suspense con piatto finale per battute",
    },
    "applause": {
        "label": "Applausi & Ovazione",
        "emoji": "👏",
        "description": "Applausi scroscianti del pubblico in sala",
    },
    "fail": {
        "label": "Fail / Sad Trombone",
        "emoji": "🎺",
        "description": "Wah-wah-wah-waaaah per figure epiche",
    },
    "victory": {
        "label": "Fanfara Vittoria",
        "emoji": "🏆",
        "description": "Squillo trionfale da vincitore",
    },
    "laser": {
        "label": "Laser Space Rave",
        "emoji": "🔫",
        "description": "Raffiche laser sci-fi da console DJ",
    },
    "censor": {
        "label": "Beep Censura TV",
        "emoji": "🤬",
        "description": "Classico BEEP televisivo di censura",
    },
    "whistle": {
        "label": "Fischio Arbitro",
        "emoji": "⚽",
        "description": "Fischio deciso da arbitro per fermare tutto",
    },
    "laugh": {
        "label": "Risata Sitcom",
        "emoji": "😂",
        "description": "Jingle comico + risata contagiosa",
    },
    "booo": {
        "label": "Buzzer Errore / Dissenso",
        "emoji": "👎",
        "description": "Sirena grave da risposta sbagliata / buuu",
    },
}


def _clamp16(val: float) -> int:
    return max(-32767, min(32767, int(val * 32767.0)))


def _write_stereo_wav(filepath: str, left_samples: list[float], right_samples: list[float], sample_rate: int = 48000):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        buf = bytearray(len(left_samples) * 4)
        idx = 0
        for l_val, r_val in zip(left_samples, right_samples):
            struct.pack_into("<hh", buf, idx, _clamp16(l_val), _clamp16(r_val))
            idx += 4
        wf.writeframes(buf)


def ensure_sfx_file(sfx_key: str) -> str:
    """Genera (se non esiste già in cache) e restituisce il file WAV a 48kHz stereo per l'effetto sonoro richiesto."""
    if sfx_key not in SOUNDBOARD_EFFECTS:
        sfx_key = "airhorn"

    os.makedirs(SFX_DIR, exist_ok=True)
    filepath = os.path.join(SFX_DIR, f"sfx_{sfx_key}_v1.wav")
    if os.path.isfile(filepath) and os.path.getsize(filepath) > 1000:
        return filepath

    sr = 48000
    left: list[float] = []
    right: list[float] = []

    if sfx_key == "airhorn":
        # Triplo colpo di trombetta da stadio (BWAH - BWAH - BWAAAAAH!)
        bursts = [(0.0, 0.16), (0.21, 0.37), (0.42, 1.25)]
        dur = 1.35
        freqs = [370.0, 466.16, 554.37, 740.0, 932.32]
        for i in range(int(sr * dur)):
            t = i / sr
            env = 0.0
            for b_start, b_end in bursts:
                if b_start <= t <= b_end:
                    rel = (t - b_start) / (b_end - b_start)
                    env = min(1.0, rel * 25.0) * (1.0 - 0.25 * rel)
                    break
            if env > 0:
                sig = 0.0
                for idx_f, f in enumerate(freqs):
                    # Leggero pitch bend iniziale tipico dell'aria compressa
                    bend = 1.0 - 0.04 * math.exp(-t * 18.0)
                    phase = 2.0 * math.pi * f * bend * t
                    # Armoniche stile ottone/sawtooth
                    sig += (math.sin(phase) + 0.45 * math.sin(2 * phase) + 0.25 * math.sin(3 * phase)) / len(freqs)
                val = math.tanh(sig * 2.4) * env * 0.85
            else:
                val = 0.0
            left.append(val)
            right.append(val)

    elif sfx_key == "siren":
        # Sirena Rave (oscillazione esponenziale 550Hz <-> 1350Hz)
        dur = 2.2
        phase = 0.0
        for i in range(int(sr * dur)):
            t = i / sr
            mod = 0.5 * (1.0 - math.cos(2.0 * math.pi * 1.4 * t))
            freq = 520.0 + 860.0 * mod
            phase += 2.0 * math.pi * freq / sr
            env = min(1.0, t * 12.0) * min(1.0, (dur - t) * 8.0)
            sig = math.sin(phase) + 0.35 * math.sin(2.0 * phase) + 0.15 * math.sin(3.0 * phase)
            pan = 0.5 + 0.35 * math.sin(2.0 * math.pi * 2.8 * t)
            val = math.tanh(sig * 1.5) * env * 0.78
            left.append(val * pan)
            right.append(val * (1.0 - pan * 0.5))

    elif sfx_key == "drop":
        # Sub-Bass Drop EDM (170Hz -> 30Hz + impatto iniziale)
        dur = 1.8
        phase = 0.0
        rng = random.Random(42)
        for i in range(int(sr * dur)):
            t = i / sr
            freq = 30.0 + 150.0 * math.exp(-t * 2.6)
            phase += 2.0 * math.pi * freq / sr
            env = min(1.0, t * 80.0) * math.exp(-t * 1.15)
            impact = (rng.uniform(-1.0, 1.0) * math.exp(-t * 22.0)) * 0.5
            sub = math.sin(phase) + 0.4 * math.sin(2.0 * phase)
            val = math.tanh((sub + impact) * 2.0) * env * 0.90
            left.append(val)
            right.append(val)

    elif sfx_key == "drumroll":
        # Rullo di tamburi (0.0 - 1.35s) + Ba-Dum (1.40 - 1.65s) + Piatto Crash (1.70 - 2.5s)
        dur = 2.5
        rng = random.Random(77)
        for i in range(int(sr * dur)):
            t = i / sr
            val = 0.0
            if t < 1.35:
                # Rullo rapido (22 colpi al secondo in crescendo)
                hit_pos = (t * 22.0) % 1.0
                hit_env = math.exp(-hit_pos * 5.5) * (0.35 + 0.65 * (t / 1.35))
                noise = rng.uniform(-1.0, 1.0)
                body = math.sin(2.0 * math.pi * 185.0 * t)
                val = (0.7 * noise + 0.3 * body) * hit_env * 0.75
            elif 1.40 <= t < 1.55:
                # "Ba" (Tom alto)
                dt = t - 1.40
                f = 160.0 * math.exp(-dt * 6.0) + 90.0
                val = math.sin(2.0 * math.pi * f * dt) * math.exp(-dt * 14.0) * 0.9
            elif 1.56 <= t < 1.72:
                # "Dum" (Cassa/Tom basso)
                dt = t - 1.56
                f = 120.0 * math.exp(-dt * 8.0) + 55.0
                val = math.sin(2.0 * math.pi * f * dt) * math.exp(-dt * 12.0) * 0.95
            elif t >= 1.72:
                # "TSSSS!" (Piatto Crash metallico)
                dt = t - 1.72
                cymbal_env = math.exp(-dt * 3.8)
                shimmer = (
                    math.sin(2 * math.pi * 3420 * dt)
                    + math.sin(2 * math.pi * 5180 * dt)
                    + math.sin(2 * math.pi * 7810 * dt)
                    + rng.uniform(-1.0, 1.0) * 1.4
                ) * 0.28
                val = shimmer * cymbal_env * 0.85
            left.append(val)
            right.append(val * 0.96)

    elif sfx_key == "applause":
        # Applausi scroscianti stereo (molti battiti di mani sovrapposti con densità realistica)
        dur = 2.4
        n_samples = int(sr * dur)
        left = [0.0] * n_samples
        right = [0.0] * n_samples
        rng = random.Random(101)
        # Genera 240 singoli battiti di mani distribuiti nello spazio stereo
        for _ in range(240):
            start_t = rng.uniform(0.02, dur - 0.18)
            start_idx = int(start_t * sr)
            clap_len = int(rng.uniform(0.035, 0.065) * sr)
            pan_l = rng.uniform(0.3, 1.0)
            pan_r = 1.3 - pan_l
            amp = rng.uniform(0.08, 0.22)
            # Filtro passa-banda semplice sul rumore del singolo battito
            prev = 0.0
            for j in range(clap_len):
                if start_idx + j >= n_samples:
                    break
                env = math.exp(-j / (clap_len * 0.28))
                raw = rng.uniform(-1.0, 1.0) - 0.65 * prev
                prev = raw
                sample = raw * env * amp
                left[start_idx + j] += sample * pan_l
                right[start_idx + j] += sample * pan_r
        # Invilluppo generale crescendo + fade-out
        for i in range(n_samples):
            t = i / sr
            global_env = min(1.0, t * 5.0) * min(1.0, (dur - t) * 2.5)
            left[i] = math.tanh(left[i] * 1.8) * global_env * 0.85
            right[i] = math.tanh(right[i] * 1.8) * global_env * 0.85

    elif sfx_key == "fail":
        # Sad Trombone: Wah - Wah - Wah - Waaaaah (D4 -> C#4 -> C4 -> B3 con glissando e vibrato)
        notes = [
            (0.00, 0.34, 293.66, 293.66),
            (0.38, 0.72, 277.18, 277.18),
            (0.76, 1.10, 261.63, 261.63),
            (1.14, 2.25, 246.94, 215.00),
        ]
        dur = 2.35
        phase = 0.0
        for i in range(int(sr * dur)):
            t = i / sr
            val = 0.0
            for n_start, n_end, f_start, f_end in notes:
                if n_start <= t <= n_end:
                    rel = (t - n_start) / (n_end - n_start)
                    f = f_start + (f_end - f_start) * rel
                    # Vibrato wah-wah
                    f *= 1.0 + 0.018 * math.sin(2.0 * math.pi * 6.5 * (t - n_start))
                    phase += 2.0 * math.pi * f / sr
                    env = min(1.0, (t - n_start) * 25.0) * min(1.0, (n_end - t) * 10.0)
                    sig = (
                        math.sin(phase)
                        + 0.55 * math.sin(2 * phase)
                        + 0.30 * math.sin(3 * phase)
                        + 0.15 * math.sin(4 * phase)
                    ) * 0.48
                    val = sig * env * 0.85
                    break
            left.append(val)
            right.append(val)

    elif sfx_key == "victory":
        # Fanfara Vittoria: Do5 - Mi5 - Sol5 - Do6!
        notes = [
            (0.00, 0.14, [523.25, 659.25]),
            (0.16, 0.30, [659.25, 783.99]),
            (0.32, 0.46, [783.99, 1046.50]),
            (0.50, 1.45, [523.25, 659.25, 783.99, 1046.50]),
        ]
        dur = 1.55
        for i in range(int(sr * dur)):
            t = i / sr
            val = 0.0
            for n_start, n_end, chord in notes:
                if n_start <= t <= n_end:
                    env = min(1.0, (t - n_start) * 40.0) * math.exp(-(t - n_start) * 1.4)
                    sig = 0.0
                    for f in chord:
                        ph = 2.0 * math.pi * f * t
                        sig += (math.sin(ph) + 0.4 * math.sin(2 * ph) + 0.2 * math.sin(3 * ph)) / len(chord)
                    val = sig * env * 0.85
                    break
            left.append(val)
            right.append(val)

    elif sfx_key == "laser":
        # Tripla raffica Laser Sci-Fi Rave
        bursts = [0.0, 0.26, 0.52]
        dur = 1.15
        phase = 0.0
        for i in range(int(sr * dur)):
            t = i / sr
            val = 0.0
            for b in bursts:
                if b <= t < b + 0.24:
                    dt = t - b
                    freq = 160.0 + 2400.0 * math.exp(-dt * 18.0)
                    phase += 2.0 * math.pi * freq / sr
                    env = math.exp(-dt * 7.5)
                    val = math.tanh(math.sin(phase) * 1.8) * env * 0.82
                    break
            left.append(val)
            right.append(val)

    elif sfx_key == "censor":
        # Classico Beep 1000Hz da Censura TV
        dur = 0.85
        for i in range(int(sr * dur)):
            t = i / sr
            env = min(1.0, t * 60.0) * min(1.0, (dur - t) * 60.0)
            val = math.sin(2.0 * math.pi * 1000.0 * t) * env * 0.80
            left.append(val)
            right.append(val)

    elif sfx_key == "whistle":
        # Fischio d'arbitro trillo (2850Hz + 3020Hz + modulazione pallina 42Hz)
        bursts = [(0.0, 0.22), (0.28, 0.95)]
        dur = 1.05
        rng = random.Random(55)
        for i in range(int(sr * dur)):
            t = i / sr
            val = 0.0
            for b_start, b_end in bursts:
                if b_start <= t <= b_end:
                    dt = t - b_start
                    env = min(1.0, dt * 45.0) * min(1.0, (b_end - t) * 25.0)
                    trill = 0.65 + 0.35 * math.sin(2.0 * math.pi * 42.0 * t)
                    tone = 0.6 * math.sin(2.0 * math.pi * 2840.0 * t) + 0.4 * math.sin(2.0 * math.pi * 3010.0 * t)
                    breath = rng.uniform(-0.12, 0.12)
                    val = (tone * trill + breath) * env * 0.78
                    break
            left.append(val)
            right.append(val)

    elif sfx_key == "laugh":
        # Jingle comico rimbalzante (Boing-Boing-Pop!)
        dur = 1.25
        phase = 0.0
        for i in range(int(sr * dur)):
            t = i / sr
            if t < 0.55:
                freq = 280.0 + 320.0 * math.sin(2.0 * math.pi * 5.5 * t) * (t / 0.55)
                phase += 2.0 * math.pi * freq / sr
                val = math.sin(phase) * 0.75
            else:
                dt = t - 0.55
                freq = 440.0 + 440.0 * math.exp(-dt * 6.0)
                phase += 2.0 * math.pi * freq / sr
                val = math.sin(phase) * math.exp(-dt * 4.5) * 0.80
            left.append(val)
            right.append(val)

    else:  # "booo" / Buzzer errore
        dur = 1.15
        for i in range(int(sr * dur)):
            t = i / sr
            env = min(1.0, t * 40.0) * min(1.0, (dur - t) * 15.0)
            # Onda quadra/dente di sega grave stile Buzzer TV (110Hz + 155Hz dissonante)
            sig = 0.0
            for f in (110.0, 156.0):
                ph = 2.0 * math.pi * f * t
                sig += math.sin(ph) + 0.5 * math.sin(2 * ph) + 0.33 * math.sin(3 * ph) + 0.25 * math.sin(4 * ph)
            val = math.tanh(sig * 0.8) * env * 0.80
            left.append(val)
            right.append(val)

    _write_stereo_wav(filepath, left, right, sr)
    return filepath
