"""
Modulo Razor Scripts per Ultima Online Outlands (Franchino Bot).
Interroga e gestisce la banca dati locale di oltre 600+ script ufficiali da https://outlands.uorazorscripts.com
per tutte le 52 skill di Outlands.
Permette agli utenti di:
- Richiedere uno script via comando vocale ("Franchino script per magery", "dammi lo script di lockpicking")
- Richiedere tutti gli script per una skill via comando slash (/script <skill>)
- Scaricare il file .razor direttamente in chat per importarlo in Razor con 1 clic
- Selezionare da un menu interattivo (Select Menu Discord) qualsiasi script tra quelli disponibili per quella skill
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

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "outlands_knowledge.db")
COLOR_FRANCHINO = 0xF1C40F  # Oro caldo console Franchino

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
    "spiritspeak": ("SpiritSpeak", "spiritspeak"),
    "spirit speak": ("SpiritSpeak", "spiritspeak"),
    "spiriti": ("SpiritSpeak", "spiritspeak"),
    "parlare con gli spiriti": ("SpiritSpeak", "spiritspeak"),
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
    "addomesticare": ("Animal Taming", "animaltaming"),
    "addomesticamento": ("Animal Taming", "animaltaming"),
    "animallore": ("Animal Lore", "animallore"),
    "animal lore": ("Animal Lore", "animallore"),
    "lore animali": ("Animal Lore", "animallore"),
    "veterinary": ("Veterinary", "veterinary"),
    "vet": ("Veterinary", "veterinary"),
    "veterinaria": ("Veterinary", "veterinary"),
    "curare pet": ("Veterinary", "veterinary"),
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
    "macing": ("Mace Fighting", "macing"),
    "mace": ("Mace Fighting", "macing"),
    "mace fighting": ("Mace Fighting", "macing"),
    "mazza": ("Mace Fighting", "macing"),
    "mazze": ("Mace Fighting", "macing"),
    "archery": ("Archery", "archery"),
    "arco": ("Archery", "archery"),
    "arciere": ("Archery", "archery"),
    "balestra": ("Archery", "archery"),
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
    "bende": ("Healing", "healing"),
    "healer": ("Healing", "healing"),
    "cura": ("Healing", "healing"),

    # Bard
    "musicianship": ("Musicianship", "musicianship"),
    "musica": ("Musicianship", "musicianship"),
    "music": ("Musicianship", "musicianship"),
    "bardo": ("Musicianship", "musicianship"),
    "provocation": ("Provocation", "provocation"),
    "provo": ("Provocation", "provocation"),
    "provocare": ("Provocation", "provocation"),
    "provocazione": ("Provocation", "provocation"),
    "peacemaking": ("Peacemaking", "peacemaking"),
    "peace": ("Peacemaking", "peacemaking"),
    "calmare": ("Peacemaking", "peacemaking"),
    "calma": ("Peacemaking", "peacemaking"),
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
    "removetrap": ("Remove Trap", "removetrap"),
    "remove trap": ("Remove Trap", "removetrap"),
    "trappole": ("Remove Trap", "removetrap"),
    "disarma trappole": ("Remove Trap", "removetrap"),
    "detecthidden": ("Detect Hidden", "detecthidden"),
    "detect hidden": ("Detect Hidden", "detecthidden"),
    "detect": ("Detect Hidden", "detecthidden"),
    "trova nascosti": ("Detect Hidden", "detecthidden"),
    "poisoning": ("Poisoning", "poisoning"),
    "poison": ("Poisoning", "poisoning"),
    "veleno": ("Poisoning", "poisoning"),
    "avvelenare": ("Poisoning", "poisoning"),

    # Crafting & Gathering
    "mining": ("Mining", "mining"),
    "miner": ("Mining", "mining"),
    "minare": ("Mining", "mining"),
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
    "carpentry": ("Carpentry", "carpentry"),
    "falegname": ("Carpentry", "carpentry"),
    "falegnameria": ("Carpentry", "carpentry"),
    "tailoring": ("Tailoring", "tailoring"),
    "sarto": ("Tailoring", "tailoring"),
    "sartoria": ("Tailoring", "tailoring"),
    "alchemy": ("Alchemy", "alchemy"),
    "alchimia": ("Alchemy", "alchemy"),
    "pozioni": ("Alchemy", "alchemy"),
    "alchimista": ("Alchemy", "alchemy"),
    "inscribe": ("Inscription", "inscribe"),
    "inscription": ("Inscription", "inscribe"),
    "iscrizione": ("Inscription", "inscribe"),
    "pergamene": ("Inscription", "inscribe"),
    "scrolls": ("Inscription", "inscribe"),
    "tinkering": ("Tinkering", "tinkering"),
    "tinker": ("Tinkering", "tinkering"),
    "attrezzi": ("Tinkering", "tinkering"),
    "cartography": ("Cartography", "cartography"),
    "cartografia": ("Cartography", "cartography"),
    "mappe": ("Cartography", "cartography"),
    "tmap": ("Cartography", "cartography"),
    "tmaps": ("Cartography", "cartography"),
    "cooking": ("Cooking", "cooking"),
    "cucinare": ("Cooking", "cooking"),
    "cibo": ("Cooking", "cooking"),

    # Miscellaneous
    "camping": ("Camping", "camping"),
    "campeggio": ("Camping", "camping"),
    "fuoco": ("Camping", "camping"),
    "begging": ("Begging", "begging"),
    "elemosina": ("Begging", "begging"),
    "forensics": ("Forensic Evaluation", "forensics"),
    "forensic": ("Forensic Evaluation", "forensics"),
    "itemid": ("Item Identification", "itemid"),
    "item id": ("Item Identification", "itemid"),
    "identificare": ("Item Identification", "itemid"),
    "tasteid": ("Taste Identification", "tasteid"),
    "armslore": ("Arms Lore", "armslore"),
    "arms lore": ("Arms Lore", "armslore"),
    "perizia": ("Arms Lore", "armslore"),
    "tracking": ("Tracking", "tracking"),
    "tracciare": ("Tracking", "tracking"),
    "focus": ("Focus", "focus"),
    "arcane": ("Arcane", "arcane"),
}


def get_db_connection() -> Optional[sqlite3.Connection]:
    """Restituisce una connessione al database SQLite locale."""
    if not os.path.isfile(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning(f"Errore connessione database razor_scripts: {e}")
        return None


def resolve_skill_from_text(text: str) -> Optional[Tuple[str, str]]:
    """
    Riconosce la skill richiesta da una frase naturale o query.
    Ritorna (nome_formattato, slug_canonico) oppure None.
    """
    clean = text.lower().strip(" ?!.,;:\"'")
    clean = re.sub(
        r"^(?:script|razorscript|razor\s+script|dammi\s+(?:lo\s+|un\s+)?script(?:\s+per|\s+di|\s+del)?|"
        r"mostra(?:\s+lo|\s+gli)?\s+script(?:\s+per|\s+di|\s+del)?|"
        r"hai\s+(?:uno\s+)?script(?:\s+per|\s+di|\s+del)?|"
        r"codice\s+razor(?:\s+per|\s+di|\s+del)?|"
        r"cerco\s+(?:uno\s+)?script(?:\s+per|\s+di|\s+del)?)\s*",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()

    # Rimuove preposizioni o parole come "per", "di", "della", "allenare", "livellare"
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
    Ritorna (True, skill_slug) oppure (False, "").
    """
    t_low = text.lower().strip(" ?!.,;:\"'")

    # Se cita esplicitamente "script" o "razor"
    script_triggers = (
        r"\b(?:script|scripts|razorscript|razor\s+script|macro\s+razor|codice\s+razor)\b",
    )
    has_script_trigger = any(re.search(pat, t_low) for pat in script_triggers)

    if not has_script_trigger:
        # Controlla comandi slash o prefissi diretti
        if t_low.startswith(("script ", "scripts ", "razor ")):
            has_script_trigger = True

    if has_script_trigger:
        res = resolve_skill_from_text(t_low)
        if res:
            return True, res[1]

    return False, ""


