"""
memoria.py - La memoria lunga di Franchino
==========================================
Franchino non è un assistente che dimentica: se qualcuno lo tratta male (lo
manda via, lo insulta, lo sfotte) se lo ricorda e risponde a tono. La memoria è
per utente, persistente su disco e **decade nel tempo**: chi si scusa torna
amico, chi insiste alza il livello.

Come funziona:
- `detect_offense()` classifica la frase (insulto / minaccia / sfottò / congedo)
  con un peso. Le frasi legittime ("stop", "pausa", "metti m2o") non matchano.
- Ogni offesa aggiunge punti al profilo dell'utente; i punti si dimezzano ogni
  24 ore senza nuove offese (memoria lunga, ma non eterna).
- Il livello (0-3) guida la risposta: ironica → ferma → fredda.
- `nota_prompt()` passa il contesto al cervello AI, così anche le risposte
  generative tengono conto dello storico.

File dati: data/franchino_memoria.json (scrittura atomica).
"""

import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("Franchino.Memoria")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STORE_PATH = os.path.join(DATA_DIR, "franchino_memoria.json")

# Mezza vita della memoria: ogni 24h senza offese i punti si dimezzano
DECAY_HALF_LIFE_SEC = 24 * 3600
MAX_UTENTI = 500          # limite anti-crescita infinita del file
MAX_ESEMPI = 3            # quante frasi offensiva ricordare per utente

# Soglie dei livelli sui punti (dopo il decadimento)
SOGLIA_LIVELLO_1 = 1.5
SOGLIA_LIVELLO_2 = 4.0
SOGLIA_LIVELLO_3 = 8.0

KIND_INSULTO = "insulto"
KIND_MINACCIA = "minaccia"
KIND_SFOTTO = "sfotto"
KIND_CONGEDO = "congedo"

PESI = {
    KIND_INSULTO: 3.0,
    KIND_MINACCIA: 3.5,
    KIND_SFOTTO: 2.0,
    KIND_CONGEDO: 1.0,
}

ETICHETTE = {
    KIND_INSULTO: "insulto",
    KIND_MINACCIA: "minaccia",
    KIND_SFOTTO: "sfottò",
    KIND_CONGEDO: "congedo",
}


def _patterns(*items: str) -> re.Pattern:
    """Compila una regex con confini di parola (robusta rispetto allo STT)."""
    corpo = "|".join(items)
    return re.compile(rf"(?<![a-z0-9])(?:{corpo})(?![a-z0-9])", re.IGNORECASE)


# --- Insulti veri e propri -------------------------------------------------
_INSULTI = _patterns(
    r"coglion\w*", r"cazzone", r"cazzona", r"stronz\w*", r"cretin\w*", r"idiota\w*",
    r"imbecille", r"deficient\w*", r"ritardat\w*", r"handicappat\w*", r"mongoloide",
    r"scem\w*", r"stupid\w*", r"sfigat\w*", r"perdente",
    r"merda", r"merdaccia", r"pezzo di merda", r"testa di cazzo", r"testa di rapa",
    r"vaffanculo", r"affanculo", r"fanculo", r"vai a cagare", r"va a cagare",
    r"vai a quel paese", r"fottiti", r"fatti fottere", r"fatti inculare",
    r"morto di fame", r"cornuto", r"bastardo", r"figlio di puttana", r"troia", r"zoccola",
    r"inutile", r"sei un disastro", r"patetico", r"ridicolo",
)

# --- Minacce ---------------------------------------------------------------
_MINACCE = _patterns(
    r"ti ammazzo", r"ti spacco", r"ti distruggo", r"ti banno", r"ti butto fuori",
    r"ti disintegro", r"ti cancello", r"ti faccio fuori", r"ti meno", r"ti prendo a calci",
    r"ti segnalo", r"ti banniamo", r"ti reporto",
)

# --- Sfottò sulla competenza del bot ---------------------------------------
_SFOTTI = _patterns(
    r"fai schifo", r"sei una pippa", r"sei scarso", r"sei una schiappa",
    r"non capisci niente", r"non capisci un cazzo", r"non servi a niente",
    r"sei inutile", r"sei un incapace", r"non vali niente", r"sei penoso",
    r"fai cagare", r"fai pena", r"sei la vergogna", r"sei da buttare",
)

# --- Congedi: da soli non contano molto, ripetuti pesano -------------------
_CONGEDI = _patterns(
    r"esci", r"vattene", r"levati", r"togliti", r"sparisci", r"scompari",
    r"smamma", r"fuori dalle scatole", r"non ti voglio", r"nessuno ti vuole",
    r"chi ti ha chiamato", r"non ti sopporto", r"sei noioso", r"stufa",
)

# Espressioni in cui non c'è offesa anche se contengono una parola chiave
_FINTI_POSITIVI = _patterns(
    r"sei un grande", r"sei il migliore", r"sei una leggenda", r"sei un mito",
    r"sei un idolo", r"sei forte", r"sei un genio", r"grande franchino",
    r"che spettacolo", r"sei troppo forte", r"sei un fenomeno",
)


