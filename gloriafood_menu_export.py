#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gloriafood_menu_export.py

Exporte votre carte GloriaFood (catégories, plats, descriptions, prix, tailles,
options, liens des photos) dans menu.json et menu.csv, avant l'arrêt de
GloriaFood annoncé par Oracle pour le 30 avril 2027.

Exports your GloriaFood menu (categories, items, descriptions, prices, sizes,
options, photo links) to menu.json and menu.csv, before the GloriaFood
shutdown announced by Oracle for 30 April 2027.

Python 3.8 ou plus récent, bibliothèque standard seulement. Licence MIT.

Usage :
    python gloriafood_menu_export.py https://www.mon-restaurant.fr
    python gloriafood_menu_export.py 11111111-2222-4333-8444-555555555555
    python gloriafood_menu_export.py --input menu-gloriafood-brut.json

À utiliser pour votre propre carte. Outil indépendant, sans lien avec Oracle :
GloriaFood est une marque d'Oracle. L'adresse lue est celle qu'utilise le
bouton de commande public ; elle n'est pas documentée et peut changer.
"""

import argparse
import csv
import datetime
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__version__ = "1.0.0"

PROJECT_URL = "https://github.com/robibi14/gloriafood-menu-export"
USER_AGENT = "gloriafood-menu-export/{} (+{})".format(__version__, PROJECT_URL)

# Adresse publique lue par le bouton de commande GloriaFood (aucun identifiant de connexion).
MENU_API = "https://www.restaurantlogin.com/api/restaurant/{ruid}/menu?active=true&pictures=true"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UUID_RE = re.compile(r"^" + _UUID + r"$", re.IGNORECASE)
RUID_PATTERNS = (
    # Bouton de commande ou de réservation : <span class="glf-button" data-glf-ruid="...">
    re.compile(r"data-glf-ruid\s*=\s*[\"']\s*(" + _UUID + r")\s*[\"']", re.IGNORECASE),
    # Lien direct vers la page de commande : ...?restaurant_uid=... ou ...&ruid=...
    re.compile(r"[?&;](?:restaurant_uid|ruid)=(" + _UUID + r")", re.IGNORECASE),
)

MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_MENU_BYTES = 30 * 1024 * 1024
DEFAULT_TIMEOUT = 20
DEFAULT_OUTPUT = "export-gloriafood"

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_NOT_FOUND = 2
EXIT_NETWORK = 3
EXIT_FORMAT = 4
EXIT_OUTPUT = 5

MESSAGES = {
    "fr": {
        "bad_input": "Entrée non reconnue : donnez l'adresse de votre site (https://...) ou votre identifiant de restaurant GloriaFood.",
        "need_target": "Indiquez l'adresse de votre site, votre identifiant de restaurant GloriaFood, ou --input FICHIER.",
        "fetch_site": "Lecture de la page {url}",
        "no_ruid": "Aucun bouton GloriaFood trouvé sur {url}. Essayez la page où se trouve votre bouton « Commander » ou « Réserver », ou donnez directement votre identifiant de restaurant : c'est la valeur de data-glf-ruid dans le code du bouton.",
        "several_ruids": "Plusieurs restaurants trouvés sur cette page : {others}. Le premier est exporté ; relancez avec l'identifiant voulu pour un autre.",
        "ruid_found": "Identifiant de restaurant trouvé : {ruid}",
        "fetch_menu": "Lecture de la carte publique (une seule requête)",
        "not_found": "Aucune carte pour cet identifiant (erreur 404). Le compte GloriaFood est probablement fermé ou supprimé, ou l'identifiant est faux.",
        "http_error": "Le serveur a répondu par une erreur {code} sur {url}. Réessayez plus tard.",
        "timeout": "Pas de réponse au bout de {timeout} secondes ({url}). Vérifiez votre connexion, ou augmentez --timeout.",
        "network": "Connexion impossible à {url} : {reason}",
        "too_large": "Réponse trop volumineuse ({url}), lecture arrêtée par sécurité.",
        "redirect": "Redirection refusée vers une adresse qui n'est pas http ou https.",
        "bad_json": "La réponse n'est pas une carte GloriaFood lisible ({detail}).",
        "read_input": "Impossible de lire le fichier {path} : {detail}",
        "exists": "{path} existe déjà. Choisissez un autre dossier avec -o, ou ajoutez --force pour remplacer.",
        "write_error": "Impossible d'écrire {path} : {detail}",
        "done": "Carte exportée. Catégories : {categories}. Plats : {items}. Tailles : {sizes}. Groupes d'options : {groups}.",
        "files": "Fichiers écrits :",
        "reminder": "Pensez aussi à exporter vos clients et l'historique de vos commandes depuis votre administration GloriaFood : cet outil ne lit que la carte publique.",
        "interrupted": "Interrompu.",
        "required": "obligatoire",
    },
    "en": {
        "bad_input": "Input not recognised: give your website address (https://...) or your GloriaFood restaurant ID.",
        "need_target": "Give your website address, your GloriaFood restaurant ID, or --input FILE.",
        "fetch_site": "Reading page {url}",
        "no_ruid": "No GloriaFood button found on {url}. Try the page that shows your 'Order' or 'Book' button, or give your restaurant ID directly: it is the data-glf-ruid value in the button code.",
        "several_ruids": "Several restaurants found on this page: {others}. The first one is exported; run again with the ID you want for another one.",
        "ruid_found": "Restaurant ID found: {ruid}",
        "fetch_menu": "Reading the public menu (a single request)",
        "not_found": "No menu for this ID (404 error). The GloriaFood account is probably closed or deleted, or the ID is wrong.",
        "http_error": "The server returned error {code} for {url}. Please try again later.",
        "timeout": "No answer after {timeout} seconds ({url}). Check your connection, or raise --timeout.",
        "network": "Could not connect to {url}: {reason}",
        "too_large": "Response too large ({url}), reading stopped for safety.",
        "redirect": "Redirect refused to an address that is not http or https.",
        "bad_json": "The response is not a readable GloriaFood menu ({detail}).",
        "read_input": "Could not read file {path}: {detail}",
        "exists": "{path} already exists. Pick another folder with -o, or add --force to replace it.",
        "write_error": "Could not write {path}: {detail}",
        "done": "Menu exported. Categories: {categories}. Items: {items}. Sizes: {sizes}. Option groups: {groups}.",
        "files": "Files written:",
        "reminder": "Remember to also export your customers and your order history from your GloriaFood admin: this tool only reads the public menu.",
        "interrupted": "Interrupted.",
        "required": "required",
    },
}

CSV_HEADERS = {
    "fr": ["categorie", "plat", "description", "prix", "taille", "supplement_taille",
           "prix_avec_taille", "options", "photo", "rupture_de_stock"],
    "en": ["category", "item", "description", "price", "size", "size_supplement",
           "price_with_size", "options", "picture", "out_of_stock"],
}


class ExportError(Exception):
    """Erreur prévue : un message clair pour le restaurateur et un code de sortie."""

    def __init__(self, key, exit_code, **details):
        super().__init__(key)
        self.key = key
        self.exit_code = exit_code
        self.details = details

    def message(self, lang):
        return MESSAGES[lang][self.key].format(**self.details)


# --------------------------------------------------------------------------- réseau

class _HttpOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Suit au plus 5 redirections, et seulement vers http ou https."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise ExportError("redirect", EXIT_NETWORK)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpOnlyRedirect)


def fetch(url, max_bytes, timeout, accept):
    """Une requête GET, réponse limitée à max_bytes. Renvoie les octets lus."""
    if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
        raise ExportError("bad_input", EXIT_INPUT)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    })
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ExportError("not_found", EXIT_NOT_FOUND)
        raise ExportError("http_error", EXIT_NETWORK, code=error.code, url=url)
    except (socket.timeout, TimeoutError):
        raise ExportError("timeout", EXIT_NETWORK, timeout=timeout, url=url)
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise ExportError("timeout", EXIT_NETWORK, timeout=timeout, url=url)
        raise ExportError("network", EXIT_NETWORK, url=url, reason=error.reason)
    if len(body) > max_bytes:
        raise ExportError("too_large", EXIT_NETWORK, url=url)
    return body


# --------------------------------------------------------------------------- entrée

def parse_target(value):
    """Renvoie ("ruid", identifiant) ou ("site", adresse). Lève ExportError sinon."""
    text = (value or "").strip()
    if UUID_RE.match(text):
        return "ruid", text.lower()
    if not text or any(c.isspace() for c in text):
        raise ExportError("bad_input", EXIT_INPUT)
    if "://" not in text:
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ExportError("bad_input", EXIT_INPUT)
    return "site", text


def find_ruids(html):
    """Identifiants de restaurant GloriaFood présents dans une page, sans doublon, dans l'ordre."""
    found = []
    for pattern in RUID_PATTERNS:
        for match in pattern.finditer(html):
            ruid = match.group(1).lower()
            if ruid not in found:
                found.append(ruid)
    return found