def get_scripts_for_skill(skill_slug: str) -> List[Dict[str, Any]]:
    """
    Recupera tutti gli script Razor per una data skill dal database SQLite.
    Ordina per data di aggiornamento/creazione decrescente.
    """
    conn = get_db_connection()
    if not conn:
        return []
    try:
        c = conn.cursor()
        c.execute("""
            SELECT script_id, skill, skill_slug, title, description, author, version, tags, code, url, created_at, updated_at
            FROM razor_scripts
            WHERE lower(skill_slug) = ? OR lower(skill) = ?
            ORDER BY CASE WHEN code IS NOT NULL AND length(code) > 0 THEN 0 ELSE 1 END,
                     updated_at DESC, created_at DESC
        """, (skill_slug.lower(), skill_slug.lower()))
        rows = [dict(r) for r in c.fetchall()]
        return rows
    except Exception as e:
        logger.error(f"Errore recupero script per {skill_slug}: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_script_by_id(script_id: str) -> Optional[Dict[str, Any]]:
    """Recupera un singolo script per ID dal database locale."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM razor_scripts WHERE script_id = ?", (script_id,))
        row = c.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Errore recupero script {script_id}: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


class RazorScriptSelect(discord.ui.Select):
    """Dropdown Select Menu per scegliere uno degli script della skill."""

    def __init__(self, scripts: List[Dict[str, Any]], skill_name: str):
        self.scripts_by_id = {s["script_id"]: s for s in scripts}
        self.skill_name = skill_name

        # Massimo 25 opzioni per Select Menu di Discord
        options = []
        for idx, s in enumerate(scripts[:25], 1):
            title = s["title"][:95]
            author = s.get("author") or "Community"
            desc = s.get("description") or f"Script Razor per {skill_name}"
            desc = desc.replace("\n", " ").strip()[:95]
            options.append(
                discord.SelectOption(
                    label=f"{idx}. {title}",
                    value=s["script_id"],
                    description=f"Autore: {author} • {desc}"[:100],
                    emoji="📜",
                )
            )

        super().__init__(
            placeholder=f"Seleziona uno dei {len(scripts)} script per {skill_name}...",
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

        title = script["title"]
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

        # Se il codice supera 1700 caratteri, manda anteprima + file .razor scaricabile
        file_to_send = None
        code_preview = ""
        clean_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', title)[:40] + ".razor"
        file_to_send = discord.File(io.BytesIO(code.encode("utf-8")), filename=clean_filename)

        if len(code) <= 1700:
            code_preview = f"```razor\n{code}\n```"
        else:
            code_preview = (
                f"```razor\n{code[:1600]}\n# ... [Codice completo nel file {clean_filename} allegato sotto!] ...\n```\n"
                f"📥 *Ho allegato il file `{clean_filename}` pronto da copiare nella cartella Scripts di Razor!*"
            )

        await interaction.response.send_message(
            content=f"✨ Ecco il codice per **{title}** (selezionato da {interaction.user.mention}):\n{code_preview}",
            embed=embed,
            file=file_to_send,
        )


class RazorScriptView(discord.ui.View):
    """View contenente il Select Menu per navigare tutti gli script."""

    def __init__(self, scripts: List[Dict[str, Any]], skill_name: str):
        super().__init__(timeout=180.0)
        if scripts:
            self.add_item(RazorScriptSelect(scripts, skill_name))


def build_razor_scripts_response(
    skill_query: str,
    requested_idx: Optional[int] = None,
) -> Tuple[Optional[discord.Embed], Optional[discord.ui.View], Optional[discord.File], str, Optional[str]]:
    """
    Costruisce la risposta completa (Embed con la lista di tutti gli script,
    View interattiva, file .razor del primo/richiesto script, voice answer e snippet codice).
    Ritorna: (embed, view, file, voice_answer, code_content)
    """
    skill_info = resolve_skill_from_text(skill_query)
    if not skill_info:
        # Fallback ricerca diretta
        skill_name = skill_query.title()
        skill_slug = skill_query.lower().strip()
    else:
        skill_name, skill_slug = skill_info

    scripts = get_scripts_for_skill(skill_slug)
    if not scripts:
        # Prova ricerca FTS
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
                conn.close()

    if not scripts:
        voice = f"Non ho trovato script Razor per {skill_name} su outlands.uorazorscripts.com ragazzi!"
        return None, None, None, voice, None

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
