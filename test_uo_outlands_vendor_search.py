import os
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import discord

import uo_outlands_service
from uo_outlands_service import (
    VendorSearchItem,
    VendorSearchResult,
    VendorSearchService,
    build_vendor_search_embed,
    VOICE_VENDOR_SEARCH_REPLY,
    UO_PORTAL_VENDOR_SEARCH_URL,
)


class TestUOOutlandsVendorSearch(unittest.IsolatedAsyncioTestCase):
    """Test suite completa per il servizio Vendor Search di UO Outlands."""

    def setUp(self):
        self.service = VendorSearchService()

    def test_vendor_item_and_result_dataclass(self):
        """Verifica la creazione e serializzazione di VendorSearchItem e VendorSearchResult."""
        item = VendorSearchItem(
            item_name="Aspect Core (Fire)",
            price=25000,
            amount=2,
            vendor_name="Fire Shop",
            owner_name="Ignis",
            location_x=1420,
            location_y=1850,
            location_z=5,
            vendor_serial=987654,
            last_synced="2026-10-14T18:00:00Z",
            properties=["Aspect: Fire", "Tier: 3"],
        )
        d = item.to_dict()
        self.assertEqual(d["item_name"], "Aspect Core (Fire)")
        self.assertEqual(d["price"], 25000)
        self.assertEqual(d["amount"], 2)
        self.assertEqual(d["vendor_name"], "Fire Shop")
        self.assertEqual(d["location_x"], 1420)

        res = VendorSearchResult(
            query="aspect core",
            has_credentials=True,
            items=[item],
            total_found=1,
            portal_url=UO_PORTAL_VENDOR_SEARCH_URL
        )
        self.assertEqual(len(res.items), 1)
        self.assertTrue(res.has_credentials)

    def test_build_embed_without_credentials_shows_setup_guidance(self):
        """Verifica che senza credenziali venga mostrato un embed informativo con link diretto al portale."""
        res = VendorSearchResult(
            query="mastery chain",
            has_credentials=False,
            items=[],
            total_found=0,
            portal_url=UO_PORTAL_VENDOR_SEARCH_URL
        )
        embed = build_vendor_search_embed(res)
        self.assertIn("mastery chain", embed.title.lower())
        self.assertIn("OUTLANDS_PORTAL_EMAIL", embed.description)
        self.assertIn("OUTLANDS_PORTAL_PASSWORD", embed.description)
        self.assertIn(UO_PORTAL_VENDOR_SEARCH_URL, embed.description)

    def test_build_embed_with_items_renders_details_and_prices(self):
        """Verifica che con articoli trovati l'embed elenchi prezzi, coordinate e link al vendor."""
        item1 = VendorSearchItem(
            item_name="Aspect Core (Fire)",
            price=22000,
            amount=1,
            vendor_name="Vulcan Goods",
            owner_name="Hephaestus",
            location_x=1200,
            location_y=1600,
            vendor_serial=112233
        )
        item2 = VendorSearchItem(
            item_name="Aspect Core (Fire)",
            price=24500,
            amount=3,
            vendor_name="Flame Emporium",
            owner_name="Blaze",
            location_x=1350,
            location_y=1720,
            vendor_serial=445566
        )
        res = VendorSearchResult(
            query="aspect core fire",
            has_credentials=True,
            items=[item1, item2],
            total_found=2
        )
        embed = build_vendor_search_embed(res)
        self.assertIn("aspect core fire", embed.title.lower())
        self.assertEqual(len(embed.fields), 2)
        # Verifica field 1
        self.assertIn("22,000 GP", embed.fields[0].name)
        self.assertIn("Vulcan Goods", embed.fields[0].value)
        self.assertIn("(X: 1200, Y: 1600)", embed.fields[0].value)
        # Verifica field 2
        self.assertIn("24,500 GP", embed.fields[1].name)
        self.assertIn("x3", embed.fields[1].name)
        self.assertIn("Flame Emporium", embed.fields[1].value)

    def test_build_embed_when_empty_shows_helpful_message(self):
        """Verifica che quando nessun vendor vende l'oggetto venga mostrato un messaggio chiaro."""
        res = VendorSearchResult(
            query="nonexistent_item_xyz",
            has_credentials=True,
            items=[],
            total_found=0
        )
        embed = build_vendor_search_embed(res)
        self.assertIn("nonexistent_item_xyz", embed.title)
        self.assertIn("Nessun player vendor sta attualmente vendendo", embed.description)

    def test_build_embed_with_error_message(self):
        """Verifica che un errore di rete o autenticazione generi un embed di errore."""
        res = VendorSearchResult(
            query="runebook",
            has_credentials=True,
            error_message="Connessione al portale scaduta",
            portal_url=UO_PORTAL_VENDOR_SEARCH_URL
        )
        embed = build_vendor_search_embed(res)
        self.assertIn("Connessione al portale scaduta", embed.description)

    async def test_search_vendor_without_credentials_returns_fallback_result(self):
        """Verifica che se le credenziali non sono presenti nell'ambiente, ritorni il fallback pulito."""
        with patch.dict(os.environ, {"OUTLANDS_PORTAL_EMAIL": "", "OUTLANDS_PORTAL_PASSWORD": ""}):
            srv = VendorSearchService()
            self.assertFalse(srv.has_credentials)
            res = await srv.search_vendor("aspect core")
            self.assertFalse(res.has_credentials)
            self.assertEqual(res.query, "aspect core")
            self.assertEqual(len(res.items), 0)

    async def test_search_vendor_cache_hit_returns_instantaneously(self):
        """Verifica che le ricerche identiche entro il TTL ritornino dalla cache senza chiamate esterne."""
        cached_res = VendorSearchResult(
            query="test_cache_item",
            has_credentials=True,
            items=[
                VendorSearchItem(item_name="Cached Item", price=5000, vendor_name="Cache Vendor")
            ],
            total_found=1
        )
        import time as _t
        self.service._cache["test_cache_item"] = (cached_res, _t.monotonic())

        # Anche senza credenziali, la cache deve avere priorità e ritornare il risultato
        res = await self.service.search_vendor("test_cache_item")
        self.assertEqual(res.query, "test_cache_item")
        self.assertEqual(len(res.items), 1)
        self.assertEqual(res.items[0].price, 5000)

    async def test_search_vendor_parses_json_api_response_correctly(self):
        """Verifica il parsing corretto della risposta JSON simulata da portal.uooutlands.com/api/VendorSearch/Search."""
        fake_api_data = {
            "totalCount": 15,
            "items": [
                {
                    "name": "Fire Aspect Core",
                    "price": 28000,
                    "amount": 2,
                    "vendorSerial": 778899,
                    "vendor": {
                        "vendorName": "Outlands Fire Emporium",
                        "ownerCharName": "Pyromancer",
                        "locationX": 1450,
                        "locationY": 1680,
                        "locationZ": 0,
                        "lastSyncedAt": "2026-10-14T19:00:00Z"
                    }
                }
            ]
        }

        parsed = self.service._parse_search_data(
            query="fire aspect core",
            data=fake_api_data,
            portal_url=UO_PORTAL_VENDOR_SEARCH_URL,
            max_results=5
        )
        self.assertEqual(parsed.total_found, 15)
        self.assertEqual(len(parsed.items), 1)
        item = parsed.items[0]
        self.assertEqual(item.item_name, "Fire Aspect Core")
        self.assertEqual(item.price, 28000)
        self.assertEqual(item.amount, 2)
        self.assertEqual(item.vendor_name, "Outlands Fire Emporium")
        self.assertEqual(item.owner_name, "Pyromancer")
        self.assertEqual(item.location_x, 1450)
        self.assertEqual(item.location_y, 1680)
        self.assertEqual(item.vendor_serial, 778899)


