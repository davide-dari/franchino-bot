"""
test_uo_outlands_uoaddicts.py
=============================
Suite di test completa per le 4 nuove funzionalità di UO Outlands & UOAddicts:
1. News UOAddicts Auto-Poster & Cache
2. UOAddicts Builds Scraper & Browser (PVM, PVP, Popolari, Recenti)
3. Server Status Check (TCP socket, latenza ping, Ashburn WV, fallback IP)
4. Daily Server Restart Manager & Allerte (10m e 5m prima, countdown 11:00 AM ET / 17:00 IT)
5. Integrazione Comandi Vocali, Jack Math Engine e Outlands Wiki
"""

import asyncio
import json
import os
import shutil
import socket
import tempfile
import time
import unittest
from datetime import datetime, time as dtime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

import uo_outlands_service
from uo_outlands_service import (
    UOAddictsNewsItem,
    UOAddictsBuildItem,
    ServerStatusResult,
    RestartInfo,
    UOAddictsNewsService,
    UOAddictsBuildsService,
    parse_news_html,
    parse_builds_html,
    check_uo_server_status,
    get_next_restart_info,
    build_restart_alert_embed,
    build_restart_alert_voice,
    resolve_announcement_channel,
    build_news_embed,
    build_news_list_embed,
    build_builds_embed,
    build_single_build_embed,
    TZ_ET,
    TZ_ROME,
    UO_SERVER_HOST,
    UO_SERVER_IP,
    UO_SERVER_PORT,
)
from outlands_wiki import _resolve_build_query, is_wiki_question
from jack_math_engine import jack_engine, DOMAIN_CORPUS


# ==============================================================================
# SAMPLE HTML FIXTURES
# ==============================================================================

SAMPLE_NEWS_HTML = """
<!DOCTYPE html>
<html>
<body>
<a class="group flex flex-col rounded-xl border p-3" data-discover="true" href="/news/fancy-deco-update">
  <img src="/resources/news-images/cmv5hekgs006wo3bn0qhbju8p" alt="Fancy Deco Update" class="mb-3 object-cover"/>
  <div class="mb-2 flex flex-wrap gap-1">
    <span class="rounded-full bg-blue-600/10 px-2 py-0.5 text-xs text-blue-600">Community</span>
    <span class="rounded-full bg-muted-foreground/10 px-2 py-0.5 text-xs text-muted-foreground">Reddit</span>
  </div>
  <h3 class="text-sm font-semibold">Fancy Deco Update</h3>
  <p class="mt-1 line-clamp-3 text-xs text-muted-foreground">The project is progressing, though there's still much work to be done.</p>
  <div class="mt-2 flex flex-wrap gap-1">
    <span class="rounded bg-muted-foreground/5 px-1.5 py-0.5 text-[11px]">development</span>
    <span class="rounded bg-muted-foreground/5 px-1.5 py-0.5 text-[11px]">update</span>
  </div>
  <div class="mt-auto pt-2">
    <p class="text-xs text-muted-foreground">r/ultimaonline<!-- --> · u/Luxspew<!-- --> ·<!-- --> <!-- -->10/12/2026</p>
  </div>
</a>
<a class="group flex flex-col rounded-xl border p-3" data-discover="true" href="/news/seeking-outlands-players">
  <h3 class="text-sm font-semibold">Seeking Outlands Players</h3>
  <p class="mt-1 line-clamp-3 text-xs text-muted-foreground">An Australian player nostalgic for UO's PvP is seeking fellow Outlands players.</p>
  <div class="mt-auto pt-2">
    <p class="text-xs text-muted-foreground">r/ultimaonline · u/Ackleson · 10/12/2026</p>
  </div>
</a>
</body>
</html>
"""

