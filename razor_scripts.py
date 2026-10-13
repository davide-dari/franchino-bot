"""
Modulo Razor Scripts per Ultima Online Outlands (Franchino Bot).
Interroga e gestisce la banca dati locale di oltre 600+ script ufficiali da https://outlands.uorazorscripts.com
per tutte le 51 skill di Outlands.
Permette agli utenti di:
- Richiedere uno script via comando vocale ("Franchino script per magery", "mi passi lo script di taming", "dammi lo script per pesca")
- Richiedere tutti gli script per una skill via comando slash (/script <skill>) o sfogliare tutte le skill (/script lista)
- Scaricare il file .razor direttamente in chat per importarlo in Razor con 1 clic
- Selezionare da un menu interattivo (Select Menu Discord) qualsiasi script o skill
- Fallback automatico ultra-veloce tra database SQLite e cache JSON locale (data/razor_scripts_cache.json)
"""

import os
import re
import io
import json
import sqlite3
import logging
import asyncio
from typing import List, Dict, Optional, Tuple, Any
import discord

logger = logging.getLogger("FranchinoRazorScripts")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "outlands_knowledge.db")
CACHE_JSON_PATH = os.path.join(DATA_DIR, "razor_scripts_cache.json")
COLOR_FRANCHINO = 0xF1C40F  # Oro caldo console Franchino

# Cache in-memory degli script (caricata da JSON in caso di assenza/errore del database)
_IN_MEMORY_SCRIPTS_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None
_IN_MEMORY_ID_CACHE: Optional[Dict[str, Dict[str, Any]]] = None