@dataclass
class Offense:
    """Una singola offesa riconosciuta."""

    kind: str
    weight: float
    matched: str
    text: str

    @property
    def label(self) -> str:
        return ETICHETTE.get(self.kind, self.kind)


@dataclass
class UserRecord:
    """Profilo di un utente che ha trattato male Franchino."""

    user_id: str
    name: str = ""
    points: float = 0.0
    count: int = 0
    last: float = 0.0
    last_kind: str = ""
    kinds: Dict[str, int] = field(default_factory=dict)
    examples: List[str] = field(default_factory=list)

    @property
    def level(self) -> int:
        if self.points >= SOGLIA_LIVELLO_3:
            return 3
        if self.points >= SOGLIA_LIVELLO_2:
            return 2
        if self.points >= SOGLIA_LIVELLO_1:
            return 1
        return 0

    @property
    def dismissals(self) -> int:
        return int(self.kinds.get(KIND_CONGEDO, 0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "points": round(self.points, 2),
            "count": self.count,
            "last": self.last,
            "last_kind": self.last_kind,
            "kinds": self.kinds,
            "examples": self.examples,
        }

    @classmethod
    def from_dict(cls, user_id: str, data: Dict[str, Any]) -> "UserRecord":
        return cls(
            user_id=user_id,
            name=str(data.get("name") or ""),
            points=float(data.get("points") or 0.0),
            count=int(data.get("count") or 0),
            last=float(data.get("last") or 0.0),
            last_kind=str(data.get("last_kind") or ""),
            kinds=dict(data.get("kinds") or {}),
            examples=list(data.get("examples") or []),
        )


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_offense(text: str) -> Optional[Offense]:
    """Riconosce un'offesa rivolta a Franchino. None se la frase è legittima."""
    norm = _normalize(text)
    if not norm or len(norm) < 3:
        return None

    # Un complimento non è mai un'offesa, anche se contiene "inutile" o simili
    if _FINTI_POSITIVI.search(norm):
        return None

    for kind, pattern in (
        (KIND_MINACCIA, _MINACCE),
        (KIND_INSULTO, _INSULTI),
        (KIND_SFOTTO, _SFOTTI),
        (KIND_CONGEDO, _CONGEDI),
    ):
        match = pattern.search(norm)
        if match:
            return Offense(kind=kind, weight=PESI[kind], matched=match.group(0), text=text)

    return None


class Memoria:
    """Archivio persistente dei profili, con decadimento nel tempo."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or STORE_PATH
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._dirty = False

    # -- persistenza ---------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    self._data = data["users"]
        except Exception as e:
            logger.debug(f"Memoria non leggibile ({self.path}): {e}")

    def _save(self) -> None:
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"users": self._data}, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception as e:
            logger.debug(f"Memoria non scrivibile ({self.path}): {e}")

    def _prune(self) -> None:
        """Mantiene il file limitato: rimuove i profili più vecchi e spenti."""
        if len(self._data) <= MAX_UTENTI:
            return
        ordinati = sorted(
            self._data.items(),
            key=lambda kv: (float(kv[1].get("points") or 0), float(kv[1].get("last") or 0)),
        )
        for uid, _ in ordinati[: len(self._data) - MAX_UTENTI]:
            self._data.pop(uid, None)
        self._dirty = True

    # -- lettura/scrittura ---------------------------------------------
    @staticmethod
    def _decay(points: float, last: float, now: float) -> float:
        """Dimezza i punti per ogni 24h di silenzio."""
        if points <= 0 or last <= 0:
            return points
        trascorso = max(0.0, now - last)
        if trascorso < DECAY_HALF_LIFE_SEC:
            return points
        dimezzamenti = trascorso / DECAY_HALF_LIFE_SEC
        return points * (0.5 ** dimezzamenti)

    def get(self, user_id: Any) -> Optional[UserRecord]:
        """Profilo aggiornato (con decadimento applicato), None se mai offeso."""
        uid = str(user_id)
        with self._lock:
            self._load()
            raw = self._data.get(uid)
            if not raw:
                return None
            record = UserRecord.from_dict(uid, raw)
            now = time.time()
            decaduto = self._decay(record.points, record.last, now)
            if abs(decaduto - record.points) > 0.01:
                record.points = decaduto
                raw["points"] = round(decaduto, 2)
                self._dirty = True
                self._save()
            return record if record.points > 0 else None

    def level(self, user_id: Any) -> int:
        record = self.get(user_id)
        return record.level if record else 0

    def record(self, user_id: Any, name: str, offense: Offense) -> UserRecord:
        """Registra l'offesa e restituisce il profilo aggiornato."""
        uid = str(user_id)
        now = time.time()
        with self._lock:
            self._load()
            raw = self._data.get(uid) or {}
            record = UserRecord.from_dict(uid, raw)
            record.points = self._decay(record.points, record.last, now) + offense.weight
            record.count += 1
            record.last = now
            record.last_kind = offense.kind
            record.name = name or record.name
            record.kinds[offense.kind] = int(record.kinds.get(offense.kind, 0)) + 1
            if offense.matched:
                esempio = offense.matched.lower().strip()
                if esempio and esempio not in record.examples:
                    record.examples = ([esempio] + record.examples)[:MAX_ESEMPI]
            self._data[uid] = record.to_dict()
            self._dirty = True
            self._prune()
            self._save()
            logger.info(
                f"📝 Memoria: {name or uid} ha offeso Franchino ({offense.label}): "
                f"{record.count}ª volta, punti {record.points:.1f}, livello {record.level}"
            )
            return record

    def forget(self, user_id: Any) -> bool:
        """Cancella il profilo (usato dalle scuse o dall'admin)."""
        uid = str(user_id)
        with self._lock:
            self._load()
            if self._data.pop(uid, None) is None:
                return False
            self._dirty = True
            self._save()
            return True

    def all_records(self) -> List[UserRecord]:
        with self._lock:
            self._load()
            now = time.time()
            out = []
            for uid, raw in self._data.items():
                rec = UserRecord.from_dict(uid, raw)
                rec.points = self._decay(rec.points, rec.last, now)
                if rec.points > 0:
                    out.append(rec)
            return sorted(out, key=lambda r: r.points, reverse=True)

    # -- risposte ------------------------------------------------------
    def reply(self, record: UserRecord) -> Tuple[str, str]:
        """Restituisce (testo_chat, testo_voce) per la risposta a tono.

        Il tono scala con il livello: ironico, poi fermo, poi freddo.
        """
        nome = record.name or "fratello"
        n = record.count
        livello = record.level
        congedi = record.dismissals

        chat_varianti: List[str] = []
        voce_varianti: List[str] = []

        if livello <= 1:
            contesto = "mi hai mandato via" if record.last_kind == KIND_CONGEDO else "me l'hai detto"
            chat_varianti = [
                f"😏 Ah, quindi {contesto}... Va bene **{nome}**, la notte è lunga e io ho la memoria lunga: me lo segno. ✨",
                f"🎙️ Calma **{nome}**, calma! Io sto qui per la musica, mica per le offese... Però il conto lo tengo, sai? Magia! ✨",
                f"😌 **{nome}**, la prima volta si sorride. E io sorrido... ma segno tutto nel libro della notte. 📖",
            ]
            voce_varianti = [
                f"Va bene {nome}, me lo segno!",
                f"Calma {nome}, la console è mia e io ho memoria lunga!",
            ]
        elif livello == 2:
            chat_varianti = [
                f"😐 **{nome}**, la prima volta si scherza. Questa è la **{n}ª** e comincia a non farmi ridere. "
                f"Qua si sta per la musica: porta rispetto o finisci in lista. 🎙️",
                f"⚡ **{nome}**, ti ho perdonato {n} volte. La pazienza è tanta ma non infinita: "
                f"la prossima volta ti rispondo solo se parli di musica. ⚠️",
                f"🎛️ Fratello, d'accordo la goliardia... ma **{n}** colpi sono troppi. "
                f"Il rispetto è la prima regola della console. 💥",
            ]
            voce_varianti = [
                f"{nome}, questa è la {n}ª volta: porta rispetto!",
                f"Fratello, {n} colpi sono troppi. Porta rispetto in console.",
            ]
        else:
            chat_varianti = [
                f"❄️ **{nome}**, ho la memoria lunga e tu sei a **{n}** colpi. "
                f"Da adesso ti rispondo solo se parli di musica: il resto lo lascio cadere. 🎙️",
                f"🧊 Ti ho segnato, **{nome}**. **{n}** volte. Continua e ti tratto come l'aria: "
                f"presente, ma muto. La console non si insulta. 🎛️",
                f"⛔ **{nome}**, conto chiuso: {n} offese. Io resto qui per la musica e per chi la rispetta. "
                f"Tu, da adesso, parli con il silenzio. 🌑",
            ]
            voce_varianti = [
                f"Ho la memoria lunga {nome}: {n} colpi. Da adesso ti rispondo solo per la musica.",
                f"Ti ho segnato {nome}. La console non si insulta.",
            ]

        if record.last_kind == KIND_CONGEDO and congedi >= 3:
            chat_varianti.append(
                f"🚪 **{nome}**, mi hai mandato via **{congedi}** volte. "
                f"Se non vuoi la musica, basta chiedere: non serve trattarmi così. 🎙️"
            )

        return random.choice(chat_varianti), random.choice(voce_varianti)

    def prompt_note(self, user_id: Any) -> str:
        """Nota da iniettare nel prompt del cervello AI, se l'utente ha uno storico."""
        record = self.get(user_id)
        if not record or record.level <= 0:
            return ""
        return (
            f"\n[MEMORIA: questo utente ti ha mancato di rispetto {record.count} volte "
            f"(ultima: {ETICHETTE.get(record.last_kind, record.last_kind)}, livello {record.level}/3). "
            "Rispondi con fermezza e ironia, senza insultare: dagli chiaramente a intendere che te lo ricordi.]"
        )


# Istanza condivisa
memoria = Memoria()
