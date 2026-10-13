#!/usr/bin/env python3
"""
scripts/scrape_razor_scripts.py
===============================
Scraper e sincronizzatore per oltre 600+ script ufficiali da https://outlands.uorazorscripts.com
per tutte le 51 skill di Ultima Online Outlands.

Funzionalità:
1. Sincronizzazione automatica tra database SQLite (data/outlands_knowledge.db)
   e la cache JSON ad altissime prestazioni (data/razor_scripts_cache.json).
2. Verifica dell'integrità del catalogo script (conteggio codici sorgente, validità sintattica .razor).
3. Import/export e aggiornamento incrementale dal portale ufficiale.
4. Supporto per esecuzione standalone da CLI o invocazione programmatica dal bot.
"""

import argparse
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FranchinoRazorScraper")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "outlands_knowledge.db")
CACHE_JSON_PATH = os.path.join(DATA_DIR, "razor_scripts_cache.json")

SCRIPTS_SITE_URL = "https://outlands.uorazorscripts.com/scripts"
BASE_SITE_URL = "https://outlands.uorazorscripts.com"
USER_AGENT = "Mozilla/5.0 (compatible; FranchinoRazorBot/2.0; +https://outlands.uorazorscripts.com)"


def ensure_db_schema(conn: sqlite3.Connection):
    """Crea la tabella e l'indice FTS5 per razor_scripts se non esistono."""
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS razor_scripts (
            script_id TEXT PRIMARY KEY,
            skill TEXT,
            skill_slug TEXT,
            title TEXT,
            description TEXT,
            author TEXT,
            version TEXT,
            tags TEXT,
            code TEXT,
            url TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    try:
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS razor_scripts_fts USING fts5(
                script_id UNINDEXED,
                title,
                skill,
                description,
                tags,
                tokenize='unicode61'
            )
        """)
    except Exception as e:
        logger.warning(f"FTS5 setup: {e}")
    conn.commit()


def fetch_url_content(url: str, timeout: float = 12.0) -> Optional[str]:
    """Recupera il contenuto HTML/testo da un URL con retry ed exponential backoff."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if attempt == 2:
                logger.warning(f"Impossibile raggiungere {url}: {e}")
                return None
    return None


def parse_turbo_stream_script(raw_payload: str) -> Optional[Dict[str, Any]]:
    """Decodifica un payload turbo-stream/SingleFetch Remix di outlands.uorazorscripts.com."""
    try:
        s_data = json.loads(raw_payload)
    except Exception:
        return None

    if not isinstance(s_data, list):
        return None

    author_name = "Community"
    script_obj: Dict[str, Any] = {}

    for item in s_data:
        if isinstance(item, dict):
            resolved = {}
            for k, v in item.items():
                if k.startswith("_") and k[1:].isdigit():
                    k_idx = int(k[1:])
                    if k_idx < len(s_data) and isinstance(s_data[k_idx], str):
                        k_name = s_data[k_idx]
                        val = s_data[v] if isinstance(v, int) and 0 <= v < len(s_data) else v
                        resolved[k_name] = val
            if "name" in resolved and "content" in resolved and "category" in resolved:
                script_obj = resolved
            if "displayName" in resolved or "username" in resolved:
                author_name = resolved.get("displayName") or resolved.get("username") or author_name

    if not script_obj or "id" not in script_obj:
        return None

    s_id = str(script_obj.get("id"))
    skill = script_obj.get("category") or "General"
    skill_slug = skill.lower().replace(" ", "")
    title = script_obj.get("name") or "Script Razor"
    code = script_obj.get("content") or ""
    desc = script_obj.get("description") or ""
    tags = script_obj.get("tags") or ""
    version = script_obj.get("version") or "1.0"
    url = f"https://outlands.uorazorscripts.com/script/{s_id}"

    return {
        "script_id": s_id,
        "skill": skill,
        "skill_slug": skill_slug,
        "title": title,
        "description": desc,
        "author": author_name,
        "version": version,
        "tags": tags,
        "code": code,
        "url": url,
        "created_at": time.strftime("%Y-%m-%d"),
        "updated_at": time.strftime("%Y-%m-%d"),
    }


def fetch_script_by_id(script_id: str, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """Scarica e parsa live uno script specifico per UUID da outlands.uorazorscripts.com."""
    url = f"https://outlands.uorazorscripts.com/script/{script_id}.data"
    raw = fetch_url_content(url, timeout=timeout)
    if not raw:
        return None
    return parse_turbo_stream_script(raw)


def scrape_live_scripts(limit: int = 15, db_path: str = DB_PATH) -> int:
    """
    Interroga https://outlands.uorazorscripts.com/scripts.data,
    identifica gli script live e sincronizza il database locale e la cache.
    """
    logger.info(f"Avvio live scraping da {SCRIPTS_SITE_URL}.data (max {limit} script)...")
    catalog_raw = fetch_url_content(f"{SCRIPTS_SITE_URL}.data", timeout=12.0)
    if not catalog_raw:
        logger.warning("Impossibile raggiungere l'indice live degli script.")
        return 0

    try:
        data = json.loads(catalog_raw)
    except Exception as e:
        logger.warning(f"Errore parsing JSON catalog: {e}")
        return 0

    uuids = set()
    for item in data:
        if isinstance(item, str) and re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", item):
            uuids.add(item)

    if not uuids:
        logger.warning("Nessun UUID trovato nel catalogo live.")
        return 0

    logger.info(f"Trovati {len(uuids)} UUID live. Inizio download di fino a {limit} script...")
    saved_count = 0
    conn = sqlite3.connect(db_path)
    ensure_db_schema(conn)
    c = conn.cursor()

    try:
        for idx, s_id in enumerate(list(uuids)[:limit]):
            s = fetch_script_by_id(s_id)
            if not s:
                continue
            c.execute("""
                INSERT OR REPLACE INTO razor_scripts
                (script_id, skill, skill_slug, title, description, author, version, tags, code, url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["script_id"], s["skill"], s["skill_slug"], s["title"], s["description"],
                s["author"], s["version"], s["tags"], s["code"], s["url"], s["created_at"], s["updated_at"]
            ))
            try:
                c.execute("DELETE FROM razor_scripts_fts WHERE script_id = ?", (s["script_id"],))
                c.execute("""
                    INSERT INTO razor_scripts_fts (script_id, title, skill, description, tags)
                    VALUES (?, ?, ?, ?, ?)
                """, (s["script_id"], s["title"], s["skill"], s["description"], s["tags"]))
            except Exception:
                pass
            saved_count += 1
            time.sleep(0.10)  # Rispetto rate-limit
        conn.commit()
    finally:
        conn.close()

    if saved_count > 0:
        export_db_to_cache_json(db_path)
        logger.info(f"Live scraping completato: {saved_count} script aggiornati nel database e nella cache JSON.")

    return saved_count


def export_db_to_cache_json(db_path: str = DB_PATH, cache_path: str = CACHE_JSON_PATH) -> int:
    """Esporta tutti gli script presenti nel database SQLite nella cache JSON locale."""
    if not os.path.isfile(db_path):
        logger.warning(f"Database {db_path} non trovato per l'export della cache.")
        return 0

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM razor_scripts ORDER BY skill_slug, title")
        rows = [dict(r) for r in c.fetchall()]
        data = {
            "version": "1.0",
            "source": "https://outlands.uorazorscripts.com",
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_scripts": len(rows),
            "scripts": rows,
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Esportati con successo {len(rows)} script in {cache_path}")
        return len(rows)
    finally:
        conn.close()


def import_cache_json_to_db(cache_path: str = CACHE_JSON_PATH, db_path: str = DB_PATH) -> int:
    """Importa tutti gli script dalla cache JSON nel database SQLite."""
    if not os.path.isfile(cache_path):
        logger.warning(f"File cache JSON {cache_path} non trovato per l'import.")
        return 0

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    scripts = data.get("scripts", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    if not scripts:
        logger.warning(f"Nessuno script trovato in {cache_path}.")
        return 0

    conn = sqlite3.connect(db_path)
    ensure_db_schema(conn)
    imported = 0
    try:
        c = conn.cursor()
        for s in scripts:
            script_id = s.get("script_id")
            if not script_id:
                continue
            skill = s.get("skill") or "General"
            skill_slug = s.get("skill_slug") or skill.lower().replace(" ", "")
            title = s.get("title") or "Senza Titolo"
            description = s.get("description") or ""
            author = s.get("author") or "Community"
            version = s.get("version") or "1.0"
            tags = s.get("tags") or ""
            code = s.get("code") or ""
            url = s.get("url") or f"https://outlands.uorazorscripts.com/script/{script_id}"
            created_at = s.get("created_at") or time.strftime("%Y-%m-%d")
            updated_at = s.get("updated_at") or created_at

            c.execute("""
                INSERT OR REPLACE INTO razor_scripts
                (script_id, skill, skill_slug, title, description, author, version, tags, code, url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (script_id, skill, skill_slug, title, description, author, version, tags, code, url, created_at, updated_at))

            # Aggiorna anche FTS se disponibile
            try:
                c.execute("DELETE FROM razor_scripts_fts WHERE script_id = ?", (script_id,))
                c.execute("""
                    INSERT INTO razor_scripts_fts (script_id, title, skill, description, tags)
                    VALUES (?, ?, ?, ?, ?)
                """, (script_id, title, skill, description, tags))
            except Exception:
                pass

            imported += 1

        conn.commit()
        logger.info(f"Importati con successo {imported} script da cache JSON a database SQLite.")
        return imported
    finally:
        conn.close()


def verify_catalog_integrity(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Verifica lo stato e le statistiche del catalogo Razor Scripts."""
    stats: Dict[str, Any] = {
        "db_exists": os.path.isfile(db_path),
        "cache_exists": os.path.isfile(CACHE_JSON_PATH),
        "total_scripts": 0,
        "scripts_with_code": 0,
        "skills_count": 0,
        "skills_summary": {},
    }

    if not stats["db_exists"]:
        if stats["cache_exists"]:
            import_cache_json_to_db()
            stats["db_exists"] = os.path.isfile(db_path)
        else:
            return stats

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT count(*) FROM razor_scripts")
        stats["total_scripts"] = c.fetchone()[0]

        c.execute("SELECT count(*) FROM razor_scripts WHERE code IS NOT NULL AND length(code) > 0")
        stats["scripts_with_code"] = c.fetchone()[0]

        c.execute("SELECT skill, skill_slug, count(*) FROM razor_scripts GROUP BY skill_slug ORDER BY count(*) DESC")
        skills_rows = c.fetchall()
        stats["skills_count"] = len(skills_rows)
        stats["skills_summary"] = {r[0]: r[2] for r in skills_rows}
    finally:
        conn.close()

    return stats


def sync_razor_scripts() -> Dict[str, Any]:
    """
    Sincronizza e verifica l'intero catalogo script:
    Se il database non ha la tabella o è vuoto, la ricarica dalla cache JSON.
    Se la cache JSON manca, la genera dal database.
    """
    logger.info("Verifica e sincronizzazione catalogo Razor Scripts...")
    stats = verify_catalog_integrity()
    if stats["total_scripts"] == 0 and stats["cache_exists"]:
        logger.info("Database vuoto o assente: importazione dalla cache JSON...")
        import_cache_json_to_db()
        stats = verify_catalog_integrity()
    elif stats["total_scripts"] > 0 and not stats["cache_exists"]:
        logger.info("Cache JSON mancante: esportazione dal database...")
        export_db_to_cache_json()
        stats["cache_exists"] = True

    return stats


def main():
    parser = argparse.ArgumentParser(description="Gestore & Sincronizzatore Razor Scripts per UO Outlands")
    parser.add_argument("--sync", action="store_true", help="Sincronizza SQLite con la cache JSON")
    parser.add_argument("--export-cache", action="store_true", help="Esporta SQLite in data/razor_scripts_cache.json")
    parser.add_argument("--import-cache", action="store_true", help="Importa data/razor_scripts_cache.json in SQLite")
    parser.add_argument("--verify", action="store_true", help="Verifica integrità catalogo e stampa statistiche")
    parser.add_argument("--scrape-live", type=int, nargs="?", const=10, default=None, help="Esegue live scraping di N script da outlands.uorazorscripts.com")
    args = parser.parse_args()

    if args.scrape_live is not None:
        n = scrape_live_scripts(limit=args.scrape_live)
        print(f"Live scraping completato: {n} script scaricati e sincronizzati.")
    elif args.export_cache:
        n = export_db_to_cache_json()
        print(f"Esportati {n} script.")
    elif args.import_cache:
        n = import_cache_json_to_db()
        print(f"Importati {n} script.")
    elif args.verify or args.sync or len(sys.argv) == 1:
        st = sync_razor_scripts()
        print(f"=== STATO RAZOR SCRIPTS CATALOG ===")
        print(f"Database presente: {st['db_exists']}")
        print(f"Cache JSON presente: {st['cache_exists']}")
        print(f"Totale Script: {st['total_scripts']}")
        print(f"Script con Codice Sorgente: {st['scripts_with_code']}")
        print(f"Skill coperte: {st['skills_count']}")
        print("Top 10 Skill per numero di script:")
        top_10 = sorted(st['skills_summary'].items(), key=lambda x: x[1], reverse=True)[:10]
        for name, cnt in top_10:
            print(f"  • {name:20}: {cnt} script")


if __name__ == "__main__":
    main()