def menu_url(ruid):
    if not UUID_RE.match(ruid or ""):
        raise ExportError("bad_input", EXIT_INPUT)
    return MENU_API.format(ruid=ruid.lower())


def parse_menu_bytes(body):
    try:
        raw = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ExportError("bad_json", EXIT_FORMAT, detail=type(error).__name__)
    check_raw(raw)
    return raw


def check_raw(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("categories"), list):
        raise ExportError("bad_json", EXIT_FORMAT, detail="categories")


# --------------------------------------------------------------------------- carte

def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _decimal(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sorted(entries):
    """Trie selon le champ « sort » de GloriaFood, en gardant l'ordre d'origine à égalité."""
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    return [e for _, e in sorted(enumerate(entries), key=lambda p: (_int_or_none(p[1].get("sort")) or 0, p[0]))]


def _amount(value, decimals):
    number = _decimal(value)
    if number is None:
        return None
    quantum = Decimal(1).scaleb(-decimals)
    return float(number.quantize(quantum, rounding=ROUND_HALF_UP))


def _groups(groups, decimals):
    result = []
    for group in _sorted(groups):
        result.append({
            "name": _text(group.get("name")),
            "required": bool(group.get("required")),
            "force_min": _int_or_none(group.get("force_min")),
            "force_max": _int_or_none(group.get("force_max")),
            "allow_quantity": bool(group.get("allow_quantity")),
            "choices": [{
                "name": _text(option.get("name")),
                "price": _amount(option.get("price"), decimals),
                "default": bool(option.get("default")),
                "out_of_stock": bool(option.get("is_out_of_stock")),
            } for option in _sorted(group.get("options"))],
        })
    return result


def normalize(raw, ruid=None, now=None):
    """Transforme la réponse de GloriaFood en une carte simple et stable."""
    check_raw(raw)
    config = raw.get("currency_config") if isinstance(raw.get("currency_config"), dict) else {}
    decimals = _int_or_none(config.get("decimals"))
    decimals = 2 if decimals is None or not 0 <= decimals <= 4 else decimals
    now = now or datetime.datetime.now(datetime.timezone.utc)

    categories = []
    for category in _sorted(raw.get("categories")):
        items = []
        for item in _sorted(category.get("items")):
            base = _amount(item.get("price"), decimals)
            sizes = []
            for size in item.get("sizes") or []:
                if not isinstance(size, dict):
                    continue
                supplement = _amount(size.get("price"), decimals)
                total = None
                if base is not None or supplement is not None:
                    total = _amount((_decimal(base) or 0) + (_decimal(supplement) or 0), decimals)
                sizes.append({
                    "name": _text(size.get("name")),
                    "price_supplement": supplement,
                    "price_total": total,
                    "default": bool(size.get("default")),
                    "options": _groups(size.get("groups"), decimals),
                })
            items.append({
                "name": _text(item.get("name")),
                "description": _text(item.get("description")),
                "price": base,
                "picture": _text(item.get("picture")) or None,
                "picture_hi_res": _text(item.get("picture_hi_res")) or None,
                "out_of_stock": bool(item.get("is_out_of_stock")),
                "options": _groups(item.get("groups"), decimals),
                "sizes": sizes,
            })
        categories.append({
            "name": _text(category.get("name")),
            "description": _text(category.get("description")),
            "options": _groups(category.get("groups"), decimals),
            "items": items,
        })

    languages = raw.get("languages") if isinstance(raw.get("languages"), list) else []
    return {
        "format": "gloriafood-menu-export/1",
        "exported_at": now.replace(microsecond=0).isoformat(),
        "exported_with": USER_AGENT,
        "restaurant_id": ruid,
        "menu_name": _text(raw.get("name")),
        "currency": _text(raw.get("currency") or config.get("currency")) or None,
        "decimals": decimals,
        "languages": [_text(lang) for lang in languages],
        "categories": categories,
    }


def stats(menu):
    groups = set()
    items = sizes = 0
    for category in menu["categories"]:
        for group in category["options"]:
            groups.add(("c", category["name"], group["name"]))
        for item in category["items"]:
            items += 1
            sizes += len(item["sizes"])
            for group in item["options"]:
                groups.add(("i", category["name"], item["name"], group["name"]))
            for size in item["sizes"]:
                for group in size["options"]:
                    groups.add(("s", category["name"], item["name"], size["name"], group["name"]))
    return {"categories": len(menu["categories"]), "items": items, "sizes": sizes, "groups": len(groups)}


# --------------------------------------------------------------------------- CSV

def format_amount(value, decimals, lang, sign=False):
    if value is None:
        return ""
    number = Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    text = "{:f}".format(number)
    if sign and number > 0:
        text = "+" + text
    return text.replace(".", ",") if lang == "fr" else text


def safe_cell(text):
    """Neutralise une cellule qu'un tableur prendrait pour une formule (=, +, @, -x)."""
    if not text:
        return text
    first = text[0]
    if first in "=+@\t\r" or (first == "-" and len(text) > 1 and text[1] not in " -"):
        return "'" + text
    return text


def _groups_text(groups, decimals, lang):
    parts = []
    choice_sep = " ; " if lang == "fr" else "; "
    label_sep = " : " if lang == "fr" else ": "
    for group in groups:
        choices = []
        for choice in group["choices"]:
            label = choice["name"]
            if choice["price"]:
                label += " (" + format_amount(choice["price"], decimals, lang, sign=True) + ")"
            choices.append(label)
        name = group["name"]
        if group["required"]:
            name += " (" + MESSAGES[lang]["required"] + ")"
        parts.append(name + label_sep + choice_sep.join(choices))
    return " | ".join(parts)


def csv_rows(menu, lang="fr"):
    """Une ligne par plat, ou une ligne par taille quand le plat en a."""
    decimals = menu["decimals"]
    yes, no = ("oui", "non") if lang == "fr" else ("yes", "no")
    rows = []
    for category in menu["categories"]:
        for item in category["items"]:
            common = category["options"] + item["options"]
            variants = item["sizes"] or [None]
            for size in variants:
                groups = common + (size["options"] if size else [])
                rows.append([
                    safe_cell(category["name"]),
                    safe_cell(item["name"]),
                    safe_cell(item["description"]),
                    format_amount(item["price"], decimals, lang),
                    safe_cell(size["name"]) if size else "",
                    format_amount(size["price_supplement"], decimals, lang) if size else "",
                    format_amount(size["price_total"], decimals, lang) if size else "",
                    safe_cell(_groups_text(groups, decimals, lang)),
                    safe_cell(item["picture_hi_res"] or item["picture"] or ""),
                    yes if item["out_of_stock"] else no,
                ])
    return rows


# --------------------------------------------------------------------------- fichiers

def output_paths(folder, keep_raw=False, force=False):
    """Chemins des fichiers à écrire ; refuse d'écraser un fichier existant sans --force."""
    targets = {
        "json": os.path.join(folder, "menu.json"),
        "csv": os.path.join(folder, "menu.csv"),
    }
    if keep_raw:
        targets["raw"] = os.path.join(folder, "menu-gloriafood-brut.json")
    if not force:
        for path in targets.values():
            if os.path.exists(path):
                raise ExportError("exists", EXIT_OUTPUT, path=path)
    return targets


def write_outputs(menu, raw, folder, lang="fr", force=False, keep_raw=False):
    targets = output_paths(folder, keep_raw=keep_raw, force=force)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(targets["json"], "w", encoding="utf-8", newline="\n") as handle:
            json.dump(menu, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        # utf-8-sig : le BOM permet à Excel d'afficher correctement les accents.
        with open(targets["csv"], "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";" if lang == "fr" else ",")
            writer.writerow(CSV_HEADERS[lang])
            writer.writerows(csv_rows(menu, lang))
        if keep_raw:
            with open(targets["raw"], "w", encoding="utf-8", newline="\n") as handle:
                json.dump(raw, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
    except OSError as error:
        raise ExportError("write_error", EXIT_OUTPUT, path=folder, detail=error.strerror or error)
    return list(targets.values())


# --------------------------------------------------------------------------- programme

def build_parser():
    parser = argparse.ArgumentParser(
        prog="gloriafood_menu_export.py",
        description="Exporte votre carte GloriaFood en JSON et CSV / Export your GloriaFood menu to JSON and CSV.",
        epilog="À utiliser pour votre propre carte / Use it for your own menu. " + PROJECT_URL,
    )
    parser.add_argument("target", nargs="?",
                        help="adresse de votre site ou identifiant de restaurant / website address or restaurant ID")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT,
                        help="dossier de sortie / output folder (défaut / default: %(default)s)")
    parser.add_argument("--input", metavar="FICHIER",
                        help="convertir une réponse déjà enregistrée, sans réseau / convert a saved response, offline")
    parser.add_argument("--lang", choices=("fr", "en"), default="fr",
                        help="langue des messages et du CSV / language of messages and CSV (défaut / default: fr)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="délai par requête en secondes / timeout per request in seconds (défaut / default: %(default)s)")
    parser.add_argument("--raw", action="store_true",
                        help="garder aussi la réponse brute / also keep the raw response")
    parser.add_argument("--force", action="store_true",
                        help="remplacer des fichiers existants / overwrite existing files")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser


def _say(text):
    print(text, flush=True)


def run(args):
    lang = args.lang
    ruid = None
    # Avant toute requête : inutile d'interroger GloriaFood si les fichiers ne peuvent pas être écrits.
    output_paths(args.output, keep_raw=args.raw, force=args.force)
    if args.input:
        try:
            with open(args.input, "rb") as handle:
                body = handle.read(MAX_MENU_BYTES + 1)
        except OSError as error:
            raise ExportError("read_input", EXIT_INPUT, path=args.input, detail=error.strerror or error)
        if len(body) > MAX_MENU_BYTES:
            raise ExportError("too_large", EXIT_INPUT, url=args.input)
        raw = parse_menu_bytes(body)
        if args.target:
            kind, value = parse_target(args.target)
            ruid = value if kind == "ruid" else None
    else:
        if not args.target:
            raise ExportError("need_target", EXIT_INPUT)
        kind, value = parse_target(args.target)
        if kind == "site":
            _say(MESSAGES[lang]["fetch_site"].format(url=value))
            page = fetch(value, MAX_PAGE_BYTES, args.timeout, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5")
            # Les identifiants sont en ASCII : latin-1 lit n'importe quelle page sans erreur.
            found = find_ruids(page.decode("latin-1"))
            if not found:
                raise ExportError("no_ruid", EXIT_NOT_FOUND, url=value)
            if len(found) > 1:
                _say(MESSAGES[lang]["several_ruids"].format(others=", ".join(found)))
            ruid = found[0]
            _say(MESSAGES[lang]["ruid_found"].format(ruid=ruid))
        else:
            ruid = value
        _say(MESSAGES[lang]["fetch_menu"])
        raw = parse_menu_bytes(fetch(menu_url(ruid), MAX_MENU_BYTES, args.timeout, "application/json"))

    menu = normalize(raw, ruid=ruid)
    paths = write_outputs(menu, raw, args.output, lang=lang, force=args.force, keep_raw=args.raw)
    _say(MESSAGES[lang]["done"].format(**stats(menu)))
    _say(MESSAGES[lang]["files"])
    for path in paths:
        _say("  " + os.path.abspath(path))
    _say(MESSAGES[lang]["reminder"])
    return EXIT_OK


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        args.timeout = DEFAULT_TIMEOUT
    try:
        return run(args)
    except ExportError as error:
        print(error.message(args.lang), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print(MESSAGES[args.lang]["interrupted"], file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