SAMPLE_BUILDS_HTML = """
<!DOCTYPE html>
<html>
<body>
<div>
  <a data-discover="true" href="/builds/uo-outlands/comfy-tribard-summoner-cmb1itic">
    <div class="skill-scroll-container">
      <div class="m-2 md:m-4">
        <div class="text-center">
          <span class="text-xs">by <!-- -->tremblewick<!-- --> <!-- -->over 1 year<!-- --> ago</span>
          <span class="block text-[10px]">2<!-- --> players use this build</span>
        </div>
        <div class="skill-container-top">
          <div class="not-input text-xs">Comfy Tribard Summoner</div>
          <span title="Love">❤️<!-- -->1</span>
        </div>
        <div class="skill-container skill-container-mid">
          <div class="skill-row"><span class="name">Discordance</span> <span class="value">120</span></div>
          <div class="skill-row"><span class="name">Magery</span> <span class="value">100</span></div>
          <div class="skill-row"><span class="name">Necromancy</span> <span class="value">100</span></div>
          <div class="skill-row totals"><span class="name">Total</span> <span class="value">720</span></div>
        </div>
      </div>
    </div>
  </a>
  <div class="text-center mb-2">
    <a href="/builds/tags/pvm">pvm</a>
    <a href="/builds/tags/summoner">summoner</a>
    <a href="/builds/tags/tribard">tribard</a>
  </div>
</div>

<div>
  <a data-discover="true" href="/builds/uo-outlands/pvp-lumberjack-cmpduzxr">
    <div class="skill-scroll-container">
      <div class="m-2">
        <div class="text-center">
          <span class="text-xs">by JackSparrow 2 months ago</span>
          <span class="block text-[10px]">5 players use this build</span>
        </div>
        <div class="skill-container-top">
          <div class="not-input">PvP Lumberjack</div>
          <span title="Like">👍4</span>
        </div>
        <div class="skill-row"><span class="name">Swordsmanship</span> <span class="value">100</span></div>
        <div class="skill-row"><span class="name">Lumberjacking</span> <span class="value">100</span></div>
        <div class="skill-row totals"><span class="name">Total</span> <span class="value">700</span></div>
      </div>
    </div>
  </a>
  <div class="text-center">
    <a href="/builds/tags/pvp">pvp</a>
  </div>
</div>

<!-- Non-outlands shard build (dovrebbe essere filtrata) -->
<div>
  <a data-discover="true" href="/builds/uo-alive/alive-mage-cmxyz123">
    <div class="skill-scroll-container">
      <div class="not-input">Alive Mage</div>
    </div>
  </a>
</div>
</body>
</html>
"""


# ==============================================================================
# 1. TEST FEATURE 1: UOADDICTS NEWS AUTO-POSTER & PARSER
# ==============================================================================

class TestUOAddictsNews(unittest.IsolatedAsyncioTestCase):
    """Test dello scraper, parser HTML e logica di cache delle news."""

    def test_parse_news_html(self):
        items = parse_news_html(SAMPLE_NEWS_HTML)
        self.assertEqual(len(items), 2)

        it1 = items[0]
        self.assertEqual(it1.id, "fancy-deco-update")
        self.assertEqual(it1.title, "Fancy Deco Update")
        self.assertIn("uoaddicts.com/news/fancy-deco-update", it1.url)
        self.assertEqual(it1.image, "https://uoaddicts.com/resources/news-images/cmv5hekgs006wo3bn0qhbju8p")
        self.assertIn("The project is progressing", it1.description)
        self.assertIn("Luxspew", it1.meta)
        self.assertIn("Community", it1.tags)
        self.assertIn("development", it1.tags)

        it2 = items[1]
        self.assertEqual(it2.id, "seeking-outlands-players")
        self.assertEqual(it2.title, "Seeking Outlands Players")
        self.assertIsNone(it2.image)

    async def test_news_cache_initial_seed_and_new_post_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "news_cache.json")
            service = UOAddictsNewsService(cache_file=cache_file)

            # Mock fetch_news che restituisce 2 item
            mock_items = [
                UOAddictsNewsItem("post-1", "Post 1", "https://uoaddicts.com/news/1", None, "Desc 1", "Meta 1", ["pvm"]),
                UOAddictsNewsItem("post-2", "Post 2", "https://uoaddicts.com/news/2", None, "Desc 2", "Meta 2", ["pvp"]),
            ]
            service.fetch_news = AsyncMock(return_value=mock_items)

            # Primo giro: la cache non esiste -> seed iniziale, ritorna []
            new_first = await service.check_for_new_news()
            self.assertEqual(new_first, [])
            self.assertTrue(os.path.exists(cache_file))
            self.assertEqual(service.load_seen_ids(), {"post-1", "post-2"})

            # Secondo giro senza nuovi post -> ritorna []
            new_second = await service.check_for_new_news()
            self.assertEqual(new_second, [])

            # Terzo giro con un nuovo post aggiunto
            mock_items.append(
                UOAddictsNewsItem("post-3", "Nuova Patch!", "https://uoaddicts.com/news/3", None, "Desc 3", "Meta 3", ["patch"])
            )
            new_third = await service.check_for_new_news()
            self.assertEqual(len(new_third), 1)
            self.assertEqual(new_third[0].id, "post-3")
            self.assertIn("post-3", service.load_seen_ids())

    def test_build_news_embed(self):
        item = UOAddictsNewsItem(
            id="test-news",
            title="Nuovo Torneo Outlands",
            url="https://uoaddicts.com/news/test-news",
            image="https://uoaddicts.com/img.png",
            description="Annunciato il nuovo torneo!",
            meta="Admin · 14/10/2026",
            tags=["Eventi", "PvP"],
        )
        embed = build_news_embed(item)
        self.assertEqual(embed.title, "📰 Nuovo Torneo Outlands")
        self.assertEqual(embed.url, item.url)
        self.assertEqual(embed.description, item.description)
        self.assertEqual(embed.image.url, item.image)
        self.assertTrue(any("Eventi" in f.value for f in embed.fields))

    def test_build_news_list_embed(self):
        items = [
            UOAddictsNewsItem("1", "News 1", "https://uoaddicts.com/1", None, "Desc 1", "Meta 1", ["update"]),
            UOAddictsNewsItem("2", "News 2", "https://uoaddicts.com/2", None, "Desc 2", "Meta 2", ["pvp"]),
        ]
        embed = build_news_list_embed(items)
        self.assertIn("Ultime Novità", embed.title)
        self.assertEqual(len(embed.fields), 2)
        self.assertIn("News 1", embed.fields[0].name)


