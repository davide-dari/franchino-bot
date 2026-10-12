import asyncio
import difflib
import html as html_lib
import json
import logging
import os
import re
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

# Catalogo in memoria di tutte le ~2.140 pagine di wiki.uooutlands.com
_ALL_WIKI_TITLES: List[str] = []
_LOWER_TO_TITLE: Dict[str, str] = {}
_INDEX_LOADED: bool = False

# Dizionario rapido dei termini italiani più comuni su Ultima Online Outlands
IT_TO_UO_TERMS: Dict[str, str] = {
    "addomesticare": "Animal Taming",
    "addomesticamento": "Animal Taming",
    "domare": "Animal Taming",
    "taming": "Animal Taming",
    "bestiario": "Taming Bestiary",
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


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
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
    global _ALL_WIKI_TITLES, _LOWER_TO_TITLE, _INDEX_LOADED

    if _INDEX_LOADED and _ALL_WIKI_TITLES:
        return _ALL_WIKI_TITLES

    # 1. Prova a caricare la cache su disco se aggiornata nelle ultime 24 ore
    try:
        if os.path.isfile(INDEX_CACHE_FILE):
            mtime = os.path.getmtime(INDEX_CACHE_FILE)
            with open(INDEX_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, list) and len(cached) > 500:
                _ALL_WIKI_TITLES = cached
                _LOWER_TO_TITLE = {t.lower(): t for t in _ALL_WIKI_TITLES}
                _INDEX_LOADED = True
                if time.time() - mtime < 86400:
                    return _ALL_WIKI_TITLES
    except Exception as e:
        logger.debug(f"Errore lettura cache indice wiki: {e}")

    # 2. Scarica tutte le pagine da wiki.uooutlands.com via MediaWiki API (allpages)
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
        with urllib.request.urlopen(req, timeout=8.0) as resp:
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
        with urllib.request.urlopen(req, timeout=8.0) as resp:
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
        with urllib.request.urlopen(req, timeout=8.0) as resp:
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
    q_low = question.lower().strip(" ?!.,;:")

    # Parole chiave di sezione (per trovare l'ancora #Sezione più pertinente alla domanda!)
    section_keywords: List[str] = []
    keyword_map = {
        "training": ("allena", "allenare", "allenamento", "alzare", "livellare", "salire", "training", "gain", "macrare"),
        "tameable": ("addomesticato", "tamed", "pet", "follower", "slot", "min taming", "quanta skill", "domare"),
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
        r"^(?:mi\s+dici|puoi\s+dirmi|dimmi\s+tutto\s+su|dimmi\s+di|dimmi|spiegami\s+bene|spiegami|parlami\s+di|raccontami\s+di)\s+",
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
        r"quanta\s+skill\s+serve\s+per|quanto\s+serve\s+per|quali\s+sono\s+le\s+statistiche\s+di|statistiche\s+di|quali\s+sono\s+i|quali\s+sono\s+le|quali\s+sono)\s+",
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
        r"\s+(?:su\s+uo\s+outlands|su\s+outlands|in\s+outlands|sulla\s+wiki|su\s+ultima\s+online)$",
        "",
        topic,
        flags=re.IGNORECASE,
    )
    topic = re.sub(r"^(?:il|lo|la|i|gli|le|un|uno|una|l')\s*", "", topic.strip()).strip(" ?!.,;:")

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

    # 3. Ricerca tramite MediaWiki API (title + full-text)
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
                data = _http_get_json(f"{WIKI_API_URL}?{params}")
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

    # 4. Fuzzy match fonetico/ortografico sull'intero catalogo di 2.139 pagine (es. pronuncia italiana di nomi inglesi!)
    if catalog:
        best_title = None
        best_ratio = 0.0
        for cand in candidates:
            c_low = cand.lower().strip()
            matches = difflib.get_close_matches(c_low, list(_LOWER_TO_TITLE.keys()), n=1, cutoff=0.68)
            if matches:
                ratio = difflib.SequenceMatcher(None, c_low, matches[0]).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_title = _LOWER_TO_TITLE[matches[0]]
        if best_title:
            return best_title

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


def _resolve_build_query(question: str) -> Optional[dict]:
    """Riconosce e risponde istantaneamente alle domande sulle build e template meta di UO Outlands."""
    q_low = question.lower().strip()
    if not any(k in q_low for k in ("build", "template", "classe", "bardo", "tamer", "dexxer", "ladro", "necro", "starter", "iniziare", "farmare", "soldi", "gold", "paladino", "spada", "arciere")):
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
    elif any(k in q_low for k in ("iniziare", "principiante", "beginner", "soldi", "gold", "farmare", "ricco", "farm")):
        matched_key = "starter_gold"

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
    q_low = question.lower().strip()
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
    elif any(k in q_low for k in ("lockpick", "grimaldelli", "trap wire", "remove trap")):
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


async def query_outlands_wiki(question: str) -> Optional[dict]:
    """
    Interroga la Wiki di UO Outlands:
    1. Risponde all'istante alle domande sulle Build e Template da 700 punti.
    2. Risponde all'istante alle domande su come usare gli oggetti speciali (Mastery Chain, Aspect, Codex, ecc.).
    3. Cerca dinamicamente tra le 2.140 pagine di wiki.uooutlands.com traducendo la sezione di riferimento.
    """
    # 1. Controllo Build / Template
    build_info = _resolve_build_query(question)
    if build_info:
        return build_info

    # 2. Controllo Oggetti Speciali & Meccaniche
    item_info = _resolve_special_item_query(question)
    if item_info:
        return item_info

    # 3. Ricerca Dinamica MediaWiki API su tutto il sito wiki.uooutlands.com
    info = await asyncio.to_thread(_search_outlands_wiki_sync, question)
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


def is_wiki_question(command_part: str) -> Tuple[bool, str]:
    """
    Determina se la frase rivolta a Franchino è una domanda su UO Outlands (https://wiki.uooutlands.com).
    Riconosce qualsiasi domanda ("cos'è...", "come creare una build...", "come si usa questo oggetto...",
    "dove si trova...", "quanta skill...", "statistiche di...", "spiegami...", ecc.) oppure qualsiasi riferimento
    alle ~2.140 pagine della Wiki!
    """
    cmd = command_part.strip()
    cmd_low = cmd.lower().strip(" ?!.,;:")

    # Esclude i comandi musicali, DM o Admin espliciti
    non_wiki_starts = (
        "metti ", "riproduci ", "suona ", "play ", "aggiungi ", "accoda ", "salta", "skip",
        "pausa", "riprendi", "stop", "basta", "ferma", "zitto", "alza", "abbassa", "volume",
        "salva", "preferiti", "attiva radio", "disattiva radio", "riavvia", "sblocca", "reset",
        "esci", "disconnetti", "comandi", "dimmi i comandi", "manda messaggio", "manda un messaggio",
        "invia messaggio", "scrivi a ", "sposta ", "porta ", "caccia ", "scollega ", "muta ", "smuta ",
        "togli il ruolo", "dai il ruolo", "metti il ruolo", "assegna il ruolo", "crea il ruolo",
        "cambia soprannome", "rinomina ", "resetta soprannome", "metti in timeout", "timeout a ",
        "pulisci la chat", "cancella ", "crea stanza", "crea canale", "chiudi la stanza", "apri la stanza",
        "espelli ", "banna ", "sbanna ", "trombetta", "sirena", "applausi", "rullo", "fail",
        "vittoria", "laser", "censura", "fischio", "risata", "buuu", "bass boost", "mega bass",
        "nightcore", "slowed", "8d", "karaoke", "vaporwave", "scoiattolo", "voce profonda", "normale",
    )
    if cmd_low.startswith(non_wiki_starts):
        return False, ""

    # Pattern dedicati a Build, Template, Oggetti e Meccaniche
    if any(k in cmd_low for k in (
        "build", "template", "classe", "come buildare", "miglior build", "consigliami una build",
        "come si usa", "come usare", "a cosa serve", "a che serve", "cosa fa", "come funziona",
        "come craftare", "come si crafta", "dove farmare", "come fare soldi", "come fare gold",
        "tamer", "bardo", "dexxer", "ladro", "necro", "aspect", "mastery chain", "codex", "mushrooms",
    )):
        return True, cmd

    # Pattern di domande naturali in italiano
    question_patterns = (
        r"^(?:wiki|outlands|uo|ultima\s+online)\s+(.+)$",
        r"^(?:cerca\s+sulla\s+wiki|cerca\s+su\s+outlands|chiedi\s+alla\s+wiki)\s+(.+)$",
        r"^(?:che\s+cos'è|che\s+cosa\s+è|cos'è|cosa\s+è|cosa\s+sono|chi\s+è|chi\s+sono)\s+(.+)$",
        r"^(?:come\s+funziona|come\s+funzionano|come\s+si\s+fa|come\s+si\s+usa|come\s+si\s+sblocca|come\s+si\s+attiva|come\s+si\s+ottiene|come\s+si\s+allena|come\s+alleno|come\s+posso\s+allenare)\s+(.+)$",
        r"^(?:dove\s+si\s+trova|dove\s+si\s+trovano|dove\s+trovo|dove\s+si\s+allena|dov'è)\s+(.+)$",
        r"^(?:quanta\s+skill|quanto\s+serve|quali\s+sono|qual\s+è|a\s+cosa\s+serve|a\s+che\s+serve|perché|statistiche\s+di|info\s+su)\s+(.+)$",
        r"^(?:spiegami|parlami\s+di|raccontami\s+di|dimmi\s+tutto\s+su|dimmi\s+cosa\s+sai\s+su|mi\s+spieghi|mi\s+dici)\s+(.+)$",
    )

    for pat in question_patterns:
        if re.match(pat, cmd, flags=re.IGNORECASE):
            return True, cmd

    if "outlands" in cmd_low or "wiki" in cmd_low:
        return True, cmd

    # Se l'utente pronuncia direttamente il nome di una pagina dell'indice di UO Outlands (es. "Franchino Eldritch Drake")
    if _LOWER_TO_TITLE and cmd_low in _LOWER_TO_TITLE:
        return True, cmd

    return False, ""

