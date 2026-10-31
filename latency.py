"""
latency.py - Telemetria di latenza per stadio della pipeline di Franchino
=========================================================================
Senza numeri si ottimizza a caso. Questo modulo traccia, per ogni richiesta
(vocale o chat), il tempo speso in ciascuno stadio reale:

    stt          riconoscimento vocale (Google Speech / motore locale)
    jack         classificazione deterministica dell'intento
    llm          inferenza Ollama (0 ms quando risponde l'euristica)
    tts          generazione dell'audio vocale (edge-tts / gTTS)
    first_audio  dal riconoscimento all'inizio della riproduzione vocale
    total        durata complessiva della richiesta

Uso (non invasivo, basato su contextvars: funziona anche nei task creati con
asyncio.create_task, che copiano il contesto):

    from latency import latency

    trace = latency.start(src="voice")
    ...      # lavoro
    trace.mark("stt")
    ...      # lavoro
    latency.finish()          # logga la riga riassuntiva

Una richiesta abbandonata (es. audio senza wake word) non produce log: usa
latency.abandon("motivo") per registrarla a livello DEBUG.
"""

import contextvars
import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("Franchino.Latency")

# Soglie oltre le quali lo stadio viene evidenziato come lento (millisecondi).
# Tarate sul target Oracle Ampere A1 (2 vCPU): se un valore le supera, c'è
# qualcosa da guardare.
SLOW_STAGE_MS: Dict[str, float] = {
    "stt": 1200.0,
    "jack": 50.0,
    "llm": 9000.0,
    "tts": 1200.0,
    "first_audio": 2500.0,
    "total": 15000.0,
}

# Stadi che possono arrivare dopo la chiusura della richiesta: il TTS viene
# lanciato in background e l'audio parte quando il player è libero.
LATE_STAGES = ("tts", "first_audio")

_current: contextvars.ContextVar[Optional["RequestTrace"]] = contextvars.ContextVar(
    "franchino_latency_trace", default=None
)


class RequestTrace:
    """Traccia temporale di una singola richiesta."""

    def __init__(self, src: str = "voice", user: str = "", intent: str = ""):
        self.id = uuid.uuid4().hex[:8]
        self.src = src
        self.user = user
        self.intent = intent
        self.started = time.perf_counter()
        self.marks: Dict[str, float] = {}
        self.notes: Dict[str, Any] = {}
        self.finished = False

    # -- misurazione ---------------------------------------------------
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def mark(self, stage: str) -> float:
        """Registra il tempo (ms dall'inizio della richiesta) a cui finisce uno stadio."""
        ms = self.elapsed_ms()
        self.marks[stage] = ms
        if self.finished and stage in LATE_STAGES:
            logger.info(f"req={self.id} src={self.src} late {stage}={ms:.0f}ms")
        return ms

    def duration_ms(self, stage: str) -> Optional[float]:
        """Durata dello stadio: differenza dal marcatore precedente in ordine cronologico."""
        if stage not in self.marks:
            return None
        precedenti = [t for s, t in self.marks.items() if t <= self.marks[stage] and s != stage]
        base = max(precedenti) if precedenti else 0.0
        return self.marks[stage] - base

    # -- annotazioni ---------------------------------------------------
    def set_user(self, user: str):
        if user and not self.user:
            self.user = user

    def set_intent(self, intent: str):
        if intent:
            self.intent = intent

    def note(self, key: str, value: Any):
        self.notes[key] = value

    # -- output --------------------------------------------------------
    def line(self) -> str:
        parti = [f"req={self.id}", f"src={self.src}"]
        if self.user:
            parti.append(f"user={self.user}")
        if self.intent:
            parti.append(f"intent={self.intent}")
        for stage in ("stt", "jack", "llm", "tts", "first_audio"):
            if stage in self.marks:
                parti.append(f"{stage}={self.marks[stage]:.0f}ms")
        parti.append(f"total={self.elapsed_ms() / 1000.0:.2f}s")
        for key, value in self.notes.items():
            parti.append(f"{key}={value}")
        return " ".join(parti)

    def finish(self) -> str:
        riga = self.line()
        self.finished = True
        lenti = [
            f"{stage}={self.marks[stage]:.0f}ms>{SLOW_STAGE_MS[stage]:.0f}ms"
            for stage in self.marks
            if stage in SLOW_STAGE_MS and self.marks[stage] - self._base_for(stage) > SLOW_STAGE_MS[stage]
        ]
        if lenti:
            logger.warning(f"🐢 {riga} | STADI LENTI: {', '.join(lenti)}")
        else:
            logger.info(f"⏱️ {riga}")
        return riga

    def _base_for(self, stage: str) -> float:
        precedenti = [t for s, t in self.marks.items() if t <= self.marks[stage] and s != stage]
        return max(precedenti) if precedenti else 0.0

    def abandon(self, reason: str = "ignored"):
        self.finished = True
        logger.debug(f"req={self.id} src={self.src} abbandonata ({reason}) dopo {self.elapsed_ms():.0f}ms")


class LatencyTelemetry:
    """Facade usata dal resto del bot. Tutti i metodi sono no-op senza traccia attiva."""

    def start(self, src: str = "voice", user: str = "", intent: str = "") -> RequestTrace:
        trace = RequestTrace(src=src, user=user, intent=intent)
        _current.set(trace)
        return trace

    def current(self) -> Optional[RequestTrace]:
        return _current.get()

    def mark(self, stage: str) -> None:
        trace = _current.get()
        if trace is not None:
            trace.mark(stage)

    def set_intent(self, intent: str) -> None:
        trace = _current.get()
        if trace is not None:
            trace.set_intent(intent)

    def set_user(self, user: str) -> None:
        trace = _current.get()
        if trace is not None:
            trace.set_user(user)

    def note(self, key: str, value: Any) -> None:
        trace = _current.get()
        if trace is not None:
            trace.note(key, value)

    def finish(self) -> Optional[str]:
        trace = _current.get()
        if trace is None or trace.finished:
            return None
        try:
            return trace.finish()
        finally:
            _current.set(None)

    def abandon(self, reason: str = "ignored") -> None:
        trace = _current.get()
        if trace is not None and not trace.finished:
            trace.abandon(reason)
        _current.set(None)


# Istanza condivisa
latency = LatencyTelemetry()
