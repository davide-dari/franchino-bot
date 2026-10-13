import asyncio
import difflib
import html as html_lib
import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

import discord

from config import COLOR_FRANCHINO

logger = logging.getLogger("Franchino.OutlandsWiki")

WIKI_API_URL = "https://wiki.uooutlands.com/api.php"
WIKI_BASE_URL = "https://wiki.uooutlands.com/"
INDEX_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "outlands_wiki_index.json")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "outlands_knowledge.db")

OUTLANDS_DUNGEONS = [
    "Aegis Keep", "Cavernam", "Darkmire Temple", "Field of Souls", "Inferno",
    "Kraul Hive", "Mount Petram", "New Player Dungeon", "Nusero", "Omni Realm",
    "Ossuary", "Pulma", "Shadowspire Cathedral", "The Mausoleum", "Tidal Tomb",
    "Time Dungeon", "Undermountain", "Ocean", "Wilderness"
]

def get_db_connection() -> Optional[sqlite3.Connection]:
    """Restituisce una connessione SQLite ottimizzata al database della conoscenza di Outlands."""
    if not os.path.isfile(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning(f"Errore connessione DB outlands_knowledge: {e}")
        return None

# Catalogo in memoria di tutte le ~2.212 pagine di wiki.uooutlands.com
_ALL_WIKI_TITLES: List[str] = []
_LOWER_TO_TITLE: Dict[str, str] = {}
_INDEX_LOADED: bool = False

# Set di tutti i clean_title della wiki (caricato a runtime dal DB) per rilevamento immediato
# di qualsiasi pagina ufficiale nominata dall'utente (skill, dungeon, aspetti, ecc.)
_WIKI_CLEAN_TITLES_SET: set = set()  # es. {"magery", "fishing", "aegis keep", "fire aspect", ...}

# ==============================================================================
# NORMALIZZAZIONE FONETICA STT ITALIANO (Google Speech-to-Text it-IT)
# Corregge sistematicamente le trascrizioni fonetiche italianizzate di termini gaming inglesi
# e meccaniche di Ultima Online Outlands prima di qualsiasi elaborazione NLU/ricerca.
# ==============================================================================
UO_PHONETIC_REPLACEMENTS: List[Tuple[re.Pattern, str]] = [
    # --- 1. PET & ANIMAL COMPANIONS (es. "pat", "pad", "ped", "pette", "petti", "pe", "pè", "pé", "ipad per farmare") ---
    # Varianti ipad / i pad
    (re.compile(r'\b(migliori|miglior|quali|che|top|consigliami|guida\s+(?:agli|ai)?|parlami\s+d(?:ei|egli))\s+(?:ipad|i\s+pad)\b', re.IGNORECASE), r'\1 pet'),
    (re.compile(r'\b(?:i\s+pad|ipad|l\'ipad|gli\s+ipad)\b(?=\s+(?:per|da|del|dei|in|di|tamer|taming|farm|farmare|dungeon|pvm))', re.IGNORECASE), 'i pet'),
    (re.compile(r'\b(?:ipad)\b(?=\s+(?:per|da|del|dei|in|di|tamer|taming|farm|farmare|dungeon|pvm))', re.IGNORECASE), 'pet'),

    # Varianti con 'bet', 'bed', 'beth', 'bette', 'betti'
    (re.compile(r'\b(i|il|un|uno|che|quale|quali|dei|degli|del|ai|agli|al|coi|con|questo|questi|quel|quelli|ogni|tutti\s+i|miglior|migliori|top|tier\s+list|lista|elenco|consigliami|guida\s+(?:ai|agli|a)?|parlami\s+d(?:ei|egli|el)|spiegami\s+i?|mostrami\s+i?|info\s+su|scheda)\s+(?:bet|bed|beth|bette|betti)\b', re.IGNORECASE), r'\1 pet'),
    (re.compile(r'\b(?:bet|bed|beth|bette|betti)\b(?=\s+(?:per|da|del|dei|di|in|tamer|taming|farm|farmare|pvm|pvp|dungeon|boss|usare|posso))', re.IGNORECASE), 'pet'),

    # Varianti con 'pe', 'pè', 'pé' (parlato rapido o troncamento STT di 'pet')
    (re.compile(r'\b(i|il|un|uno|che|quale|quali|dei|degli|del|dal|ai|agli|al|coi|con|nel|questo|questi|quel|quelli|ogni|tutti\s+i|miglior|migliori|top|tier\s+list|lista|elenco|consigliami|guida\s+(?:ai|agli|a)?|parlami\s+d(?:ei|egli|el)|spiegami\s+i?|mostrami\s+i?|info\s+su|scheda)\s+(?:pe|pè|pé)\b', re.IGNORECASE), r'\1 pet'),
    (re.compile(r'\b(?:pe|pè|pé)\b(?=\s+(?:per|da|del|dei|delle|di|in|con|e|o|posso|usare|scegliere|prendere|comprare|farmare|tamer|taming|pvm|pvp|dungeon|boss|tank|dps|forti|forte|migliori|migliore|tier|livello|allena|addomestica))', re.IGNORECASE), 'pet'),

    # Forme acustiche dirette di pet / pets (es. "pat", "pad", "ped", "pette", "petti", "peth", "petz", "pezz", "pez", "pett", "pads", "pats", "peds")
    # Esclude 'mouse pad' tramite negative lookbehind
    (re.compile(r'(?<!mouse\s)\b(?:pat|pad|ped|pette|petti|peth|petz|pezz|pez|pett)\b', re.IGNORECASE), 'pet'),
    (re.compile(r'\b(?:pets|pads|pats|peds)\b', re.IGNORECASE), 'pet'),

    # --- 2. BUILD & TEMPLATES (es. "bild", "bilde", "bilds", "bield", "biuld") ---
    (re.compile(r'\b(?:bild|bilde|bilds|bield|bielt|biuld|builde|buid|beald)\b', re.IGNORECASE), 'build'),
    (re.compile(r'\b(?:bildare|bildami|buildami|sbildare)\b', re.IGNORECASE), 'buildare'),
    (re.compile(r'\b(?:bildato|bildata|bildati|bildate)\b', re.IGNORECASE), 'buildato'),
    (re.compile(r'\b(?:bilder|bilding)\b', re.IGNORECASE), 'builder'),

    # --- 3. TAMING & TAMER (es. "teming", "teiming", "temer", "teimer") ---
    (re.compile(r'\b(?:animal\s+teming|animal\s+teiming|enimal\s+teming|enimal\s+taming|animal\s+tamin)\b', re.IGNORECASE), 'animal taming'),
    (re.compile(r'\b(?:animal\s+temer|animal\s+teimer|enimal\s+temer|enimal\s+tamer)\b', re.IGNORECASE), 'animal tamer'),
    (re.compile(r'\b(?:teming|teiming|tayming|tamin|temin|taiming|teyming|teimin|teymin)\b', re.IGNORECASE), 'taming'),
    (re.compile(r'\b(?:temer|teimer|taymer|teymar|teimur|teymir|temers|teimers)\b', re.IGNORECASE), 'tamer'),
    (re.compile(r'\b(al|del|il|un|per\s+il|come)\s+temere\b', re.IGNORECASE), r'\1 tamer'),
    (re.compile(r'\btemere\b(?=\s+(?:mage|arciere|dexxer|pvm|pvp|build|guida|pet|animal))', re.IGNORECASE), 'tamer'),

    # --- 4. DRAKE (es. "dreik", "dreic", "drec", "drek") ---
    (re.compile(r'\b(?:dreik|dreic|drec|drek|dreyk|draik)\b', re.IGNORECASE), 'drake'),
    (re.compile(r'\b(?:dreiks|dreics|dreiki|dreici|drakes)\b', re.IGNORECASE), 'drake'),
    (re.compile(r'\b(?:eldritch\s+dreik|eldritc\s+drake|eldritch\s+dreic|eldritc\s+dreik)\b', re.IGNORECASE), 'eldritch drake'),
    (re.compile(r'\b(cold|fire|poison|energy|water|blood|earth|ember|air|dusk|swamp)\s+(?:dreik|dreic)\b', re.IGNORECASE), r'\1 drake'),

    # --- 5. SLAYER (es. "sleier", "sleiar", "sleger", "sleye") ---
    (re.compile(r'\b(?:sleier|sleiar|sleger|sleye|sleer|slajer|slaier|slayr)\b', re.IGNORECASE), 'slayer'),
    (re.compile(r'\b(?:sleiers|sleiars|slegers|slayers)\b', re.IGNORECASE), 'slayer'),

    # --- 6. DEXXER (es. "dexer", "dexter", "decs", "dextar") ---
    (re.compile(r'\b(?:dexer|dexter|decs|dextar|deccer|dexxers|dexers|decsar)\b', re.IGNORECASE), 'dexxer'),

    # --- 7. ASPECT & ASPECT CORE (es. "aspet", "aspek", "aspekt", "distillazioni") ---
    (re.compile(r'\b(gli|l\'|un|agli|dagli|sugli|degli|questo|questi|che|quali|miglior|migliori)\s+(?:aspet|aspek|aspekt|aspec)\b', re.IGNORECASE), r'\1 aspect'),
    (re.compile(r'\b(?:aspet|aspek|aspekt|aspec)\b(?=\s+(?:core|tome|gear|mastery|command|fire|water|air|earth|void|tier|cor|tom|di|in|su|per|outlands))', re.IGNORECASE), 'aspect'),
    (re.compile(r'\b(?:aspet\s+core|aspek\s+core|aspekt\s+core|aspect\s+cor|aspetti\s+core|aspetti\s+cor|aspect\s+cors)\b', re.IGNORECASE), 'aspect core'),
    (re.compile(r'\b(?:aspet\s+tome|aspek\s+tome|aspect\s+tom|aspetti\s+tome)\b', re.IGNORECASE), 'aspect tome'),
    (re.compile(r'\b(?:distillazioni|distilleiscion|distillation|distilleisciun|distillazione)\b', re.IGNORECASE), 'aspect distillation'),

    # --- 8. DUNGEON (es. "dangeon", "dangion", "dongion") ---
    (re.compile(r'\b(?:dangeon|dangion|dongion|dangiun|danjon|dungeons|dangeons)\b', re.IGNORECASE), 'dungeon'),

    # --- 9. LOCKPICKING & LOCKPICK (es. "locpic", "locpiking") ---
    (re.compile(r'\b(?:locpic|locpik|lockpic)\b', re.IGNORECASE), 'lockpick'),
    (re.compile(r'\b(?:locpiking|locpicking|lockpiking)\b', re.IGNORECASE), 'lockpicking'),

    # --- 10. MASTERY CHAIN (es. "masteri cein", "mastery cein") ---
    (re.compile(r'\b(?:masteri\s+cein|mastery\s+cein|masteri\s+chain|masteri\s+cian|mastery\s+cian|masteri\s+scien)\b', re.IGNORECASE), 'mastery chain'),
    (re.compile(r'\bmasteri\b', re.IGNORECASE), 'mastery'),

    # --- 11. SUMMONER (es. "sammoner", "samoner") ---
    (re.compile(r'\b(?:sammoner|samoner|semmoner)\b', re.IGNORECASE), 'summoner'),
    (re.compile(r'\b(?:sammon|semmon)\b', re.IGNORECASE), 'summon'),

    # --- 12. SPAWN & LOOT (es. "spon", "lut", "lutare") ---
    (re.compile(r'\b(?:spon|sponare|sponna|sponano)\b', re.IGNORECASE), 'spawn'),
    (re.compile(r'\b(?:lut|lutare|luttare|lootare)\b', re.IGNORECASE), 'loot'),

    # --- 13. CODEX & BESTIARY (es. "codecs", "bestieri") ---
    (re.compile(r'\b(?:codecs|codec|kodex)\b', re.IGNORECASE), 'codex'),
    (re.compile(r'\b(?:bestieri|bestiari)\b', re.IGNORECASE), 'bestiary'),
    (re.compile(r'\b(?:taming\s+bestieri|teming\s+bestiari|teming\s+bestiary)\b', re.IGNORECASE), 'taming bestiary'),

    # --- 14. SIGLE & ACRONIMI (PvM, PvP, PK) ---
    (re.compile(r'\b(?:pi\s+vu\s+emme|pi\s+v\s+emme|piviemme)\b', re.IGNORECASE), 'pvm'),
    (re.compile(r'\b(?:pi\s+vu\s+pi|pi\s+v\s+pi|pivipi)\b', re.IGNORECASE), 'pvp'),
    (re.compile(r'\b(?:pi\s+cappa|pi\s+ka|picappa)\b', re.IGNORECASE), 'pk'),

    # --- 15. CREATURE SPECIFICHE ---
    (re.compile(r'\b(?:frenzi|frenzid|frenzy)\s+ostard\b', re.IGNORECASE), 'frenzied ostard'),
    (re.compile(r'\b(?:naitmer|naitmar|nightmer|naitmair)\b', re.IGNORECASE), 'nightmare'),
    (re.compile(r'\b(?:fair\s+stid|fairsteed|fair\s+steed)\b', re.IGNORECASE), 'fire steed'),
    (re.compile(r'\b(?:corps\s+worm|corps\s+vorm|corp\s+worm)\b', re.IGNORECASE), 'corpse worm'),
    (re.compile(r'\b(?:bludwolf|blood\s+wolf|blud\s+wolf)\b', re.IGNORECASE), 'bloodwolf'),
    (re.compile(r'\b(?:helhaund|hell\s+hound|hel\s+hound)\b', re.IGNORECASE), 'hellhound'),

    # --- 16. COMANDI VOCALI & MUSICALI ---
    (re.compile(r'\b(?:skippa|schip|skipp)\b', re.IGNORECASE), 'skip'),
    (re.compile(r'\b(?:stoppa|stopp)\b', re.IGNORECASE), 'stop'),
    (re.compile(r'\b(?:plei|pley)\b', re.IGNORECASE), 'play'),
    (re.compile(r'\b(?:sciaffol|shuffol|shuffel)\b', re.IGNORECASE), 'shuffle'),
    (re.compile(r'\b(?:lup)\b', re.IGNORECASE), 'loop'),
]


def normalize_uo_speech(text: str) -> str:
    """
    Normalizza una trascrizione vocale italiana (Google STT it-IT),
    sostituendo le pronunce italianizzate di termini inglesi e gaming
    con i corrispettivi esatti di Ultima Online Outlands.
    """
    if not text:
        return ""
    norm = text.strip()
    for pat, repl in UO_PHONETIC_REPLACEMENTS:
        norm = pat.sub(repl, norm)
    return re.sub(r'\s+', ' ', norm).strip()


# Dizionario rapido dei termini italiani più comuni su Ultima Online Outlands
IT_TO_UO_TERMS: Dict[str, str] = {
    "addomesticare": "Animal Taming",
    "addomesticamento": "Animal Taming",
    "domare": "Animal Taming",
    "taming": "Animal Taming",
    "pet": "Taming Bestiary",
    "pets": "Taming Bestiary",
    "bestie": "Taming Bestiary",
    "animali": "Taming Bestiary",
    "creature": "Taming Bestiary",
    "famigli": "Taming Bestiary",
    "famiglio": "Taming Bestiary",
    "cuccioli": "Taming Bestiary",
    "tamer": "Animal Taming",
    "bestiario": "Taming Bestiary",
    "bestiario taming": "Taming Bestiary",
    "build": "Character Templates",
    "builds": "Character Templates",
    "template": "Character Templates",
    "classi": "Character Templates",
    "classe": "Character Templates",
    "drake": "Drake",
    "slayer": "Slayer",
    "slayers": "Slayer",
    "armi slayer": "Slayer",
    "scassinare": "Lockpicking",
    "scassinamento": "Lockpicking",
    "serrature": "Lockpicking",
    "forzieri": "Lockpicking",
    "pesca": "Fishing",
    "pescare": "Fishing",
    "minare": "Mining",
    "minatore": "Mining",
    "minerali": "Mining",
    "legna": "Lumberjacking",
    "boscaiolo": "Lumberjacking",
    "fabbro": "Blacksmithy",
    "forgiare": "Blacksmithy",
    "sarto": "Tailoring",
    "sartoria": "Tailoring",
    "falegname": "Carpentry",
    "alchimia": "Alchemy",
    "pozioni": "Alchemy",
    "magia": "Magery",
    "incantesimi": "Magery",
    "necromanzia": "Necromancy",
    "negromanzia": "Necromancy",
    "cavalleria": "Chivalry",
    "paladino": "Chivalry",
    "bardo": "Musicianship",
    "pacificazione": "Peacemaking",
    "provocazione": "Provocation",
    "discordia": "Discordance",
    "veleno": "Poisoning",
    "avvelenare": "Poisoning",
    "rubare": "Stealing",
    "ladro": "Stealing",
    "nascondersi": "Hiding",
    "furtività": "Stealth",
    "invisibilità": "Stealth",
    "arciere": "Archery",
    "arco": "Archery",
    "spada": "Swordsmanship",
    "spade": "Swordsmanship",
    "mazza": "Mace Fighting",
    "mazze": "Mace Fighting",
    "scherma": "Fencing",
    "lancia": "Fencing",
    "lotta": "Wrestling",
    "pugni": "Wrestling",
    "parare": "Parrying",
    "scudo": "Parrying",
    "curare": "Healing",
    "guarire": "Healing",
    "bende": "Healing",
    "veterinario": "Veterinary",
    "veterinaria": "Veterinary",
    "tesoro": "Treasure Hunting",
    "mappe del tesoro": "Treasure Maps",
    "mappa del tesoro": "Treasure Maps",
    "nave": "Ships",
    "navi": "Ships",
    "barca": "Ships",
    "barche": "Ships",
    "casa": "Housing",
    "case": "Housing",
    "gilda": "Guilds",
    "gilde": "Guilds",
    "aspetti": "Aspect Mastery",
    "aspetto": "Aspect Mastery",
    "aspect": "Aspect Mastery",
    "catena": "Mastery Chain",
    "catena della maestria": "Mastery Chain",
    "mastery chain": "Mastery Chain",
    "codice": "Codex",
    "codici": "Codex",
    "santuario": "Sanctuary Dungeons",
    "prigioni": "Dungeons",
    "dungeon": "Dungeons",
    "funghi": "Dungeon Mushrooms",
    "carovane": "Trade Caravans",
    "carovana": "Trade Caravans",
    "società": "Societies",
    "arena": "Arena",
    "fazioni": "Factions",
    "assedio": "Town Struggle",
    "echi": "Echoes",
    "echo": "Echoes",
}


# ==============================================================================
# DATABASE DELLE BUILD & TEMPLATE META DI UO OUTLANDS (700 Punti Skill)
# ==============================================================================
OUTLANDS_BUILDS: Dict[str, dict] = {
    "bardo_arciere": {
        "title": "Bardo Arciere PvM (Bard Archer)",
        "wiki_page": "Character Templates",
        "wiki_url": "https://wiki.uooutlands.com/Character_Templates#Bard_Archer",
        "role": "Farm PvM a distanza e Dungeon in solitaria",
        "skills": [
            "100 Archery (Danno a distanza con arco e balestra)",
            "100 Musicianship (Abilità base per tutti gli strumenti)",
            "100 Provocation (Fa combattere i mostri tra di loro)",
            "100 Discordance (Riduce difese, resistenze e danno dei mostri del 20%+)",
            "100 Tactics (Aumenta il danno base delle frecce)",
            "100 Anatomy (Danno extra e cure più potenti)",
            "100 Healing (Cura te stesso e i compagni con le bende)",
        ],
        "stats": "Str 100 | Dex 100 | Int 25",
        "recommended_aspects": "Command Aspect (massimizza il danno dei mostri provocati) oppure Fortune Aspect (massimizza l'oro e i drop).",
        "how_to_play": (
            "1. Entra nel dungeon e suona Provocation su due mostri vicini per farli scannare tra loro.\n"
            "2. Lancia Discordance sul mostro più forte per dimezzargli le difese.\n"
            "3. Finiscili con l'arco da distanza di sicurezza mentre le bende ti tengono a piena vita!"
        ),
        "voice_summary": (
            "Per il Bardo Arciere ti consiglio questo template da 700 punti: 100 Arceria, Musica, Provocazione, Discordia, "
            "Tattica, Anatomia e Guarigione. Come aspetto usa Command o Fortune. La tattica è semplice: fai scannare i mostri tra loro "
            "con Provocazione, abbassagli le difese con Discordia e abbattili da lontano con l'arco. Ti ho mandato la scheda completa in privato!"
        ),
    },
    "tamer_mage": {
        "title": "Tamer Mage (Animal Tamer / Summoner)",
        "wiki_page": "Animal Taming",
        "wiki_url": "https://wiki.uooutlands.com/Animal_Taming",
        "role": "Il re indiscusso del PvM nei Boss e Dungeon di alto livello",
        "skills": [
            "100 Animal Taming (Addomestica e controlla le creature)",
            "100 Animal Lore (Visualizza statistiche e cura con bende i pet)",
            "100 Veterinary (Cura e ressa i pet caduti con le bende)",
            "100 Magery (Incantesimi di cura, attacco e teletrasporto)",
            "100 Meditation (Rigenerazione mana continua)",
            "100 Evaluating Intelligence (Danno magico scalato)",
            "100 Wrestling oppure 100 Camping (Difesa dai colpi o bonus danni/risorse)",
        ],
        "stats": "Str 90 | Dex 35 | Int 100",
        "recommended_aspects": "Command Aspect (essenziale per i pet) oppure Earth Aspect (armatura e mana infinito).",
        "how_to_play": (
            "1. Schiera i tuoi pet (es. Drake, Fenice, Scorpione, Ragno o Frenzied Ostard).\n"
            "2. Manda i pet all'attacco con 'All Kill' e posizionati a distanza per curarli con bende (Veterinary) e Greater Heal.\n"
            "3. Lancia debuff e magie di supporto sui boss mentre i tuoi pet infliggono il 90% del danno."
        ),
        "voice_summary": (
            "La build Tamer Mage è la più forte per i boss di Outlands: 100 Taming, Animal Lore, Veterinaria, Magery, Meditazione, "
            "Eval Int e Wrestling o Camping. Come aspetto devi assolutamente usare Command Aspect. Manda i pet con All Kill, tienili "
            "vivi con bende e magie di supporto. Ti ho inviato tutti i dettagli in messaggio privato!"
        ),
    },
    "dexxer_spada": {
        "title": "Dexxer Spadaccino / Melee (Swordsman Paladin)",
        "wiki_page": "Swordsmanship",
        "wiki_url": "https://wiki.uooutlands.com/Swordsmanship",
        "role": "Combattente corpo a corpo veloce e letale con colpi ad area",
        "skills": [
            "100 Swordsmanship (Armi a lama, spadoni e alabarde ad area)",
            "100 Tactics (Danno fisico aumentato)",
            "100 Anatomy (Danno bonus e velocità bende)",
            "100 Healing (Cura continua con le bende)",
            "100 Parrying (Blocca i colpi dei mostri con lo scudo o armi a due mani)",
            "100 Chivalry (Buff sacri: Sacred Journey, Enemy of One, Consecrate Weapon)",
            "100 Magic Resistance oppure 100 Armslore (Difesa magica o bonus armature)",
        ],
        "stats": "Str 100 | Dex 100 | Int 25",
        "recommended_aspects": "Fire Aspect (danni ad area esplosivi), Void Aspect (danni puri) oppure War Aspect.",
        "how_to_play": (
            "1. Attiva 'Consecrate Weapon' ed 'Enemy of One' con Chivalry prima di ingaggiare.\n"
            "2. Usa spadoni a due mani (Halberd o Bardiche) per attivare i colpi ad area (Cleave) su gruppi di mostri.\n"
            "3. Applica bende in automatico ad ogni secondo mentre assorbi i colpi con Parrying."
        ),
        "voice_summary": (
            "Per il Dexxer Spadaccino usa: 100 Swordsmanship, Tattica, Anatomia, Guarigione, Parrying, Chivalry e Magic Resistance. "
            "L'aspetto migliore è Fire Aspect per i danni ad area esplosivi con alabarda. Attiva i buff di Chivalry e affetta i mostri a "
            "colpi ravvicinati. Ti ho mandato la build completa in privato!"
        ),
    },
    "ladro_dungeon": {
        "title": "Dungeon Chest Thief & Treasure Hunter (Ladro Scassinatore)",
        "wiki_page": "Lockpicking",
        "wiki_url": "https://wiki.uooutlands.com/Lockpicking",
        "role": "Guadagnare centinaia di migliaia di gold aprendo forzieri senza combattere",
        "skills": [
            "100 Lockpicking (Scassina casse dei dungeon e forzieri del tesoro)",
            "100 Remove Trap (Disinnesca trappole esplosive e velenose)",
            "100 Hiding (Invisibilità istantanea sul posto)",
            "100 Stealth (Cammina invisibile in mezzo ai mostri)",
            "100 Magery (Teletrasporto con Recall, Invisibilità e cura)",
            "100 Meditation (Mana sempre carico per castare Recall)",
            "100 Item Identification oppure 100 Cartography (Identifica loot raro o scava Mappe del Tesoro)",
        ],
        "stats": "Str 90 | Dex 35 | Int 100",
        "recommended_aspects": "Fortune Aspect (aumenta il bottino dei forzieri) oppure Shadow Aspect (bonus invisibilità).",
        "how_to_play": (
            "1. Entra nei dungeon in Stealth senza farti vedere da nessun mostro.\n"
            "2. Avvicinati ai Dungeon Chests: disinnesca la trappola con Remove Trap.\n"
            "3. Fai doppio click sui Lockpicks, scassina il forziere, svuota il bottino e scappa con Recall!"
        ),
        "voice_summary": (
            "La build da Ladro dei Forzieri è la più ricca per fare soldi facili: 100 Lockpicking, Remove Trap, Hiding, Stealth, "
            "Magery, Meditazione e Item ID o Cartography. Aspetto Fortune o Shadow. Cammini invisibile nei dungeon, disinneschi "
            "le trappole, scassini i forzieri e scappi con il malloppo. Ti ho inviato la scheda in messaggio privato!"
        ),
    },
    "summoner_necro": {
        "title": "Necro Mage Summoner (Negromante Invocatore)",
        "wiki_page": "Necromancy",
        "wiki_url": "https://wiki.uooutlands.com/Necromancy",
        "role": "Evocare orde di scheletri e spiriti con maledizioni devastanti",
        "skills": [
            "100 Necromancy (Incantesimi oscuri, Evil Omen, Blood Oath, Wither)",
            "100 Spirit Speaking (Potenzia tutte le evocazioni e cura con spiriti)",
            "100 Magery (Evocazioni Magery: Demoni, Elementali, Greater Heal)",
            "100 Meditation (Rigenerazione rapida di mana)",
            "100 Evaluating Intelligence (Danni magici al massimo)",
            "100 Inscription (10% danno magico extra e pergamene potenziate)",
            "100 Wrestling (Difesa corpo a corpo dai mostri)",
        ],
        "stats": "Str 90 | Dex 35 | Int 100",
        "recommended_aspects": "Death Aspect (esplosioni necrotiche e rigenerazione mana) oppure Poison Aspect.",
        "how_to_play": (
            "1. Evoca Demoni o Elementali della Terra per tenere impegnati i mostri.\n"
            "2. Lancia Evil Omen e maledizioni di Necromancy sui bersagli.\n"
            "3. Ricarica mana assorbendo gli spiriti con Spirit Speaking e finisci ad area con Wither."
        ),
        "voice_summary": (
            "Per il Necromante Invocatore ti consiglio: 100 Necromancy, Spirit Speaking, Magery, Meditazione, Eval Int, Inscription "
            "e Wrestling. Come aspetto usa Death Aspect per le esplosioni oscure. Evoca spiriti e demoni, lancia maledizioni e guarda i mostri "
            "cadere senza mai finire il mana. Trovi tutti i dettagli nel messaggio privato!"
        ),
    },
    "starter_gold": {
        "title": "Miglior Build per Iniziare da Zero (Starter Gold Farm)",
        "wiki_page": "New Player Guide",
        "wiki_url": "https://wiki.uooutlands.com/New_Player_Guide",
        "role": "Farmare gold e imparare il gioco senza investire soldi in equipaggiamento",
        "skills": [
            "100 Archery o Swordsmanship (Arma fisica a costo zero)",
            "100 Tactics (Danno puro)",
            "100 Anatomy (Danno e bende)",
            "100 Healing (Cura continua ed economica)",
            "100 Musicianship (Abilità economica per bardo)",
            "100 Provocation (Fai uccidere i mostri tra loro nei Sanctuary Dungeons)",
            "100 Camping o Chivalry (Bonus viaggio e danni)",
        ],
        "stats": "Str 100 | Dex 100 | Int 25",
        "recommended_aspects": "Fortune Aspect (per accumulare oro e materiali velocemente all'inizio).",
        "how_to_play": (
            "1. Crea il personaggio nell'Isola dei Principianti (Shelter Island) e alza le skill base.\n"
            "2. Vai nei Sanctuary Dungeons dove non si può essere attaccati da altri giocatori (no PvP).\n"
            "3. Provoca i mostri tra loro e raccogli oro, pergamene e reagenti per comprare la tua prima casa!"
        ),
        "voice_summary": (
            "Se stai iniziando da zero su Outlands, la build migliore è il Bardo Melee o Bardo Arciere: ti bastano arco, bende e un liuto. "
            "Alza Provocazione e Musica: nei Sanctuary Dungeons sei al sicuro dal PvP, fai combattere i mostri tra loro e tiri su centinaia "
            "di migliaia di gold in pochissimo tempo. Ti ho mandato la guida per principianti in privato!"
        ),
    },
    "meta_overview": {
        "title": "Le 6 Migliori Build Meta di UO Outlands (700 Punti Skill)",
        "wiki_page": "Character Templates",
        "wiki_url": "https://wiki.uooutlands.com/Character_Templates",
        "role": "I 6 archetipi più forti ed efficienti per PvM, Dungeon e Farm di Outlands",
        "skills": [
            "🏹 **Bardo Arciere (Bard Archer)**: Farm a distanza in solitaria (Provocation + Archery)",
            "🐾 **Tamer Mage (Animal Taming)**: Il re dei boss end-game e dungeon con i pet",
            "⚔️ **Dexxer Spada (Melee Paladin)**: Danni ad area ravvicinati con Cleave e Chivalry",
            "💀 **Summoner Necro (Necromancy)**: Evocazioni di massa e maledizioni con mana continuo",
            "🗝️ **Ladro da Dungeon (Chest Thief)**: Ricchezza estrema aprendo forzieri senza combattere",
            "💰 **Starter Gold Farm (Beginner)**: Per iniziare da zero nei Sanctuary Dungeons e fare subito oro",
        ],
        "stats": "Str 90-100 | Dex 35-100 | Int 25-100 a seconda del template",
        "recommended_aspects": "Command per Tamer/Bardo, Fire/Void per Dexxer, Fortune per Ladro/Starter, Death per Necro.",
        "how_to_play": (
            "Scegli l'archetipo più adatto al tuo stile di gioco:\n"
            "• Vuoi combattere corpo a corpo? Scegli il Dexxer Spada.\n"
            "• Vuoi dominare da lontano con gli animali? Scegli il Tamer Mage.\n"
            "• Vuoi farmare in totale relax nei dungeon? Scegli il Bardo Arciere o il Ladro!\n"
            "Dì *\"Franchino build Tamer\"* o *\"Franchino build Dexxer\"* per la guida dettagliata di ciascuna!"
        ),
        "voice_summary": (
            "Le migliori build su Outlands sono 6: Bardo Arciere per i dungeon in solitaria; Tamer Mage per dominare i boss con i pet; "
            "Dexxer con Spada per il corpo a corpo ad area; Summoner Necro per evocare orde oscure; Ladro per svuotare i forzieri; "
            "e lo Starter Gold per iniziare da zero e fare subito centinaia di migliaia di monete! Chiedimi una build specifica per tutti i dettagli!"
        ),
    },
}


# ==============================================================================
# DATABASE DEI MIGLIORI PET & ANIMAL TAMING DI UO OUTLANDS
# ==============================================================================
OUTLANDS_BEST_PETS: dict = {
    "title": "I Migliori Pet per Farmare (Animal Taming Tier List)",
    "wiki_page": "Taming Bestiary",
    "wiki_url": "https://wiki.uooutlands.com/Taming_Bestiary",
    "role": "I Pet più forti ed efficienti per farmare Dungeon, Boss e Mostri Paragon",
    "top_pets": [
        "🦖 **Frenzied Ostard (Pack da 5 - 1 slot cad.)**: Il miglior DPS iniziale e per farm veloce nei primi dungeon. Altissima velocità di corsa e colpi rapidissimi in sciame.",
        "🐉 **Drake / Eldritch Drake (2-3 slot)**: Il pet all-round più solido per dungeon intermedi e avanzati. Respiro di fuoco (Fire Breath), tanti punti vita e resistenza elevata per tankare.",
        "🔥 **Fire Steed / Nightmare (2 slot)**: Ottimo compromesso tra danno magico ad area, mobilità e velocità di ingaggio.",
        "🦂 **Giant Scorpion / Corpse Worm (1-2 slot)**: Infliggono veleno letale continuo (Deadly/Lethal Poison) e debuff, devastanti per logorare boss con armatura alta.",
        "🦅 **Phoenix / Colossal / Dragon (3 slot - End-game)**: I giganti del PvM, adatti a reggere i boss e i paragon più brutali di Outlands.",
    ],
    "key_tips": (
        "• **Taming Bestiary**: Fai doppio click sul libro del Bestiario per sbloccare le abilità passive dei tuoi pet fino al Tier 10.\n"
        "• **Command Aspect**: L'Aspetto obbligatorio per i Tamer, aumenta del 100%+ i danni e la sopravvivenza dei pet.\n"
        "• **Macro All Kill**: Imposta su Razor una macro veloce per mandare tutti i pet sul bersaglio stando a distanza di sicurezza per curarli con bende (Veterinary) e Greater Heal."
    ),
    "voice_summary": (
        "Per farmare su Outlands i migliori pet sono: 5 Frenzied Ostard per velocità e danni altissimi all'inizio; "
        "Drake o Eldritch Drake per tankare mostri pesanti nei dungeon; oppure Scorpioni e Corpse Worm per il veleno letale sui boss. "
        "Potenziali sempre con il Taming Bestiary e il Command Aspect! Ti ho mandato tutti i dettagli in messaggio privato!"
    ),
}


# ==============================================================================
# DATABASE DEGLI OGGETTI SPECIALI & MECCANICHE DI UO OUTLANDS
# ==============================================================================
OUTLANDS_SPECIAL_ITEMS: Dict[str, dict] = {
    "mastery_chain": {
        "name": "Mastery Chain (Catena della Maestria)",
        "wiki_page": "Mastery Chain",
        "wiki_url": "https://wiki.uooutlands.com/Mastery_Chain",
        "how_to_use": (
            "• **Cos'è**: È una collana speciale che si indossa nello slot collo e potenzia enormemente il tuo personaggio.\n"
            "• **Come si usa**: Fai doppio click sulla catena per aprire il menu dei Links.\n"
            "• **Come livellare**: Guadagnando Weapon o Spell Exp nei dungeon sblocchi nuovi castoni (Link Slots).\n"
            "• **Inserire Links**: Puoi incastonare Passive Links (danno, gold, exp, difese) e Medallions rari comprati o droppati dai boss."
        ),
        "voice_summary": (
            "La Mastery Chain è la collana più importante di Outlands: si indossa nel collo e si livella facendo esperienza nei dungeon. "
            "Facendo doppio click apri il menu per incastonare i Links e i Medaglioni, che ti danno bonus passivi enormi a danno, oro guadagnato "
            "e resistenze. Ti ho inviato la guida completa della catena in messaggio privato!"
        ),
    },
    "aspect_core": {
        "name": "Aspect Core & Aspect Distillation (Aspetti di Maestria)",
        "wiki_page": "Aspect Mastery",
        "wiki_url": "https://wiki.uooutlands.com/Aspect_Mastery",
        "how_to_use": (
            "• **Cos'è**: Il sistema di progressione centrale di Outlands (14 Aspetti: Fire, Water, Air, Earth, Command, Void, ecc.).\n"
            "• **Come si usano i Core**: Fai doppio click sul tuo Aspect Tome con i Cores nello zaino per sbloccare i Tier dell'aspetto.\n"
            "• **Come si usano le Distillations**: Servono per caricare i punti esperienza del tuo Aspetto attivo.\n"
            "• **Attivazione su Equip**: Una volta sbloccato l'aspetto, fai doppio click sull'Aspect Gear per infondere armi, armature o spellbook."
        ),
        "voice_summary": (
            "Gli Aspect Core e le Distillations servono per sbloccare e potenziare i 14 Aspetti di Outlands. Fai doppio click sul tuo Aspect Tome: "
            "i Core sbloccano i Tier dell'aspetto, mentre le Distillations caricano l'esperienza. Poi infondi le tue armi e armature per scatenare "
            "effetti devastanti. Ti ho mandato la guida degli aspetti in privato!"
        ),
    },
    "rune_codex": {
        "name": "Rune Codex (Codice delle Rune)",
        "wiki_page": "Codex",
        "wiki_url": "https://wiki.uooutlands.com/Codex",
        "how_to_use": (
            "• **Cos'è**: Il libro di viaggio definitivo che sostituisce le vecchie runebook.\n"
            "• **Come si usa**: Fai doppio click sul Rune Codex per aprire la mappa con tutti i punti di viaggio salvati.\n"
            "• **Memorizzare Rune**: Trascina una runa marcata sopra il Codex per salvarla per sempre (non perdi mai le coordinate!).\n"
            "• **Viaggiare**: Clicca su una destinazione e premi Recall, Gate Travel o Sacred Journey."
        ),
        "voice_summary": (
            "Il Rune Codex è il libro magico di viaggio di Outlands. Fai doppio click sopra per aprirlo: trascina le rune marcate dentro "
            "per salvarle per sempre senza rischio di perderle. Puoi viaggiare con Recall, Gate Travel o Sacred Journey con un solo click. "
            "Ti ho inviato il link alla pagina della Wiki in messaggio privato!"
        ),
    },
    "resource_hoard": {
        "name": "Resource Hoard (Forziere di Raccolta Risorse)",
        "wiki_page": "Housing",
        "wiki_url": "https://wiki.uooutlands.com/Housing#Resource_Containers",
        "how_to_use": (
            "• **Cos'è**: Un forziere speciale posizionabile all'interno della propria casa.\n"
            "• **Come si usa**: Fai doppio click o premi il pulsante 'Deposit All' mentre sei vicino al forziere.\n"
            "• **Funzione**: Svuota istantaneamente il tuo zaino da tutti i materiali da crafting (lingotti, legna, pelli, erbe, reagenti, gemme) organizzandoli da solo!"
        ),
        "voice_summary": (
            "Il Resource Hoard è un contenitore per la casa comodissimo: quando torni dal farm, fai doppio click sopra e premi Deposit All. "
            "Ti svuota lo zaino in un secondo sistemando lingotti, legna, pelli, erbe e reagenti nei rispettivi scomparti. Ti ho mandato il link in privato!"
        ),
    },
    "dungeon_mushrooms": {
        "name": "Dungeon Mushrooms (Funghi dei Dungeon)",
        "wiki_page": "Dungeon Mushrooms",
        "wiki_url": "https://wiki.uooutlands.com/Dungeon_Mushrooms",
        "how_to_use": (
            "• **Cos'è**: Funghi magici luminosi che crescono spontaneamente sul pavimento dei dungeon.\n"
            "• **Come si usa**: Raccoglili e fai doppio click sopra per mangiarli.\n"
            "• **Effetti**: Forniscono un buff di 10 minuti che aumenta il danno PvM, la rigenerazione di mana e stamina.\n"
            "• **Mushroom Pouch**: Puoi conservarli nella sacca speciale Mushroom Pouch per non farli marcire."
        ),
        "voice_summary": (
            "I Dungeon Mushrooms sono funghi magici che trovi per terra nei dungeon. Raccoglili e fai doppio click per mangiarli: ti danno "
            "un buff di dieci minuti che aumenta il danno contro i mostri e ricarica mana e stamina. Puoi conservarli nella Mushroom Pouch. "
            "Tutti i dettagli te li ho inviati in privato!"
        ),
    },
    "taming_bestiary": {
        "name": "Taming Bestiary (Bestiario delle Creature)",
        "wiki_page": "Taming Bestiary",
        "wiki_url": "https://wiki.uooutlands.com/Taming_Bestiary",
        "how_to_use": (
            "• **Cos'è**: Il libro ufficiale per tutti gli addestratori di creature (Tamers).\n"
            "• **Come si usa**: Fai doppio click sul Bestiary per vedere l'elenco di tutte le creature addomesticabili del server.\n"
            "• **Progressione**: Man mano che combatti con un tipo di creatura, accumuli exp nel Bestiario per sbloccare Follower Traits e potenziare i tuoi pet fino al Tier 10!"
        ),
        "voice_summary": (
            "Il Taming Bestiary è il libro fondamentale per chi usa il Tamer: fai doppio click sopra per vedere le statistiche di tutti i pet del gioco. "
            "Combattendo con i tuoi pet accumuli punti nel libro per sbloccare abilità uniche e potenziare le loro statistiche fino al Tier 10. "
            "Ti ho inviato il link diretto in messaggio privato!"
        ),
    },
    "echoes": {
        "name": "Echoes (Echi dell'Anima per Cambio Classe)",
        "wiki_page": "Echoes",
        "wiki_url": "https://wiki.uooutlands.com/Echoes",
        "how_to_use": (
            "• **Cos'è**: Cristalli magici che catturano un intero set di 700 punti abilità (template).\n"
            "• **Come si usa**: Fai doppio click sull'Echo per salvare il tuo template attuale o caricarne uno salvato.\n"
            "• **Vantaggio**: Ti permette di passare all'istante da Guerriero a Mago o Bardo in 5 secondi senza dover rialzare le skill da capo!"
        ),
        "voice_summary": (
            "Gli Echoes sono cristalli magici che memorizzano un intero template di abilità da 700 punti. Facendo doppio click puoi cambiare "
            "completamente classe in cinque secondi, passando ad esempio da guerriero a mago senza dover rimacrare le abilità da zero. "
            "Tutti i dettagli li trovi nel messaggio privato!"
        ),
    },
    "mastery_scrolls": {
        "name": "Mastery Scrolls (Pergamene Skill 105-120)",
        "wiki_page": "Mastery Chain",
        "wiki_url": "https://wiki.uooutlands.com/Mastery_Chain",
        "how_to_use": (
            "• **Cos'è**: Pergamene rare droppate dai Boss dei Dungeon e Shrine Battles.\n"
            "• **Come si usa**: Fai doppio click sulla pergamena corrispondente alla tua abilità (es. Swordsmanship 110).\n"
            "• **Effetto**: Alza il limite massimo della skill sopra 100 (fino a 120), garantendo precisione, danno e difese superiori."
        ),
        "voice_summary": (
            "Le Mastery Scrolls sono pergamene rare dei boss che alzano il limite massimo delle tue abilità sopra il cap di 100, "
            "fino a 120. Fai doppio click sulla pergamena della tua skill per sbloccare il cap superiore e massimizzare i tuoi danni. "
            "Ti ho mandato il link in messaggio privato!"
        ),
    },
    "lockpicks": {
        "name": "Lockpicks & Trap Wire (Grimaldelli e Kit Scasso)",
        "wiki_page": "Lockpicking",
        "wiki_url": "https://wiki.uooutlands.com/Lockpicking",
        "how_to_use": (
            "• **Come si usano**: Usa prima la skill Remove Trap sul forziere per disinnescare la trappola (con Trap Wire nello zaino).\n"
            "• **Scassinare**: Fai doppio click sui Lockpicks e seleziona il Dungeon Chest o il forziere del tesoro.\n"
            "• **Livelli**: Richiede da 50 a 100 Lockpicking in base al Tier della cassa (da Tier 1 a Tier 8 paragon chests)."
        ),
        "voice_summary": (
            "I Lockpicks si usano per aprire i forzieri dei dungeon e le mappe del tesoro. Prima usa Remove Trap con il Trap Wire nello zaino "
            "per evitare che la cassa ti esploda in faccia, poi fai doppio click sui Lockpicks e mira al forziere. Ti ho mandato tutti i dettagli in privato!"
        ),
    },
}


def _http_get_json(url: str, timeout: float = 7.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (FranchinoBot/2.0; +https://wiki.uooutlands.com)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_wiki_catalog_sync() -> List[str]:
    """
    Scarica e indicizza l'intero catalogo di tutte le ~2.140 pagine di https://wiki.uooutlands.com
    salvandolo su disco in data/outlands_wiki_index.json per ricerche istantanee su tutto il sito.
    """
    global _ALL_WIKI_TITLES, _LOWER_TO_TITLE, _INDEX_LOADED, _WIKI_CLEAN_TITLES_SET

    if _INDEX_LOADED and _ALL_WIKI_TITLES:
        return _ALL_WIKI_TITLES

    # 1. Carica istantaneamente da SQLite data/outlands_knowledge.db se disponibile
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("SELECT title, clean_title FROM pages")
            rows = c.fetchall()
            if len(rows) > 500:
                _ALL_WIKI_TITLES = [r[0] for r in rows]
                _LOWER_TO_TITLE = {r[1]: r[0] for r in rows}
                # Popola il set di clean_title per rilevamento immediato in is_wiki_question
                _WIKI_CLEAN_TITLES_SET = {r[1] for r in rows if r[1]}
                c.execute("SELECT clean_from, to_title FROM redirects")
                for c_from, t_to in c.fetchall():
                    _LOWER_TO_TITLE[c_from] = t_to
                    _WIKI_CLEAN_TITLES_SET.add(c_from)
                try:
                    c.execute("SELECT lower(name) FROM creatures")
                    for c_row in c.fetchall():
                        if c_row[0]:
                            _WIKI_CLEAN_TITLES_SET.add(c_row[0].strip())
                except Exception:
                    pass
                _INDEX_LOADED = True
                conn.close()
                logger.info(f"📚 Caricate {len(_ALL_WIKI_TITLES)} pagine ufficiali e {len(_WIKI_CLEAN_TITLES_SET)} termini nel catalogo locale outlands_knowledge.db!")
                return _ALL_WIKI_TITLES
            conn.close()
    except Exception as e:
        logger.debug(f"Errore lettura DB per catalogo: {e}")


    # 2. Carica istantaneamente la cache su disco se disponibile
    try:
        if os.path.isfile(INDEX_CACHE_FILE):
            with open(INDEX_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list) and len(cached) > 500:
                _ALL_WIKI_TITLES = cached
                _LOWER_TO_TITLE = {t.lower(): t for t in _ALL_WIKI_TITLES}
                _INDEX_LOADED = True
                return _ALL_WIKI_TITLES
    except Exception as e:
        logger.debug(f"Errore lettura cache indice wiki: {e}")

    # 3. Scarica tutte le pagine da wiki.uooutlands.com via MediaWiki API (allpages)
    titles: List[str] = []
    apcontinue: Optional[str] = None
    try:
        while True:
            params = {
                "action": "query",
                "list": "allpages",
                "aplimit": "500",
                "apfilterredir": "nonredirects",
                "format": "json",
            }
            if apcontinue:
                params["apcontinue"] = apcontinue
            url = f"{WIKI_API_URL}?{urllib.parse.urlencode(params)}"
            data = _http_get_json(url, timeout=12.0)
            batch = data.get("query", {}).get("allpages", [])
            for p in batch:
                if "title" in p:
                    titles.append(p["title"])
            cont = data.get("continue", {}).get("apcontinue")
            if not cont:
                break
            apcontinue = cont

        if len(titles) > 500:
            _ALL_WIKI_TITLES = titles
            _LOWER_TO_TITLE = {t.lower(): t for t in _ALL_WIKI_TITLES}
            _INDEX_LOADED = True
            os.makedirs(os.path.dirname(INDEX_CACHE_FILE), exist_ok=True)
            with open(INDEX_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(titles, f, ensure_ascii=False)
            logger.info(f"📚 Indicizzate tutte le {len(titles)} pagine di wiki.uooutlands.com!")
    except Exception as e:
        logger.warning(f"Errore durante l'indicizzazione completa di wiki.uooutlands.com: {e}")

    return _ALL_WIKI_TITLES


async def preload_outlands_wiki_index():
    """Avvia in background lo studio/indicizzazione completa di tutte le pagine di wiki.uooutlands.com."""
    try:
        await asyncio.to_thread(ensure_wiki_catalog_sync)
    except Exception as e:
        logger.debug(f"Errore preload_outlands_wiki_index: {e}")


_TRANSLATION_CACHE: Dict[Tuple[str, str, str], str] = {}


def translate_text(text: str, source_lang: str = "en", target_lang: str = "it") -> str:
    """
    Traduce un testo combinando 3 endpoint gratuiti senza API key:
    1. clients5.google.com (dict-chrome-ex)
    2. translate.googleapis.com (gtx)
    3. api.mymemory.translated.net (fallback)
    """
    if not text or not text.strip():
        return ""
    clean_in = text[:3500].strip()
    cache_key = (source_lang, target_lang, clean_in)
    if cache_key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[cache_key]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # 1. Endpoint primario: clients5.google.com (veloce e senza rate-limit 429)
    try:
        params = urllib.parse.urlencode({
            "client": "dict-chrome-ex",
            "sl": source_lang,
            "tl": target_lang,
            "q": clean_in,
        })
        req = urllib.request.Request(f"https://clients5.google.com/translate_a/t?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                first = data[0]
                out = first if isinstance(first, str) else (first[0] if isinstance(first, list) and first else "")
                if out and out.strip():
                    _TRANSLATION_CACHE[cache_key] = out.strip()
                    return out.strip()
    except Exception as e:
        logger.debug(f"clients5 translate fallback ({source_lang}->{target_lang}): {e}")

    # 2. Secondo endpoint: translate.googleapis.com
    try:
        params = urllib.parse.urlencode({
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "dt": "t",
            "q": clean_in,
        })
        req = urllib.request.Request(f"https://translate.googleapis.com/translate_a/single?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data, list) and data[0]:
                out = "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
                if out:
                    _TRANSLATION_CACHE[cache_key] = out
                    return out
    except Exception as e:
        logger.debug(f"googleapis translate fallback ({source_lang}->{target_lang}): {e}")

    # 3. Terzo endpoint fallback: MyMemory
    try:
        params = urllib.parse.urlencode({
            "q": clean_in[:490],
            "langpair": f"{source_lang}|{target_lang}",
        })
        req = urllib.request.Request(f"https://api.mymemory.translated.net/get?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            out = data.get("responseData", {}).get("translatedText", "").strip()
            if out:
                _TRANSLATION_CACHE[cache_key] = out
                return out
    except Exception as e:
        logger.debug(f"MyMemory translate fallback ({source_lang}->{target_lang}): {e}")

    return clean_in


def _strip_tags(html_fragment: str) -> str:
    """Rimuove i tag HTML e decodifica le entità lasciando testo pulito."""
    txt = re.sub(r"<style.*?</style>|<script.*?</script>", "", html_fragment, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<span class=\"mw-editsection\">.*?</span>\s*</span>", "", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<br\s*/?>", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html_lib.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _convert_html_table_to_text(table_html: str) -> str:
    """
    Converte le tabelle HTML di wiki.uooutlands.com (inclusi i box statistiche dei mostri,
    Taming Bestiary, Aspect Tiers, Mastery Chain, ecc.) in testo leggibile chiave: valore.
    """
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.DOTALL | re.IGNORECASE)
    parsed_lines: List[str] = []
    pending_headers: List[str] = []

    for row_html in rows:
        ths = [_strip_tags(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", row_html, flags=re.DOTALL | re.IGNORECASE)]
        tds = [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.DOTALL | re.IGNORECASE)]

        # Caso 1: riga di sole intestazioni <th> (es. Slayer | Difficulty | Gold Value | Hits | ...)
        if ths and not tds:
            non_empty_ths = [h for h in ths if h]
            if len(non_empty_ths) > 1:
                pending_headers = ths
            continue

        # Caso 2: riga di valori <td> subito dopo una riga di intestazioni <th>
        if tds and pending_headers and not ths:
            pairs = []
            for h, v in zip(pending_headers, tds):
                h_clean = h.strip()
                v_clean = v.strip()
                if h_clean and v_clean and v_clean != "?":
                    pairs.append(f"{h_clean}: {v_clean}")
            if pairs:
                parsed_lines.append("• " + " | ".join(pairs))
            pending_headers = []
            continue

        # Caso 3: riga mista (es. Follower Abilities: <th>Nome Abilità</th> + <td>Tipo</td> + <td>Descrizione</td>)
        cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, flags=re.DOTALL | re.IGNORECASE)]
        cells = [c for c in cells if c and c != "?"]
        if cells:
            line = " — ".join(cells)
            if len(line) > 3:
                parsed_lines.append(f"• {line}")

    return "\n".join(parsed_lines)


def _parse_wiki_html_into_sections(html_content: str, page_title: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """
    Analizza l'HTML renderizzato da MediaWiki (che include anche tutti i template Semantic MediaWiki
    come {{CreaturePage}}) e restituisce:
    - Lista di sezioni: [{'title': ..., 'anchor': ..., 'content': ...}, ...]
    - URL dell'immagine principale della pagina (se presente)
    """
    # Estrae la prima immagine significativa della pagina (es. foto della creatura o icona)
    thumbnail_url = None
    for img_match in re.finditer(r"<img[^>]+src=\"(/images/[^\"]+)\"", html_content, flags=re.IGNORECASE):
        src = img_match.group(1)
        if not any(skip in src.lower() for skip in ("icon-", "edit", "poweredby")):
            thumbnail_url = f"https://wiki.uooutlands.com{src}"
            break
    if not thumbnail_url:
        m_any = re.search(r"<img[^>]+src=\"(/images/[^\"]+)\"", html_content, flags=re.IGNORECASE)
        if m_any:
            thumbnail_url = f"https://wiki.uooutlands.com{m_any.group(1)}"

    # Sostituisce tutte le <table>...</table> con il loro testo strutturato
    def _table_repl(match):
        return "\n" + _convert_html_table_to_text(match.group(0)) + "\n"

    processed_html = re.sub(
        r"<table[^>]*>.*?</table>",
        _table_repl,
        html_content,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Divide l'HTML in base alle intestazioni <h2> / <h3> / <h4> con <span class="mw-headline" id="...">
    heading_pattern = re.compile(
        r"<h[234][^>]*>.*?<span[^>]*class=\"[^\"]*mw-headline[^\"]*\"[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</span>.*?</h[234]>",
        flags=re.DOTALL | re.IGNORECASE,
    )

    sections: List[Dict[str, str]] = []
    last_pos = 0
    current_title = "Panoramica Generale"
    current_anchor = ""

    for m in heading_pattern.finditer(processed_html):
        chunk_html = processed_html[last_pos : m.start()]
        cleaned_chunk = _html_chunk_to_clean_lines(chunk_html)
        if cleaned_chunk:
            sections.append({
                "title": current_title,
                "anchor": current_anchor,
                "content": cleaned_chunk,
            })
        current_anchor = html_lib.unescape(m.group(1)).strip()
        current_title = _strip_tags(m.group(2)) or current_anchor
        last_pos = m.end()

    tail_chunk = _html_chunk_to_clean_lines(processed_html[last_pos:])
    if tail_chunk:
        sections.append({
            "title": current_title,
            "anchor": current_anchor,
            "content": tail_chunk,
        })

    return sections, thumbnail_url


def _html_chunk_to_clean_lines(chunk_html: str) -> str:
    """Converte paragrafi <p>, elementi lista <li> e righe tabella in testo ordinato."""
    # Trasforma <li> in bullet point e </p> / </tr> / </div> in a-capo
    txt = re.sub(r"<li[^>]*>", "\n• ", chunk_html, flags=re.IGNORECASE)
    txt = re.sub(r"</(?:p|li|div|tr|ul|ol)>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<style.*?</style>|<script.*?</script>", "", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<span class=\"mw-editsection\">.*?</span>\s*</span>", "", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html_lib.unescape(txt)

    lines: List[str] = []
    for raw_line in txt.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line in ("•", "• -"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _extract_topic_and_keywords(question: str) -> Tuple[str, List[str]]:
    """
    Estrae il soggetto principale da cercare sulla Wiki e le parole chiave specifiche della domanda
    per individuare la sezione esatta (#anchor) di riferimento.
    """
    q_norm = normalize_uo_speech(question)
    q_low = q_norm.lower().strip(" ?!.,;:")

    # Parole chiave di sezione (per trovare l'ancora #Sezione più pertinente alla domanda!)
    section_keywords: List[str] = []
    keyword_map = {
        "training": ("allena", "allenare", "allenamento", "alzare", "livellare", "salire", "training", "gain", "macrare"),
        "tameable": ("addomesticato", "tamed", "pet", "pets", "follower", "slot", "min taming", "quanta skill", "domare", "bestiario", "bestiary", "famiglio", "famigli"),
        "abilities": ("abilità", "abilita", "poteri", "special", "spells", "attacchi", "abilities", "ability", "breath"),
        "unlock": ("sblocca", "sbloccare", "attivare", "attiva", "ottenere", "creare", "craftare", "unlock", "activate", "essence"),
        "tier": ("livello", "livelli", "tier", "upgrade", "potenziare", "esperienza", "exp", "xp"),
        "location": ("dove", "trova", "trovano", "mappa", "posizione", "zona", "dungeon", "location", "spawn"),
        "loot": ("drop", "droppa", "bottino", "tesoro", "ricompense", "gold", "oro", "loot", "reward"),
        "pvp": ("pvp", "combattimento", "danno", "damage", "resistenza", "armor"),
    }
    for en_kw, it_triggers in keyword_map.items():
        if any(t in q_low for t in it_triggers):
            section_keywords.append(en_kw)

    # Pulisce l'inizio della frase per estrarre l'argomento principale
    topic = re.sub(
        r"^(?:cerca\s+sulla\s+wiki|cerca\s+su\s+outlands|sulla\s+wiki|su\s+outlands|wiki|outlands)\s+",
        "",
        q_low,
        flags=re.IGNORECASE,
    )
    topic = re.sub(
        r"^(?:mi\s+dici|puoi\s+dirmi|dimmi\s+tutto\s+su|dimmi\s+di|dimmi|spiegami\s+bene|spiegami|"
        r"parlami\s+di|parlami\s+dei|parlami\s+delle|parlami\s+del|parla\s+dei|parla\s+delle|parla\s+del|parla\s+di|"
        r"raccontami\s+di|guida\s+(?:a|ai|al|su|per|agli)|consigli\s+(?:su|per)|"
        r"consigliami\s+(?:su\s+|per\s+|un\s+|una\s+|uno\s+)?|cosa\s+mi\s+consigli\s+(?:su\s+|per\s+)?)\s*",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.sub(
        r"^(?:che\s+cos'è|che\s+cosa\s+è|cos'è|cosa\s+è|cosa\s+sono|chi\s+è|chi\s+sono|"
        r"come\s+funziona|come\s+funzionano|come\s+si\s+fa\s+a|come\s+si\s+fa|come\s+si\s+usa|"
        r"come\s+si\s+sblocca|come\s+sblocco|come\s+si\s+attiva|come\s+si\s+ottiene|"
        r"come\s+si\s+allena|come\s+alleno|come\s+posso\s+allenare|dove\s+si\s+allena|"
        r"dove\s+si\s+trova|dove\s+si\s+trovano|dove\s+trovo|a\s+cosa\s+serve|a\s+che\s+serve|"
        r"quanta\s+skill\s+serve\s+per|quanto\s+serve\s+per|quali\s+sono\s+le\s+statistiche\s+di|statistiche\s+di|"
        r"quali\s+sono\s+i|quali\s+sono\s+le|quali\s+sono|qual\s+è\s+(?:il|la|l')|quale\s+è\s+(?:il|la|l')|"
        r"che|quale|quali)\s+",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.sub(
        r"^(?:addomesticare|domare|sbloccare|allenare|usare|uccidere|fare)\s+",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.sub(
        r"\s+(?:posso\s+usare|posso\s+scegliere|usare|scegliere|prendere|mettere|consigli|mi\s+consigli|per\s+farmare|da\s+farmare|per\s+iniziare)$",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.sub(
        r"\s+(?:su\s+uo\s+outlands|su\s+outlands|in\s+outlands|sulla\s+wiki|su\s+ultima\s+online)$",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.sub(r"^(?:il|lo|la|i|gli|le|un|uno|una|dei|degli|delle|del|della)\s+|^l['’]\s*", "", topic.strip()).strip(" ?!.,;:")

    return (topic or q_low), section_keywords


def _find_best_page_in_catalog(raw_topic: str, en_topic: str, full_question: str) -> Optional[str]:
    """
    Cerca la pagina migliore su tutto il sito wiki.uooutlands.com combinando:
    1. Dizionario diretto IT -> UO Outlands
    2. Ricerca esatta e fuzzy sull'indice completo delle ~2.140 pagine della Wiki
    3. Ricerca MediaWiki API (sia per titolo sia full-text nel contenuto di tutte le pagine)
    """
    catalog = ensure_wiki_catalog_sync()

    # 1. Controllo dizionario IT -> UO Outlands
    mapped_term = None
    for it_word, uo_term in IT_TO_UO_TERMS.items():
        if raw_topic == it_word or f" {it_word} " in f" {raw_topic} ":
            mapped_term = uo_term
            break

    candidates = []
    if mapped_term:
        candidates.append(mapped_term)
    if raw_topic and raw_topic not in candidates:
        candidates.append(raw_topic)
    if en_topic and en_topic.lower() not in [c.lower() for c in candidates]:
        candidates.append(en_topic)

    # 2. Match diretto sull'indice completo delle 2.139 pagine
    if _LOWER_TO_TITLE:
        for cand in candidates:
            c_low = cand.lower().strip()
            if c_low in _LOWER_TO_TITLE:
                return _LOWER_TO_TITLE[c_low]
            # Prova singolare/plurale
            if c_low.endswith("s") and c_low[:-1] in _LOWER_TO_TITLE:
                return _LOWER_TO_TITLE[c_low[:-1]]
            if f"{c_low}s" in _LOWER_TO_TITLE:
                return _LOWER_TO_TITLE[f"{c_low}s"]

    # 3. Fuzzy match rapido in memoria sull'indice completo (istantaneo, <0.01s)
    if _LOWER_TO_TITLE:
        best_title = None
        best_ratio = 0.0
        for cand in candidates:
            c_low = cand.lower().strip()
            matches = difflib.get_close_matches(c_low, list(_LOWER_TO_TITLE.keys()), n=1, cutoff=0.72)
            if matches:
                ratio = difflib.SequenceMatcher(None, c_low, matches[0]).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_title = _LOWER_TO_TITLE[matches[0]]
        if best_title:
            return best_title

    # 4. Ricerca online tramite MediaWiki API (title + full-text) solo se non trovato in locale
    for query_str in candidates:
        for srwhat in ("text", "title"):
            try:
                params = urllib.parse.urlencode({
                    "action": "query",
                    "list": "search",
                    "srsearch": query_str,
                    "srwhat": srwhat,
                    "srlimit": 8,
                    "format": "json",
                })
                data = _http_get_json(f"{WIKI_API_URL}?{params}", timeout=5.0)
                hits = data.get("query", {}).get("search", [])
                if hits:
                    q_clean = query_str.lower().strip()
                    exact = next((h["title"] for h in hits if h["title"].lower() == q_clean), None)
                    if exact:
                        return exact
                    starts = next((h["title"] for h in hits if h["title"].lower().startswith(q_clean)), None)
                    return starts or hits[0]["title"]
            except Exception as e:
                logger.debug(f"Errore API search '{query_str}': {e}")

    # 5. Ultimo fallback: ricerca full-text sulla domanda intera tradotta in inglese
    en_full = translate_text(full_question, "it", "en")
    for q_fb in (en_full, raw_topic):
        try:
            params = urllib.parse.urlencode({
                "action": "query",
                "list": "search",
                "srsearch": q_fb,
                "srwhat": "text",
                "srlimit": 5,
                "format": "json",
            })
            data = _http_get_json(f"{WIKI_API_URL}?{params}")
            hits = data.get("query", {}).get("search", [])
            if hits:
                return hits[0]["title"]
        except Exception:
            pass

    return None


def _search_outlands_wiki_sync(question: str) -> Optional[dict]:
    """
    Cerca la pagina e la sezione esatta su https://wiki.uooutlands.com, traduce tutto in italiano
    e prepara il link diretto alla sezione di riferimento (#anchor) da inviare anche in DM.
    """
    raw_topic, section_keywords = _extract_topic_and_keywords(question)
    en_topic = translate_text(raw_topic, "it", "en")
    en_question = translate_text(question, "it", "en").lower()

    page_title = _find_best_page_in_catalog(raw_topic, en_topic, question)
    if not page_title:
        return None

    # Scarica la pagina completa renderizzata (prop=text|sections) così anche tutte le tabelle e {{CreaturePage}} sono incluse!
    try:
        params = urllib.parse.urlencode({
            "action": "parse",
            "page": page_title,
            "prop": "text|sections",
            "redirects": "1",
            "format": "json",
        })
        parse_data = _http_get_json(f"{WIKI_API_URL}?{params}", timeout=12.0)
        parsed = parse_data.get("parse", {})
        actual_title = parsed.get("title", page_title)
        html_content = parsed.get("text", {}).get("*", "")
    except Exception as e:
        logger.error(f"Errore download pagina wiki '{page_title}': {e}")
        return None

    if not html_content:
        return None

    sections, thumbnail_url = _parse_wiki_html_into_sections(html_content, actual_title)
    if not sections:
        return None

    base_page_url = f"{WIKI_BASE_URL}{urllib.parse.quote(actual_title.replace(' ', '_'))}"

    # Individua la SEZIONE DI RIFERIMENTO più pertinente alla domanda dell'utente
    best_section = sections[0]
    best_sec_score = -1.0

    # Parole inglesi significative della domanda
    q_words = [
        w for w in re.findall(r"[a-zA-Z]{3,}", f"{en_question} {' '.join(section_keywords)}")
        if w not in ("what", "how", "where", "the", "and", "for", "with", "outlands", "wiki", "does", "work", "about", "tell")
    ]

    for idx, sec in enumerate(sections):
        sec_title_low = sec["title"].lower()
        sec_body_low = sec["content"].lower()
        score = 0.0

        # Bonus per corrispondenza con l'intento della domanda (es. Training, Tameable Stats, Unlocking, Abilities)
        for kw in section_keywords:
            if kw in sec_title_low:
                score += 5.0
            elif kw in sec_body_low:
                score += 1.5

        for qw in q_words:
            if qw in sec_title_low and qw not in actual_title.lower():
                score += 3.5
            elif qw in sec_body_low and qw not in actual_title.lower():
                score += 0.6

        # Se non ci sono parole chiave specifiche, privilegia la prima sezione descrittiva
        if idx == 0:
            score += 2.0
        elif idx == 1 and len(sections[0]["content"]) < 80:
            score += 2.2

        if score > best_sec_score and len(sec["content"]) > 15:
            best_sec_score = score
            best_section = sec

    # Costruisce il link diretto alla sezione di riferimento (#anchor)
    ref_anchor = best_section.get("anchor", "")
    ref_section_title = best_section.get("title", "Panoramica Generale")
    if ref_anchor:
        ref_section_url = f"{base_page_url}#{urllib.parse.quote(ref_anchor)}"
    else:
        ref_section_url = base_page_url

    # Seleziona le sezioni da tradurre (la sezione di riferimento + la panoramica iniziale se diversa)
    chosen_sections = [best_section]
    if sections[0] is not best_section and len(sections[0]["content"]) > 20:
        chosen_sections.append(sections[0])
    elif len(sections) > 1 and sections[1] is not best_section:
        chosen_sections.append(sections[1])

    en_blocks = []
    total_chars = 0
    for sec in chosen_sections:
        snippet = sec["content"][:950]
        en_blocks.append(f"[{sec['title']}]\n{snippet}")
        total_chars += len(snippet)
        if total_chars >= 1700:
            break

    translated_full = translate_text("\n\n".join(en_blocks), "en", "it")
    translated_sec_title = translate_text(ref_section_title, "en", "it") if ref_section_title != "Panoramica Generale" else ref_section_title

    # Costruisce la risposta parlata in italiano per la voce di Franchino (dalla sezione di riferimento!)
    spoken_en = best_section["content"].replace("• ", "").replace("\n", ". ")
    spoken_en = re.sub(r"\.\s*\.+", ".", spoken_en)
    spoken_en = re.sub(r"\s+", " ", spoken_en).strip()
    if len(spoken_en) > 420:
        cut = spoken_en[:420].rfind(".")
        if cut > 140:
            spoken_en = spoken_en[: cut + 1]
        else:
            spoken_en = spoken_en[:420] + "..."

    spoken_it = translate_text(spoken_en, "en", "it")
    voice_answer = (
        f"Ecco cosa dice la Wiki di Outlands su {actual_title}, nella sezione {translated_sec_title}: "
        f"{spoken_it} Ti ho mandato il link diretto alla sezione in messaggio privato!"
    )

    # Elenco di tutte le sezioni della pagina con link cliccabili per approfondire in DM
    other_section_links: List[str] = []
    for sec in sections[:8]:
        anc = sec.get("anchor", "")
        stitle = sec.get("title", "Introduzione")
        s_url = f"{base_page_url}#{urllib.parse.quote(anc)}" if anc else base_page_url
        marker = "👉 " if sec is best_section else "• "
        other_section_links.append(f"{marker}[**{stitle}**]({s_url})")

    return {
        "page_title": actual_title,
        "page_url": base_page_url,
        "section_title": ref_section_title,
        "section_title_it": translated_sec_title,
        "section_url": ref_section_url,
        "voice_answer": voice_answer,
        "translated_body": translated_full,
        "section_links_md": "\n".join(other_section_links),
        "thumbnail_url": thumbnail_url,
    }


def _resolve_pets_query(question: str) -> Optional[dict]:
    """Riconosce e risponde istantaneamente alle domande sui migliori pet per farmare su UO Outlands."""
    q_norm = normalize_uo_speech(question)
    q_low = q_norm.lower().strip(" ?!.,;:")

    # Se l'utente sta chiedendo specificamente una build o template (es. "build tamer", "build per tamer"),
    # lasciamo che sia _resolve_build_query a rispondere con la scheda del Tamer Mage!
    is_pure_build = any(k in q_low for k in ("build", "template", "classe", "come buildare", "skill template"))
    has_explicit_pet_pick = any(k in q_low for k in (
        "miglior pet", "migliori pet", "quali pet", "che pet", "top pet", "lista pet",
        "guida ai pet", "guida pet", "consigli pet", "consigliami un pet", "pet per", "pet da",
        "pet pvm", "pet dungeon", "pet boss"
    ))
    if is_pure_build and not has_explicit_pet_pick:
        return None

    # Domanda diretta o parola chiave standalone sui pet
    if q_low in ("pet", "pets", "i pet", "top pet", "guida pet", "guida ai pet", "taming bestiary", "bestiario taming", "lista pet"):
        b = OUTLANDS_BEST_PETS
        embed = discord.Embed(
            title=f"🐾 {b['title']}",
            url=b["wiki_url"],
            description=f"**Ruolo**: {b['role']}\n\n**🏆 I Migliori Pet Consigliati:**\n" + "\n\n".join(b["top_pets"]),
            color=COLOR_FRANCHINO,
        )
        embed.add_field(name="💡 Consigli Fondamentali per il Farm", value=b["key_tips"], inline=False)
        embed.add_field(
            name="🔗 Approfondisci sul Taming Bestiary",
            value=f"[Apri la guida `{b['wiki_page']}` su wiki.uooutlands.com]({b['wiki_url']})",
            inline=False,
        )
        embed.set_footer(text="Franchino UO Outlands Pet Advisor • wiki.uooutlands.com")
        return {
            "page_title": b["title"],
            "section_title": "Top Farming Pets & Bestiary Guide",
            "section_url": b["wiki_url"],
            "voice_answer": b["voice_summary"],
            "embed": embed,
        }

    has_pet_word = any(re.search(r'\b' + re.escape(k) + r'\b', q_low) for k in (
        "pet", "pets", "bestie", "animali", "creature", "famigli", "cuccioli", "tamer", "taming",
        "bestiario", "bestiary", "addomesticare", "domare"
    ))
    has_farm_or_best = any(k in q_low for k in (
        "miglior", "migliori", "farmare", "farm", "forte", "forti", "top", "consigli",
        "consiglia", "consigliami", "usare", "scegliere", "quali", "quale", "che", "tier",
        "livello", "pvm", "dungeon", "tank", "dps", "guida", "spiegami", "parlami", "parla",
        "come funzionano", "lista", "elenco", "raccontami", "dimmi", "iniziare", "info", "cosa sono"
    ))
    if (has_pet_word and has_farm_or_best) or has_explicit_pet_pick:
        b = OUTLANDS_BEST_PETS
        embed = discord.Embed(
            title=f"🐾 {b['title']}",
            url=b["wiki_url"],
            description=f"**Ruolo**: {b['role']}\n\n**🏆 I Migliori Pet Consigliati:**\n" + "\n\n".join(b["top_pets"]),
            color=COLOR_FRANCHINO,
        )
        embed.add_field(name="💡 Consigli Fondamentali per il Farm", value=b["key_tips"], inline=False)
        embed.add_field(
            name="🔗 Approfondisci sul Taming Bestiary",
            value=f"[Apri la guida `{b['wiki_page']}` su wiki.uooutlands.com]({b['wiki_url']})",
            inline=False,
        )
        embed.set_footer(text="Franchino UO Outlands Pet Advisor • wiki.uooutlands.com")
        return {
            "page_title": b["title"],
            "section_title": "Top Farming Pets & Bestiary Guide",
            "section_url": b["wiki_url"],
            "voice_answer": b["voice_summary"],
            "embed": embed,
        }
    return None


def _resolve_build_query(question: str) -> Optional[dict]:
    """Riconosce e risponde istantaneamente alle domande sulle build e template meta di UO Outlands."""
    q_norm = normalize_uo_speech(question)
    q_low = q_norm.lower().strip()
    if not any(k in q_low for k in (
        "build", "template", "classe", "bardo", "tamer", "dexxer", "ladro", "necro",
        "starter", "iniziare", "farmare", "soldi", "gold", "paladino", "spada", "arciere"
    )):
        return None

    matched_key = None
    if any(k in q_low for k in ("bardo", "bard", "arciere", "arco")):
        matched_key = "bardo_arciere"
    elif any(k in q_low for k in ("tamer", "taming", "addomesticare", "pet", "bestie")):
        matched_key = "tamer_mage"
    elif any(k in q_low for k in ("ladro", "thief", "lockpick", "scassinare", "forzieri", "casse")):
        matched_key = "ladro_dungeon"
    elif any(k in q_low for k in ("necro", "negromante", "summoner", "evocatore", "scheletri")):
        matched_key = "summoner_necro"
    elif any(k in q_low for k in ("dexxer", "spadaccino", "spada", "melee", "corpo a corpo")):
        matched_key = "dexxer_spada"
    elif any(k in q_low for k in ("iniziare", "principiante", "beginner", "soldi", "gold", "starter")):
        matched_key = "starter_gold"
    elif any(k in q_low for k in ("miglior", "migliori", "consigli", "consiglia", "consigliami", "quali", "che", "top", "meta", "farmare", "farm", "pvm")):
        matched_key = "meta_overview"

    if not matched_key or matched_key not in OUTLANDS_BUILDS:
        return None

    b = OUTLANDS_BUILDS[matched_key]
    embed = discord.Embed(
        title=f"⚔️ Build UO Outlands: {b['title']}",
        url=b["wiki_url"],
        description=f"**Ruolo**: {b['role']}\n\n**🎯 700 Punti Abilità (Skill Template)**:\n" + "\n".join(b["skills"]),
        color=COLOR_FRANCHINO,
    )
    embed.add_field(name="✨ Statistiche Consigliate", value=f"`{b['stats']}`", inline=True)
    embed.add_field(name="🔮 Aspetti Consigliati (Aspect Mastery)", value=b["recommended_aspects"], inline=False)
    embed.add_field(name="📖 Come Giocarla & Consigli Tattici", value=b["how_to_play"], inline=False)
    embed.add_field(
        name="🔗 Guida Ufficiale sulla Wiki",
        value=f"[Apri la guida `{b['wiki_page']}` su wiki.uooutlands.com]({b['wiki_url']})",
        inline=False,
    )
    embed.set_footer(text="Franchino UO Outlands Build Advisor • wiki.uooutlands.com")

    return {
        "page_title": b["title"],
        "section_title": "Skill Template & Aspect Guide",
        "section_url": b["wiki_url"],
        "voice_answer": b["voice_summary"],
        "embed": embed,
    }


def _resolve_special_item_query(question: str) -> Optional[dict]:
    """Riconosce e risponde istantaneamente a come usare gli oggetti iconici e meccaniche di UO Outlands."""
    q_norm = normalize_uo_speech(question)
    q_low = q_norm.lower().strip()
    matched_key = None

    if any(k in q_low for k in ("mastery chain", "catena della maestria", "catena", "links", "medallions")):
        matched_key = "mastery_chain"
    elif any(k in q_low for k in ("aspect core", "distillation", "aspect tome", "nuclei aspetto", "distillazioni", "aspetti")):
        matched_key = "aspect_core"
    elif any(k in q_low for k in ("rune codex", "codex", "codice rune", "libro rune", "runebook")):
        matched_key = "rune_codex"
    elif any(k in q_low for k in ("resource hoard", "forziere risorse", "cassa risorse", "deposit all")):
        matched_key = "resource_hoard"
    elif any(k in q_low for k in ("dungeon mushroom", "funghi dungeon", "funghi dei dungeon", "mushroom pouch")):
        matched_key = "dungeon_mushrooms"
    elif any(k in q_low for k in ("bestiary", "bestiario", "taming book", "libro taming")):
        matched_key = "taming_bestiary"
    elif any(k in q_low for k in ("echoes", "echo", "echi", "cambio classe", "cristallo classe")):
        matched_key = "echoes"
    elif any(k in q_low for k in ("mastery scroll", "pergamene 120", "skill scroll", "pergamena skill")):
        matched_key = "mastery_scrolls"
    elif (any(k in q_low for k in ("lockpicks", "grimaldelli", "trap wire", "remove trap")) or re.search(r"\blockpicks?\b", q_low)) and "lockpicking" not in q_low:
        matched_key = "lockpicks"

    if not matched_key or matched_key not in OUTLANDS_SPECIAL_ITEMS:
        return None

    it = OUTLANDS_SPECIAL_ITEMS[matched_key]
    embed = discord.Embed(
        title=f"🎒 Guida Oggetto UO Outlands: {it['name']}",
        url=it["wiki_url"],
        description=it["how_to_use"],
        color=COLOR_FRANCHINO,
    )
    embed.add_field(
        name="🔗 Sezione di Riferimento sulla Wiki",
        value=f"[Apri `{it['name']}` su wiki.uooutlands.com]({it['wiki_url']})",
        inline=False,
    )
    embed.set_footer(text="Franchino UO Outlands Item Guide • wiki.uooutlands.com")

    return {
        "page_title": it["name"],
        "section_title": "Come si usa & Meccaniche",
        "section_url": it["wiki_url"],
        "voice_answer": it["voice_summary"],
        "embed": embed,
    }


def _resolve_creature_query(question: str, conn: sqlite3.Connection) -> Optional[dict]:
    """Riconosce e restituisce all'istante la scheda tecnica ufficiale di qualsiasi creatura o mostro di Outlands."""
    q_norm = normalize_uo_speech(question)
    q_low = q_norm.lower().strip()
    c = conn.cursor()

    c.execute("""
        SELECT name FROM creatures
        WHERE instr(?, lower(name)) > 0 AND length(name) >= 4
        ORDER BY length(name) DESC LIMIT 1
    """, (q_low,))
    row = c.fetchone()
    if not row:
        # Controllo fuzzy rapido di fallback su nomi creature (es. plurale in -s o lieve disallineamento)
        tokens = [w.strip(" ,.!?:;-\"'") for w in q_low.split() if len(w.strip(" ,.!?:;-\"'")) >= 4]
        stopwords = {
            "cosa", "come", "dove", "quando", "quanta", "quanto", "quali", "quale",
            "info", "dimmi", "parlami", "spiegami", "statistiche", "scheda", "trova",
            "trovo", "sono", "serve", "guida", "bestie", "animali", "creature", "dungeon"
        }
        cand_tokens = [t for t in tokens if t not in stopwords]
        if cand_tokens:
            c.execute("SELECT name FROM creatures WHERE length(name) >= 4")
            all_names = [r["name"] for r in c.fetchall()]
            name_map = {n.lower(): n for n in all_names}
            lower_names = list(name_map.keys())
            for tok in cand_tokens:
                tok_sing = tok[:-1] if tok.endswith("s") and len(tok) > 4 else None
                if tok_sing and tok_sing in name_map:
                    row = {"name": name_map[tok_sing]}
                    break
                close = difflib.get_close_matches(tok, lower_names, n=1, cutoff=0.82)
                if close:
                    row = {"name": name_map[close[0]]}
                    break

    if not row:
        return None

    name = row["name"]
    if not re.search(r'\b' + re.escape(name) + r's?\b', q_norm, re.IGNORECASE):
        if len(name) < len(q_norm) * 0.35:
            return None

    c.execute("SELECT * FROM creatures WHERE name = ?", (name,))
    cr = dict(c.fetchone())

    is_tame = bool(cr.get("is_tameable"))
    min_tam = cr.get("min_taming")
    slots = cr.get("control_slots")
    hits = cr.get("hits") or 0
    armor = cr.get("armor") or 0
    resist = cr.get("magic_resist") or 0
    min_d = cr.get("min_dmg") or 0
    max_d = cr.get("max_dmg") or 0
    slayer = cr.get("slayer") or "Nessuno"
    gold = cr.get("gold_value") or 0
    diff = cr.get("difficulty") or 0.0
    loc = cr.get("location") or "Outlands"
    abils = cr.get("abilities") or ""
    c_class = cr.get("creature_class") or "Combattimento"
    creature_url = cr.get("url") or f"{WIKI_BASE_URL}{name.replace(' ', '_')}"

    if is_tame:
        embed_title = f"🐾 {name} — Creatura Addomesticabile (Pet)"
        desc = (
            f"**🐾 Statistiche di Addomesticamento (Taming)**\n"
            f"• **Min Taming Richiesto**: `{min_tam or 'N/D'}`\n"
            f"• **Slot di Controllo**: `{slots or 1} slot`\n"
            f"• **Ruolo / Classe**: `{c_class}`\n"
            f"• **Abilità Passive**: `{abils or 'Nessuna'}`\n"
            f"• **Habitat / Luogo**: `{loc}`\n\n"
            f"**⚔️ Statistiche di Combattimento**\n"
            f"• **Punti Vita (Hits)**: `{hits:,}` | **Armatura**: `{armor}`\n"
            f"• **Danno Melee**: `{min_d} - {max_d}` | **Resistenza Magica**: `{resist}`\n"
            f"• **Gruppo Slayer**: `{slayer}` | **Valore Oro**: `~{gold:,} gp`"
        )
        voice_answer = (
            f"L'esemplare di {name} richiede {min_tam or 'N/D'} di Animal Taming e occupa {slots or 1} slot di controllo! "
            f"Ha {hits:,} punti vita" + (f" e l'abilità {abils}" if abils else "") + f". Si trova nei meandri di {loc}! "
            "Ti ho inviato la scheda tecnica completa nei dettagli! Magia!"
        )
    else:
        embed_title = f"👹 {name} — Mostro di UO Outlands"
        desc = (
            f"**👹 Statistiche del Mostro**\n"
            f"• **Punti Vita (Hits)**: `{hits:,}`\n"
            f"• **Gruppo Slayer**: `{slayer}` *(usa armi o talismani di questo tipo per fare +danno)*\n"
            f"• **Danno Melee**: `{min_d} - {max_d}` | **Armatura**: `{armor}`\n"
            f"• **Resistenza Magica**: `{resist}` | **Difficoltà**: `{diff}`\n"
            f"• **Bottino Medio in Oro**: `~{gold:,} gp`\n"
            f"• **Habitat / Dungeon**: `{loc}`"
        )
        voice_answer = (
            f"Il {name} si trova a {loc}! Ha ben {hits:,} punti vita, appartiene allo slayer {slayer} "
            f"e rilascia circa {gold:,} monete d'oro! Fai molta attenzione nei dungeon ragazzi! "
            "Ti ho mandato tutti i dati della creatura in messaggio privato! Magia!"
        )

    embed = discord.Embed(
        title=embed_title,
        url=creature_url,
        description=desc,
        color=COLOR_FRANCHINO,
    )
    embed.add_field(
        name="🔗 Pagina Wiki Ufficiale",
        value=f"[Apri la scheda di `{name}` su wiki.uooutlands.com]({creature_url})",
        inline=False,
    )
    embed.set_footer(text="Franchino UO Outlands Bestiary Advisor • wiki.uooutlands.com")

    return {
        "page_title": name,
        "section_title": "Statistiche & Proprietà Ufficiali",
        "section_url": creature_url,
        "voice_answer": voice_answer,
        "embed": embed,
    }


def _resolve_dungeon_query(question: str, conn: sqlite3.Connection) -> Optional[dict]:
    """Riconosce e restituisce all'istante la guida completa a qualsiasi dungeon di Outlands."""
    q_norm = normalize_uo_speech(question)
    q_low = q_norm.lower()
    matched = None
    for d in sorted(OUTLANDS_DUNGEONS, key=len, reverse=True):
        short = d.replace("The ", "").split()[0]
        if re.search(r'\b' + re.escape(d) + r'\b', q_low, re.IGNORECASE) or re.search(r'\b' + re.escape(short) + r'\b', q_low, re.IGNORECASE):
            matched = d
            break
    if not matched:
        return None

    c = conn.cursor()
    c.execute("""
        SELECT name, slayer, hits, gold_value FROM creatures
        WHERE location LIKE ?
        ORDER BY hits DESC LIMIT 6
    """, (f"%{matched}%",))
    bosses = [dict(r) for r in c.fetchall()]

    c.execute("""
        SELECT DISTINCT slayer FROM creatures
        WHERE location LIKE ? AND slayer != ''
    """, (f"%{matched}%",))
    slayers = [r[0] for r in c.fetchall()]

    c.execute("""
        SELECT name, min_taming, control_slots FROM creatures
        WHERE location LIKE ? AND is_tameable = 1
        ORDER BY min_taming DESC LIMIT 4
    """, (f"%{matched}%",))
    tames = [dict(r) for r in c.fetchall()]

    dungeon_url = f"{WIKI_BASE_URL}Category:{matched.replace(' ', '_')}"
    top_boss_names = ", ".join([b["name"] for b in bosses[:3]]) or "creature oscure"
    slayer_str = ", ".join(slayers[:4]) or "Misti"

    desc = f"**🏰 Esplorazione Dungeon: {matched}**\n\n"
    if bosses:
        desc += "**👑 Boss Principali & Creature Pericolose:**\n"
        for b in bosses:
            desc += f"• **{b['name']}**: `{b['hits']:,} HP` | Slayer: `{b['slayer']}` | Oro: `~{b['gold_value']:,} gp`\n"
        desc += "\n"

    if slayers:
        desc += f"**⚔️ Slayer Consigliati:**\n• " + " | • ".join([f"`{s}`" for s in slayers[:5]]) + "\n\n"

    if tames:
        desc += "**🐾 Pet Addomesticabili in questo Dungeon:**\n"
        for t in tames:
            desc += f"• **{t['name']}** (Min Taming: `{t['min_taming']}`, Slot: `{t['control_slots']}`)\n"
        desc += "\n"

    voice_answer = (
        f"Il dungeon {matched} è uno dei più caldi di Outlands! I boss principali sono {top_boss_names}, "
        f"con mostri di tipo {slayer_str}! Prepara armi con questi slayer e fai scorta di bende ragazzi! "
        "Ti ho mandato la guida completa e i boss in messaggio privato! Magia!"
    )

    embed = discord.Embed(
        title=f"🏰 Dungeon Guide: {matched}",
        url=dungeon_url,
        description=desc,
        color=COLOR_FRANCHINO,
    )
    embed.add_field(
        name="🔗 Pagina Wiki Ufficiale del Dungeon",
        value=f"[Apri la guida di `{matched}` su wiki.uooutlands.com]({dungeon_url})",
        inline=False,
    )
    embed.set_footer(text="Franchino UO Outlands Dungeon Advisor • wiki.uooutlands.com")

    return {
        "page_title": f"Dungeon: {matched}",
        "section_title": "Guida, Boss & Creature",
        "section_url": dungeon_url,
        "voice_answer": voice_answer,
        "embed": embed,
    }


def _resolve_local_knowledge_page(question: str, conn: sqlite3.Connection) -> Optional[dict]:
    """Cerca e risolve istantaneamente qualsiasi argomento sulle ~2.212 pagine ufficiali di Outlands."""
    raw_topic, section_keywords = _extract_topic_and_keywords(question)
    en_topic = translate_text(raw_topic, "it", "en")

    c = conn.cursor()
    mapped_term = None
    for it_word, uo_term in IT_TO_UO_TERMS.items():
        if raw_topic == it_word or f" {it_word} " in f" {raw_topic} ":
            mapped_term = uo_term
            break

    candidates = []
    if mapped_term:
        candidates.append(mapped_term)
    if raw_topic and raw_topic not in candidates:
        candidates.append(raw_topic)
    if en_topic and en_topic.lower() not in [c.lower() for c in candidates]:
        candidates.append(en_topic)

    page_row = None
    for cand in candidates:
        clean_cand = cand.lower().strip()
        # 1. Controllo Redirect
        c.execute("SELECT to_title FROM redirects WHERE clean_from = ?", (clean_cand,))
        red = c.fetchone()
        if red:
            target_title = red[0].replace("Category:", "").strip().lower()
            c.execute("SELECT * FROM pages WHERE clean_title = ? OR title = ?", (target_title, red[0]))
            page_row = c.fetchone()
            if page_row:
                break

        # 2. Corrispondenza esatta su clean_title o title
        c.execute("SELECT * FROM pages WHERE clean_title = ? OR title = ?", (clean_cand, cand))
        page_row = c.fetchone()
        if page_row:
            break

        # 3. Match prefisso
        c.execute("SELECT * FROM pages WHERE clean_title LIKE ? ORDER BY length(clean_title) LIMIT 1", (f"{clean_cand}%",))
        page_row = c.fetchone()
        if page_row:
            break

    # 4. Ricerca Full-Text con FTS5
    if not page_row:
        search_str = f"{en_topic} {raw_topic}"
        words = [
            w for w in re.findall(r'[a-zA-Z0-9]+', search_str)
            if len(w) > 2 and w.lower() not in (
                'come', 'cosa', 'dove', 'perche', 'della', 'delle', 'degli', 'nella', 'nelle',
                'how', 'what', 'where', 'the', 'and', 'for', 'with', 'outlands', 'wiki'
            )
        ]
        if words:
            fts_query = ' '.join(words[:4])
            try:
                c.execute("""
                    SELECT p.* FROM pages_fts fts
                    JOIN pages p ON fts.rowid = p.page_id
                    WHERE pages_fts MATCH ?
                    ORDER BY bm25(pages_fts, 10.0, 5.0, 2.0, 1.0, 0.5)
                    LIMIT 1
                """, (fts_query,))
                page_row = c.fetchone()
            except Exception as e:
                logger.debug(f"FTS search error: {e}")

    if not page_row:
        return None

    page_data = dict(page_row)
    actual_title = page_data["title"]
    base_page_url = page_data["url"]
    thumbnail_url = page_data.get("thumbnail_url")
    sections_json_str = page_data.get("sections_json") or "[]"
    sections = json.loads(sections_json_str) if sections_json_str else []

    if not sections:
        content_txt = page_data.get("summary") or page_data.get("content") or ""
        sections = [{"title": "Panoramica Generale", "anchor": "", "content": content_txt}]

    best_section = sections[0]
    best_sec_score = -1.0
    for idx, sec in enumerate(sections):
        s_title = sec["title"].lower()
        s_body = sec["content"].lower()
        score = 0.0
        for kw in section_keywords:
            if kw.lower() in s_title:
                score += 5.0
            elif kw.lower() in s_body:
                score += 1.5
        for cand in candidates:
            c_low = cand.lower()
            if c_low in s_title:
                score += 4.0
            elif c_low in s_body:
                score += 1.0
        if idx == 0:
            score += 1.2
        if score > best_sec_score and len(sec["content"]) > 15:
            best_sec_score = score
            best_section = sec

    ref_anchor = best_section.get("anchor", "")
    ref_sec_title = best_section.get("title", "Panoramica")
    if ref_anchor:
        ref_sec_url = f"{base_page_url}#{urllib.parse.quote(ref_anchor)}"
    else:
        ref_sec_url = base_page_url

    sec_content = best_section["content"][:2200]
    translated_overview = translate_text(sec_content, "en", "it")
    translated_sec_title = (
        translate_text(ref_sec_title, "en", "it")
        if ref_sec_title != "Panoramica Generale"
        else ref_sec_title
    )

    embed = discord.Embed(
        title=f"📖 Guida Approfondita: {actual_title} — {translated_sec_title}",
        url=ref_sec_url,
        description=translated_overview[:3200],
        color=COLOR_FRANCHINO,
    )

    # 2. Cerca sezione Allenamento / Training se presente (fondamentale per le skill!)
    training_sec = None
    for sec in sections:
        st = sec["title"].lower()
        if any(tw in st for tw in ("training", "allenamento", "leveling", "progression", "how to train")):
            if len(sec["content"]) > 20 and sec != best_section:
                training_sec = sec
                break

    if training_sec:
        t_content = training_sec["content"][:950]
        t_trans = translate_text(t_content, "en", "it")
        embed.add_field(
            name="📚 Guida all'Allenamento (Training & Livellamento)",
            value=t_trans[:1024],
            inline=False,
        )

    # 3. Cerca sezione Meccaniche / Formule / Incantesimi / Reagenti
    mech_sec = None
    for sec in sections:
        st = sec["title"].lower()
        if any(mw in st for mw in ("detailed info", "mechanics", "formulas", "spells", "reagents", "charged", "abilities", "interrupts")):
            if len(sec["content"]) > 20 and sec not in (best_section, training_sec):
                mech_sec = sec
                break

    if mech_sec:
        m_title = translate_text(mech_sec["title"], "en", "it")
        m_content = mech_sec["content"][:950]
        m_trans = translate_text(m_content, "en", "it")
        embed.add_field(
            name=f"⚙️ {m_title}",
            value=m_trans[:1024],
            inline=False,
        )

    # 4. Integrazione con Razor Scripts: verifica se ci sono script ufficiali per questo argomento
    try:
        from razor_scripts import get_scripts_for_skill, resolve_skill_from_text
        sk_info = resolve_skill_from_text(clean_cand if 'clean_cand' in locals() else actual_title)
        if not sk_info:
            sk_info = resolve_skill_from_text(actual_title)
        if sk_info:
            sk_name, sk_slug = sk_info
            scripts = get_scripts_for_skill(sk_slug)
            if scripts:
                top_s = scripts[0]
                top_author = top_s.get("author") or "Community"
                embed.add_field(
                    name=f"📜 Script Razor Ufficiali Disponibili ({len(scripts)} per {sk_name})",
                    value=(
                        f"• Trovati **{len(scripts)} script Razor** pronti all'uso su `outlands.uorazorscripts.com`!\n"
                        f"• Consigliato: **[{top_s['title']}]({top_s['url']})** *(di {top_author})*\n"
                        f"• Chiedimi a voce: *\"Franchino script {sk_name}\"* o digita `/script {sk_slug}` per vedere tutti gli script e scaricare il file `.razor`!"
                    ),
                    inline=False,
                )
    except Exception as e:
        logger.debug(f"Errore aggiunta campo razor_scripts: {e}")

    embed.add_field(
        name="🎯 Link Diretto alla Sezione Ufficiale",
        value=f"🔗 [**Apri `{actual_title} → {ref_sec_title}` su wiki.uooutlands.com**]({ref_sec_url})",
        inline=False,
    )

    other_section_links = []
    for sec in sections[:8]:
        anc = sec.get("anchor", "")
        stitle = sec.get("title", "Introduzione")
        s_url = f"{base_page_url}#{urllib.parse.quote(anc)}" if anc else base_page_url
        marker = "👉 " if sec is best_section else "• "
        other_section_links.append(f"{marker}[**{stitle}**]({s_url})")

    if other_section_links:
        embed.add_field(
            name="📑 Tutte le Sezioni della Guida",
            value="\n".join(other_section_links)[:1000],
            inline=False,
        )

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    embed.set_footer(text="Franchino UO Outlands Encyclopedia • wiki.uooutlands.com")

    spoken_raw = sec_content.replace("• ", "").replace("\n", ". ")
    spoken_raw = re.sub(r"\.\s*\.+", ".", spoken_raw)
    spoken_raw = re.sub(r"\s+", " ", spoken_raw).strip()
    if len(spoken_raw) > 380:
        cut = spoken_raw[:380].rfind(".")
        spoken_raw = spoken_raw[: cut + 1] if cut > 140 else spoken_raw[:380] + "..."
    spoken_it = translate_text(spoken_raw, "en", "it")

    training_note = ""
    if training_sec:
        training_note = " Ho incluso anche la scaletta completa di allenamento e le formule!"

    voice_answer = (
        f"Ragazzi, ecco la guida approfondita su {actual_title}: {spoken_it}{training_note} "
        "Ti ho inviato tutti i dettagli, le formule e gli script pronti in chat! Magia!"
    )

    return {
        "page_title": actual_title,
        "section_title": translated_sec_title,
        "section_url": ref_sec_url,
        "voice_answer": voice_answer,
        "embed": embed,
        "translated_body": translated_overview,
        "thumbnail_url": thumbnail_url,
    }


def get_outlands_rag_context(query: str, max_chars: int = 1200) -> str:
    """
    Recupera informazioni di contesto ufficiali da data/outlands_knowledge.db
    per alimentare il ragionamento AI RAG (Retrieval-Augmented Generation) di Gemma 2 2B.
    """
    conn = get_db_connection()
    if not conn:
        return ""
    try:
        c = conn.cursor()
        q_low = query.lower()

        c.execute("""
            SELECT name, is_tameable, min_taming, control_slots, hits, min_dmg, max_dmg,
                   armor, magic_resist, slayer, gold_value, difficulty, location, abilities
            FROM creatures
            WHERE instr(?, lower(name)) > 0 AND length(name) >= 4
            ORDER BY length(name) DESC LIMIT 1
        """, (q_low,))
        cr = c.fetchone()
        if cr:
            cr_dict = dict(cr)
            lines = [f"CREATURA UFFICIALE OUTLANDS: {cr_dict['name']}"]
            if cr_dict.get("is_tameable"):
                lines.append(f"Addomesticabile: Sì (Min Taming: {cr_dict['min_taming']}, Slot: {cr_dict['control_slots']})")
                if cr_dict.get("abilities"):
                    lines.append(f"Abilità: {cr_dict['abilities']}")
            else:
                lines.append("Mostro/Boss selvatico")
            lines.append(f"Punti Vita: {cr_dict['hits']}, Danno: {cr_dict['min_dmg']}-{cr_dict['max_dmg']}, Armatura: {cr_dict['armor']}")
            lines.append(f"Slayer: {cr_dict['slayer']}, Oro: ~{cr_dict['gold_value']} gp, Luogo: {cr_dict['location']}")
            return "\n".join(lines)

        for d in OUTLANDS_DUNGEONS:
            if d.lower() in q_low or d.split()[0].lower() in q_low:
                c.execute("""
                    SELECT name, slayer, hits, gold_value FROM creatures
                    WHERE location LIKE ? ORDER BY hits DESC LIMIT 4
                """, (f"%{d}%",))
                bosses = [f"{r['name']} ({r['hits']} HP, Slayer {r['slayer']})" for r in c.fetchall()]
                return f"DUNGEON UFFICIALE OUTLANDS: {d}\nBoss principali: {', '.join(bosses)}"

        raw_topic, _ = _extract_topic_and_keywords(query)
        words = [w for w in re.findall(r'[a-zA-Z0-9]+', raw_topic) if len(w) > 2]
        if words:
            fts_q = ' '.join(words[:3])
            c.execute("""
                SELECT p.title, p.summary, p.content FROM pages_fts fts
                JOIN pages p ON fts.rowid = p.page_id
                WHERE pages_fts MATCH ?
                ORDER BY bm25(pages_fts, 10.0, 5.0, 2.0, 1.0, 0.5)
                LIMIT 1
            """, (fts_q,))
            row = c.fetchone()
            if row:
                txt = row["summary"] or row["content"]
                return f"GUIDA UFFICIALE OUTLANDS: {row['title']}\n{txt[:max_chars]}"
    except Exception as e:
        logger.debug(f"Errore RAG context: {e}")
    finally:
        conn.close()
    return ""


async def query_outlands_wiki(question: str) -> Optional[dict]:
    """
    Interroga la conoscenza ufficiale di UO Outlands:
    1. Risponde all'istante alle domande sui Migliori Pet da farmare e Animal Taming.
    2. Risponde all'istante alle domande sulle Build e Template da 700 punti.
    3. Risponde all'istante alle domande su come usare gli oggetti speciali (Mastery Chain, Aspect, Codex, ecc.).
    4. Consulta il Database SQLite locale data/outlands_knowledge.db (1.301 creature, 2.212 pagine, sub-millisecond).
    5. Solo come fallback contatta online wiki.uooutlands.com.
    """
    # 1. Controllo Pet & Animal Taming
    pets_info = _resolve_pets_query(question)
    if pets_info:
        return pets_info

    # 2. Controllo Build / Template
    build_info = _resolve_build_query(question)
    if build_info:
        return build_info

    # 3. Controllo Oggetti Speciali & Meccaniche
    item_info = _resolve_special_item_query(question)
    if item_info:
        return item_info

    # 4. Ricerca Database Locale SQLite ad altissime prestazioni (sub-millisecond!)
    conn = get_db_connection()
    if conn:
        try:
            # 4a. Controllo Creature & Mostri
            cr_info = _resolve_creature_query(question, conn)
            if cr_info:
                conn.close()
                return cr_info

            # 4b. Controllo Dungeon & Luoghi
            dg_info = _resolve_dungeon_query(question, conn)
            if dg_info:
                conn.close()
                return dg_info

            # 4c. Controllo Articoli & Guide delle ~2.212 Pagine
            pg_info = _resolve_local_knowledge_page(question, conn)
            if pg_info:
                conn.close()
                return pg_info
        except Exception as e:
            logger.warning(f"Errore query DB locale Outlands: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # 5. Fallback Online MediaWiki API su tutto il sito wiki.uooutlands.com
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_search_outlands_wiki_sync, question), timeout=10.0)
    except Exception as e:
        logger.warning(f"Errore o timeout ricerca wiki outlands sync: {e}")
        return None
    if not info:
        return None

    title = info["page_title"]
    sec_title = info["section_title"]
    sec_url = info["section_url"]
    page_url = info["page_url"]

    embed = discord.Embed(
        title=f"📖 {title} — Sezione: {sec_title}",
        url=sec_url,
        description=info["translated_body"][:3600],
        color=COLOR_FRANCHINO,
    )
    embed.add_field(
        name="🎯 Link Diretto alla Sezione di Riferimento",
        value=f"🔗 [**Apri `{title} → {sec_title}` su wiki.uooutlands.com**]({sec_url})",
        inline=False,
    )
    if info.get("section_links_md"):
        embed.add_field(
            name="📑 Tutte le Sezioni di questa Pagina",
            value=info["section_links_md"][:1000],
            inline=False,
        )
    if info.get("thumbnail_url"):
        embed.set_thumbnail(url=info["thumbnail_url"])

    embed.set_footer(
        text=f"Franchino UO Outlands Encyclopedia • Pagina: {page_url}"
    )

    info["embed"] = embed
    return info


def is_help_question(command_part: str) -> bool:
    """
    Riconosce le domande di tipo 'aiuto outlands', 'cosa puoi fare su outlands',
    'quali argomenti conosci su outlands', 'come puoi aiutarci su outlands', 'guida outlands', 'help outlands', ecc.
    """
    cmd_low = normalize_uo_speech(command_part).lower().strip(" ?!.,;:")

    # Pattern di richiesta aiuto con 'outlands' o 'uo' presente nella frase
    help_with_outlands = (
        r"\b(?:aiuto|help|guida|cosa\s+puoi\s+fare|cosa\s+sai\s+fare|cosa\s+sai|cosa\s+conosci|"
        r"quali\s+argomenti|che\s+argomenti|quali\s+sono\s+gli\s+argomenti|di\s+cosa\s+sei\s+esperto|"
        r"cosa\s+puoi\s+dirmi|cosa\s+mi\s+puoi\s+dire|cosa\s+puoi\s+rispondere|"
        r"cosa\s+puoi\s+aiutarmi|in\s+cosa\s+puoi\s+aiutarmi|in\s+cosa\s+puoi\s+aiutare|"
        r"in\s+cosa\s+ci\s+puoi\s+aiutare|come\s+puoi\s+aiutarci|come\s+ci\s+puoi\s+aiutare|"
        r"come\s+puoi\s+aiutare|cosa\s+puoi\s+fare\s+per\s+aiutarci|cosa\s+puoi\s+fare\s+per\s+aiutare|"
        r"cosa\s+puoi\s+fare\s+per\s+noi|cosa\s+possiamo\s+chiederti|cosa\s+ti\s+posso\s+chiedere|"
        r"cosa\s+ti\s+possiamo\s+chiedere|cosa\s+posso\s+chiederti|"
        r"comandi\s+outlands|cosa\s+fai\s+su|come\s+funzioni|spiegami\s+outlands)"
        r"\b.{0,50}\b(?:outlands|uo|ultima\s+online)\b",
    )
    for pat in help_with_outlands:
        if re.search(pat, cmd_low, re.IGNORECASE):
            return True

    # Pattern diretti (outlands/uo prima o dopo la parola chiave)
    direct_patterns = (
        r"\b(?:outlands|uo)\b.{0,50}\b(?:aiuto|help|guida|comando|comandi|argomenti|argomento|info)\b",
        r"^(?:aiuto|help)\s+(?:outlands|uo)\b",
        r"^(?:outlands|uo)\s+(?:aiuto|help|guida)\b",
        r"^cosa\s+(?:puoi\s+fare|sai\s+fare|sai)\s+su\s+(?:outlands|uo)\b",
        r"^quali\s+argomenti\s+(?:conosci|sai|puoi\s+fare|tratti)\s+su\s+(?:outlands|uo)\b",
        r"^dimmi\s+cosa\s+sai\s+(?:di|su)\s+(?:outlands|uo)\b",
        r"^cosa\s+sai\s+(?:di|su)\s+(?:outlands|uo)\b",
        r"^di\s+cosa\s+mi\s+parli\s+su\s+(?:outlands|uo)\b",
        r"^argomenti\s+(?:outlands|uo)\b",
        r"^come\s+(?:puoi\s+aiutarci|ci\s+puoi\s+aiutare|puoi\s+aiutare)\s+su\s+(?:outlands|uo)\b",
        r"^in\s+cosa\s+(?:puoi\s+aiutarci|ci\s+puoi\s+aiutare|puoi\s+aiutare)\s+su\s+(?:outlands|uo)\b",
        r"^cosa\s+puoi\s+fare\s+per\s+(?:aiutarci|aiutare|aiutare\s+gli\s+utenti|noi)\s+su\s+(?:outlands|uo)\b",
        r"^(?:cosa|quali)\s+argomenti\s+(?:puoi\s+fare|ci\s+sono)\s+su\s+(?:outlands|uo)\b",
    )
    for pat in direct_patterns:
        if re.search(pat, cmd_low, re.IGNORECASE):
            return True

    return False


def get_outlands_help_embed(page_count: int = 2212, creature_count: int = 1301) -> Tuple[discord.Embed, str]:
    """
    Genera un Discord Embed ricco con tutte le categorie di domande che Franchino conosce
    su UO Outlands, con esempi concreti e statistiche reali del database.
    Ritorna (embed, voice_answer).
    """
    # Leggi i conteggi reali dal DB
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM pages")
            page_count = c.fetchone()[0] or page_count
            c.execute("SELECT COUNT(*) FROM creatures")
            creature_count = c.fetchone()[0] or creature_count
            conn.close()
    except Exception:
        pass

    embed = discord.Embed(
        title="\u1f5fa\ufe0f Franchino UO Outlands \u2014 Cosa posso fare per te?",
        description=(
            f"Sono Franchino, esperto di **Ultima Online Outlands**! Ho indicizzato "
            f"**{page_count} pagine ufficiali** e **{creature_count} creature** da "
            f"[wiki.uooutlands.com](https://wiki.uooutlands.com).\n"
            "Puoi chiedermi qualsiasi cosa su questi argomenti:"
        ),
        color=COLOR_FRANCHINO,
        url="https://wiki.uooutlands.com",
    )

    embed.add_field(
        name="\u2694\ufe0f Skill & Meccaniche di Gioco",
        value=(
            "\u2022 *\"Cosa fa la skill Magery?\"*\n"
            "\u2022 *\"Come si allena Lockpicking?\"*\n"
            "\u2022 *\"Spiegami la Provocation\"*\n"
            "\u2022 *\"Come funziona Animal Taming?\"*\n"
            "\u2022 *\"A cosa serve la skill Fishing?\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f432 Creature & Mostri (1.301 indicizzati)",
        value=(
            "\u2022 *\"Statistiche del Drake\"*\n"
            "\u2022 *\"Dove trovo il Nightmare?\"*\n"
            "\u2022 *\"Frenzied Ostard \xe8 domabile?\"*\n"
            "\u2022 *\"Che slayer serve per i demoni?\"*\n"
            "\u2022 *\"Quanto oro droppa il Bloodwolf?\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f43e Pet & Animal Taming",
        value=(
            "\u2022 *\"Quali sono i migliori pet per farmare?\"*\n"
            "\u2022 *\"Miglior pet per i dungeon?\"*\n"
            "\u2022 *\"Come funziona il Taming Bestiary?\"*\n"
            "\u2022 *\"Quanta skill serve per domare un Drake?\"*\n"
            "\u2022 *\"Top 5 pet per il tamer?\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f3d7\ufe0f Build & Template (700 Punti Skill)",
        value=(
            "\u2022 *\"Miglior build per iniziare?\"*\n"
            "\u2022 *\"Build Tamer Mage spiegata\"*\n"
            "\u2022 *\"Come buildare il Dexxer Spada?\"*\n"
            "\u2022 *\"Template Bardo Arciere\"*\n"
            "\u2022 *\"Build Necromante Summoner\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f52e Aspetti & Mastery Chain",
        value=(
            "\u2022 *\"Cos'\xe8 l'Aspect Core?\"*\n"
            "\u2022 *\"Come si usa la Mastery Chain?\"*\n"
            "\u2022 *\"Spiegami il Command Aspect\"*\n"
            "\u2022 *\"Come si sblocca il Fire Aspect?\"*\n"
            "\u2022 *\"A cosa servono le Aspect Distillations?\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f3f0 Dungeon & Zone",
        value=(
            "\u2022 *\"Dimmi tutto su Aegis Keep\"*\n"
            "\u2022 *\"Come si entra a Nusero?\"*\n"
            "\u2022 *\"Mostri di Pulma\"*\n"
            "\u2022 *\"Boss dell'Ossuary\"*\n"
            "\u2022 *\"Guida al dungeon Inferno\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f5dd\ufe0f Oggetti Speciali & Meccaniche",
        value=(
            "\u2022 *\"Come si usa il Rune Codex?\"*\n"
            "\u2022 *\"Cosa sono i Dungeon Mushrooms?\"*\n"
            "\u2022 *\"Lockpicks \u2014 come funzionano?\"*\n"
            "\u2022 *\"Cosa sono gli Echoes?\"*\n"
            "\u2022 *\"Come usare il Taming Bestiary?\"*"
        ),
        inline=False,
    )
    embed.add_field(
        name="📜 Script Razor Ufficiali (outlands.uorazorscripts.com)",
        value=(
            "• *\"Franchino script Magery\"* — Script macro Magia con recall e cast\n"
            "• *\"Franchino script Lockpicking\"* — Scassinamento casse automatico\n"
            "• *\"Franchino script Animal Taming\"* — Macro addomesticamento e release\n"
            "• *\"Franchino script Pesca / Mining / Lumber\"* — Raccolta risorse AFK/attiva\n"
            "• Comando Slash: `/script [skill]` (scarica il file `.razor` pronto all'uso!)"
        ),
        inline=False,
    )
    embed.add_field(
        name="\U0001f4a1 Come chiedermi info (vocale o testuale)",
        value=(
            "\U0001f399\ufe0f **Voce**: *\"Franchino [domanda]\"* o *\"Franchino script [skill]\"* \u2014 Rispondo a voce!\n"
            "\u2328\ufe0f **Slash**: `/outlands [domanda]`, `/wiki [domanda]` o `/script [skill]`\n"
            "\U0001f4d6 **Fonti**: [wiki.uooutlands.com](https://wiki.uooutlands.com) e [outlands.uorazorscripts.com](https://outlands.uorazorscripts.com)"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Franchino UO Outlands Encyclopedia \u2022 {page_count} pagine \u2022 {creature_count} creature \u2022 wiki.uooutlands.com")

    voice_answer = (
        f"Magia ragazzi! Sono il vostro esperto di Ultima Online Outlands! "
        f"Ho nel mio archivio {page_count} pagine ufficiali e {creature_count} creature dalla wiki. "
        "Potete chiedermi tutto su skill, dungeon, creature, build, aspetti, pet e meccaniche del gioco! "
        "Vi ho mandato la guida completa degli argomenti in chat! Chiudi gli occhi e chiedi qualsiasi cosa... Magia!"
    )

    return embed, voice_answer


def is_wiki_question(command_part: str) -> Tuple[bool, str]:
    """
    Determina se la frase rivolta a Franchino e' una domanda su UO Outlands.
    Normalizza foneticamente il parlato italiano e riconosce qualsiasi domanda naturale
    oppure qualsiasi riferimento alle ~2.212 pagine della Wiki, creature o dungeon!

    Strategia (in ordine di priorita'):
    1. Normalizzazione fonetica STT italiano -> termini UO
    2. Esclusione esplicita comandi musicali/admin/DM (con eccezione se dopo il verbo c'e' un termine UO)
    3. Rilevamento domande aiuto/guida outlands
    4. DB title-set match: qualsiasi singola parola o coppia di parole che coincide
       con un clean_title del database wiki (skill, dungeon, aspetti, oggetti, ...)
    5. Pattern UO (keyword single-word + frasi composte + dungeon)
    6. Pattern domande naturali in italiano
    7. Match creature nel DB (solo se il nome e' lungo almeno 8 char)
    """
    cmd_norm = normalize_uo_speech(command_part)
    cmd = cmd_norm.strip()
    cmd_low = cmd.lower().strip(" ?!.,;:")

    # --- Esclude i comandi musicali/admin espliciti ---
    non_wiki_starts = (
        "metti ", "riproduci ", "suona ", "play ", "aggiungi ", "accoda ", "salta",
        "skip", "pausa", "riprendi", "stop", "basta", "ferma", "zitto", "alza",
        "abbassa", "volume", "salva", "preferiti", "attiva radio", "disattiva radio",
        "riavvia", "sblocca", "reset", "esci", "disconnetti", "dimmi i comandi",
        "manda messaggio", "manda un messaggio", "invia messaggio", "scrivi a ",
        "sposta ", "porta ", "caccia ", "scollega ", "muta ", "smuta ",
        "togli il ruolo", "dai il ruolo", "metti il ruolo", "assegna il ruolo",
        "crea il ruolo", "cambia soprannome", "rinomina ", "resetta soprannome",
        "metti in timeout", "timeout a ", "pulisci la chat", "cancella ",
        "crea stanza", "crea canale", "chiudi la stanza", "apri la stanza",
        "espelli ", "banna ", "sbanna ", "trombetta", "sirena", "applausi",
        "rullo", "fail", "vittoria", "laser", "censura", "fischio", "risata",
        "buuu", "bass boost", "mega bass", "nightcore", "slowed", "8d", "karaoke",
        "vaporwave", "scoiattolo", "voce profonda", "normale", "ripeti", "loop",
        "shuffle", "mescola", "mischia", "coda", "mostra la coda",
        "chi canta", "canzone precedente", "torna indietro", "aspetta",
    )

    if cmd_low.startswith(non_wiki_starts) or cmd_low in (
        "skip", "stop", "pausa", "riprendi", "ripeti", "loop", "shuffle", "coda", "aspetta"
    ):
        return False, ""

    # Se è una richiesta specifica di script Razor, delega al modulo dedicato razor_scripts
    try:
        from razor_scripts import is_script_question
        is_sc, _ = is_script_question(cmd_low)
        if is_sc:
            return False, ""
    except Exception:
        pass

    # --- 0. Domanda di aiuto/guida outlands ---
    if is_help_question(cmd_low):
        return True, cmd

    # --- 1. Termini chiave UO Outlands con word boundary (alta priorita') ---
    uo_kw = re.compile(
        r"\b(?:pet|pets|build|builds|template|tamer|taming|dexxer|aspect|slayer|slayers|"
        r"drake|ostard|nightmare|bestiario|bestiary|lockpicking|codex|pvm|pvp)\b",
        re.IGNORECASE,
    )
    if uo_kw.search(cmd_low):
        return True, cmd

    uo_phrases = (
        "come buildare", "miglior build", "migliori build", "consigliami una build", "che build mi consigli",
        "come si usa", "come usare", "a cosa serve", "a che serve", "cosa fa", "come funziona",
        "come craftare", "come si crafta", "dove farmare", "come fare soldi", "come fare gold",
        "mastery chain", "animal taming", "taming bestiary", "aspect core", "aspect tome",
        "corpse worm", "frenzied ostard", "bloodwolf", "hellhound", "tier list",
        "guida ai pet", "guida pet", "guida tamer", "guida build", "statistiche del pet", "statistiche dei pet",
        "bardo arciere", "dexxer spada", "tamer mage", "ladro dungeon",
    )
    if any(p in cmd_low for p in uo_phrases):
        return True, cmd

    # --- 2. DB title-set match (qualsiasi clean_title della wiki) ---
    if _WIKI_CLEAN_TITLES_SET:
        _generic_single_words = {
            "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "e", "o",
            "di", "da", "in", "con", "su", "per", "tra", "fra", "del", "della",
            "dei", "degli", "delle", "al", "alla", "ai", "agli", "a", "che",
            "chi", "cosa", "come", "dove", "quando", "se", "ma", "anche", "non",
            "mi", "ti", "si", "ci", "vi", "bird", "bull", "wolf", "ocean",
            "time", "fire", "eyes", "star", "cold", "tree", "gold", "iron",
            "wood", "ship", "song", "love", "night", "dark", "light", "house",
            "city", "moon", "sun", "wind", "water", "bear", "boar", "frog",
            "goat", "hare", "hawk", "lion", "pig", "rat", "stag", "toad",
            "cat", "dog", "cow", "snake", "fish", "deer", "sheep", "horse",
            "blue", "green", "red", "black", "white", "gray", "grey", "free",
            "play", "stop", "back", "more", "live", "game", "book", "band",
            "rock", "head", "face", "hand", "mind", "life", "baby", "girl", "boy",
        }
        # Se l'intera frase corrisponde esattamente a un titolo wiki
        if cmd_low in _WIKI_CLEAN_TITLES_SET and cmd_low not in _generic_single_words:
            return True, cmd

        words = cmd_low.split()
        # Se è una parola singola distintiva di almeno 5 caratteri
        if len(words) == 1 and words[0] in _WIKI_CLEAN_TITLES_SET and words[0] not in _generic_single_words and len(words[0]) >= 5:
            return True, cmd

        # Match su bigrammi esatti nel DB (es. "aegis keep", "darkmire temple", "fire aspect")
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if bigram in _WIKI_CLEAN_TITLES_SET and (words[i] not in _generic_single_words or words[i+1] not in _generic_single_words):
                return True, cmd

    # --- 3. Dungeons ufficiali di Outlands ---
    uo_dungeon_full = (
        "aegis keep", "cavernam", "darkmire temple", "field of souls",
        "kraul hive", "mount petram", "new player dungeon", "nusero", "omni realm",
        "ossuary", "pulma", "shadowspire cathedral", "the mausoleum", "tidal tomb",
        "time dungeon", "undermountain",
    )
    if any(df in cmd_low for df in uo_dungeon_full):
        return True, cmd
    if re.search(r"\b(?:aegis|cavernam|darkmire|petram|nusero|ossuary|pulma|shadowspire|mausoleum)\b", cmd_low):
        return True, cmd
    if re.search(r"\b(?:dungeon\s+(?:inferno|ocean|wilderness|time)|(?:inferno|ocean|wilderness|time)\s+dungeon)\b", cmd_low):
        return True, cmd

    # --- 4. Termini singoli diretti ---
    standalone_terms = {
        "pet", "pets", "taming", "tamer", "build", "template", "drake", "slayer", "dexxer",
        "aspect", "mastery chain", "codex", "bestiary", "bestiario", "dungeon", "lockpicking",
    }
    if cmd_low in standalone_terms or cmd_low in IT_TO_UO_TERMS:
        return True, cmd

    # --- 5. Pattern domande naturali in italiano ---
    question_patterns = (
        r"^(?:wiki|outlands|uo|ultima\s+online)\s+(.+)$",
        r"^(?:cerca\s+sulla\s+wiki|cerca\s+su\s+outlands|chiedi\s+alla\s+wiki)\s+(.+)$",
        r"^(?:che\s+cos'\xe8|che\s+cosa\s+\xe8|cos'\xe8|cosa\s+\xe8|cosa\s+sono|chi\s+\xe8|chi\s+sono)\s+(.+)$",
        r"^(?:come\s+funziona|come\s+funzionano|come\s+si\s+fa|come\s+si\s+usa|come\s+si\s+sblocca|come\s+si\s+attiva|come\s+si\s+ottiene|come\s+si\s+allena|come\s+alleno|come\s+posso\s+allenare|come\s+expare|come\s+livellare|come\s+giocare)\s+(.+)$",
        r"^(?:dove\s+si\s+trova|dove\s+si\s+trovano|dove\s+trovo|dove\s+si\s+allena|dove\s+alleno|dov'\xe8|dove\s+comprare|dove\s+si\s+compra)\s+(.+)$",
        r"^(?:quanta\s+skill|quanto\s+serve|quali\s+sono|qual\s+\xe8|quale\s+\xe8|a\s+cosa\s+serve|a\s+che\s+serve|perch\xe9|statistiche\s+di|stats\s+di|info\s+su)\s+(.+)$",
        r"^(?:spiegami|parlami\s+di|parlami\s+dei|parlami\s+delle|parlami\s+del|parla\s+dei|parla\s+delle|parla\s+del|parla\s+di|raccontami\s+di|dimmi\s+tutto\s+su|dimmi\s+cosa\s+sai\s+su|mi\s+spieghi|mi\s+dici)\s+(.+)$",
        r"^(?:che|quale|quali)\s+(?:pet|build|classe|template|arma|dungeon|aspetto|aspect|slayer|creatura|mostro)\b",
        r"^(?:consigliami|consigli\s+su|consigli\s+per|mi\s+consigli|cosa\s+mi\s+consigli)\s+(.+)$",
        r"^(?:guida\s+a|guida\s+ai|guida\s+al|guida\s+su|guida\s+per|guida)\s+(.+)$",
    )
    for pat in question_patterns:
        if re.match(pat, cmd, flags=re.IGNORECASE):
            return True, cmd

    if "outlands" in cmd_low or "wiki" in cmd_low:
        return True, cmd

    # --- 6. Match diretto sul catalogo completo ---
    if _LOWER_TO_TITLE and cmd_low in _LOWER_TO_TITLE:
        return True, cmd

    # --- 7. Controllo SQLite per creature ---
    try:
        conn = get_db_connection()
        if conn:
            c = conn.cursor()
            c.execute("SELECT name FROM creatures WHERE lower(name) = ? OR lower(name) || 's' = ? LIMIT 1", (cmd_low, cmd_low))
            cr = c.fetchone()
            if not cr and len(cmd_low) >= 8:
                c.execute("SELECT name FROM creatures WHERE length(name) >= 8 AND instr(name, ' ') > 0")
                for r in c.fetchall():
                    c_name = r["name"].lower()
                    if re.search(r"\b" + re.escape(c_name) + r"s?\b", cmd_low):
                        cr = r
                        break
            conn.close()
            if cr:
                return True, cmd
    except Exception:
        pass

    return False, ""


# Inizializza automaticamente la cache del catalogo all'importazione
try:
    ensure_wiki_catalog_sync()
except Exception:
    pass
