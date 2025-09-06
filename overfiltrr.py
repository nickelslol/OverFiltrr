import os
import sys
import json
import uuid
import yaml
import re
import operator
import logging
import logging.config
import time
from contextlib import contextmanager
from contextvars import ContextVar
import hmac
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union, Callable, Iterable

from flask import Flask, request
from waitress import serve

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from rapidfuzz import fuzz

# Rich console logging (required)
from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install as rich_tracebacks

# =========================
# App and global constants
# =========================
app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIRECTORY = os.path.join(SCRIPT_DIR, 'logs')
os.makedirs(LOG_DIRECTORY, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIRECTORY, 'script.log')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

REQUIRED_KEYS = [
    'OVERSEERR_BASEURL',
    'DRY_RUN',
    'API_KEYS',
    'TV_CATEGORIES',
    'MOVIE_CATEGORIES'
]

# Initialise logging as early as possible so any import-time logs go through Rich
# (defined below; safe forward reference in Python once function is declared)
# We'll define setup_logging next and invoke it immediately after its definition.

# =========================
# Logging setup (Rich optional)
# =========================

# Context vars used to enrich log records and drive indentation
CTX_REQUEST_ID: ContextVar[str] = ContextVar('request_id', default='')
CTX_CORRELATION_ID: ContextVar[str] = ContextVar('correlation_id', default='')
CTX_INDENT: ContextVar[int] = ContextVar('indent', default=0)
CTX_SCOPE: ContextVar[str] = ContextVar('scope', default='')

class ContextDefaultsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - logging plumbing
        if not hasattr(record, 'request_id'):
            record.request_id = CTX_REQUEST_ID.get()
        if not hasattr(record, 'correlation_id'):
            record.correlation_id = CTX_CORRELATION_ID.get()
        # keep scope/indent mainly for console formatting
        record.scope = getattr(record, 'scope', CTX_SCOPE.get())
        record.indent = getattr(record, 'indent', CTX_INDENT.get())
        return True