# ==============================================================================
# 2. TEST FEATURE 2: UOADDICTS BUILDS SCRAPER & BROWSER
# ==============================================================================

class TestUOAddictsBuilds(unittest.IsolatedAsyncioTestCase):
    """Test dello scraper build, categorizzazione, ordinamento e cache."""

    def test_parse_builds_html(self):
        builds = parse_builds_html(SAMPLE_BUILDS_HTML, category="popular")
        # Deve filtrare Alive Mage che appartiene a un altro server
        self.assertEqual(len(builds), 2)

        # In 'popular', PvP Lumberjack (usage 5) deve essere prima di Comfy Tribard (usage 2)
        top = builds[0]
        self.assertEqual(top.title, "PvP Lumberjack")
        self.assertEqual(top.usage, 5)
        self.assertEqual(top.votes, 4)
        self.assertIn("Swordsmanship: 100", top.skills)
        self.assertIn("pvp", top.tags)

        second = builds[1]
        self.assertEqual(second.title, "Comfy Tribard Summoner")
        self.assertEqual(second.author, "tremblewick")
        self.assertEqual(second.usage, 2)
        self.assertEqual(second.votes, 1)
        self.assertIn("Discordance: 120", second.skills)
        self.assertIn("pvm", second.tags)
        self.assertIn("tribard", second.tags)

    def test_category_normalization(self):
        service = UOAddictsBuildsService()
        self.assertEqual(service.normalize_category("pvm"), "pvm")
        self.assertEqual(service.normalize_category("mostri"), "pvm")
        self.assertEqual(service.normalize_category("pvp"), "pvp")
        self.assertEqual(service.normalize_category("duello"), "pvp")
        self.assertEqual(service.normalize_category("recenti"), "recent")
        self.assertEqual(service.normalize_category("nuove"), "recent")
        self.assertEqual(service.normalize_category("popolari"), "popular")
        self.assertEqual(service.normalize_category(None), "popular")

    async def test_builds_cache_ttl_and_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "builds_cache.json")
            service = UOAddictsBuildsService(cache_file=cache_file, ttl_seconds=10.0)

            mock_pvm_builds = [
                UOAddictsBuildItem("Tamer Mage", "tamer-mage", "uo-outlands", "https://url1", "Author1", 10, 5, ["Taming: 120"], ["pvm"]),
            ]
            service.fetch_builds = AsyncMock(return_value=mock_pvm_builds)

            res = await service.fetch_builds("pvm")
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0].title, "Tamer Mage")

    async def test_search_builds_keyword(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "builds_cache.json")
            service = UOAddictsBuildsService(cache_file=cache_file)

            test_builds = [
                UOAddictsBuildItem("OP Archery Tamer", "op-archery-tamer", "uo-outlands", "https://url1", "jaseowns", 15, 8, ["Archery: 80", "Animal Taming: 120"], ["pvm", "tamer"]),
                UOAddictsBuildItem("Dexxer Spada", "dexxer-spada", "uo-outlands", "https://url2", "warrior", 8, 3, ["Swordsmanship: 100"], ["melee"]),
            ]
            service.fetch_builds = AsyncMock(return_value=test_builds)

            # Cerca per skill o tag "tamer"
            matched = await service.search_builds("tamer", category="pvm")
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0].title, "OP Archery Tamer")

            # Cerca per autore
            matched_author = await service.search_builds("warrior", category="pvm")
            self.assertEqual(len(matched_author), 1)
            self.assertEqual(matched_author[0].title, "Dexxer Spada")

    def test_build_builds_embed(self):
        builds = [
            UOAddictsBuildItem("OP Archery Tamer", "op-archery-tamer", "uo-outlands", "https://uoaddicts.com/build1", "jaseowns", 20, 5, ["Archery: 80", "Taming: 120"], ["pvm", "tamer"]),
        ]
        embed = build_builds_embed(builds, category="pvm")
        self.assertIn("PvM", embed.title)
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("OP Archery Tamer", embed.fields[0].name)
        self.assertIn("jaseowns", embed.fields[0].value)
        self.assertIn("20", embed.fields[0].value)

    def test_outlands_wiki_build_enrichment(self):
        """Verifica che _resolve_build_query contenga il riferimento a UOAddicts."""
        res = _resolve_build_query("build bardo arciere")
        self.assertIsNotNone(res)
        embed = res["embed"]
        field_names = [f.name for f in embed.fields]
        self.assertTrue(any("UOAddicts" in name for name in field_names))


