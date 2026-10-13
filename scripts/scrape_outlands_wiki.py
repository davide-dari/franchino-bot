#!/usr/bin/env python3
"""
scripts/scrape_outlands_wiki.py
===============================
Scraper approfondito per https://wiki.uooutlands.com/.
Estrae:
1. Tutti i moduli dati strutturati Lua (TameableCreatureData, WildCreatureData, FollowerAbilityData, SummonableCreatureData).
2. Tutte le ~2.140 pagine di namespace 0 (Skills, Aspect Mastery, Dungeons, Bosses, Mechanics, Items, Housing, Ships, PvP, ecc.).
3. Popola il database SQLite ad altissime prestazioni: data/outlands_knowledge.db
   con indici FTS5 (Full-Text Search) e tabelle dedicate per le Creature.
"""

import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("OutlandsScraper")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "outlands_knowledge.db")
WIKI_API_URL = "https://wiki.uooutlands.com/api.php"
WIKI_BASE_URL = "https://wiki.uooutlands.com/"
USER_AGENT = "Mozilla/5.0 (compatible; FranchinoBot/2.0; +https://github.com/davide-dari/franchino-bot)"


def http_get_json(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(1.0 + attempt * 1.5)
    return {}


def parse_lua_table(lua_code: str) -> Dict[str, dict]:
    """Estrae dizionari chiave/valore da tabelle Lua annidate."""
    results: Dict[str, dict] = {}
    entries = re.findall(r'\[\"([^\"]+)\"\]\s*=\s*\{(.*?)\}', lua_code, re.DOTALL)
    for name, body in entries:
        props = {}
        for k, v in re.findall(r'\[\"([^\"]+)\"\]\s*=\s*\"?([^\",\n]+)\"?', body):
            val_clean = v.strip().strip('"').strip("'")
            props[k] = val_clean
        results[name] = props
    return results


def clean_wikitext(text: str) -> str:
    """Pulisce la sintassi wikitext trasformandola in testo chiaro e leggibile."""
    if not text:
        return ""
    # Rimuove commenti HTML
    t = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # Rimuove categorie
    t = re.sub(r'\[\[Category:[^\]]+\]\]', '', t, flags=re.IGNORECASE)
    # Rimuove noinclude
    t = re.sub(r'<noinclude>.*?</noinclude>', '', t, flags=re.DOTALL | re.IGNORECASE)
    # Trasforma link wikilink [[Link|Testo]] -> Testo e [[Link]] -> Link
    t = re.sub(r'\[\[(?:[^|\]]+\|)?([^\]]+)\]\]', r'\1', t)
    # Trasforma grassetto e corsivo
    t = re.sub(r"'''([^']+)'''", r'**\1**', t)
    t = re.sub(r"''([^']+)''", r'*\1*', t)
    # Converte tabelle wikitext basic in linee
    t = re.sub(r'\{\| class=[^\n]+\n', '', t)
    t = re.sub(r'\{\|.*?\n', '', t)
    t = re.sub(r'\|\}', '', t)
    t = re.sub(r'\n\|-\s*', '\n', t)
    t = re.sub(r'\n\|\s*', '\n• ', t)
    t = re.sub(r'\n!\s*', '\n📊 ', t)
    # Pulizia spazi multipli
    lines = [l.strip() for l in t.split('\n')]
    non_empty = [l for l in lines if l]
    return '\n'.join(non_empty)


def extract_sections(cleaned_text: str) -> Tuple[str, List[dict]]:
    """Estrae il sommario e le sezioni strutturate dal testo wikitext."""
    lines = cleaned_text.split('\n')
    summary_lines = []
    sections = []
    cur_section_title = None
    cur_section_lines = []

    for line in lines:
        h_match = re.match(r'^(={2,4})\s*(.*?)\s*\1$', line)
        if h_match:
            if cur_section_title:
                s_text = '\n'.join(cur_section_lines).strip()
                if s_text:
                    sections.append({
                        "title": cur_section_title,
                        "anchor": cur_section_title.replace(' ', '_'),
                        "content": s_text,
                    })
            elif summary_lines:
                pass
            cur_section_title = h_match.group(2).strip()
            cur_section_lines = []
        else:
            if cur_section_title is not None:
                cur_section_lines.append(line)
            else:
                summary_lines.append(line)

    if cur_section_title and cur_section_lines:
        s_text = '\n'.join(cur_section_lines).strip()
        if s_text:
            sections.append({
                "title": cur_section_title,
                "anchor": cur_section_title.replace(' ', '_'),
                "content": s_text,
            })

    summary = '\n'.join(summary_lines).strip()
    return summary, sections


def setup_database(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS pages (
        page_id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE,
        clean_title TEXT,
        categories TEXT,
        summary TEXT,
        content TEXT,
        sections_json TEXT,
        url TEXT,
        thumbnail_url TEXT,
        updated_at REAL
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS creatures (
        name TEXT PRIMARY KEY,
        is_tameable INTEGER DEFAULT 0,
        min_taming REAL,
        control_slots INTEGER,
        creature_class TEXT,
        combat_type TEXT,
        hits INTEGER,
        min_dmg INTEGER,
        max_dmg INTEGER,
        armor INTEGER,
        magic_resist INTEGER,
        slayer TEXT,
        gold_value INTEGER,
        difficulty REAL,
        location TEXT,
        abilities TEXT,
        url TEXT
    );
    """)

    c.execute("DROP TABLE IF EXISTS pages_fts;")
    c.execute("""
    CREATE VIRTUAL TABLE pages_fts USING fts5(
        title,
        clean_title,
        categories,
        summary,
        content,
        content='pages',
        content_rowid='page_id'
    );
    """)

    c.execute("DROP TABLE IF EXISTS creatures_fts;")
    c.execute("""
    CREATE VIRTUAL TABLE creatures_fts USING fts5(
        name,
        slayer,
        location,
        abilities,
        creature_class,
        combat_type
    );
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS redirects (
        from_title TEXT PRIMARY KEY,
        clean_from TEXT,
        to_title TEXT
    );
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_pages_clean_title ON pages(clean_title);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_creatures_name ON creatures(name);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_creatures_location ON creatures(location);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_creatures_slayer ON creatures(slayer);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_redirects_clean_from ON redirects(clean_from);")
    conn.commit()


def fetch_lua_modules() -> dict:
    logger.info("📦 Scarico moduli Lua con dati strutturati ufficiali...")
    modules = [
        "Module:TameableCreatureData",
        "Module:WildCreatureData",
        "Module:FollowerAbilityData",
        "Module:SummonableCreatureData",
    ]
    pipe = "|".join([urllib.parse.quote(m) for m in modules])
    url = f"{WIKI_API_URL}?action=query&titles={pipe}&prop=revisions&rvprop=content&rvslots=main&format=json"
    data = http_get_json(url)

    parsed_modules = {}
    for p in data.get("query", {}).get("pages", {}).values():
        title = p.get("title")
        rev = p.get("revisions", [{}])[0]
        code = rev.get("slots", {}).get("main", {}).get("*", "")
        parsed_modules[title] = parse_lua_table(code)
        logger.info(f"✅ Estratto {title}: {len(parsed_modules[title])} record trovati!")

    return parsed_modules


def get_all_page_titles() -> List[str]:
    logger.info("📑 Ottengo elenco completo delle pagine di namespace 0 e namespace 14 (Dungeons & Categorie)...")
    titles = []
    
    # Namespace 0 (Main articles)
    apcontinue = ""
    while True:
        url = f"{WIKI_API_URL}?action=query&list=allpages&apnamespace=0&apfilterredir=nonredirects&aplimit=500&format=json"
        if apcontinue:
            url += f"&apcontinue={urllib.parse.quote(apcontinue)}"
        d = http_get_json(url)
        for p in d.get("query", {}).get("allpages", []):
            titles.append(p["title"])
        cont = d.get("continue", {}).get("apcontinue")
        if not cont:
            break
        apcontinue = cont

    # Namespace 14 (Category pages con guide ai dungeon)
    url_cats = f"{WIKI_API_URL}?action=query&list=allpages&apnamespace=14&aplimit=500&format=json"
    d_cats = http_get_json(url_cats)
    for p in d_cats.get("query", {}).get("allpages", []):
        titles.append(p["title"])

    logger.info(f"📚 Trovate {len(titles)} pagine ufficiali (inclusi tutti i dungeon e categorie)!")
    return titles


def fetch_and_save_redirects(conn: sqlite3.Connection):
    logger.info("🔀 Ottengo tutti i redirect di UO Outlands...")
    c = conn.cursor()
    url = f"{WIKI_API_URL}?action=query&list=allpages&apnamespace=0&apfilterredir=redirects&aplimit=500&format=json"
    d = http_get_json(url)
    red_titles = [p["title"] for p in d.get("query", {}).get("allpages", [])]

    batch_size = 50
    for i in range(0, len(red_titles), batch_size):
        chunk = red_titles[i : i + batch_size]
        pipe_str = "|".join([urllib.parse.quote(t) for t in chunk])
        r_url = f"{WIKI_API_URL}?action=query&titles={pipe_str}&redirects=1&format=json"
        try:
            r_data = http_get_json(r_url)
            for red in r_data.get("query", {}).get("redirects", []):
                from_t = red.get("from", "")
                to_t = red.get("to", "")
                if from_t and to_t:
                    clean_from = from_t.lower().replace("_", " ").strip()
                    c.execute(
                        "INSERT OR REPLACE INTO redirects (from_title, clean_from, to_title) VALUES (?, ?, ?)",
                        (from_t, clean_from, to_t),
                    )
        except Exception as e:
            logger.warning(f"Errore recupero redirect batch: {e}")
    conn.commit()
    logger.info("✅ Redirect indicizzati nel database!")


def scrape_and_save_all():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    setup_database(conn)

    # 1. Scarica i moduli Lua
    lua_data = fetch_lua_modules()
    tameables = lua_data.get("Module:TameableCreatureData", {})
    wilds = lua_data.get("Module:WildCreatureData", {})
    abilities = lua_data.get("Module:FollowerAbilityData", {})

    # 2. Inserisce le creature nella tabella dedicata
    logger.info("🦖 Popolo la tabella 'creatures' con tutti i mostri e pet...")
    all_creature_names = set(list(tameables.keys()) + list(wilds.keys()))
    c = conn.cursor()

    for name in all_creature_names:
        t_info = tameables.get(name, {})
        w_info = wilds.get(name, {})
        is_tameable = 1 if name in tameables else 0

        min_taming = float(t_info.get("taming", 0)) if t_info.get("taming") else None
        slots = int(t_info.get("slots", 0)) if t_info.get("slots") else None
        c_class = t_info.get("class") or ""
        combat = t_info.get("combat") or w_info.get("ai") or ""
        hits = int(w_info.get("hits", 0)) if w_info.get("hits") else (int(t_info.get("hits", 0)) if t_info.get("hits") else None)
        min_dmg = int(w_info.get("minmeleedmg", 0)) if w_info.get("minmeleedmg") else (int(t_info.get("mindmg", 0)) if t_info.get("mindmg") else None)
        max_dmg = int(w_info.get("maxmeleedmg", 0)) if w_info.get("maxmeleedmg") else (int(t_info.get("maxdmg", 0)) if t_info.get("maxdmg") else None)
        armor = int(w_info.get("armor", 0)) if w_info.get("armor") else (int(t_info.get("armor", 0)) if t_info.get("armor") else None)
        resist = int(w_info.get("magicresist", 0)) if w_info.get("magicresist") else (int(t_info.get("magicresist", 0)) if t_info.get("magicresist") else None)
        slayer = w_info.get("slayer") or ""
        gold = int(w_info.get("goldvalue", 0)) if w_info.get("goldvalue") else None
        diff = float(w_info.get("difficulty", 0)) if w_info.get("difficulty") else None
        loc = w_info.get("location") or ""
        abils = t_info.get("passiveability") or ""

        url = f"{WIKI_BASE_URL}{name.replace(' ', '_')}"

        c.execute("""
        INSERT OR REPLACE INTO creatures (
            name, is_tameable, min_taming, control_slots, creature_class, combat_type,
            hits, min_dmg, max_dmg, armor, magic_resist, slayer, gold_value, difficulty,
            location, abilities, url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name, is_tameable, min_taming, slots, c_class, combat,
            hits, min_dmg, max_dmg, armor, resist, slayer, gold, diff,
            loc, abils, url
        ))

        c.execute("""
        INSERT INTO creatures_fts (name, slayer, location, abilities, creature_class, combat_type)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (name, slayer, loc, abils, c_class, combat))

    conn.commit()
    logger.info(f"✅ Inserite {len(all_creature_names)} creature nel database!")

    # 3. Scarica e indicizza tutti i redirect
    fetch_and_save_redirects(conn)

    # 4. Scarica tutte le pagine in lotti da 50 (inclusi dungeon e categorie)
    titles = get_all_page_titles()
    batch_size = 50
    total_batches = (len(titles) + batch_size - 1) // batch_size
    now = time.time()

    logger.info(f"🚀 Inizio scraping parallelo di {len(titles)} articoli ({total_batches} lotti)...")

    saved_pages = 0
    for b_idx in range(total_batches):
        batch = titles[b_idx * batch_size : (b_idx + 1) * batch_size]
        pipe_str = "|".join([urllib.parse.quote(t) for t in batch])
        url = (
            f"{WIKI_API_URL}?action=query&titles={pipe_str}&prop=revisions|categories|images"
            f"&rvprop=content&rvslots=main&cllimit=50&imlimit=5&format=json"
        )
        try:
            d = http_get_json(url)
            pages_dict = d.get("query", {}).get("pages", {})
            for pid, p in pages_dict.items():
                title = p.get("title", "")
                if not title:
                    continue
                clean_title = title.lower().replace("_", " ").strip()
                if clean_title.startswith("category:"):
                    clean_title = clean_title.replace("category:", "").strip()

                cats = [c["title"].replace("Category:", "").strip() for c in p.get("categories", [])]
                cats_str = ", ".join(cats)

                rev = p.get("revisions", [{}])[0]
                raw_content = rev.get("slots", {}).get("main", {}).get("*", rev.get("*", ""))
                clean_text = clean_wikitext(raw_content)

                # Se la pagina è una creatura, arricchiamo il testo con i dati strutturati estratti
                if title in all_creature_names:
                    c_props = []
                    t_entry = tameables.get(title)
                    w_entry = wilds.get(title)
                    if t_entry:
                        c_props.append(f"Min Taming: {t_entry.get('taming', 'N/A')}")
                        c_props.append(f"Control Slots: {t_entry.get('slots', 'N/A')}")
                        if t_entry.get('passiveability'):
                            c_props.append(f"Follower Abilities: {t_entry['passiveability']}")
                    if w_entry:
                        if w_entry.get('location'):
                            c_props.append(f"Location: {w_entry['location']}")
                        if w_entry.get('slayer'):
                            c_props.append(f"Slayer Type: {w_entry['slayer']}")
                        if w_entry.get('hits'):
                            c_props.append(f"Hits: {w_entry['hits']}")
                        if w_entry.get('goldvalue'):
                            c_props.append(f"Gold Value: {w_entry['goldvalue']}")
                        if w_entry.get('difficulty'):
                            c_props.append(f"Difficulty: {w_entry['difficulty']}")
                    if c_props:
                        clean_text = f"**{title} Statistics & Properties:**\n• " + "\n• ".join(c_props) + "\n\n" + clean_text

                summary, sections = extract_sections(clean_text)
                sections_json = json.dumps(sections, ensure_ascii=False)
                page_url = f"{WIKI_BASE_URL}{title.replace(' ', '_')}"

                # Immagine di anteprima
                thumb = None
                imgs = p.get("images", [])
                for img in imgs:
                    iname = img.get("title", "")
                    if any(iname.lower().endswith(ext) for ext in (".jpg", ".png", ".gif")):
                        thumb = f"{WIKI_BASE_URL}Special:FilePath/{urllib.parse.quote(iname.replace('File:', ''))}"
                        break

                c.execute("""
                INSERT OR REPLACE INTO pages (
                    title, clean_title, categories, summary, content,
                    sections_json, url, thumbnail_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    title, clean_title, cats_str, summary, clean_text,
                    sections_json, page_url, thumb, now
                ))

                page_rowid = c.lastrowid
                c.execute("""
                INSERT INTO pages_fts (rowid, title, clean_title, categories, summary, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (page_rowid, title, clean_title, cats_str, summary, clean_text))

                saved_pages += 1
            conn.commit()
            logger.info(f"  [{b_idx+1}/{total_batches}] Salvate {len(pages_dict)} pagine (totale: {saved_pages})...")
        except Exception as e:
            logger.error(f"Errore nel lotto {b_idx+1}: {e}")

    conn.commit()
    conn.close()

    db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    logger.info(f"🎉 Scraping completato con successo! {saved_pages} pagine salvate in '{DB_PATH}' ({db_size_mb:.2f} MB)")


if __name__ == "__main__":
    scrape_and_save_all()
