# -*- coding: utf-8 -*-
"""Tests sans réseau : python -m unittest discover -s tests -v"""

import csv
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gloriafood_menu_export as gfe  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "pizzeria-exemple.json")
FAKE_RUID = "11111111-2222-4333-8444-555555555555"


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Remplace le réseau : renvoie un corps, ou lève l'exception donnée."""

    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, dict(request.header_items()), timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.body)


class NetworkPatch:
    def __init__(self, opener):
        self.opener = opener

    def __enter__(self):
        self.saved = gfe._OPENER
        gfe._OPENER = self.opener
        return self.opener

    def __exit__(self, *exc):
        gfe._OPENER = self.saved
        return False


class ParseTargetTests(unittest.TestCase):
    def test_ruid(self):
        self.assertEqual(gfe.parse_target(" " + FAKE_RUID.upper() + " "), ("ruid", FAKE_RUID))

    def test_site_with_and_without_scheme(self):
        self.assertEqual(gfe.parse_target("https://www.exemple.fr/carte"), ("site", "https://www.exemple.fr/carte"))
        self.assertEqual(gfe.parse_target("exemple.fr"), ("site", "https://exemple.fr"))

    def test_refuses_other_schemes_and_garbage(self):
        for value in ("file:///etc/passwd", "ftp://exemple.fr", "", "deux mots", "https://"):
            with self.assertRaises(gfe.ExportError) as caught:
                gfe.parse_target(value)
            self.assertEqual(caught.exception.exit_code, gfe.EXIT_INPUT)

    def test_menu_url_only_accepts_uuid(self):
        self.assertIn(FAKE_RUID, gfe.menu_url(FAKE_RUID))
        with self.assertRaises(gfe.ExportError):
            gfe.menu_url("../../autre-chose")


class FindRuidTests(unittest.TestCase):
    def test_button_and_link(self):
        other = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        html = (
            '<span class="glf-button" data-glf-cuid="x" data-glf-ruid="%s">Commander</span>'
            "<span class='glf-button' data-glf-ruid='%s'>Réserver</span>"
            '<a href="https://example.com/ordering?restaurant_uid=%s">Commander</a>'
        ) % (FAKE_RUID, FAKE_RUID.upper(), other)
        self.assertEqual(gfe.find_ruids(html), [FAKE_RUID, other])

    def test_nothing(self):
        self.assertEqual(gfe.find_ruids("<html><body>Bonjour</body></html>"), [])