# Mappatura sinonimi italiani, inglesi e abbreviazioni -> (Nome Formattato, slug_canonico)
SKILL_MAP: Dict[str, Tuple[str, str]] = {
    # Magia & Cast
    "magery": ("Magery", "magery"),
    "magia": ("Magery", "magery"),
    "mago": ("Magery", "magery"),
    "mage": ("Magery", "magery"),
    "spell": ("Magery", "magery"),
    "spells": ("Magery", "magery"),
    "meditation": ("Meditation", "meditation"),
    "meditazione": ("Meditation", "meditation"),
    "meditare": ("Meditation", "meditation"),
    "med": ("Meditation", "meditation"),
    "evalint": ("EvalInt", "evalint"),
    "eval int": ("EvalInt", "evalint"),
    "evaluating intelligence": ("EvalInt", "evalint"),
    "valutazione intelligenza": ("EvalInt", "evalint"),
    "magicresist": ("MagicResist", "magicresist"),
    "resist": ("MagicResist", "magicresist"),
    "resisting spells": ("MagicResist", "magicresist"),
    "resistenza magica": ("MagicResist", "magicresist"),
    "resistenza": ("MagicResist", "magicresist"),
    "resistere": ("MagicResist", "magicresist"),
    "spiritspeak": ("SpiritSpeak", "spiritspeak"),
    "spirit speak": ("SpiritSpeak", "spiritspeak"),
    "spiriti": ("SpiritSpeak", "spiritspeak"),
    "parlare con gli spiriti": ("SpiritSpeak", "spiritspeak"),
    "parlare con spiriti": ("SpiritSpeak", "spiritspeak"),
    "necromancy": ("Necromancy", "necromancy"),
    "necro": ("Necromancy", "necromancy"),
    "necromante": ("Necromancy", "necromancy"),
    "negromanzia": ("Necromancy", "necromancy"),
    "chivalry": ("Chivalry", "chivalry"),
    "chiv": ("Chivalry", "chivalry"),
    "cavalleria": ("Chivalry", "chivalry"),
    "paladino": ("Chivalry", "chivalry"),
    "paladin": ("Chivalry", "chivalry"),

    # Taming & Animali
    "taming": ("Animal Taming", "animaltaming"),
    "animaltaming": ("Animal Taming", "animaltaming"),
    "animal taming": ("Animal Taming", "animaltaming"),
    "tamer": ("Animal Taming", "animaltaming"),
    "tamare": ("Animal Taming", "animaltaming"),
    "tammare": ("Animal Taming", "animaltaming"),
    "domare": ("Animal Taming", "animaltaming"),
    "addomesticare": ("Animal Taming", "animaltaming"),
    "addomesticamento": ("Animal Taming", "animaltaming"),
    "animallore": ("Animal Lore", "animallore"),
    "animal lore": ("Animal Lore", "animallore"),
    "lore animali": ("Animal Lore", "animallore"),
    "veterinary": ("Veterinary", "veterinary"),
    "vet": ("Veterinary", "veterinary"),
    "veterinaria": ("Veterinary", "veterinary"),
    "curare pet": ("Veterinary", "veterinary"),
    "cura pet": ("Veterinary", "veterinary"),
    "herding": ("Herding", "herding"),
    "pastore": ("Herding", "herding"),

    # Combattimento Melee / Ranged
    "swords": ("Swordsmanship", "swords"),
    "swordsmanship": ("Swordsmanship", "swords"),
    "spada": ("Swordsmanship", "swords"),
    "spade": ("Swordsmanship", "swords"),
    "fencing": ("Fencing", "fencing"),
    "scherma": ("Fencing", "fencing"),
    "stocco": ("Fencing", "fencing"),
    "pugnale": ("Fencing", "fencing"),
    "macing": ("Mace Fighting", "macing"),
    "mace": ("Mace Fighting", "macing"),
    "mace fighting": ("Mace Fighting", "macing"),
    "mazza": ("Mace Fighting", "macing"),
    "mazze": ("Mace Fighting", "macing"),
    "archery": ("Archery", "archery"),
    "arco": ("Archery", "archery"),
    "arciere": ("Archery", "archery"),
    "balestra": ("Archery", "archery"),
    "frecce": ("Archery", "archery"),
    "parry": ("Parrying", "parry"),
    "parrying": ("Parrying", "parry"),
    "parata": ("Parrying", "parry"),
    "scudo": ("Parrying", "parry"),
    "tactics": ("Tactics", "tactics"),
    "tattica": ("Tactics", "tactics"),
    "wrestling": ("Wrestling", "wrestling"),
    "lotta": ("Wrestling", "wrestling"),
    "pugni": ("Wrestling", "wrestling"),
    "anatomy": ("Anatomy", "anatomy"),
    "anatomia": ("Anatomy", "anatomy"),
    "healing": ("Healing", "healing"),
    "curare": ("Healing", "healing"),
    "curarsi": ("Healing", "healing"),
    "bende": ("Healing", "healing"),
    "benda": ("Healing", "healing"),
    "healer": ("Healing", "healing"),
    "cura": ("Healing", "healing"),

    # Bard
    "musicianship": ("Musicianship", "musicianship"),
    "musica": ("Musicianship", "musicianship"),
    "music": ("Musicianship", "musicianship"),
    "suonare": ("Musicianship", "musicianship"),
    "bardo": ("Musicianship", "musicianship"),
    "provocation": ("Provocation", "provocation"),
    "provo": ("Provocation", "provocation"),
    "provocare": ("Provocation", "provocation"),
    "provocazione": ("Provocation", "provocation"),
    "peacemaking": ("Peacemaking", "peacemaking"),
    "peace": ("Peacemaking", "peacemaking"),
    "calmare": ("Peacemaking", "peacemaking"),
    "calma": ("Peacemaking", "peacemaking"),
    "pace": ("Peacemaking", "peacemaking"),
    "discordance": ("Discordance", "discordance"),
    "disco": ("Discordance", "discordance"),
    "discordia": ("Discordance", "discordance"),

    # Rogue & Furtività
    "lockpicking": ("Lockpicking", "lockpicking"),
    "lockpick": ("Lockpicking", "lockpicking"),
    "scassinare": ("Lockpicking", "lockpicking"),
    "scassinamento": ("Lockpicking", "lockpicking"),
    "grimaldello": ("Lockpicking", "lockpicking"),
    "casse": ("Lockpicking", "lockpicking"),
    "serrature": ("Lockpicking", "lockpicking"),
    "hiding": ("Hiding", "hiding"),
    "hide": ("Hiding", "hiding"),
    "nascondersi": ("Hiding", "hiding"),
    "nascondere": ("Hiding", "hiding"),
    "stealth": ("Stealth", "stealth"),
    "invisibile": ("Stealth", "stealth"),
    "camminare nascosto": ("Stealth", "stealth"),
    "stealing": ("Stealing", "stealing"),
    "rubare": ("Stealing", "stealing"),
    "ladro": ("Stealing", "stealing"),
    "furto": ("Stealing", "stealing"),
    "snooping": ("Snooping", "snooping"),
    "snoop": ("Snooping", "snooping"),
    "sbirciare": ("Snooping", "snooping"),
    "borseggio": ("Snooping", "snooping"),
    "borseggiare": ("Snooping", "snooping"),
    "removetrap": ("Remove Trap", "removetrap"),
    "remove trap": ("Remove Trap", "removetrap"),
    "trappole": ("Remove Trap", "removetrap"),
    "disarma trappole": ("Remove Trap", "removetrap"),
    "disarmare trappole": ("Remove Trap", "removetrap"),
    "detecthidden": ("Detect Hidden", "detecthidden"),
    "detect hidden": ("Detect Hidden", "detecthidden"),
    "detect": ("Detect Hidden", "detecthidden"),
    "trova nascosti": ("Detect Hidden", "detecthidden"),
    "trovare nascosti": ("Detect Hidden", "detecthidden"),
    "poisoning": ("Poisoning", "poisoning"),
    "poison": ("Poisoning", "poisoning"),
    "veleno": ("Poisoning", "poisoning"),
    "veleni": ("Poisoning", "poisoning"),
    "avvelenare": ("Poisoning", "poisoning"),

    # Crafting & Gathering
    "mining": ("Mining", "mining"),
    "miner": ("Mining", "mining"),
    "minare": ("Mining", "mining"),
    "scavare": ("Mining", "mining"),
    "miniera": ("Mining", "mining"),
    "miniere": ("Mining", "mining"),
    "estrazione": ("Mining", "mining"),
    "lumberjacking": ("Lumberjacking", "lumberjacking"),
    "lumber": ("Lumberjacking", "lumberjacking"),
    "legna": ("Lumberjacking", "lumberjacking"),
    "taglialegna": ("Lumberjacking", "lumberjacking"),
    "pesca": ("Fishing", "fishing"),
    "fishing": ("Fishing", "fishing"),
    "pescare": ("Fishing", "fishing"),
    "pescatore": ("Fishing", "fishing"),
    "blacksmith": ("Blacksmithy", "blacksmith"),
    "blacksmithy": ("Blacksmithy", "blacksmith"),
    "fabbro": ("Blacksmithy", "blacksmith"),
    "forgia": ("Blacksmithy", "blacksmith"),
    "forgiare": ("Blacksmithy", "blacksmith"),
    "carpentry": ("Carpentry", "carpentry"),
    "falegname": ("Carpentry", "carpentry"),
    "falegnameria": ("Carpentry", "carpentry"),
    "legno": ("Carpentry", "carpentry"),
    "tailoring": ("Tailoring", "tailoring"),
    "sarto": ("Tailoring", "tailoring"),
    "sartoria": ("Tailoring", "tailoring"),
    "cucire": ("Tailoring", "tailoring"),
    "alchemy": ("Alchemy", "alchemy"),
    "alchimia": ("Alchemy", "alchemy"),
    "pozioni": ("Alchemy", "alchemy"),
    "alchimista": ("Alchemy", "alchemy"),
    "inscribe": ("Inscription", "inscribe"),
    "inscription": ("Inscription", "inscribe"),
    "iscrizione": ("Inscription", "inscribe"),
    "iscrivere": ("Inscription", "inscribe"),
    "pergamene": ("Inscription", "inscribe"),
    "scrolls": ("Inscription", "inscribe"),
    "scrivere": ("Inscription", "inscribe"),
    "tinkering": ("Tinkering", "tinkering"),
    "tinker": ("Tinkering", "tinkering"),
    "attrezzi": ("Tinkering", "tinkering"),
    "costruire": ("Tinkering", "tinkering"),
    "cartography": ("Cartography", "cartography"),
    "cartografia": ("Cartography", "cartography"),
    "mappe": ("Cartography", "cartography"),
    "tmap": ("Cartography", "cartography"),
    "tmaps": ("Cartography", "cartography"),
    "cooking": ("Cooking", "cooking"),
    "cucinare": ("Cooking", "cooking"),
    "cucina": ("Cooking", "cooking"),
    "cibo": ("Cooking", "cooking"),

    # Miscellaneous & Utility
    "camping": ("Camping", "camping"),
    "campeggio": ("Camping", "camping"),
    "campeggiare": ("Camping", "camping"),
    "fuoco": ("Camping", "camping"),
    "begging": ("Begging", "begging"),
    "elemosina": ("Begging", "begging"),
    "forensics": ("Forensic Evaluation", "forensics"),
    "forensic": ("Forensic Evaluation", "forensics"),
    "autopsia": ("Forensic Evaluation", "forensics"),
    "itemid": ("Item Identification", "itemid"),
    "item id": ("Item Identification", "itemid"),
    "identificare": ("Item Identification", "itemid"),
    "tasteid": ("Taste Identification", "tasteid"),
    "assaggiare": ("Taste Identification", "tasteid"),
    "armslore": ("Arms Lore", "armslore"),
    "arms lore": ("Arms Lore", "armslore"),
    "perizia": ("Arms Lore", "armslore"),
    "perizia armi": ("Arms Lore", "armslore"),
    "tracking": ("Tracking", "tracking"),
    "tracciare": ("Tracking", "tracking"),
    "focus": ("Focus", "focus"),
    "arcane": ("Arcane", "arcane"),
    "arcano": ("Arcane", "arcane"),
    "snippet": ("Snippet", "snippet"),
    "snippets": ("Snippet", "snippet"),
    "utility": ("Snippet", "snippet"),
    "macro": ("Snippet", "snippet"),
}