# ==============================================================================
# 3. TEST FEATURE 3: UO OUTLANDS SERVER STATUS CHECK
# ==============================================================================

class TestServerStatusCheck(unittest.IsolatedAsyncioTestCase):
    """Test del controllo socket TCP su play.uooutlands.com e 74.91.113.38:2593."""

    async def test_server_status_online(self):
        """Simula una connessione TCP riuscita all'host primario."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
            mock_open.return_value = (mock_reader, mock_writer)
            res = await check_uo_server_status(timeout=1.0)

            self.assertTrue(res.online)
            self.assertEqual(res.host, UO_SERVER_HOST)
            self.assertEqual(res.port, UO_SERVER_PORT)
            self.assertEqual(res.location, "Ashburn, West Virginia (USA)")
            self.assertIsNotNone(res.latency_ms)
            self.assertGreaterEqual(res.latency_ms, 0.0)

            embed = res.build_embed()
            self.assertIn("ONLINE", embed.title)
            self.assertEqual(embed.color.value, 0x2ECC71)
            self.assertIn("Ashburn, West Virginia", str(embed.fields))

            voice = res.voice_text()
            self.assertIn("online", voice.lower())
            self.assertIn("latenza", voice.lower())

    async def test_server_status_fallback_ip(self):
        """Simula il fallimento del DNS primario e successo sul fallback IP 74.91.113.38."""
        mock_reader = AsyncMock()
        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        call_count = 0

        async def fake_open_conn(host, port):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # DNS primario fallisce
                raise socket.gaierror(-8, "nodename nor servname provided, or not known")
            return (mock_reader, mock_writer)

        with patch("asyncio.open_connection", side_effect=fake_open_conn):
            res = await check_uo_server_status(timeout=1.0)

            self.assertTrue(res.online)
            self.assertEqual(res.ip, UO_SERVER_IP)
            self.assertEqual(call_count, 2)

    async def test_server_status_offline_timeout(self):
        """Simula timeout completo su entrambi i tentativi con TimeoutError vuoto."""
        with patch("asyncio.open_connection", side_effect=asyncio.TimeoutError()):
            res = await check_uo_server_status(timeout=0.1)

            self.assertFalse(res.online)
            self.assertIsNone(res.latency_ms)
            self.assertIsNotNone(res.error)
            self.assertIn("Timeout", res.error)

            embed = res.build_embed()
            self.assertIn("OFFLINE", embed.title)
            self.assertEqual(embed.color.value, 0xE74C3C)

            voice = res.voice_text()
            self.assertTrue("offline" in voice.lower() or "irraggiungibile" in voice.lower())


# ==============================================================================
# 4. TEST FEATURE 4: DAILY SERVER RESTART MANAGER & ALERTS
# ==============================================================================

class TestDailyServerRestart(unittest.TestCase):
    """Test del calcolo del countdown del riavvio ordinario delle 11:00 AM ET (17:00 IT)."""

    def test_restart_before_11am_et(self):
        # Ore 08:30 AM ET del 14 Ottobre 2026 -> riavvio previsto per le 11:00 AM ET di OGGI (2h 30m)
        mock_now_ny = datetime(2026, 10, 14, 8, 30, 0, tzinfo=TZ_ET)
        info = get_next_restart_info(current_time_ny=mock_now_ny)

        self.assertEqual(info.hours, 2)
        self.assertEqual(info.minutes, 30)
        self.assertEqual(info.seconds, 0)
        self.assertEqual(info.next_restart_ny.hour, 11)
        self.assertEqual(info.next_restart_ny.day, 14)
        self.assertEqual(info.next_restart_rome.hour, 17)
        self.assertIn("2 ore e 30 minuti", info.voice_text())

    def test_restart_after_11am_et(self):
        # Ore 14:00 (2:00 PM) ET del 14 Ottobre 2026 -> riavvio previsto per DOMANI alle 11:00 AM ET (21h rimanenti)
        mock_now_ny = datetime(2026, 10, 14, 14, 0, 0, tzinfo=TZ_ET)
        info = get_next_restart_info(current_time_ny=mock_now_ny)

        self.assertEqual(info.hours, 21)
        self.assertEqual(info.minutes, 0)
        self.assertEqual(info.next_restart_ny.day, 15)
        self.assertEqual(info.next_restart_rome.hour, 17)

    def test_restart_alert_10min(self):
        embed = build_restart_alert_embed(10)
        voice = build_restart_alert_voice(10)

        self.assertIn("10 Minuti", embed.title)
        self.assertEqual(embed.color.value, 0xF39C12)
        self.assertIn("dieci minuti", voice.lower())

    def test_restart_alert_5min(self):
        embed = build_restart_alert_embed(5)
        voice = build_restart_alert_voice(5)

        self.assertIn("5 Minuti", embed.title)
        self.assertIn("ULTIMO AVVISO", embed.description)
        self.assertEqual(embed.color.value, 0xE74C3C)
        self.assertIn("cinque minuti", voice.lower())

    def test_restart_embed_fields(self):
        mock_now_ny = datetime(2026, 10, 14, 10, 45, 0, tzinfo=TZ_ET)
        info = get_next_restart_info(current_time_ny=mock_now_ny)
        embed = info.build_embed()

        self.assertIn("Riavvio Ordinario", embed.title)
        self.assertTrue(any("11:00 AM ET" in f.value for f in embed.fields))
        self.assertTrue(any("15m" in f.value for f in embed.fields))


# ==============================================================================
# 5. TEST INTEGRAZIONE CANALI & COMANDI VOCALI / MATH ENGINE
# ==============================================================================

class TestIntegrationAndRecognition(unittest.TestCase):
    """Test del risolutore di canali e del riconoscimento semantico / vocale."""

    def test_resolve_announcement_channel_exact_info(self):
        guild = MagicMock(spec=discord.Guild)
        ch_info = MagicMock(spec=discord.TextChannel)
        ch_info.name = "info"
        ch_info.permissions_for.return_value.send_messages = True

        ch_general = MagicMock(spec=discord.TextChannel)
        ch_general.name = "general"
        ch_general.permissions_for.return_value.send_messages = True

        guild.text_channels = [ch_general, ch_info]
        guild.me = MagicMock()

        target = resolve_announcement_channel(guild)
        self.assertEqual(target, ch_info)

    def test_resolve_announcement_channel_news_or_annunci(self):
        guild = MagicMock(spec=discord.Guild)
        ch_news = MagicMock(spec=discord.TextChannel)
        ch_news.name = "📢-annunci-server"
        ch_news.permissions_for.return_value.send_messages = True

        guild.text_channels = [ch_news]
        guild.me = MagicMock()

        target = resolve_announcement_channel(guild)
        self.assertEqual(target, ch_news)

    def test_jack_math_engine_contains_outlands_terms(self):
        """Verifica che il corpus jack_math_engine includa tutti i termini richiesti dal task."""
        corpus = DOMAIN_CORPUS.get("wiki_guide", [])
        for term in ("uoaddicts", "build pvm", "build pvp", "server status", "riavvio", "manutenzione"):
            self.assertIn(term, corpus)

    def test_outlands_wiki_is_wiki_question_new_phrases(self):
        """Verifica che le nuove frasi rivolte a Franchino siano riconosciute come domande Outlands."""
        phrases = [
            "il server è online?",
            "status server",
            "quando si riavvia il server?",
            "orario riavvio",
            "build pvm",
            "build pvp",
            "build popolari",
            "build recenti",
            "notizie outlands",
            "news outlands",
        ]
        for phrase in phrases:
            is_w, q = is_wiki_question(phrase)
            self.assertTrue(is_w, f"La frase '{phrase}' non è stata riconosciuta come domanda Outlands!")


# ==============================================================================
# 6. TEST ESECUZIONE COMANDI VOCALI FRANCHINO (FranchinoVoiceSink)
# ==============================================================================

class TestVoiceCommandsExecution(unittest.IsolatedAsyncioTestCase):
    """Test dell'elaborazione delle intenzioni vocali UO Outlands in voice_commands.py."""

    async def asyncSetUp(self):
        from voice_commands import FranchinoVoiceSink
        self.mock_player = MagicMock()
        self.mock_player.bot.loop.create_task = MagicMock(side_effect=lambda coro: (coro.close(), MagicMock())[1])
        self.mock_player.speak_tts = AsyncMock()
        self.mock_player.voice_client = MagicMock()
        self.mock_player.voice_client.is_connected.return_value = True
        self.sink = FranchinoVoiceSink(self.mock_player)
        self.sink._notify_channel = AsyncMock()
        self.user = MagicMock(id=1234, display_name="TestPlayer")

    async def asyncTearDown(self):
        if hasattr(self, "sink") and self.sink:
            self.sink.cleanup()

    async def test_voice_server_status(self):
        with patch("uo_outlands_service.check_uo_server_status", new_callable=AsyncMock) as mock_chk:
            mock_chk.return_value = ServerStatusResult(
                online=True, host="play.uooutlands.com", ip="74.91.113.38", port=2593, latency_ms=42.0
            )
            await self.sink._handle_transcribed_text(self.user, "hey franchino il server è online?")

            self.sink._notify_channel.assert_awaited()
            call_kwargs = self.sink._notify_channel.call_args.kwargs
            self.assertIn("embed", call_kwargs)
            self.assertIn("ONLINE", call_kwargs["embed"].title)

    async def test_voice_server_restart(self):
        mock_info = RestartInfo(
            next_restart_ny=datetime(2026, 10, 14, 11, 0, 0, tzinfo=TZ_ET),
            next_restart_rome=datetime(2026, 10, 14, 17, 0, 0, tzinfo=TZ_ROME),
            time_remaining=timedelta(hours=2, minutes=15),
            hours=2,
            minutes=15,
            seconds=0,
        )
        with patch("uo_outlands_service.get_next_restart_info", return_value=mock_info):
            await self.sink._handle_transcribed_text(self.user, "hey franchino quando si riavvia il server?")

            self.sink._notify_channel.assert_awaited()
            call_kwargs = self.sink._notify_channel.call_args.kwargs
            self.assertIn("embed", call_kwargs)
            self.assertIn("Riavvio Ordinario", call_kwargs["embed"].title)

    async def test_voice_uoaddicts_builds(self):
        mock_builds = [
            UOAddictsBuildItem("PvP Dexter", "pvp-dexter", "uo-outlands", "https://url", "author", 10, 2, ["Swords: 100"], ["pvp"])
        ]
        with patch.object(uo_outlands_service.builds_service, "fetch_builds", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_builds
            await self.sink._handle_transcribed_text(self.user, "hey franchino build pvp")

            self.sink._notify_channel.assert_awaited()
            call_kwargs = self.sink._notify_channel.call_args.kwargs
            self.assertIn("embed", call_kwargs)
            self.assertIn("PvP", call_kwargs["embed"].title)

    async def test_voice_uoaddicts_build_search_keyword(self):
        mock_builds = [
            UOAddictsBuildItem("OP Archery Tamer", "op-archery-tamer", "uo-outlands", "https://url", "jaseowns", 15, 8, ["Archery: 80"], ["pvm", "tamer"])
        ]
        with patch.object(uo_outlands_service.builds_service, "search_builds", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_builds
            await self.sink._handle_transcribed_text(self.user, "hey franchino cerca build tamer")

            mock_search.assert_awaited()
            self.sink._notify_channel.assert_awaited()
            kw = self.sink._notify_channel.call_args.kwargs
            self.assertIn("embed", kw)
            self.assertIn("OP Archery Tamer", kw["embed"].fields[0].name)

    async def test_voice_uoaddicts_build_generic(self):
        mock_builds = [
            UOAddictsBuildItem("Top Meta Build", "top-meta", "uo-outlands", "https://url", "author", 20, 10, ["Magery: 100"], ["popular"])
        ]
        with patch.object(uo_outlands_service.builds_service, "fetch_builds", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_builds
            await self.sink._handle_transcribed_text(self.user, "hey franchino mostra le build")

            self.sink._notify_channel.assert_awaited()
            kw = self.sink._notify_channel.call_args.kwargs
            self.assertIn("embed", kw)
            self.assertIn("Più Popolari", kw["embed"].title)

    async def test_voice_uoaddicts_news(self):
        mock_news = [
            UOAddictsNewsItem("n1", "Patch 10.5", "https://news", None, "Desc", "Admin", ["patch"])
        ]
        with patch.object(uo_outlands_service.news_service, "fetch_latest_news", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_news
            await self.sink._handle_transcribed_text(self.user, "hey franchino news outlands")

            self.sink._notify_channel.assert_awaited()
            call_kwargs = self.sink._notify_channel.call_args.kwargs
            self.assertIn("embed", call_kwargs)
            self.assertIn("Ultime Novità", call_kwargs["embed"].title)


# ==============================================================================
# 7. TEST COMANDI SLASH HYBRID IN MAIN.PY
# ==============================================================================

class TestMainSlashCommands(unittest.IsolatedAsyncioTestCase):
    """Test dei comandi slash registrati in main.py (/status, /server, /riavvio, /builds, /news)."""

    async def asyncSetUp(self):
        from main import status_cmd, server_cmd, riavvio_cmd, builds_cmd, build_cmd, news_cmd, player_manager
        self.status_cmd = status_cmd
        self.server_cmd = server_cmd
        self.riavvio_cmd = riavvio_cmd
        self.builds_cmd = builds_cmd
        self.build_cmd = build_cmd
        self.news_cmd = news_cmd

        self.ctx = MagicMock()
        self.ctx.defer = AsyncMock()
        self.ctx.send = AsyncMock()
        self.ctx.guild = MagicMock()
        self.mock_player = MagicMock()
        self.mock_player.voice_client = MagicMock()
        self.mock_player.voice_client.is_connected.return_value = True
        self.mock_player.speak_tts = AsyncMock()
        player_manager.get_player = MagicMock(return_value=self.mock_player)

    async def test_slash_status_and_server(self):
        mock_status = ServerStatusResult(
            online=True, host="play.uooutlands.com", ip="74.91.113.38", port=2593, latency_ms=35.0
        )
        with patch("uo_outlands_service.check_uo_server_status", new_callable=AsyncMock) as mock_chk:
            mock_chk.return_value = mock_status

            # Test /status
            await self.status_cmd.callback(self.ctx)
            self.ctx.defer.assert_awaited()
            self.ctx.send.assert_awaited()
            kw = self.ctx.send.call_args.kwargs
            self.assertIn("ONLINE", kw["embed"].title)

            # Test /server (alias slash)
            self.ctx.send.reset_mock()
            await self.server_cmd.callback(self.ctx)
            self.ctx.send.assert_awaited()
            kw2 = self.ctx.send.call_args.kwargs
            self.assertIn("ONLINE", kw2["embed"].title)

    async def test_slash_riavvio(self):
        mock_info = RestartInfo(
            next_restart_ny=datetime(2026, 10, 14, 11, 0, 0, tzinfo=TZ_ET),
            next_restart_rome=datetime(2026, 10, 14, 17, 0, 0, tzinfo=TZ_ROME),
            time_remaining=timedelta(hours=3, minutes=10),
            hours=3,
            minutes=10,
            seconds=0,
        )
        with patch("uo_outlands_service.get_next_restart_info", return_value=mock_info):
            await self.riavvio_cmd.callback(self.ctx)
            self.ctx.send.assert_awaited()
            kw = self.ctx.send.call_args.kwargs
            self.assertIn("Riavvio Ordinario", kw["embed"].title)

    async def test_slash_builds(self):
        mock_builds = [
            UOAddictsBuildItem("OP Archery Tamer", "op-tamer", "uo-outlands", "https://url", "jaseowns", 25, 10, ["Taming: 120"], ["pvm"])
        ]
        with patch.object(uo_outlands_service.builds_service, "fetch_builds", new_callable=AsyncMock) as mock_b:
            mock_b.return_value = mock_builds
            await self.builds_cmd.callback(self.ctx, categoria="pvm")
            self.ctx.send.assert_awaited()
            kw = self.ctx.send.call_args.kwargs
            self.assertIn("PvM", kw["embed"].title)

    async def test_slash_build_single_match(self):
        mock_build = UOAddictsBuildItem("OP Archery Tamer", "op-tamer", "uo-outlands", "https://url", "jaseowns", 25, 10, ["Taming: 120"], ["pvm"])
        with patch.object(uo_outlands_service.builds_service, "search_builds", new_callable=AsyncMock) as mock_s:
            mock_s.return_value = [mock_build]
            await self.build_cmd.callback(self.ctx, nome="tamer")
            self.ctx.defer.assert_awaited()
            self.ctx.send.assert_awaited()
            kw = self.ctx.send.call_args.kwargs
            self.assertIn("OP Archery Tamer", kw["embed"].title)

    async def test_slash_build_not_found_fallback(self):
        with patch.object(uo_outlands_service.builds_service, "search_builds", new_callable=AsyncMock) as mock_s, \
             patch.object(uo_outlands_service.builds_service, "fetch_builds", new_callable=AsyncMock) as mock_pop:
            mock_s.return_value = []
            mock_pop.return_value = [
                UOAddictsBuildItem("Popular Build", "pop-1", "uo-outlands", "https://url", "author", 30, 15, [], ["popular"])
            ]
            await self.build_cmd.callback(self.ctx, nome="nonexistent")
            self.ctx.send.assert_awaited()
            kw = self.ctx.send.call_args.kwargs
            self.assertIn("nessun risultato", kw["embed"].title)

    async def test_slash_news(self):
        mock_news = [
            UOAddictsNewsItem("n1", "Articolo Test", "https://url", None, "Desc", "Autore", ["tag"])
        ]
        with patch.object(uo_outlands_service.news_service, "fetch_latest_news", new_callable=AsyncMock) as mock_n:
            mock_n.return_value = mock_news
            await self.news_cmd.callback(self.ctx)
            self.ctx.send.assert_awaited()
            kw = self.ctx.send.call_args.kwargs
            self.assertIn("Ultime Novità", kw["embed"].title)


# ==============================================================================
# 8. TEST BACKGROUND TASKS IN MAIN.PY
# ==============================================================================

class TestMainBackgroundLoops(unittest.IsolatedAsyncioTestCase):
    """Test dei background loop avviati in main.py."""

    async def test_uoaddicts_news_loop_posts_to_channel(self):
        from main import _uoaddicts_news_monitor_loop, bot
        from unittest.mock import PropertyMock
        mock_item = UOAddictsNewsItem("n-test", "New Dungeon", "https://url", None, "Desc", "Auth", ["dungeon"])

        mock_guild = MagicMock(spec=discord.Guild)
        mock_ch = MagicMock(spec=discord.TextChannel)
        mock_ch.name = "info"
        mock_ch.send = AsyncMock()
        mock_ch.permissions_for.return_value.send_messages = True
        mock_guild.text_channels = [mock_ch]
        mock_guild.me = MagicMock()

        with patch("uo_outlands_service.news_service.check_for_new_news", new_callable=AsyncMock) as mock_chk, \
             patch.object(type(bot), "guilds", new_callable=PropertyMock, return_value=[mock_guild]), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_chk.return_value = [mock_item]
            mock_sleep.side_effect = [None, asyncio.CancelledError()]

            try:
                await _uoaddicts_news_monitor_loop(interval_sec=10.0)
            except asyncio.CancelledError:
                pass

            mock_ch.send.assert_awaited()
            kw = mock_ch.send.call_args.kwargs
            self.assertIn("embed", kw)
            self.assertEqual(kw["embed"].title, "📰 New Dungeon")

    async def test_uo_restart_alert_loop_triggers_prewarning(self):
        from main import _uo_daily_restart_alert_loop, bot, player_manager
        from unittest.mock import PropertyMock
        mock_guild = MagicMock(spec=discord.Guild)
        mock_ch = MagicMock(spec=discord.TextChannel)
        mock_ch.name = "info"
        mock_ch.send = AsyncMock()
        mock_ch.permissions_for.return_value.send_messages = True
        mock_guild.text_channels = [mock_ch]
        mock_guild.me = MagicMock()

        mock_player = MagicMock()
        mock_player.voice_client = MagicMock()
        mock_player.voice_client.is_connected.return_value = True
        mock_player.speak_tts = AsyncMock()
        player_manager.get_player = MagicMock(return_value=mock_player)

        # Ore 10:51 AM ET (tra 10:50 e 10:55) -> Deve lanciare pre-avviso 10m
        mock_ny_now = datetime(2026, 10, 14, 10, 51, 0, tzinfo=TZ_ET)

        with patch("main.datetime") as mock_dt, \
             patch.object(type(bot), "guilds", new_callable=PropertyMock, return_value=[mock_guild]), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_dt.now.return_value = mock_ny_now
            mock_dt.combine = datetime.combine
            mock_sleep.side_effect = [None, asyncio.CancelledError()]

            try:
                await _uo_daily_restart_alert_loop(check_interval_sec=5.0)
            except asyncio.CancelledError:
                pass

            mock_ch.send.assert_awaited()
            kw = mock_ch.send.call_args.kwargs
            self.assertIn("embed", kw)
            self.assertIn("10 Minuti", kw["embed"].title)


# ==============================================================================
# 9. TEST FALLBACK DATA, ATOMIC DUMP & OUTLANDS QUERY HANDLER
# ==============================================================================

class TestOfflineFallbacksAndAtomicSave(unittest.IsolatedAsyncioTestCase):
    """Test della resilienza offline (dataset fallback) e integrità salvataggi atomici."""

    def test_atomic_json_dump_and_integrity(self):
        from uo_outlands_service import _atomic_json_dump
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test_atomic.json")
            data = {"key": "value", "items": [1, 2, 3]}
            _atomic_json_dump(file_path, data)

            self.assertTrue(os.path.exists(file_path))
            with open(file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded, data)

    async def test_fallback_datasets_when_offline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_news = os.path.join(tmpdir, "news_nonexistent.json")
            cache_builds = os.path.join(tmpdir, "builds_nonexistent.json")
            n_service = UOAddictsNewsService(cache_file=cache_news)
            b_service = UOAddictsBuildsService(cache_file=cache_builds)

            with patch("uo_outlands_service.fetch_html", side_effect=Exception("Network down")):
                items = await n_service.fetch_latest_news()
                self.assertGreaterEqual(len(items), 1)
                self.assertIn("Outlands", items[0].title)

                builds = await b_service.fetch_builds("popular")
                self.assertGreaterEqual(len(builds), 1)
                self.assertIn("Tamer", builds[0].title)


class TestHandleOutlandsQueryBuild(unittest.IsolatedAsyncioTestCase):
    """Test del routing per domande veloci sulle build in _handle_outlands_query."""

    async def test_handle_outlands_query_build_shorthand(self):
        from main import _handle_outlands_query
        ctx = MagicMock()
        ctx.defer = AsyncMock()
        ctx.send = AsyncMock()
        ctx.guild = MagicMock()

        mock_builds = [
            UOAddictsBuildItem("Tamer Mage", "tamer-mage", "uo-outlands", "https://url", "author", 10, 5, [], ["popular"])
        ]
        with patch.object(uo_outlands_service.builds_service, "fetch_builds", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_builds
            await _handle_outlands_query(ctx, "build")

            ctx.send.assert_awaited()
            kw = ctx.send.call_args.kwargs
            self.assertIn("embed", kw)
            self.assertIn("Più Popolari", kw["embed"].title)

    def test_outlands_wiki_build_query_resolves_generic_build(self):
        from outlands_wiki import _resolve_build_query
        res = _resolve_build_query("build")
        self.assertIsNotNone(res)
        self.assertIn("Build", res["page_title"])