class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - pure formatting
        payload = {
            'ts': self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            'lvl': record.levelname,
            'msg': record.getMessage(),
            'rid': getattr(record, 'request_id', ''),
            'cid': getattr(record, 'correlation_id', ''),
            'scope': getattr(record, 'scope', ''),
        }
        if record.exc_info:
            payload['exc'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

# SUCCESS level for friendly end-of-request summary
SUCCESS = 25
logging.addLevelName(SUCCESS, 'OK')

RICH_CONSOLE: Optional[Console] = None

def setup_logging():
    global RICH_CONSOLE
    level = os.environ.get('LOG_LEVEL', 'INFO')
    # Remove any pre-existing handlers (Flask/waitress/basicConfig) to avoid duplicate prefixes
    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    # Ensure common third-party loggers don't install their own handlers
    for name in ('werkzeug', 'waitress', 'urllib3', 'requests'):
        try:
            lg = logging.getLogger(name)
            lg.handlers.clear()
            lg.propagate = True
        except Exception:
            pass

    # Always attach JSON file handler for machine logs
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel('DEBUG')
    file_handler.setFormatter(JsonLineFormatter())
    file_handler.addFilter(ContextDefaultsFilter())

    handlers: List[logging.Handler] = [file_handler]

    # Rich console is mandatory now
    RICH_CONSOLE = Console(soft_wrap=True)
    rich_tracebacks(show_locals=False)
    rh = RichHandler(
        console=RICH_CONSOLE,
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        show_level=True,
        markup=False,
        log_time_format='[%H:%M:%S.%f]'
    )
    rh.setLevel('DEBUG')
    rh.addFilter(ContextDefaultsFilter())
    handlers.append(rh)

    logging.basicConfig(level=level, handlers=handlers)

# Call setup_logging immediately so even early import-time logs use Rich
setup_logging()

# =========================
# Config loading and checks
# =========================
def load_config(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logging.critical(f"Configuration file 'config.yaml' not found at {path}.")
        sys.exit(1)
    except yaml.YAMLError as e:
        logging.critical(f"Error parsing 'config.yaml': {e}")
        sys.exit(1)

    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        logging.critical(f"Missing required configuration keys: {', '.join(missing)}")
        sys.exit(1)

    if not isinstance(config.get('DRY_RUN'), bool):
        logging.critical("DRY_RUN must be a boolean.")
        sys.exit(1)

    return config

# =========================
# Early CLI: generate a webhook token
# =========================
if __name__ == '__main__':
    argv = sys.argv[1:]
    gen_flags = {"--gen-webhook-token", "--gen-token", "gen-token", "gen-webhook-token"}
    if any(flag in argv for flag in gen_flags):
        try:
            import secrets
            size = 32
            if '--size' in argv:
                i = argv.index('--size')
                if i + 1 < len(argv):
                    size = int(argv[i + 1])
            print(secrets.token_urlsafe(size))
            sys.exit(0)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)

config = load_config(CONFIG_PATH)

OVERSEERR_BASEURL: str = config['OVERSEERR_BASEURL'].rstrip('/')
DRY_RUN: bool = config['DRY_RUN']
API_KEYS: dict = config['API_KEYS']
TV_CATEGORIES: dict = config['TV_CATEGORIES']
MOVIE_CATEGORIES: dict = config['MOVIE_CATEGORIES']

# Optional webhook security
WEBHOOK_CONFIG = config.get('WEBHOOK') or {}
WEBHOOK_TOKEN: Optional[str] = WEBHOOK_CONFIG.get('TOKEN') if isinstance(WEBHOOK_CONFIG, dict) else None
# Enforce token check only if a token is configured, to avoid breaking setups unexpectedly
ENFORCE_WEBHOOK_TOKEN: bool = bool(WEBHOOK_TOKEN)

# Approval behavior gate
ALLOW_AUTO_APPROVE: bool = bool(config.get('ALLOW_AUTO_APPROVE', True))

NOTIFIARR_CONFIG = config.get('NOTIFIARR') or {}
NOTIFIARR_APIKEY = NOTIFIARR_CONFIG.get('API_KEY')
NOTIFIARR_CHANNEL = NOTIFIARR_CONFIG.get('CHANNEL')
NOTIFIARR_SOURCE = NOTIFIARR_CONFIG.get('SOURCE', 'Overseerr')
NOTIFIARR_TIMEOUT = int(NOTIFIARR_CONFIG.get('TIMEOUT', 10))

# Optional server configuration
SERVER_CONFIG = config.get('SERVER') or {}
SERVER_HOST = SERVER_CONFIG.get('HOST', '0.0.0.0')
SERVER_PORT = int(SERVER_CONFIG.get('PORT', 12210))
SERVER_THREADS = int(SERVER_CONFIG.get('THREADS', 15))
SERVER_CONNECTION_LIMIT = int(SERVER_CONFIG.get('CONNECTION_LIMIT', 500))

# =========================
# Ratings normalisation
# =========================
RATING_ORDER = [
    "G", "TV-Y", "TV-G",
    "PG", "TV-Y7", "TV-PG",
    "PG-13", "TV-14",
    "M", "MA15+",
    "R", "TV-MA",
    "NC-17", "18"
]
RATING_INDEX = {name: i for i, name in enumerate(RATING_ORDER)}

RATING_NORMALISE = {
    "G": "G",
    "PG": "PG",
    "PG13": "PG-13",
    "PG-13": "PG-13",
    "R": "R",
    "R18": "R",
    "R18+": "R",
    "NC17": "NC-17",
    "NC-17": "NC-17",
    "18": "18",
    "X18": "NC-17",
    "X18+": "NC-17",
    "M": "M",
    "MA15": "MA15+",
    "MA15+": "MA15+",
    "TVY": "TV-Y",
    "TV-Y": "TV-Y",
    "TVG": "TV-G",
    "TV-G": "TV-G",
    "TVY7": "TV-Y7",
    "TV-Y7": "TV-Y7",
    "TVPG": "TV-PG",
    "TV-PG": "TV-PG",
    "TV14": "TV-14",
    "TV-14": "TV-14",
    "TVMA": "TV-MA",
    "TV-MA": "TV-MA",
}

def normalise_rating(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip().upper().replace(' ', '')
    if s in RATING_NORMALISE:
        return RATING_NORMALISE[s]
    return RATING_NORMALISE.get(s.replace('-', ''), None)

def rating_strictness(r: str) -> int:
    return RATING_INDEX.get(r, -1)

def pick_strictest(mapped: List[str]) -> Optional[str]:
    mapped_valid = [m for m in mapped if m in RATING_INDEX]
    if not mapped_valid:
        return None
    return max(mapped_valid, key=lambda x: RATING_INDEX[x])

# =========================
# Validation
# =========================
def validate_categories(categories: dict, media_type: str) -> bool:
    valid = True
    default_key = categories.get("default")
    if default_key is None:
        logging.error(f"No default category specified for {media_type}.")
        valid = False

    for name, data in categories.items():
        if name == "default":
            continue
        if not isinstance(data, dict):
            logging.error(f"Category '{name}' must be a mapping.")
            valid = False
            continue

        # Optional marker
        if 'is_anime' in data and not isinstance(data['is_anime'], bool):
            logging.error(f"Category '{name}' has non-boolean is_anime.")
            valid = False

        apply = data.get("apply", {})
        if "root_folder" not in apply:
            logging.error(f"Category '{name}' missing apply.root_folder.")
            valid = False

        id_key = "sonarr_id" if media_type == 'tv' else "radarr_id"
        if id_key not in apply or not isinstance(apply[id_key], int):
            logging.error(f"Category '{name}' missing integer apply.{id_key}.")
            valid = False

        default_profile_id = apply.get("default_profile_id")
        if not isinstance(default_profile_id, int):
            logging.error(f"Category '{name}' missing integer apply.default_profile_id.")
            valid = False

        if "weight" not in data or not isinstance(data["weight"], int):
            logging.error(f"Category '{name}' missing integer weight.")
            valid = False

        filters = data.get("filters", {})
        if filters and not isinstance(filters, dict):
            logging.error(f"Category '{name}' filters must be a mapping if present.")
            valid = False

        rat = data.get("ratings")
        if rat is not None:
            if not isinstance(rat, dict):
                logging.error(f"Category '{name}' ratings must be a mapping.")
                valid = False
            else:
                ceiling = rat.get("ceiling")
                prefer = rat.get("prefer")
                if ceiling is not None and normalise_rating(str(ceiling)) is None:
                    logging.error(f"Category '{name}' ratings.ceiling '{ceiling}' is not recognised.")
                    valid = False
                if prefer is not None and normalise_rating(str(prefer)) is None:
                    logging.error(f"Category '{name}' ratings.prefer '{prefer}' is not recognised.")
                    valid = False

    if default_key and default_key not in categories:
        logging.error(f"Default key '{default_key}' not found in categories for {media_type}.")
        valid = False
    return valid

def validate_configuration():
    tv_ok = validate_categories(TV_CATEGORIES, 'tv')
    movie_ok = validate_categories(MOVIE_CATEGORIES, 'movie')
    if not (tv_ok and movie_ok):
        logging.critical("Configuration validation failed.")
        sys.exit(1)
    logging.info("Configuration loaded and validated successfully.")

# =========================
# Requests session client
# =========================
def build_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=5,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=[429, 502, 503, 504],
        allowed_methods=frozenset(["GET", "PUT", "POST"])
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    sess.mount('http://', adapter)
    sess.mount('https://', adapter)
    return sess

session = build_session()

class OverseerrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 6.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.headers = {
            'accept': 'application/json',
            'X-Api-Key': api_key
        }

    def get_media(self, media_type: str, tmdb_id: Union[str, int]) -> dict:
        url = f"{self.base_url}/api/v1/{media_type}/{tmdb_id}"
        r = session.get(url, headers=self.headers, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Overseerr GET {url} failed {r.status_code}: {r.text}")
        return r.json()

    def put_request(self, request_id: int, payload: dict) -> None:
        url = f"{self.base_url}/api/v1/request/{request_id}"
        hdrs = {**self.headers, 'Content-Type': 'application/json'}
        r = session.put(url, headers=hdrs, json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Overseerr PUT {url} failed {r.status_code}: {r.text}")

    def approve_request(self, request_id: int) -> None:
        url = f"{self.base_url}/api/v1/request/{request_id}/approve"
        r = session.post(url, headers=self.headers, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Approve failed {r.status_code}: {r.text}")

    def get_request_status(self, request_id: int) -> Optional[int]:
        url = f"{self.base_url}/api/v1/request/{request_id}"
        r = session.get(url, headers=self.headers, timeout=self.timeout)
        if r.status_code != 200:
            return None
        return r.json().get('status')

overseerr_client = OverseerrClient(OVERSEERR_BASEURL, API_KEYS['overseerr'])

# =========================
# Ratings extraction from Overseerr
# =========================
def extract_all_certifications(overseerr_data: dict, media_type: str) -> List[str]:
    collected: List[str] = []

    if media_type == 'movie':
        results = (overseerr_data.get('releases') or {}).get('results', [])
        for country in results:
            for rd in country.get('release_dates', []) or []:
                cert = rd.get('certification')
                if cert:
                    norm = normalise_rating(cert)
                    if norm:
                        collected.append(norm)
    else:
        results = (overseerr_data.get('contentRatings') or {}).get('results', [])
        for entry in results:
            cert = entry.get('rating')
            if cert:
                norm = normalise_rating(cert)
                if norm:
                    collected.append(norm)

    return collected

def final_age_rating(overseerr_data: dict, media_type: str) -> Optional[str]:
    mapped = extract_all_certifications(overseerr_data, media_type)
    return pick_strictest(mapped)

# =========================
# Media extraction helpers
# =========================
def log_media_details(details: dict, header: str = "Media Details", request_id: str = "", correlation_id: str = ""):
    # Pretty table via Rich
    if RICH_CONSOLE is not None:
        try:
            from rich.table import Table
            from rich.panel import Panel
            table = Table(show_header=False, box=None, padding=(0,1))
            for k, v in details.items():
                if isinstance(v, list):
                    v = ', '.join(map(str, v))
                if k == "Overview" and isinstance(v, str) and len(v) > 80:
                    v = v[:77] + '…'
                table.add_row(str(k), str(v))
            RICH_CONSOLE.print(Panel(table, title=header, expand=False))
        except Exception:
            pass

def get_media_data(overseerr_data: dict, media_type: str, request_id: str, correlation_id: str):
    genres = [g.get('name', '') for g in overseerr_data.get('genres', [])]

    keywords_data = overseerr_data.get('keywords', [])
    if isinstance(keywords_data, list):
        keywords = [k.get('name', '') for k in keywords_data]
    else:
        keywords = [k.get('name', '') for k in (keywords_data.get('results', []) if keywords_data else [])]

    release_date_str = overseerr_data.get('releaseDate') or overseerr_data.get('firstAirDate')
    release_year = None
    if release_date_str:
        try:
            release_year = datetime.strptime(release_date_str, "%Y-%m-%d").year
        except ValueError:
            logging.error(f"Invalid release date format: {release_date_str}",
                          extra={'request_id': request_id, 'correlation_id': correlation_id})

    providers: List[str] = []
    wp = overseerr_data.get('watchProviders', [])
    if isinstance(wp, list):
        for entry in wp:
            for p in entry.get('flatrate', []) or []:
                providers.append(p.get('name') or p.get('provider_name'))
    else:
        for country_data in (wp.get('results', {}) or {}).values():
            for p in country_data.get('flatrate', []) or []:
                providers.append(p.get('provider_name'))

    production_companies = [pc.get('name', '') for pc in overseerr_data.get('productionCompanies', [])]
    networks = [n.get('name', '') for n in overseerr_data.get('networks', [])] if media_type == 'tv' else []
    original_language = overseerr_data.get('originalLanguage', '')
    status = overseerr_data.get('status', '')

    overview = overseerr_data.get('overview', 'No overview available.')
    imdbId = overseerr_data.get('imdbId', '')
    posterPath = overseerr_data.get('posterPath', '')

    age_rating = final_age_rating(overseerr_data, media_type)
    collected_raw = extract_all_certifications(overseerr_data, media_type)

    details = {
        "Streaming Providers": providers,
        "Genres": genres,
        "Keywords": keywords,
        "Production Companies": production_companies,
        "Networks": networks,
        "Original Language": original_language,
        "Status": status,
        "Overview": overview,
        "IMDb ID": imdbId,
        "Poster Path": posterPath,
        "Release Year": release_year if release_year else "Unknown",
        "Age Ratings Collected": collected_raw if collected_raw else "None",
        "Final Age Rating": age_rating if age_rating else "None"
    }
    log_media_details(details, header="Fetched Media Details From Overseerr",
                      request_id=str(request_id), correlation_id=correlation_id)

    return (genres, keywords, release_year, providers, production_companies, networks,
            original_language, status, overview, imdbId, posterPath, age_rating)

# =========================
# Anime gate (deterministic)
# =========================
def is_anime_hard(
    genres: List[str],
    keywords: List[str],
    original_language: str,
    production_companies: List[str],
    networks: List[str]
) -> bool:
    """
    Deterministic anime/donghua/webtoon gate:
      1) keyword in {"anime","donghua","manhwa","webtoon"}
      2) genre contains "Animation" AND original_language in {"ja","zh","ko"}
      3) studio or network in curated set (optional fallback)
    """
    kw_l = {k.strip().lower() for k in keywords if k}
    if {"anime", "donghua", "manhwa", "webtoon"} & kw_l:
        return True

    langs = {"ja", "zh", "ko"}
    has_animation = any((g or "").lower() == "animation" for g in genres)
    if has_animation and (original_language or "").lower() in langs:
        return True

    studios = {
        "toei animation", "mappa", "aniplex", "tencent penguin pictures",
        "bilibili", "haoliners animation league", "studio ghibli",
        "production i.g", "kyoto animation", "bones", "sunrise", "a-1 pictures", "gainax"
    }
    nets = {"tv tokyo", "fuji tv", "tbs", "nhk", "tv asahi", "nippon tv", "tooniverse"}

    if any((s or "").lower() in studios for s in production_companies):
        return True
    if any((n or "").lower() in nets for n in networks):
        return True

    return False

# =========================
# Scoring (+3/+2/+1) with ratings
# =========================
def _any_match(values: List[str], needles: List[str], threshold: int = 80) -> bool:
    if not values or not needles:
        return False
    values_l = [v.lower() for v in values]
    needles_l = [n.lower() for n in needles]
    for v in values_l:
        for n in needles_l:
            if fuzz.token_set_ratio(v, n) >= threshold:
                return True
    return False

def _provider_or_network_hit(providers: List[str], networks: List[str], filt: dict) -> bool:
    prov_needles = (filt.get("providers") or [])
    netw_needles = (filt.get("networks") or [])
    return _any_match(providers, prov_needles) or _any_match(networks, netw_needles)

def _apply_simple_ratings(cat_cfg: dict, final_rating: Optional[str]) -> Tuple[int, Optional[str]]:
    rat_cfg = cat_cfg.get("ratings")
    if not isinstance(rat_cfg, dict):
        return 0, None

    ceiling_raw = rat_cfg.get("ceiling")
    prefer_raw = rat_cfg.get("prefer")

    ceiling = normalise_rating(str(ceiling_raw)) if ceiling_raw is not None else None
    prefer = normalise_rating(str(prefer_raw)) if prefer_raw is not None else None

    if not final_rating:
        return 0, None

    if ceiling and final_rating in RATING_INDEX:
        if rating_strictness(final_rating) > rating_strictness(ceiling):
            return -999, f"blocked by ceiling > {ceiling}"

    if prefer and final_rating in RATING_INDEX:
        if rating_strictness(final_rating) <= rating_strictness(prefer):
            return 1, f"+1 prefer <= {prefer}"

    return 0, None

def _score_category(
    cat_cfg: dict,
    media_genres: List[str],
    media_keywords: List[str],
    media_providers: List[str],
    media_networks: List[str],
    final_rating: Optional[str],
) -> Tuple[int, List[str]]:
    filters = (cat_cfg.get("filters") or {})
    reasons: List[str] = []
    score = 0

    if _any_match(media_genres, filters.get("genres") or []):
        score += 3; reasons.append("+3 genre")
    if _any_match(media_keywords, filters.get("keywords") or []):
        score += 2; reasons.append("+2 keyword")
    if _provider_or_network_hit(media_providers, media_networks, filters):
        score += 1; reasons.append("+1 provider/network")

    delta, why = _apply_simple_ratings(cat_cfg, final_rating)
    if delta == -999:
        return -999, [why] if why else ["blocked by ceiling"]
    if delta:
        score += delta
        reasons.append(why)

    return score, reasons

def categorise_media_scored(
    genres: List[str],
    keywords: List[str],
    providers: List[str],
    networks: List[str],
    final_rating: Optional[str],
    media_type: str,
    *,
    request_id: str,
    correlation_id: str
) -> Tuple[Optional[str], Optional[str], List[Tuple[str, int, int, List[str]]], Optional[int]]:
    """
    Pick the category with the highest positive score.
    Tie-break by higher weight.
    Fall back to the configured default if nothing is positive.
    """
    categories = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
    default_key = categories.get("default")

    best_cat = None
    best_score = float("-inf")
    best_weight = float("-inf")
    scored_table = []

    for name, cfg in categories.items():
        if name == "default" or not isinstance(cfg, dict):
            continue

        weight = int(cfg.get("weight", 0))
        score, reasons = _score_category(
            cfg, genres, keywords, providers, networks, final_rating
        )
        scored_table.append((name, score, weight, reasons))

        if score <= 0:
            continue

        if (score > best_score) or (score == best_score and weight > best_weight):
            best_cat = name
            best_score = score
            best_weight = weight

    # Omit noisy full table summary in console; file JSON still captures events

    if best_cat:
        root_folder = categories[best_cat]["apply"]["root_folder"]
        logging.debug(
            f"Category scored winner: {best_cat} (score={best_score}, weight={best_weight})",
            extra={'request_id': request_id, 'correlation_id': correlation_id}
        )
        return root_folder, best_cat, scored_table, best_score

    if default_key in categories:
        root_folder = categories[default_key]["apply"]["root_folder"]
        logging.debug(
            f"No positive score. Falling back to default '{default_key}'.",
            extra={'request_id': request_id, 'correlation_id': correlation_id}
        )
        return root_folder, default_key, scored_table, None

    logging.error("No category matched and no default is defined.",
                  extra={'request_id': request_id, 'correlation_id': correlation_id})
    return None, None, scored_table, None

# =========================
# Request-scoped logging with timing
# =========================
class RequestContext:
    def __init__(self, *, request_id: str, correlation_id: str, media: Optional[dict] = None):
        self.request_id = str(request_id)
        self.correlation_id = correlation_id
        self.media = media or {}
        self.t0: Optional[float] = None

    def __enter__(self):  # pragma: no cover - convenience
        CTX_REQUEST_ID.set(self.request_id)
        CTX_CORRELATION_ID.set(self.correlation_id)
        CTX_SCOPE.set('request')
        CTX_INDENT.set(0)
        self.t0 = time.perf_counter()
        title = self.media.get('title')
        mtype = self.media.get('type')
        tmdb = self.media.get('tmdbId')
        user = self.media.get('user')
        logging.info(f"request • {title} ({mtype}) tmdb={tmdb} rid={self.request_id} user={user}")
        return self

    def __exit__(self, exc_type, exc, tb):  # pragma: no cover - convenience
        dur = (time.perf_counter() - self.t0) if self.t0 else 0.0
        if exc:
            logging.error(f"request failed ({dur:.2f} s)", exc_info=True)
        else:
            logging.log(SUCCESS, f"request • completed ({dur:.2f} s)")

    @contextmanager
    def step(self, name: str):  # pragma: no cover - convenience
        # increase indent for console readability
        CTX_INDENT.set(CTX_INDENT.get() + 1)
        CTX_SCOPE.set(name)
        t0 = time.perf_counter()
        try:
            yield
            ms = (time.perf_counter() - t0) * 1000
            prefix = ("  " * CTX_INDENT.get()) + ("↳ " if CTX_INDENT.get() else "")
            logging.info(f"{prefix}{name} … ok ({ms:.0f} ms)")
        except Exception:
            ms = (time.perf_counter() - t0) * 1000
            prefix = ("  " * CTX_INDENT.get()) + ("↳ " if CTX_INDENT.get() else "")
            logging.error(f"{prefix}{name} failed ({ms:.0f} ms)", exc_info=True)
            raise
        finally:
            CTX_INDENT.set(max(0, CTX_INDENT.get() - 1))
            CTX_SCOPE.set('request')

# =========================
# Condition / operator engine
# =========================


def _to_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]

def _is_number(x):
    try:
        float(x)
        return True
    except Exception:
        return False

def _coerce_num(x):
    try:
        return float(x)
    except Exception:
        return x

def _norm_str(x):
    return str(x).casefold()

    

def _compare_scalar(op: str, left, right, *, case_sensitive=False):
    """Compare two scalars with string/numeric awareness and rating helpers."""
    # Rating strictness support
    if op in {"rating_lt", "rating_lte", "rating_gt", "rating_gte", "rating_eq", "rating_ne"}:
        lhs = rating_strictness(str(left)) if left is not None else -1
        rhs = rating_strictness(str(right)) if right is not None else -1
        mapping = {
            "rating_lt": operator.lt, "rating_lte": operator.le,
            "rating_gt": operator.gt, "rating_gte": operator.ge,
            "rating_eq": operator.eq, "rating_ne": operator.ne
        }
        return mapping[op](lhs, rhs)

    # Numeric if both are numbers
    if _is_number(left) and _is_number(right):
        l, r = _coerce_num(left), _coerce_num(right)
        ops = {
            "lt": operator.lt, "lte": operator.le,
            "gt": operator.gt, "gte": operator.ge,
            "eq": operator.eq, "ne": operator.ne
        }
        if op in ops:
            return ops[op](l, r)

    # String logic (case-insensitive by default)
    l = str(left) if case_sensitive else _norm_str(left)
    r = str(right) if case_sensitive else _norm_str(right)

    if op in {"eq", "=="}:
        return l == r
    if op in {"ne", "!="}:
        return l != r
    if op == "contains":
        return r in l   # left contains right (substring)
    if op == "icontains":  # kept for explicitness, but default is already casefold
        return _norm_str(right) in _norm_str(left)
    if op == "startswith":
        return l.startswith(r)
    if op == "endswith":
        return l.endswith(r)
    if op in {"regex", "iregex"}:
        flags = 0 if op == "regex" else re.IGNORECASE
        return bool(re.search(right, str(left), flags=flags))
    if op == "fuzzy":
        # right can be {"value": "netflix", "threshold": 80} or just "netflix"
        threshold = 80
        if isinstance(right, dict):
            pattern = right.get("value", "")
            threshold = int(right.get("threshold", 80))
        else:
            pattern = right
        return fuzz.token_set_ratio(str(left), str(pattern)) >= threshold

    # Fall back to Python ops if provided
    ops = {
        "<": operator.lt, "<=": operator.le,
        ">": operator.gt, ">=": operator.ge,
        "in": lambda a, b: a in b,
        "not in": lambda a, b: a not in b,
    }
    if op in ops:
        # For string "in", keep case-insensitive behaviour for sequences of strings
        return ops[op](left, right)
    return False

def _match_list_field(field_values: list, op: str, target, *, case_sensitive=False, quantifier="any"):
    """
    field_values is a list from the context, target can be scalar or list
    quantifier: any | all | none
    """
    targets = _to_list(target)
    # Normalise strings for case-insensitive matching
    def norm(x): return x if case_sensitive else (_norm_str(x) if not isinstance(x, (int, float)) else x)
    field_norm = [norm(v) for v in field_values]
    target_norm = [norm(t) for t in targets]

    def cmp_one(fv, tv):
        # Map friendly list ops to scalar comparisons
        if op in {"in", "one_of"}:
            # does any target equal the field item
            return _compare_scalar("eq", fv, tv, case_sensitive=case_sensitive)
        if op in {"contains", "icontains", "startswith", "endswith", "regex", "iregex", "fuzzy"}:
            return _compare_scalar(op, fv, tv, case_sensitive=case_sensitive)
        # Allow using numeric/string comparisons against each list element
        if op in {"lt", "lte", "gt", "gte", "eq", "ne", "<", "<=", ">", ">=", "==", "!="}:
            return _compare_scalar(op, fv, tv, case_sensitive=case_sensitive)
        return False

    results = []
    for fv in field_norm:
        if any(cmp_one(fv, tv) for tv in target_norm):
            results.append(True)
        else:
            results.append(False)

    if quantifier == "all":
        # all list elements must match at least one target
        return all(results) if field_norm else False
    if quantifier == "none":
        return not any(results)
    # default any
    return any(results)

def _between_check(value, rng):
    # rng can be [min, max] or {"min": x, "max": y}
    if value is None:
        return False
    if isinstance(rng, dict):
        low = rng.get("min", None)
        high = rng.get("max", None)
    else:
        rng = list(rng)
        low = rng[0] if len(rng) > 0 else None
        high = rng[1] if len(rng) > 1 else None
    if low is None or high is None:
        return False
    if not _is_number(value) or not _is_number(low) or not _is_number(high):
        return False
    v = float(value)
    return float(low) <= v <= float(high)

def _eval_leaf_condition(field_value, spec: dict, *, case_sensitive=False):
    """
    spec example:
      { "in": ["Netflix", "Hulu"], "quantifier": "any" }
      { "lt": 2006 }
      { "between": [1990, 1999] }
    """
    # quantifier applies to list fields only
    quantifier = spec.get("quantifier", "any").lower() if isinstance(spec, dict) else "any"

    # Allow both symbol and friendly operator names
    alias = {
        "==": "eq", "!=": "ne",
        "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
        "one_of": "in", "not_in": "not in"
    }

    # Extract operator:value pairs, skipping meta keys
    meta_keys = {"quantifier", "case_sensitive"}
    items = [(k, v) for k, v in spec.items() if k not in meta_keys]
    if not items:
        return False

    # If the field is a list, use list matcher across all operator pairs
    if isinstance(field_value, list):
        for op_raw, target in items:
            op = alias.get(op_raw, op_raw)
            # Negative list membership
            if op in {"not in"}:
                # None of targets should appear in the list
                ok = not _match_list_field(field_value, "in", target, case_sensitive=case_sensitive, quantifier="any")
            elif op == "between":
                # between over a list is true if any element falls in range
                ok = any(_between_check(v, target) for v in field_value)
            else:
                ok = _match_list_field(field_value, op, target, case_sensitive=case_sensitive, quantifier=quantifier)
            if not ok:
                return False
        return True

    # Scalar field
    for op_raw, target in items:
        op = alias.get(op_raw, op_raw)
        if op == "between":
            if not _between_check(field_value, target):
                return False
        elif op in {"in", "one_of"}:
            ok = any(_compare_scalar("eq", field_value, t, case_sensitive=case_sensitive) for t in _to_list(target))
            if not ok:
                return False
        elif op == "not in":
            ok = all(not _compare_scalar("eq", field_value, t, case_sensitive=case_sensitive) for t in _to_list(target))
            if not ok:
                return False
        else:
            if not _compare_scalar(op, field_value, target, case_sensitive=case_sensitive):
                return False
    return True

  

def evaluate_condition(condition: dict, context: dict, logic: str = 'OR') -> bool:
    """
    Human-friendly rules with combinators and backwards compatibility.

    Supported shapes:
      1) Field map (existing style and new names):
         condition:
           networks: { in: ["Netflix","Network Ten"] }
           release_year: { lt: 2006 }
           original_language: { ne: "en" }

      2) Combinators:
         condition:
           ALL:
             - release_year: { lt: 2006 }
             - original_language: { ne: "en" }
           ANY:
             - networks: { in: ["Netflix", "Network Ten"] }
             - providers: { contains: "Netflix" }
           NONE:
             - keywords: { fuzzy: { value: "kids", threshold: 70 } }

      3) List quantifier:
         condition:
           networks: { in: ["Netflix","Hulu"], quantifier: "all" }

      4) Rating helpers:
         condition:
           final_rating: { rating_lte: "PG-13" }

      5) Between (numeric):
         condition:
           release_year: { between: [1990, 1999] }

    Top-level `logic` is kept for backwards compatibility with your caller.
    """
    if not condition:
        return True

    # Handle combinators if present
    any_block = condition.get("ANY")
    all_block = condition.get("ALL")
    none_block = condition.get("NONE")

    def eval_block(block):
        if not isinstance(block, list):
            return True
        results = []
        for item in block:
            if not isinstance(item, dict):
                results.append(False)
                continue
            # each item is a single-field condition map
            ok = True
            for k, spec in item.items():
                field_value = context.get(k)
                if field_value is None:
                    ok = False
                    break
                if isinstance(spec, dict):
                    ok = _eval_leaf_condition(field_value, spec, case_sensitive=bool(spec.get("case_sensitive", False)))
                else:
                    # If a plain scalar is provided, treat as equality
                    ok = _eval_leaf_condition(field_value, {"eq": spec})
                if not ok:
                    break
            results.append(ok)
        return results

    if any_block or all_block or none_block:
        any_res = eval_block(any_block) if any_block is not None else []
        all_res = eval_block(all_block) if all_block is not None else []
        none_res = eval_block(none_block) if none_block is not None else []

        ok_any = any(any_res) if any_res else True
        ok_all = all(all_res) if all_res else True
        ok_none = not any(none_res) if none_res else True
        return ok_any and ok_all and ok_none

    # Field map path (legacy and simple)
    results = []
    for key, spec in condition.items():
        field_value = context.get(key)
        if field_value is None:
            results.append(False)
            continue
        if isinstance(spec, dict):
            results.append(_eval_leaf_condition(field_value, spec, case_sensitive=bool(spec.get("case_sensitive", False))))
        else:
            # shorthand equality: key: value
            results.append(_eval_leaf_condition(field_value, {"eq": spec}))
    if logic.upper() == "AND":
        return all(results)
    return any(results)

def evaluate_quality_profile_rules(rules: Optional[List[dict]], context: dict) -> Optional[int]:
    if not rules:
        return None
    # lowest priority number wins
    sorted_rules = sorted(rules, key=lambda x: x.get('priority', 9999))
    for rule in sorted_rules:
        condition = rule.get('condition', {})
        profile_id = rule.get('profile_id')
        logic = rule.get('logic', 'OR').upper()
        if logic not in ('AND', 'OR'):
            logic = 'OR'
        try:
            if evaluate_condition(condition, context, logic):
                logging.info("Rule matched",
                             extra={'media_label': 'Priority',
                                    'media_value': rule.get('priority', 'N/A')})
                return profile_id
        except Exception as e:
            logging.error(f"Rule evaluation error: {e}")
    return None

# =========================
# Notifiarr
# =========================
def send_notifiarr_passthrough(payload: dict) -> None:
    if not NOTIFIARR_APIKEY:
        return
    try:
        url = f"https://notifiarr.com/api/v1/notification/passthrough/{NOTIFIARR_APIKEY}"
        r = session.post(
            url,
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            timeout=NOTIFIARR_TIMEOUT,
        )
        if r.status_code == 200:
            logging.info("Notification sent via Notifiarr.")
        else:
            logging.error(f"Notifiarr passthrough failed {r.status_code}: {r.text}")
    except Exception as e:
        logging.error(f"Notifiarr passthrough exception: {e}")

# =========================
# Discord payload builders
# =========================
def construct_movie_payload(media_title, request_username, status_text, target_root_folder,
                            best_match, request_id, overview, imdbId, posterPath):
    payload = {
        "notification": {
            "update": False,
            "name": "OverFiltrr",
            "event": f"Movie Request {status_text} - {request_id}"
        },
        "discord": {
            "color": "377E22" if status_text == "Approved" else "D65845",
            "ping": {"pingUser": 0, "pingRole": 0},
            "images": {"thumbnail": "", "image": ""},
            "text": {
                "title": f"🎬 **{media_title}**",
                "icon": "",
                "content": "",
                "description": overview,
                "fields": [
                    {"title": "Requested By", "text": request_username, "inline": False},
                    {"title": "Request Status", "text": status_text, "inline": True},
                    {"title": "Categorised As", "text": best_match, "inline": True},
                ],
                "footer": "Overseerr Notification"
            },
            "ids": {"channel": NOTIFIARR_CHANNEL}
        }
    }
    if status_text != "Approved":
        payload["discord"]["text"]["fields"].append({
            "title": "NOT APPROVED",
            "text": "This was not approved, check logs or settings.",
            "inline": False
        })
    if imdbId:
        payload["notification"]["url"] = f"https://www.imdb.com/title/{imdbId}/"
    if posterPath:
        payload["discord"]["images"]["thumbnail"] = f"{TMDB_IMAGE_BASE_URL}{posterPath}"
    return payload

def construct_tv_payload(media_title, request_username, status_text, target_root_folder,
                         best_match, request_id, seasons, overview, imdbId, posterPath):
    seasons_formatted = ', '.join(str(s) for s in seasons) if seasons else 'All Seasons'
    payload = {
        "notification": {
            "update": False,
            "name": "OverFiltrr",
            "event": f"TV Request {status_text} - {request_id}"
        },
        "discord": {
            "color": "377E22" if status_text == "Approved" else "D65845",
            "ping": {"pingUser": 0, "pingRole": 0},
            "images": {"thumbnail": "", "image": ""},
            "text": {
                "title": f"📺 **{media_title}**",
                "icon": "",
                "content": "",
                "description": overview,
                "fields": [
                    {"title": "Requested By", "text": request_username, "inline": False},
                    {"title": "Request Status", "text": status_text, "inline": True},
                    {"title": "Seasons", "text": seasons_formatted, "inline": True},
                    {"title": "Categorised As", "text": best_match, "inline": True},
                ],
                "footer": "Overseerr Notification"
            },
            "ids": {"channel": NOTIFIARR_CHANNEL}
        }
    }
    if status_text != "Approved":
        payload["discord"]["text"]["fields"].append({
            "title": "NOT APPROVED",
            "text": "This was not approved, check logs or settings.",
            "inline": False
        })
    if imdbId:
        payload["notification"]["url"] = f"https://www.imdb.com/title/{imdbId}/"
    if posterPath:
        payload["discord"]["images"]["thumbnail"] = f"{TMDB_IMAGE_BASE_URL}{posterPath}"
    return payload

# =========================
# Flask routes
# =========================
@app.route('/health', methods=['GET'])
def health():
    return {'ok': True}, 200

@app.route('/webhook', methods=['POST'])
def handle_request():
    correlation_id = str(uuid.uuid4())

    # Optional token authentication for webhook requests
    if ENFORCE_WEBHOOK_TOKEN:
        provided = (request.headers.get('X-Webhook-Token', '') or '').strip()

        # Fallback: some clients can't set HTTP headers from their webhook UI.
        # Try to read a token from JSON body at { "headers": { "X-Webhook-Token": "..." } }
        if not provided:
            try:
                body_probe = request.get_json(silent=True) or {}
                if isinstance(body_probe, dict):
                    hdrs = body_probe.get('headers') or {}
                    if isinstance(hdrs, dict):
                        provided = (hdrs.get('X-Webhook-Token') or hdrs.get('x-webhook-token') or '').strip()
            except Exception:
                # Ignore JSON errors here; full parsing happens later with proper error handling
                pass

        if not provided or not hmac.compare_digest(str(provided), str(WEBHOOK_TOKEN)):
            logging.warning(
                "Unauthorized webhook: missing or invalid token",
                extra={'correlation_id': correlation_id}
            )
            return ('Unauthorized', 401)

    try:
        request_data = request.get_json(force=True, silent=False)
    except Exception:
        logging.error("Invalid JSON payload", extra={'correlation_id': correlation_id})
        return ('Bad Request', 400)

    notification_type = (request_data or {}).get('notification_type', '') or ''
    req = (request_data or {}).get('request', {}) or {}
    request_id = req.get('request_id') or ''
    extra = {'request_id': str(request_id), 'correlation_id': correlation_id}

    if notification_type == 'TEST_NOTIFICATION':
        logging.info("Test payload received", extra=extra)
        return ('Test payload received', 200)

    if notification_type == 'MEDIA_PENDING':
        process_request(request_data, correlation_id)
        return ('accepted', 202)

    logging.warning(f"Unhandled notification type: {notification_type}", extra=extra)
    return ('Unhandled notification type', 400)

# =========================
# Core processing
# =========================
def _pick_marked_anime_category(categories: dict) -> Optional[str]:
    """Return the anime category name with highest weight, if any."""
    marked = [(name, cfg) for name, cfg in categories.items()
              if name != "default" and isinstance(cfg, dict) and cfg.get("is_anime") is True]
    if not marked:
        return None
    # Highest weight wins
    marked.sort(key=lambda item: int(item[1].get("weight", 0)), reverse=True)
    return marked[0][0]

def process_request(request_data: dict, correlation_id: str) -> None:
    req = request_data.get('request') or {}
    media = request_data.get('media') or {}

    request_username = req.get('requestedBy_username', 'unknown')
    request_id = req.get('request_id')
    media_tmdbid = media.get('tmdbId')
    media_type = media.get('media_type')
    media_title = request_data.get('subject', 'Unknown Title')

    extra = {'request_id': str(request_id), 'correlation_id': correlation_id}

    if not all([request_id, media_tmdbid, media_type]):
        logging.error("Payload missing request_id or tmdbId or media_type", extra=extra)
        return

    media_meta = {"title": media_title, "type": media_type, "tmdbId": media_tmdbid, "user": request_username}

    with RequestContext(request_id=str(request_id), correlation_id=correlation_id, media=media_meta) as rc:
        # Fetch media details
        try:
            with rc.step("fetch details"):
                overseerr_data = overseerr_client.get_media(media_type, media_tmdbid)
        except Exception:
            return

        (genres, keywords, release_year, providers, production_companies, networks,
         original_language, status, overview, imdbId, posterPath, age_rating) = get_media_data(
            overseerr_data, media_type, str(request_id), correlation_id
        )

        with rc.step("age rating"):
            logging.info(f"age rating: {age_rating if age_rating else 'Unknown'}")

        # Anime gate
        target_root_folder = None
        best_match = None
        with rc.step("anime gate"):
            try:
                categories_for_type = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
                if is_anime_hard(
                    genres=genres,
                    keywords=keywords,
                    original_language=original_language,
                    production_companies=production_companies,
                    networks=networks
                ):
                    anime_cat = _pick_marked_anime_category(categories_for_type)
                    if anime_cat:
                        best_match = anime_cat
                        target_root_folder = categories_for_type[anime_cat]["apply"]["root_folder"]
                        logging.info(f"anime: yes → {best_match}")
                    else:
                        logging.info("anime: yes (no marked category)")
                else:
                    logging.info("anime: no")
            except Exception as e:
                logging.error(f"check failed: {e}")

        # If still no route, score categories
        with rc.step("scoring"):
            if not target_root_folder or not best_match:
                target_root_folder, best_match, scored_table, best_score = categorise_media_scored(
                    genres, keywords, providers, networks, age_rating,
                    media_type,
                    request_id=str(request_id), correlation_id=correlation_id
                )
                if not target_root_folder or not best_match:
                    logging.error("no matching category found")
                    return
                try:
                    ordered = sorted(scored_table, key=lambda x: (x[1], x[2]), reverse=True)
                    top = [f"{n}(s={s})" for (n, s, _w, _r) in ordered[:3]]
                    logging.info(f"winner={best_match}{f'(s={best_score})' if best_score is not None else ''} top={top}")
                except Exception:
                    pass

        # Decision apply
        with rc.step("decision"):
            categories = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
            folder_data = categories.get(best_match) or {}
            apply_data = folder_data.get('apply') or {}
            default_profile_id = apply_data.get('default_profile_id')
            quality_profile_rules = folder_data.get('quality_profile_rules') or []

            context = {
                'release_year': release_year,
                'original_language': original_language,
                'providers': providers,
                'production_companies': production_companies,
                'networks': networks,
                'status': status,
                'genres': genres,
                'keywords': keywords,
                'media_type': media_type,
                'requested_by': request_username,
                'final_rating': age_rating,
            }

            profile_id = evaluate_quality_profile_rules(quality_profile_rules, context) or default_profile_id
            if not isinstance(profile_id, int):
                logging.error("Could not determine a valid profile id")
                return

            put_data: Dict[str, Any] = {}
            target_name = apply_data.get('app_name', 'Unknown App')

            if media_type == 'movie':
                radarr_id = apply_data.get('radarr_id')
                if radarr_id is None:
                    logging.error(f"Category '{best_match}' missing radarr_id")
                    return
                put_data = {
                    "mediaType": "movie",
                    "rootFolder": target_root_folder,
                    "serverId": radarr_id,
                    "profileId": profile_id
                }
            elif media_type == 'tv':
                sonarr_id = apply_data.get('sonarr_id')
                if sonarr_id is None:
                    logging.error(f"Category '{best_match}' missing sonarr_id")
                    return
                seasons = []
                try:
                    extra_list = request_data.get('extra') or []
                    if extra_list and isinstance(extra_list[0].get('value'), str):
                        seasons = [int(s.strip()) for s in extra_list[0]['value'].split(',') if s.strip().isdigit()]
                except Exception:
                    seasons = []
                put_data = {
                    "mediaType": "tv",
                    "seasons": seasons,
                    "rootFolder": target_root_folder,
                    "serverId": sonarr_id,
                    "profileId": profile_id
                }
            else:
                logging.error(f"Unsupported media_type '{media_type}'")
                return

            logging.info(
                f"Decision: category={best_match} app={target_name} root='{put_data.get('rootFolder')}' profile={profile_id}"
            )

            # Update request and (optionally) approve
            if DRY_RUN:
                logging.warning("[DRY RUN] Would PUT request and %s",
                                "approve" if ALLOW_AUTO_APPROVE else "not approve")
            else:
                try:
                    current_status = overseerr_client.get_request_status(request_id)
                    if current_status == 2:
                        logging.info(f"Request {request_id} already approved, updating only")

                    overseerr_client.put_request(request_id, put_data)

                    if ALLOW_AUTO_APPROVE:
                        if current_status != 2:
                            overseerr_client.approve_request(request_id)
                            logging.info(f"Request {request_id} approved")
                        else:
                            logging.info(f"Request {request_id} remained approved")
                    else:
                        logging.info("Auto-approve disabled; request left pending")
                except Exception as e:
                    logging.error(f"Failed to update or approve: {e}")
                    return

        # Notification step (optional)
        if NOTIFIARR_APIKEY:
            with rc.step("notify"):
                status_code = overseerr_client.get_request_status(request_id)
                status_map = {1: 'Pending Approval', 2: 'Approved', 3: 'Declined'}
                status_text = status_map.get(status_code, 'Unknown Status')

                if media_type == 'movie':
                    payload = construct_movie_payload(
                        media_title=media_title,
                        request_username=request_username,
                        status_text=status_text,
                        target_root_folder=target_root_folder,
                        request_id=request_id,
                        overview=overview,
                        imdbId=imdbId,
                        posterPath=posterPath,
                        best_match=best_match
                    )
                else:
                    payload = construct_tv_payload(
                        media_title=media_title,
                        request_username=request_username,
                        status_text=status_text,
                        target_root_folder=target_root_folder,
                        request_id=request_id,
                        seasons=put_data.get('seasons', []),
                        overview=overview,
                        imdbId=imdbId,
                        posterPath=posterPath,
                        best_match=best_match
                    )
                send_notifiarr_passthrough(payload)
        else:
            logging.debug("No Notifiarr API key present, skipping notification", extra=extra)

    # Final status and notification
    status_code = overseerr_client.get_request_status(request_id)
    status_map = {1: 'Pending Approval', 2: 'Approved', 3: 'Declined'}
    status_text = status_map.get(status_code, 'Unknown Status')

    if NOTIFIARR_APIKEY:
        if media_type == 'movie':
            payload = construct_movie_payload(
                media_title=media_title,
                request_username=request_username,
                status_text=status_text,
                target_root_folder=target_root_folder,
                request_id=request_id,
                overview=overview,
                imdbId=imdbId,
                posterPath=posterPath,
                best_match=best_match
            )
        else:
            payload = construct_tv_payload(
                media_title=media_title,
                request_username=request_username,
                status_text=status_text,
                target_root_folder=target_root_folder,
                request_id=request_id,
                seasons=put_data.get('seasons', []),
                overview=overview,
                imdbId=imdbId,
                posterPath=posterPath,
                best_match=best_match
            )
        send_notifiarr_passthrough(payload)
    else:
        logging.debug("No Notifiarr API key present, skipping notification", extra=extra)

# =========================
# Main
# =========================
if __name__ == '__main__':
    validate_configuration()
    logging.info(
        f"Configuration valid. Starting server on {SERVER_HOST}:{SERVER_PORT}"
    )
    serve(
        app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        threads=SERVER_THREADS,
        connection_limit=SERVER_CONNECTION_LIMIT,
    )