# Raggruppamento delle skill per categorie tematiche
SKILL_CATEGORIES = {
    "🔮 Magia & Misticismo": ["magery", "meditation", "evalint", "magicresist", "spiritspeak", "necromancy", "chivalry"],
    "🐾 Taming & Animali": ["animaltaming", "animallore", "veterinary", "herding"],
    "⚔️ Combattimento Melee & Ranged": ["swords", "fencing", "macing", "archery", "parry", "tactics", "wrestling", "anatomy", "healing"],
    "🎵 Bardo & Musica": ["musicianship", "provocation", "peacemaking", "discordance"],
    "🗝️ Rogue & Furtività": ["lockpicking", "hiding", "stealth", "stealing", "snooping", "removetrap", "detecthidden", "poisoning"],
    "⚒️ Crafting & Gathering": ["mining", "lumberjacking", "fishing", "blacksmith", "carpentry", "tailoring", "alchemy", "inscribe", "tinkering", "cartography", "cooking"],
    "📜 Altro & Utilità": ["camping", "begging", "forensics", "itemid", "tasteid", "armslore", "tracking", "focus", "arcane", "snippet"],
}


def _load_scripts_from_cache_json() -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    """Carica la cache in-memory dal file JSON locale (data/razor_scripts_cache.json)."""
    global _IN_MEMORY_SCRIPTS_CACHE, _IN_MEMORY_ID_CACHE
    if _IN_MEMORY_SCRIPTS_CACHE is not None and _IN_MEMORY_ID_CACHE is not None:
        return _IN_MEMORY_SCRIPTS_CACHE, _IN_MEMORY_ID_CACHE

    by_skill: Dict[str, List[Dict[str, Any]]] = {}
    by_id: Dict[str, Dict[str, Any]] = {}

    if os.path.isfile(CACHE_JSON_PATH):
        try:
            with open(CACHE_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            scripts = data.get("scripts", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for s in scripts:
                s_id = s.get("script_id")
                if not s_id:
                    continue
                by_id[s_id] = s
                slug = (s.get("skill_slug") or s.get("skill", "")).lower().replace(" ", "")
                if slug:
                    by_skill.setdefault(slug, []).append(s)
                # Anche la skill raw
                skill_raw = (s.get("skill") or "").lower()
                if skill_raw and skill_raw != slug:
                    by_skill.setdefault(skill_raw, []).append(s)

            # Ordina ciascuna lista di skill: prima quelli con codice, poi per aggiornamento
            for slug in by_skill:
                by_skill[slug].sort(
                    key=lambda x: (
                        1 if (x.get("code") and len(x.get("code", "")) > 0) else 0,
                        x.get("updated_at") or "",
                        x.get("created_at") or "",
                    ),
                    reverse=True,
                )
            logger.info(f"Caricati {len(by_id)} script Razor dalla cache JSON ({len(by_skill)} skill indicizzate).")
        except Exception as e:
            logger.warning(f"Errore lettura {CACHE_JSON_PATH}: {e}")

    _IN_MEMORY_SCRIPTS_CACHE = by_skill
    _IN_MEMORY_ID_CACHE = by_id
    return _IN_MEMORY_SCRIPTS_CACHE, _IN_MEMORY_ID_CACHE


def get_db_connection() -> Optional[sqlite3.Connection]:
    """Restituisce una connessione al database SQLite locale ottimizzata per ARM."""
    if not os.path.isfile(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # PRAGMA ottimizzati per architettura ARM (2 core, basso overhead)
        c = conn.cursor()
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")
        c.execute("PRAGMA cache_size = -4000")  # 4 MB cache
        c.execute("PRAGMA mmap_size = 33554432")  # 32 MB memory-map
        return conn
    except Exception as e:
        logger.warning(f"Errore connessione database razor_scripts: {e}")
        return None


def resolve_skill_from_text(text: str) -> Optional[Tuple[str, str]]:
    """
    Riconosce la skill richiesta da una frase naturale o query.
    Ritorna (nome_formattato, slug_canonico) oppure None.
    """
    if not text:
        return None

    clean = text.lower().strip(" ?!.,;:\"'")
    clean = re.sub(
        r"^(?:script|scripts|razorscript|razor\s+script|macro|macro\s+razor|codice\s+razor|"
        r"dammi\s+(?:lo\s+|un\s+|la\s+|le\s+|gli\s+)?(?:script|macro|codice)(?:\s+per|\s+di|\s+del)?|"
        r"passami\s+(?:lo\s+|un\s+|la\s+|le\s+|gli\s+)?(?:script|macro|codice)(?:\s+per|\s+di|\s+del)?|"
        r"mi\s+(?:passi|daresti|dai|trovi|cerchi)\s+(?:lo\s+|un\s+|la\s+|le\s+|gli\s+)?(?:script|macro|codice)(?:\s+per|\s+di|\s+del)?|"
        r"mostra(?:\s+lo|\s+gli)?\s+(?:script|macro)(?:\s+per|\s+di|\s+del)?|"
        r"hai\s+(?:uno\s+)?(?:script|macro)(?:\s+per|\s+di|\s+del)?|"
        r"trova\s+(?:lo\s+|uno\s+)?(?:script|macro)(?:\s+per|\s+di|\s+del)?|"
        r"cerca\s+(?:lo\s+|uno\s+)?(?:script|macro)(?:\s+per|\s+di|\s+del)?)\s*",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()

    # Rimuove preposizioni o parole come "per", "di", "della", "allenare", "livellare", "expare"
    clean = re.sub(
        r"^(?:per\s+(?:allenare|livellare|expare|il|la|lo|gli|le)?|di\s+(?:allenare|il|la|lo|gli|le)?|del|della|degli|delle)\s+",
        "",
        clean,
    ).strip()

    # 1. Match diretto su SKILL_MAP
    if clean in SKILL_MAP:
        return SKILL_MAP[clean]

    # 2. Cerca parole chiave all'interno della stringa (dalla più lunga alla più corta)
    for kw in sorted(SKILL_MAP.keys(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(kw) + r"\b", clean):
            return SKILL_MAP[kw]

    return None


def is_script_question(text: str) -> Tuple[bool, str]:
    """
    Determina se la frase pronunciata dall'utente è una richiesta di script Razor.
    Ritorna:
    - (True, skill_slug) se è richiesta una skill specifica (es. "script per magery", "dammi lo script di pesca")
    - (True, "lista") se è una richiesta generale di script (es. "Franchino che script hai?", "script outlands", "lista script")
    - (False, "") se non c'entra con gli script
    """
    if not text:
        return False, ""

    t_low = text.lower().strip(" ?!.,;:\"'")

    # Se la frase contiene trigger espliciti di script / macro razor
    script_triggers = (
        r"\b(?:script|scripts|razorscript|razor\s+script|macro\s+razor|codice\s+razor)\b",
        r"\b(?:macro\s+(?:per|di|del|su|uotlands|outlands))\b",
    )
    has_script_trigger = any(re.search(pat, t_low) for pat in script_triggers)

    if not has_script_trigger:
        if t_low.startswith(("script ", "scripts ", "razor ", "macro ")):
            has_script_trigger = True

    # Domande generali sugli script ("che script hai", "quali script hai", "lista script", "script outlands")
    general_script_patterns = (
        r"^(?:script|scripts|razor|razorscript)$",
        r"\b(?:che|quali|mostrami|elenco|lista|tutti|dammi|hai)\s+(?:gli\s+|i\s+|tutti\s+gli\s+)?(?:script|scripts|macro)\b",
        r"\b(?:script|scripts)\s+(?:di\s+|su\s+|per\s+)?(?:uo|outlands|ultima\s+online|razor)\b",
        r"\b(?:uo|outlands)\s+(?:script|scripts)\b",
        r"\b(?:lista|elenco)\s+(?:degli\s+|degli\s+)?(?:script|scripts)\b",
    )
    if any(re.search(p, t_low) for p in general_script_patterns):
        # Se c'è comunque una skill specifica specificata:
        res = resolve_skill_from_text(t_low)
        if res:
            return True, res[1]
        return True, "lista"

    if has_script_trigger:
        res = resolve_skill_from_text(t_low)
        if res:
            return True, res[1]
        # Se la frase cita esplicitamente "script" o "razor" ma non specifica la skill,
        # ritorna la lista di tutte le skill disponibili
        return True, "lista"

    return False, ""


def get_scripts_for_skill(skill_slug: str) -> List[Dict[str, Any]]:
    """
    Recupera tutti gli script Razor per una data skill.
    Tenta prima da database SQLite locale, poi ripiega con altissima velocità sulla cache JSON.
    Ordina per disponibilità di codice sorgente, poi per aggiornamento.
    """
    skill_slug_clean = skill_slug.lower().strip()
    conn = get_db_connection()
    if conn:
        try:
            c = conn.cursor()
            c.execute("""
                SELECT script_id, skill, skill_slug, title, description, author, version, tags, code, url, created_at, updated_at
                FROM razor_scripts
                WHERE lower(skill_slug) = ? OR lower(skill) = ?
                ORDER BY CASE WHEN code IS NOT NULL AND length(code) > 0 THEN 0 ELSE 1 END,
                         updated_at DESC, created_at DESC
            """, (skill_slug_clean, skill_slug_clean))
            rows = [dict(r) for r in c.fetchall()]
            if rows:
                return rows
        except Exception as e:
            logger.error(f"Errore recupero SQLite script per {skill_slug}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # Fallback sulla cache JSON
    by_skill, _ = _load_scripts_from_cache_json()
    return by_skill.get(skill_slug_clean, [])


def get_script_by_id(script_id: str) -> Optional[Dict[str, Any]]:
    """Recupera un singolo script per ID da database o cache JSON."""
    conn = get_db_connection()
    if conn:
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM razor_scripts WHERE script_id = ?", (script_id,))
            row = c.fetchone()
            if row:
                return dict(row)
        except Exception as e:
            logger.error(f"Errore recupero SQLite script {script_id}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    _, by_id = _load_scripts_from_cache_json()
    return by_id.get(script_id)


def sanitize_code_block(code: str) -> str:
    """Evita che eventuali backtick nel codice rompano i blocchi Markdown di Discord."""
    return code.replace("```", "'''")


class RazorScriptSelect(discord.ui.Select):
    """Dropdown Select Menu per scegliere uno degli script della skill."""

    def __init__(self, scripts: List[Dict[str, Any]], skill_name: str, start_idx: int = 1, placeholder: Optional[str] = None):
        self.scripts_by_id = {s["script_id"]: s for s in scripts}
        self.skill_name = skill_name

        # Massimo 25 opzioni per ciascun Select Menu di Discord
        options = []
        for offset, s in enumerate(scripts[:25]):
            idx = start_idx + offset
            title = s.get("title", f"Script #{idx}")[:90]
            author = s.get("author") or "Community"
            desc = s.get("description") or f"Script Razor per {skill_name}"
            desc = desc.replace("\n", " ").strip()[:90]
            options.append(
                discord.SelectOption(
                    label=f"{idx}. {title}"[:100],
                    value=s["script_id"],
                    description=f"Autore: {author} • {desc}"[:100],
                    emoji="📜",
                )
            )

        p_holder = placeholder or f"Seleziona uno script per {skill_name}..."
        super().__init__(
            placeholder=p_holder[:100],
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        script = self.scripts_by_id.get(selected_id)
        if not script:
            script = get_script_by_id(selected_id)

        if not script:
            await interaction.response.send_message("⚠️ Script non trovato nel database.", ephemeral=True)
            return

        title = script.get("title", "Script Razor")
        author = script.get("author") or "Community"
        url = script.get("url") or f"https://outlands.uorazorscripts.com/script/{selected_id}"
        code = script.get("code") or "# Nessun codice disponibile."
        desc = script.get("description") or "Nessuna descrizione fornita."

        embed = discord.Embed(
            title=f"📜 {title}",
            url=url,
            description=f"**Autore:** `{author}`\n**Skill:** `{self.skill_name}`\n\n**Descrizione:**\n{desc[:1000]}",
            color=COLOR_FRANCHINO,
        )
        embed.set_footer(text=f"UO Razor Scripts • {url}")

        clean_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', f"{self.skill_name}_{title}")[:40] + ".razor"
        file_to_send = discord.File(io.BytesIO(code.encode("utf-8")), filename=clean_filename)

        clean_code = sanitize_code_block(code)
        if len(clean_code) <= 1500:
            code_preview = f"```razor\n{clean_code}\n```"
        else:
            code_preview = (
                f"```razor\n{clean_code[:1400]}\n# ... [Codice completo nel file {clean_filename} allegato sotto!] ...\n```\n"
                f"📥 *Ho allegato il file `{clean_filename}` pronto da copiare nella cartella Scripts di Razor!*"
            )

        await interaction.response.send_message(
            content=f"✨ Ecco il codice per **{title}** (selezionato da {interaction.user.mention}):\n{code_preview}",
            embed=embed,
            file=file_to_send,
        )


class RazorScriptView(discord.ui.View):
    """View contenente fino a 2 Select Menu per navigare gli script di una data skill."""

    def __init__(self, scripts: List[Dict[str, Any]], skill_name: str):
        super().__init__(timeout=180.0)
        if scripts:
            # Menu per i primi 25 script
            self.add_item(
                RazorScriptSelect(
                    scripts[:25],
                    skill_name,
                    start_idx=1,
                    placeholder=f"Script 1-{min(25, len(scripts))} per {skill_name}..."
                )
            )
            # Se la skill ha più di 25 script, aggiungi secondo menu per script 26-50
            if len(scripts) > 25:
                self.add_item(
                    RazorScriptSelect(
                        scripts[25:50],
                        skill_name,
                        start_idx=26,
                        placeholder=f"Script 26-{min(50, len(scripts))} per {skill_name}..."
                    )
                )


class RazorPopularSkillSelect(discord.ui.Select):
    """Dropdown Select Menu rapido con le skill più utilizzate su Outlands."""

    def __init__(self):
        popular = [
            ("🔮 Magery (96 script)", "magery", "Magia classica, recall, summon e gating"),
            ("🐾 Animal Taming (32 script)", "animaltaming", "Domare creature, addestramento e comandi pet"),
            ("🎣 Fishing (32 script)", "fishing", "Pesca in mare, SOS mib e mostri marini"),
            ("🗝️ Lockpicking (27 script)", "lockpicking", "Scassinare forzieri, casse dei dungeon e tmaps"),
            ("⛏️ Mining (28 script)", "mining", "Estrazione minerali, smelting e carrelli"),
            ("👤 Stealth (29 script)", "stealth", "Furtività, passi nascosti e trappole"),
            ("⚔️ Swordsmanship (15 script)", "swords", "Armi da taglio, dex e lumberjacking"),
            ("🏹 Archery (12 script)", "archery", "Arco e balestre a distanza"),
            ("🩹 Healing (25 script)", "healing", "Bende automatiche e cura rapida"),
            ("⚗️ Alchemy (18 script)", "alchemy", "Creazione pozioni, keg e mortaio"),
            ("⚒️ Blacksmithy (15 script)", "blacksmith", "Forgiatura armature e lingotti"),
            ("🪓 Lumberjacking (22 script)", "lumberjacking", "Tagliare legna nei boschi"),
            ("📜 Inscription (12 script)", "inscribe", "Scrittura pergamene e spellbook"),
            ("🎵 Musicianship (10 script)", "musicianship", "Strumenti musicali e bardi"),
            ("💀 Necromancy (10 script)", "necromancy", "Magia oscura e spiriti"),
        ]
        options = [
            discord.SelectOption(label=label, value=slug, description=desc[:100])
            for label, slug, desc in popular
        ]
        super().__init__(
            placeholder="⭐ Scegli direttamente una delle Top Skill più usate...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        skill_chosen = self.values[0]
        embed, view, file, voice_ans, code = build_razor_scripts_response(skill_chosen)
        if embed:
            kwargs: Dict[str, Any] = {"embed": embed}
            if view:
                kwargs["view"] = view
            if file:
                kwargs["file"] = file
            clean_code = sanitize_code_block(code) if code else ""
            if clean_code:
                if len(clean_code) <= 1200:
                    kwargs["content"] = f"✨ Ecco gli script Razor per **{skill_chosen.title()}**:\n```razor\n{clean_code}\n```"
                else:
                    kwargs["content"] = f"✨ Ecco gli script Razor per **{skill_chosen.title()}**:\n```razor\n{clean_code[:1100]}\n# ... [File completo allegato sotto!] ...\n```"
            await interaction.response.send_message(**kwargs)
        else:
            await interaction.response.send_message(f"Nessuno script trovato per {skill_chosen}.", ephemeral=True)


class RazorSkillCategorySelect(discord.ui.Select):
    """Dropdown Select Menu per sfogliare le categorie di skill."""

    def __init__(self):
        options = [
            discord.SelectOption(label="🔮 Magia & Misticismo", value="magery", description="Magery, Meditation, Necro, Chivalry, Resist..."),
            discord.SelectOption(label="🐾 Taming & Animali", value="animaltaming", description="Animal Taming, Lore, Veterinary, Herding"),
            discord.SelectOption(label="⚔️ Melee & Ranged", value="swords", description="Swords, Fencing, Macing, Archery, Healing..."),
            discord.SelectOption(label="🗝️ Rogue & Furtività", value="lockpicking", description="Lockpicking, Hiding, Stealth, Stealing..."),
            discord.SelectOption(label="⚒️ Crafting & Gathering", value="mining", description="Mining, Fishing, Blacksmith, Alchemy, Carpentry..."),
            discord.SelectOption(label="🎵 Bardo & Musica", value="musicianship", description="Musicianship, Provocation, Peace, Discord..."),
        ]
        super().__init__(
            placeholder="📁 Sfoglia per Categoria (Magia, Taming, Crafting...)...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        skill_chosen = self.values[0]
        embed, view, file, voice_ans, code = build_razor_scripts_response(skill_chosen)
        if embed:
            kwargs: Dict[str, Any] = {"embed": embed}
            if view:
                kwargs["view"] = view
            if file:
                kwargs["file"] = file
            clean_code = sanitize_code_block(code) if code else ""
            if clean_code:
                if len(clean_code) <= 1200:
                    kwargs["content"] = f"✨ Ecco gli script Razor per **{skill_chosen.title()}**:\n```razor\n{clean_code}\n```"
                else:
                    kwargs["content"] = f"✨ Ecco gli script Razor per **{skill_chosen.title()}**:\n```razor\n{clean_code[:1100]}\n# ... [File completo allegato sotto!] ...\n```"
            await interaction.response.send_message(**kwargs)
        else:
            await interaction.response.send_message(f"Nessuno script trovato per {skill_chosen}.", ephemeral=True)


class RazorSkillPickerView(discord.ui.View):
    """View per esplorare l'intero catalogo di 51 skill di Outlands."""

    def __init__(self):
        super().__init__(timeout=180.0)
        self.add_item(RazorPopularSkillSelect())
        self.add_item(RazorSkillCategorySelect())


def build_razor_skills_list_response() -> Tuple[discord.Embed, discord.ui.View, None, str, None]:
    """
    Costruisce la risposta catalogo con tutte le 51 skill divise per categoria,
    il numero di script disponibili per ciascuna e una View interattiva per selezionarle.
    """
    site_url = "https://outlands.uorazorscripts.com"
    embed = discord.Embed(
        title="📜 Catalogo Script Razor Ufficiali UO Outlands (600+ Script)",
        url=site_url,
        description=(
            "Ho a disposizione l'intero archivio ufficiale di **oltre 600 script Razor** da "
            f"[outlands.uorazorscripts.com]({site_url})!\n"
            "Puoi usare `/script <skill>` (es. `/script magery`, `/script lockpicking`, `/script taming`) "
            "oppure chiedere a voce *\"Franchino script per magery\"*.\n\n"
            "Seleziona una categoria dal menu qui sotto per esplorare tutti gli script pronti:"
        ),
        color=COLOR_FRANCHINO,
    )

    for cat_name, cat_skills in SKILL_CATEGORIES.items():
        skill_labels = []
        for s in cat_skills:
            title_name = s.title()
            for k, (form, slug) in SKILL_MAP.items():
                if slug == s:
                    title_name = form
                    break
            skill_labels.append(f"`{title_name}`")
        embed.add_field(name=cat_name, value=", ".join(skill_labels), inline=False)

    embed.set_footer(text="Franchino UO Outlands • Database ufficiale sincronizzato 100%")
    view = RazorSkillPickerView()
    voice = (
        "Magia ragazzi! Ho a disposizione oltre 600 script Razor per tutte le 51 skill di Outlands! "
        "Dalla magia al taming, lockpicking e pesca. Dimmi la skill o selezionala dal menu in chat!"
    )
    return embed, view, None, voice, None


def build_razor_scripts_response(
    skill_query: str,
    requested_idx: Optional[int] = None,
) -> Tuple[Optional[discord.Embed], Optional[discord.ui.View], Optional[discord.File], str, Optional[str]]:
    """
    Costruisce la risposta completa (Embed con la lista di tutti gli script,
    View interattiva, file .razor del primo/richiesto script, voice answer e snippet codice).
    Ritorna: (embed, view, file, voice_answer, code_content)
    """
    clean_q = (skill_query or "").strip().lower()

    # Se l'utente chiede la lista generale o aiuto
    if clean_q in ("", "lista", "list", "all", "tutti", "tutte", "skills", "skill", "aiuto", "help", "catalogo"):
        return build_razor_skills_list_response()

    skill_info = resolve_skill_from_text(skill_query)
    if not skill_info:
        # Fallback ricerca diretta
        skill_name = skill_query.title()
        skill_slug = skill_query.lower().strip()
    else:
        skill_name, skill_slug = skill_info

    scripts = get_scripts_for_skill(skill_slug)
    if not scripts:
        # Prova ricerca FTS nel database SQLite
        conn = get_db_connection()
        if conn:
            try:
                c = conn.cursor()
                c.execute("""
                    SELECT script_id FROM razor_scripts_fts WHERE razor_scripts_fts MATCH ? LIMIT 10
                """, (skill_query,))
                matching_ids = [r[0] for r in c.fetchall()]
                if matching_ids:
                    placeholders = ",".join(["?"] * len(matching_ids))
                    c.execute(f"SELECT * FROM razor_scripts WHERE script_id IN ({placeholders})", matching_ids)
                    scripts = [dict(r) for r in c.fetchall()]
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    if not scripts:
        voice = f"Non ho trovato script Razor per {skill_name} su outlands.uorazorscripts.com ragazzi! Ecco il catalogo generale con tutte le 51 skill!"
        embed = discord.Embed(
            title=f"⚠️ Nessuno Script per: {skill_name}",
            url="https://outlands.uorazorscripts.com",
            description=(
                f"Non ho trovato script specifici per **\"{skill_name}\"** su outlands.uorazorscripts.com.\n\n"
                "Ecco il **catalogo generale delle 51 skill ufficiali** (oltre 600 script). "
                "Seleziona una skill o una categoria dal menu qui sotto:"
            ),
            color=COLOR_FRANCHINO,
        )
        for cat_name, cat_skills in SKILL_CATEGORIES.items():
            skill_labels = [f"`{s.title()}`" for s in cat_skills]
            embed.add_field(name=cat_name, value=", ".join(skill_labels), inline=False)
        embed.set_footer(text="Franchino UO Outlands • Database ufficiale sincronizzato 100%")
        view = RazorSkillPickerView()
        return embed, view, None, voice, None

    # Seleziona lo script target (il primo o quello all'indice richiesto)
    selected_script = scripts[0]
    if requested_idx and 1 <= requested_idx <= len(scripts):
        selected_script = scripts[requested_idx - 1]

    # Genera Embed con l'elenco di TUTTI gli script di quella skill
    total_count = len(scripts)
    site_url = f"https://outlands.uorazorscripts.com/skills/{skill_slug}"

    embed = discord.Embed(
        title=f"📜 Script Razor Ufficiali: {skill_name} ({total_count} disponibili)",
        url=site_url,
        description=(
            f"Ho trovato **{total_count} script Razor** per **{skill_name}** da "
            f"[outlands.uorazorscripts.com]({site_url})!\n"
            f"Puoi selezionare qualsiasi script dal menu a tendina qui sotto per visualizzare il codice e scaricare il file `.razor`.\n\n"
            f"**Elenco Script per {skill_name}:**"
        ),
        color=COLOR_FRANCHINO,
    )

    # Elenco formattato
    lines = []
    for i, s in enumerate(scripts[:20], 1):
        author = s.get("author") or "Community"
        s_url = s.get("url") or f"https://outlands.uorazorscripts.com/script/{s['script_id']}"
        lines.append(f"`{i:2d}.` [**{s['title']}**]({s_url}) *(di {author})*")

    if total_count > 20:
        lines.append(f"*... e altri {total_count - 20} script visualizzabili sul sito o nel menu!*")

    embed.add_field(name="📋 Tutti gli Script della Skill", value="\n".join(lines)[:1024], inline=False)

    # Info sullo script consigliato / top
    top_title = selected_script["title"]
    top_author = selected_script.get("author") or "Community"
    top_url = selected_script.get("url") or f"https://outlands.uorazorscripts.com/script/{selected_script['script_id']}"
    top_desc = selected_script.get("description") or "Script automatico per Razor Outlands."

    embed.add_field(
        name=f"⭐ Script Consigliato: {top_title}",
        value=f"**Autore:** `{top_author}` | [🔗 Apri sul Sito]({top_url})\n{top_desc[:300]}",
        inline=False,
    )

    embed.set_footer(text=f"UO Razor Scripts Database • outlands.uorazorscripts.com")

    # File .razor pronto al download per lo script selezionato
    code = selected_script.get("code") or ""
    clean_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', f"{skill_name}_{top_title}")[:40] + ".razor"
    script_file = discord.File(io.BytesIO(code.encode("utf-8")), filename=clean_filename) if code else None

    # View con Select Menu interattivo
    view = RazorScriptView(scripts, skill_name)

    voice_answer = (
        f"Magia ragazzi! Per {skill_name} ho trovato {total_count} script su Razor Scripts! "
        f"Ti ho mostrato l'elenco completo in chat con lo script di {top_author} pronto da copiare o scaricare per Razor!"
    )

    return embed, view, script_file, voice_answer, code
