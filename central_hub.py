"""
central_hub.py - Hub Centrale dei Dati, Memoria e Conoscenza di Franchino
========================================================================
Archivia permanentemente OGNI richiesta, comando, domanda e interazione degli utenti
per arricchire continuamente il bot (memoria a lungo termine, RAG, analytics).

Progettato per essere l'HUB CENTRALE del server:
- Supporta PostgreSQL (per connessione multi-container e da qualsiasi altro progetto futuro sul server).
- Fallback automatico trasparente su SQLite WAL locale (data/central_hub.db) se Postgres non è ancora attivo.
- Qualsiasi altro servizio sul server può collegarsi allo stesso database PostgreSQL (porta 5432).
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Franchino.CentralHub")

# Configurazione Database da variabili d'ambiente
DB_HOST = os.getenv("DB_HOST", "central-hub-db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "franchino_admin")
DB_PASS = os.getenv("DB_PASS", "franchino_secure_pass")
DB_NAME = os.getenv("DB_NAME", "franchino_hub")
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
SQLITE_PATH = os.path.join(DATA_DIR, "central_hub.db")

# Flag asyncpg disponibile
try:
    import asyncpg
    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False


class CentralKnowledgeHub:
    """
    Gestore dell'archivio centrale condiviso tra Franchino e tutti i progetti futuri del server.
    """

    def __init__(self):
        self._pg_pool = None
        self._use_postgres = False
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        """Inizializza la connessione al database (PostgreSQL se raggiungibile, altrimenti SQLite)."""
        async with self._init_lock:
            if self._initialized:
                return

            os.makedirs(DATA_DIR, exist_ok=True)

            if HAS_ASYNCPG and DB_HOST:
                try:
                    logger.info(f"🔌 Tentativo di connessione a PostgreSQL su {DB_HOST}:{DB_PORT}/{DB_NAME}...")
                    self._pg_pool = await asyncpg.create_pool(
                        host=DB_HOST,
                        port=DB_PORT,
                        user=DB_USER,
                        password=DB_PASS,
                        database=DB_NAME,
                        min_size=1,
                        max_size=5,
                        timeout=5.0,
                    )
                    self._use_postgres = True
                    await self._create_pg_tables()
                    logger.info("✅ Connesso con successo al Database PostgreSQL Centrale!")
                    self._initialized = True
                    return
                except Exception as e:
                    logger.warning(f"⚠️ Impossibile collegarsi a PostgreSQL ({e}). Uso SQLite locale come storage affidabile.")
                    self._use_postgres = False

            # Inizializza SQLite
            await asyncio.to_thread(self._create_sqlite_tables)
            logger.info(f"✅ Hub centrale inizializzato su SQLite locale: {SQLITE_PATH}")
            self._initialized = True

    async def _create_pg_tables(self):
        """Crea le tabelle dell'Hub su PostgreSQL."""
        query = """
        CREATE TABLE IF NOT EXISTS interactions (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            project VARCHAR(64) DEFAULT 'franchino',
            user_id VARCHAR(64) NOT NULL,
            user_name VARCHAR(128) NOT NULL,
            channel_name VARCHAR(128),
            input_type VARCHAR(32) DEFAULT 'voice',
            raw_query TEXT NOT NULL,
            intent VARCHAR(64),
            ai_response TEXT,
            voice_summary TEXT,
            metadata JSONB,
            feedback VARCHAR(16)
        );

        CREATE TABLE IF NOT EXISTS knowledge_vault (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            project_source VARCHAR(64) DEFAULT 'franchino',
            category VARCHAR(64) NOT NULL,
            topic VARCHAR(256) NOT NULL,
            content TEXT NOT NULL,
            tags VARCHAR(256),
            hit_count INT DEFAULT 0,
            verified BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id VARCHAR(64) PRIMARY KEY,
            user_name VARCHAR(128) NOT NULL,
            favorite_genres TEXT,
            uo_build TEXT,
            interaction_count INT DEFAULT 1,
            last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id);
        CREATE INDEX IF NOT EXISTS idx_interactions_intent ON interactions(intent);
        CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at);
        CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge_vault(topic);
        """
        async with self._pg_pool.acquire() as conn:
            await conn.execute(query)

    def _create_sqlite_tables(self):
        """Crea le tabelle dell'Hub su SQLite locale."""
        conn = sqlite3.connect(SQLITE_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project TEXT DEFAULT 'franchino',
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            channel_name TEXT,
            input_type TEXT DEFAULT 'voice',
            raw_query TEXT NOT NULL,
            intent TEXT,
            ai_response TEXT,
            voice_summary TEXT,
            metadata TEXT,
            feedback TEXT
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_vault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project_source TEXT DEFAULT 'franchino',
            category TEXT NOT NULL,
            topic TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            hit_count INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 1
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            user_name TEXT NOT NULL,
            favorite_genres TEXT,
            uo_build TEXT,
            interaction_count INTEGER DEFAULT 1,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_interactions_intent ON interactions(intent);")
        conn.commit()
        conn.close()

    async def archive_interaction(
        self,
        user_id: str,
        user_name: str,
        raw_query: str,
        intent: Optional[str] = None,
        ai_response: Optional[str] = None,
        voice_summary: Optional[str] = None,
        channel_name: Optional[str] = None,
        input_type: str = "voice",
        project: str = "franchino",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Archivia in modo sicuro e asincrono ogni interazione dell'utente.
        Non blocca mai il thread principale e non solleva eccezioni fatali.
        """
        if not self._initialized:
            await self.initialize()

        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        try:
            if self._use_postgres and self._pg_pool:
                query = """
                INSERT INTO interactions (
                    project, user_id, user_name, channel_name, input_type,
                    raw_query, intent, ai_response, voice_summary, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """
                async with self._pg_pool.acquire() as conn:
                    await conn.execute(
                        query,
                        project,
                        str(user_id),
                        user_name,
                        channel_name or "Voce",
                        input_type,
                        raw_query,
                        intent or "general",
                        ai_response or "",
                        voice_summary or "",
                        meta_json,
                    )
            else:
                def _insert_sqlite():
                    conn = sqlite3.connect(SQLITE_PATH)
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO interactions (
                            project, user_id, user_name, channel_name, input_type,
                            raw_query, intent, ai_response, voice_summary, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            project,
                            str(user_id),
                            user_name,
                            channel_name or "Voce",
                            input_type,
                            raw_query,
                            intent or "general",
                            ai_response or "",
                            voice_summary or "",
                            meta_json,
                        ),
                    )
                    conn.commit()
                    conn.close()

                await asyncio.to_thread(_insert_sqlite)

            logger.info(f"💾 Interazione archiviata nell'Hub [{project}]: '{raw_query}' (Intent: {intent}) da {user_name}")
        except Exception as e:
            logger.error(f"Errore archiviazione interazione: {e}")

    async def get_stats(self) -> Dict[str, Any]:
        """Restituisce le statistiche dell'archivio centrale."""
        if not self._initialized:
            await self.initialize()

        try:
            if self._use_postgres and self._pg_pool:
                async with self._pg_pool.acquire() as conn:
                    total = await conn.fetchval("SELECT COUNT(*) FROM interactions;")
                    intents = await conn.fetch(
                        "SELECT intent, COUNT(*) as cnt FROM interactions GROUP BY intent ORDER BY cnt DESC LIMIT 5;"
                    )
                    users = await conn.fetchval("SELECT COUNT(DISTINCT user_id) FROM interactions;")
                    return {
                        "backend": "PostgreSQL (Multi-Project Shared)",
                        "total_interactions": total,
                        "unique_users": users,
                        "top_intents": [(r["intent"], r["cnt"]) for r in intents],
                    }
            else:
                def _get_sqlite_stats():
                    conn = sqlite3.connect(SQLITE_PATH)
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM interactions;")
                    total = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(DISTINCT user_id) FROM interactions;")
                    users = cur.fetchone()[0]
                    cur.execute("SELECT intent, COUNT(*) as cnt FROM interactions GROUP BY intent ORDER BY cnt DESC LIMIT 5;")
                    intents = cur.fetchall()
                    conn.close()
                    return {
                        "backend": "SQLite WAL Locale",
                        "total_interactions": total,
                        "unique_users": users,
                        "top_intents": intents,
                    }

                return await asyncio.to_thread(_get_sqlite_stats)
        except Exception as e:
            return {"error": str(e), "total_interactions": 0}

    async def get_recent_interactions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Recupera le interazioni più recenti archiviate."""
        if not self._initialized:
            await self.initialize()

        try:
            if self._use_postgres and self._pg_pool:
                async with self._pg_pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT created_at, user_name, raw_query, intent, voice_summary FROM interactions ORDER BY id DESC LIMIT $1;",
                        limit,
                    )
                    return [dict(r) for r in rows]
            else:
                def _get_sqlite():
                    conn = sqlite3.connect(SQLITE_PATH)
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT created_at, user_name, raw_query, intent, voice_summary FROM interactions ORDER BY id DESC LIMIT ?;",
                        (limit,),
                    )
                    res = []
                    for row in cur.fetchall():
                        res.append({
                            "created_at": row[0],
                            "user_name": row[1],
                            "raw_query": row[2],
                            "intent": row[3],
                            "voice_summary": row[4],
                        })
                    conn.close()
                    return res

                return await asyncio.to_thread(_get_sqlite)
        except Exception as e:
            logger.error(f"Errore recupero interazioni recenti: {e}")
            return []


# Istanza Singleton condivisa
hub = CentralKnowledgeHub()