class NormalizeTests(unittest.TestCase):
    def setUp(self):
        self.menu = gfe.normalize(load_fixture(), ruid=FAKE_RUID)

    def test_order_follows_sort(self):
        self.assertEqual([c["name"] for c in self.menu["categories"]], ["Pizzas", "Boissons", "Desserts"])
        self.assertEqual([i["name"] for i in self.menu["categories"][0]["items"]], ["Margherita", "Reine"])
        choices = self.menu["categories"][0]["options"][0]["choices"]
        self.assertEqual([c["name"] for c in choices], ["Mozzarella en plus", "Champignons"])

    def test_sizes_add_to_base_price(self):
        margherita = self.menu["categories"][0]["items"][0]
        self.assertEqual(margherita["price"], 9.5)
        self.assertEqual([(s["name"], s["price_supplement"], s["price_total"]) for s in margherita["sizes"]],
                         [("Moyenne", 0.0, 9.5), ("Grande", 3.0, 12.5)])
        self.assertEqual(margherita["sizes"][1]["options"][0]["name"], "Bord")

    def test_metadata_and_stats(self):
        self.assertEqual(self.menu["restaurant_id"], FAKE_RUID)
        self.assertEqual(self.menu["currency"], "EUR")
        self.assertEqual(self.menu["format"], "gloriafood-menu-export/1")
        self.assertEqual(gfe.stats(self.menu), {"categories": 3, "items": 5, "sizes": 2, "groups": 3})

    def test_rejects_unexpected_format(self):
        for raw in ([], {"name": "x"}, {"categories": "non"}):
            with self.assertRaises(gfe.ExportError) as caught:
                gfe.normalize(raw)
            self.assertEqual(caught.exception.exit_code, gfe.EXIT_FORMAT)

    def test_tolerates_missing_fields(self):
        menu = gfe.normalize({"categories": [{"name": "Vide", "items": [{"name": "Sans prix"}]}]})
        item = menu["categories"][0]["items"][0]
        self.assertIsNone(item["price"])
        self.assertEqual(item["sizes"], [])


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.menu = gfe.normalize(load_fixture())

    def test_french_rows(self):
        rows = gfe.csv_rows(self.menu, "fr")
        self.assertEqual(len(rows), 6)  # 5 plats, dont un en 2 tailles
        moyenne, grande = rows[0], rows[1]
        self.assertEqual(moyenne[:7], ["Pizzas", "Margherita", "Sauce tomate, mozzarella, basilic.",
                                       "9,50", "Moyenne", "0,00", "9,50"])
        self.assertEqual(grande[6], "12,50")
        self.assertIn("Supplément : Mozzarella en plus (+1,50) ; Champignons (+1,00)", grande[7])
        self.assertIn("Bord : Bord fromage (+2,00)", grande[7])
        self.assertNotIn("Bord", moyenne[7])
        limonade = rows[4]
        self.assertEqual(limonade[7], "Parfum (obligatoire) : Citron ; Grenadine (+0,30)")
        self.assertEqual(rows[5][-1], "oui")

    def test_english_rows(self):
        rows = gfe.csv_rows(self.menu, "en")
        self.assertEqual(rows[0][3], "9.50")
        self.assertEqual(rows[4][7], "Parfum (required): Citron; Grenadine (+0.30)")

    def test_formula_cells_are_neutralised(self):
        self.assertEqual(gfe.safe_cell("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(gfe.safe_cell("+33 4 00 00 00 00"), "'+33 4 00 00 00 00")
        self.assertEqual(gfe.safe_cell("@SOMME"), "'@SOMME")
        self.assertEqual(gfe.safe_cell("-30 % de sucre"), "'-30 % de sucre")
        self.assertEqual(gfe.safe_cell("- tomate, basilic"), "- tomate, basilic")
        self.assertEqual(gfe.safe_cell("Margherita"), "Margherita")


class OutputTests(unittest.TestCase):
    def test_files_are_utf8_and_not_overwritten(self):
        raw = load_fixture()
        menu = gfe.normalize(raw)
        with tempfile.TemporaryDirectory() as folder:
            paths = gfe.write_outputs(menu, raw, folder, lang="fr", keep_raw=True)
            self.assertEqual(len(paths), 3)
            with open(os.path.join(folder, "menu.csv"), "rb") as handle:
                self.assertTrue(handle.read().startswith(b"\xef\xbb\xbf"))
            with open(os.path.join(folder, "menu.csv"), encoding="utf-8-sig", newline="") as handle:
                table = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(table[0], gfe.CSV_HEADERS["fr"])
            self.assertEqual(table[4][1], "Eau minérale 50 cl")
            with open(os.path.join(folder, "menu.json"), encoding="utf-8") as handle:
                self.assertIn("Pâte maison", handle.read())
            with self.assertRaises(gfe.ExportError) as caught:
                gfe.write_outputs(menu, raw, folder)
            self.assertEqual(caught.exception.exit_code, gfe.EXIT_OUTPUT)
            gfe.write_outputs(menu, raw, folder, force=True)


class NetworkTests(unittest.TestCase):
    def test_single_request_with_honest_user_agent(self):
        with open(FIXTURE, "rb") as handle:
            opener = FakeOpener(body=handle.read())
        with NetworkPatch(opener), tempfile.TemporaryDirectory() as folder, \
                redirect_stdout(io.StringIO()):
            code = gfe.main([FAKE_RUID, "-o", folder])
            self.assertEqual(code, gfe.EXIT_OK)
            self.assertTrue(os.path.exists(os.path.join(folder, "menu.json")))
        self.assertEqual(len(opener.calls), 1)
        url, headers, timeout = opener.calls[0]
        self.assertEqual(url, gfe.MENU_API.format(ruid=FAKE_RUID))
        self.assertTrue(headers["User-agent"].startswith("gloriafood-menu-export/"))
        self.assertEqual(timeout, gfe.DEFAULT_TIMEOUT)

    def test_site_page_then_menu(self):
        page = ('<span class="glf-button" data-glf-ruid="%s"></span>' % FAKE_RUID).encode("utf-8")
        with open(FIXTURE, "rb") as handle:
            menu_body = handle.read()

        class TwoStep(FakeOpener):
            def open(self, request, timeout=None):
                self.calls.append((request.full_url, None, timeout))
                return FakeResponse(page if len(self.calls) == 1 else menu_body)

        opener = TwoStep()
        with NetworkPatch(opener), tempfile.TemporaryDirectory() as folder, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(gfe.main(["https://www.exemple.fr", "-o", folder]), gfe.EXIT_OK)
        self.assertEqual([c[0] for c in opener.calls],
                         ["https://www.exemple.fr", gfe.MENU_API.format(ruid=FAKE_RUID)])

    def test_404_means_closed_account(self):
        error = urllib.error.HTTPError(gfe.menu_url(FAKE_RUID), 404, "Not Found", {}, None)
        with NetworkPatch(FakeOpener(error=error)):
            with self.assertRaises(gfe.ExportError) as caught:
                gfe.fetch(gfe.menu_url(FAKE_RUID), 1000, 5, "application/json")
        self.assertEqual(caught.exception.key, "not_found")
        self.assertEqual(caught.exception.exit_code, gfe.EXIT_NOT_FOUND)

    def test_timeouts(self):
        for error in (socket.timeout("timed out"), urllib.error.URLError(socket.timeout("timed out"))):
            with NetworkPatch(FakeOpener(error=error)):
                with self.assertRaises(gfe.ExportError) as caught:
                    gfe.fetch("https://example.com/", 1000, 5, "text/html")
            self.assertEqual(caught.exception.key, "timeout")

    def test_size_limit(self):
        with NetworkPatch(FakeOpener(body=b"x" * 101)):
            with self.assertRaises(gfe.ExportError) as caught:
                gfe.fetch("https://example.com/", 100, 5, "text/html")
        self.assertEqual(caught.exception.key, "too_large")

    def test_page_without_button(self):
        with NetworkPatch(FakeOpener(body=b"<html>rien</html>")), redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(gfe.main(["exemple.fr", "-o", tempfile.gettempdir()]), gfe.EXIT_NOT_FOUND)
        self.assertIn("data-glf-ruid", err.getvalue())

    def test_existing_files_checked_before_any_request(self):
        opener = FakeOpener(error=AssertionError("aucune requête attendue"))
        with NetworkPatch(opener), tempfile.TemporaryDirectory() as folder, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            open(os.path.join(folder, "menu.json"), "w").close()
            self.assertEqual(gfe.main([FAKE_RUID, "-o", folder]), gfe.EXIT_OUTPUT)
        self.assertEqual(opener.calls, [])

    def test_offline_input(self):
        with NetworkPatch(FakeOpener(error=AssertionError("pas de réseau attendu"))), \
                tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            self.assertEqual(gfe.main(["--input", FIXTURE, "-o", folder, "--lang", "en"]), gfe.EXIT_OK)
            with open(os.path.join(folder, "menu.csv"), encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(next(csv.reader(handle)), gfe.CSV_HEADERS["en"])


if __name__ == "__main__":
    unittest.main()