class TestVoiceCommandVendorSearchExtraction(unittest.TestCase):
    """Verifica l'estrazione vocale delle query Vendor Search."""

    def test_voice_query_extraction_patterns(self):
        """Verifica che varie formulazioni vocali riconoscano correttamente il nome dell'oggetto."""
        from voice_commands import extract_vendor_query

        cases = {
            "cerca sul vendor aspect core": "aspect core",
            "cerca vendor aspect core": "aspect core",
            "cerca sui vendor fire aspect": "fire aspect",
            "trova sul vendor mana potion": "mana potion",
            "quanto costa mastery chain sul vendor": "mastery chain",
            "quanto costa una bag of holding sui vendor": "bag of holding",
            "quanto costa aspect core": "aspect core",
            "quanto costa un aspect core?": "aspect core",
            "prezzo aspect core": "aspect core",
            "prezzi aspect core": "aspect core",
            "prezzo di aspect core sul vendor": "aspect core",
            "prezzi vendor aspect core": "aspect core",
            "prezzi sui vendor aspect core": "aspect core",
            "cerca prezzi aspect core": "aspect core",
            "cerca i prezzi di aspect core": "aspect core",
            "vendor search aspect core": "aspect core",
            "vendor search rope": "rope",
            "vendor runebook": "runebook",
            "vendor search": "",
            "cerca sul vendor": "",
            "metti i beatles": None,
            "che ore sono": None,
        }

        for phrase, expected in cases.items():
            result = extract_vendor_query(phrase)
            self.assertEqual(result, expected, f"Fallito per '{phrase}': ottenuto {result!r}, atteso {expected!r}")


class TestMainSlashCommandsVendorSearch(unittest.TestCase):
    """Verifica che i comandi /vendor, /prezzi e !sync siano registrati correttamente in main.py."""

    def test_vendor_hybrid_command_registered(self):
        import main
        cmd_vendor = main.bot.get_command("vendor")
        self.assertIsNotNone(cmd_vendor, "Il comando /vendor non è registrato nel bot!")
        self.assertEqual(cmd_vendor.name, "vendor")
        self.assertIn("vendorsearch", cmd_vendor.aliases)

        cmd_prezzi = main.bot.get_command("prezzi")
        self.assertIsNotNone(cmd_prezzi, "Il comando /prezzi non è registrato nel bot!")
        self.assertEqual(cmd_prezzi.name, "prezzi")
        self.assertIn("prezzo", cmd_prezzi.aliases)

        cmd_sync = main.bot.get_command("sync")
        self.assertIsNotNone(cmd_sync, "Il comando !sync non è registrato nel bot!")


if __name__ == "__main__":
    unittest.main()
