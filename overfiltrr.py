import os
import sys
import json
import uuid
import yaml
import re
import operator
import argparse
import hmac
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union, Callable, Iterable

from flask import Flask, request
from waitress import serve

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from rapidfuzz import fuzz

# Logging setup (optional, with graceful fallback)
try:
    from logging_setup import (
        init_logging,
        get_logger,
        set_context,
        new_request_card,
        end_request_card,
        get_current_card,
        render_startup_card,
    )
except Exception:
    def init_logging(cfg: dict):
        pass
    def get_logger(name: str = "overfiltrr"):
        import logging
        return logging.getLogger(name)
    def set_context(**kwargs):
        pass
    def new_request_card(cfg: dict):
        return None
    def end_request_card():
        pass
    def get_current_card():
        return None
    def render_startup_card(**kwargs):
        pass

# =========================
# App and global constants
# =========================
app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

REQUIRED_KEYS = [
    'OVERSEERR_BASEURL',
    'DRY_RUN',
    'API_KEYS',
    'TV_CATEGORIES',
    'MOVIE_CATEGORIES'
]


# =========================
# Config loading and checks
# =========================
def load_config(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"Configuration file 'config.yaml' not found at {path}.", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing 'config.yaml': {e}", file=sys.stderr)
        sys.exit(1)

    missing = [k for k in REQUIRED_KEYS if k not in config]
    if missing:
        print(f"Missing required configuration keys: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(config.get('DRY_RUN'), bool):
        print("DRY_RUN must be a boolean.", file=sys.stderr)
        sys.exit(1)

    return config

# =========================
# Early CLI: generate a webhook token
"""
Runtime configuration (initialised in init_runtime()).
These globals are populated when the server starts or when commands run.
"""
OVERSEERR_BASEURL: Optional[str] = None
DRY_RUN: bool = True
API_KEYS: Dict[str, Any] = {}
TV_CATEGORIES: Dict[str, Any] = {}
MOVIE_CATEGORIES: Dict[str, Any] = {}

# Keep full runtime config for logging/console preferences
RUNTIME_CFG: Dict[str, Any] = {}

WEBHOOK_TOKEN: Optional[str] = None
ENFORCE_WEBHOOK_TOKEN: bool = False
ALLOW_AUTO_APPROVE: bool = True

NOTIFIARR_APIKEY: Optional[str] = None
NOTIFIARR_CHANNEL: Optional[str] = None
NOTIFIARR_TIMEOUT: int = 10

SERVER_HOST: str = '0.0.0.0'
SERVER_PORT: int = 12210
SERVER_THREADS: int = 15
SERVER_CONNECTION_LIMIT: int = 500

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
        valid = False

    for name, data in categories.items():
        if name == "default":
            continue
        if not isinstance(data, dict):
            valid = False
            continue

        # Optional marker
        if 'is_anime' in data and not isinstance(data['is_anime'], bool):
            valid = False

        apply = data.get("apply", {})
        if "root_folder" not in apply:
            valid = False

        id_key = "sonarr_id" if media_type == 'tv' else "radarr_id"
        if id_key not in apply or not isinstance(apply[id_key], int):
            valid = False

        default_profile_id = apply.get("default_profile_id")
        if not isinstance(default_profile_id, int):
            valid = False

        if "weight" not in data or not isinstance(data["weight"], int):
            valid = False

        filters = data.get("filters", {})
        if filters and not isinstance(filters, dict):
            valid = False

        rat = data.get("ratings")
        if rat is not None:
            if not isinstance(rat, dict):
                valid = False
            else:
                ceiling = rat.get("ceiling")
                prefer = rat.get("prefer")
                if ceiling is not None and normalise_rating(str(ceiling)) is None:
                    valid = False
                if prefer is not None and normalise_rating(str(prefer)) is None:
                    valid = False

    if default_key and default_key not in categories:
        valid = False
    return valid

def validate_configuration():
    tv_ok = validate_categories(TV_CATEGORIES, 'tv')
    movie_ok = validate_categories(MOVIE_CATEGORIES, 'movie')
    if not (tv_ok and movie_ok):
        sys.exit(1)

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

overseerr_client: Optional[OverseerrClient] = None

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
            pass

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
) -> Tuple[Optional[str], Optional[str], List[Tuple[str, int, int, List[str]]]]:
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

    # scoring table prepared

    if best_cat:
        root_folder = categories[best_cat]["apply"]["root_folder"]
        return root_folder, best_cat, scored_table

    if default_key in categories:
        root_folder = categories[default_key]["apply"]["root_folder"]
        return root_folder, default_key, scored_table

    return None, None, scored_table

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
                return profile_id
        except Exception as e:
            pass
    return None

# =========================
# Notifiarr
# =========================
def send_notifiarr_passthrough(payload: dict) -> None:
    if not NOTIFIARR_APIKEY:
        return
    try:
        url = f"https://notifiarr.com/api/v1/notification/passthrough/{NOTIFIARR_APIKEY}"
        r = session.post(url, json=payload, timeout=NOTIFIARR_TIMEOUT)
        # silent on success/failure
    except Exception as e:
        pass

# =========================
# Discord payload builders
# =========================
def construct_movie_payload(media_title, request_username, status_text,
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
            "text": "This was not approved, check settings.",
            "inline": False
        })
    if imdbId:
        payload["notification"]["url"] = f"https://www.imdb.com/title/{imdbId}/"
    if posterPath:
        payload["discord"]["images"]["thumbnail"] = f"{TMDB_IMAGE_BASE_URL}{posterPath}"
    return payload

def construct_tv_payload(media_title, request_username, status_text,
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
            "text": "This was not approved, check settings.",
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

    # Seed request context and prepare a console card
    try:
        set_context(correlation_id=correlation_id)
    except Exception:
        pass

    # Parse JSON once (accept even if content-type is off)
    request_data = request.get_json(force=True, silent=True)

    # Optional token authentication for webhook requests
    if ENFORCE_WEBHOOK_TOKEN:
        provided = (request.headers.get('X-Webhook-Token', '') or '').strip()
        if not provided and isinstance(request_data, dict):
            hdrs = request_data.get('headers') or {}
            if isinstance(hdrs, dict):
                provided = (hdrs.get('X-Webhook-Token') or hdrs.get('x-webhook-token') or '').strip()

        if not provided or not hmac.compare_digest(str(provided), str(WEBHOOK_TOKEN)):
            return ('Unauthorized', 401)

    if not isinstance(request_data, dict):
        return ('Bad Request', 400)

    notification_type = (request_data or {}).get('notification_type', '') or ''
    req = (request_data or {}).get('request', {}) or {}
    request_id = req.get('request_id') or ''
    media = (request_data or {}).get('media', {}) or {}
    media_type = media.get('media_type') or ''
    tmdb_id = media.get('tmdbId') or ''
    user = req.get('requestedBy_username') or ''
    subject = (request_data or {}).get('subject') or ''

    # Prepare a live card for this request (if configured)
    try:
        ccfg = ((RUNTIME_CFG.get('LOGGING') or {}).get('CONSOLE') or {}) if isinstance(RUNTIME_CFG, dict) else {}
        card = new_request_card(ccfg)
        if card:
            card.set_meta(title=subject, media_type=media_type, correlation_id=correlation_id,
                          request_id=str(request_id or ''), tmdb_id=str(tmdb_id or ''), user=user,
                          dry_run=DRY_RUN)
    except Exception:
        card = None
    if notification_type == 'TEST_NOTIFICATION':
        return ('Test payload received', 200)

    if notification_type == 'MEDIA_PENDING':
        logger = get_logger("overfiltrr")
        try:
            if card:
                with card.live():
                    logger.info("request.start", extra={"event": "request.start"})
                    process_request(request_data, correlation_id)
                    logger.info("request.end", extra={"event": "request.end"})
            else:
                logger.info("request.start", extra={"event": "request.start"})
                process_request(request_data, correlation_id)
                logger.info("request.end", extra={"event": "request.end"})
        except Exception:
            try:
                if card:
                    card.set_status('failed')
            except Exception:
                pass
        finally:
            try:
                end_request_card()
            except Exception:
                pass
        return ('accepted', 202)

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

    if not all([request_id, media_tmdbid, media_type]):
        try:
            set_context(request_id=request_id, tmdb_id=media_tmdbid, media_type=media_type, user=request_username)
        except Exception:
            pass
        card = get_current_card()
        if card:
            card.set_status('aborted')
        return

    # Fetch media details
    card = get_current_card()
    logger = get_logger("overfiltrr")
    try:
        set_context(request_id=request_id, tmdb_id=media_tmdbid, media_type=media_type, user=request_username)
    except Exception:
        pass
    try:
        if card:
            with card.step("Fetch media details") as s:
                overseerr_data = overseerr_client.get_media(media_type, media_tmdbid)
            logger.info("step", extra={"event": "step", "step": "Fetch media details", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
        else:
            overseerr_data = overseerr_client.get_media(media_type, media_tmdbid)
    except Exception as e:
        if card:
            card.set_status('failed')
        logger.error("fetch_failed", extra={"event": "error", "exc_type": type(e).__name__, "exc_msg": str(e)})
        return

    (genres, keywords, release_year, providers, production_companies, networks,
     original_language, status, overview, imdbId, posterPath, age_rating) = get_media_data(
        overseerr_data, media_type, str(request_id), correlation_id
    )

    # ---------- Deterministic Anime Gate ----------
    target_root_folder = None
    best_match = None

    try:
        if card:
            with card.step("Determine anime/non-anime") as s:
                if is_anime_hard(
                    genres=genres,
                    keywords=keywords,
                    original_language=original_language,
                    production_companies=production_companies,
                    networks=networks
                ):
                    categories = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
                    anime_cat = _pick_marked_anime_category(categories)
                    if anime_cat:
                        best_match = anime_cat
                        target_root_folder = categories[anime_cat]["apply"]["root_folder"]
            logger.info("step", extra={"event": "step", "step": "Determine anime/non-anime", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
        else:
            if is_anime_hard(
                genres=genres,
                keywords=keywords,
                original_language=original_language,
                production_companies=production_companies,
                networks=networks
            ):
                categories = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
                anime_cat = _pick_marked_anime_category(categories)
                if anime_cat:
                    best_match = anime_cat
                    target_root_folder = categories[anime_cat]["apply"]["root_folder"]
    except Exception as e:
        logger.error("anime_gate_error", extra={"event": "error", "exc_type": type(e).__name__, "exc_msg": str(e)})

    # If no anime route, run the scorer
    if not target_root_folder or not best_match:
        if card:
            with card.step("Score categories") as s:
                target_root_folder, best_match, scored_table = categorise_media_scored(
                    genres, keywords, providers, networks, age_rating,
                    media_type,
                    request_id=str(request_id), correlation_id=correlation_id
                )
                try:
                    card.set_scoring(scored_table)
                except Exception:
                    pass
            logger.info("step", extra={"event": "step", "step": "Score categories", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
        else:
            target_root_folder, best_match, scored_table = categorise_media_scored(
                genres, keywords, providers, networks, age_rating,
                media_type,
                request_id=str(request_id), correlation_id=correlation_id
            )

    if not target_root_folder or not best_match:
        if card:
            card.set_status('rejected')
        return

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

    if card:
        with card.step("Evaluate quality profile rules") as s:
            profile_id = evaluate_quality_profile_rules(quality_profile_rules, context) or default_profile_id
        logger.info("step", extra={"event": "step", "step": "Evaluate quality profile rules", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
    else:
        profile_id = evaluate_quality_profile_rules(quality_profile_rules, context) or default_profile_id
    if not isinstance(profile_id, int):
        if card:
            card.set_status('aborted')
        return

    put_data: Dict[str, Any] = {}
    target_name = apply_data.get('app_name', 'Unknown App')

    if media_type == 'movie':
        radarr_id = apply_data.get('radarr_id')
        if radarr_id is None:
            if card:
                card.set_status('aborted')
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
            if card:
                card.set_status('aborted')
            return
        # Seasons parsing
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
        if card:
            card.set_status('aborted')
        return

    # decision computed: category, target app, root, profile

    # Update request and (optionally) approve
    if DRY_RUN:
        if card:
            with card.step("Apply decision (dry-run)") as s:
                _ = True
            logger.info("step", extra={"event": "step", "step": "Apply decision (dry-run)", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
    else:
        try:
            if card:
                with card.step("Update request") as s:
                    current_status = overseerr_client.get_request_status(request_id)
                    if current_status == 2:
                        pass
                    overseerr_client.put_request(request_id, put_data)
                logger.info("step", extra={"event": "step", "step": "Update request", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
                if ALLOW_AUTO_APPROVE:
                    with card.step("Approve request") as s2:
                        if current_status != 2:
                            overseerr_client.approve_request(request_id)
                    logger.info("step", extra={"event": "step", "step": "Approve request", "ok": s2.ok, "delta_ms": s2.delta_ms, "cumulative_ms": s2.cumulative_ms})
            else:
                current_status = overseerr_client.get_request_status(request_id)
                if current_status == 2:
                    pass
                overseerr_client.put_request(request_id, put_data)
                if ALLOW_AUTO_APPROVE and current_status != 2:
                    overseerr_client.approve_request(request_id)
        except Exception as e:
            if card:
                card.set_status('failed')
            logger.error("update_failed", extra={"event": "error", "exc_type": type(e).__name__, "exc_msg": str(e)})
            return

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
                request_id=request_id,
                seasons=put_data.get('seasons', []),
                overview=overview,
                imdbId=imdbId,
                posterPath=posterPath,
                best_match=best_match
            )
        if card:
            with card.step("Send notification") as s:
                send_notifiarr_passthrough(payload)
            logger.info("step", extra={"event": "step", "step": "Send notification", "ok": s.ok, "delta_ms": s.delta_ms, "cumulative_ms": s.cumulative_ms})
        else:
            send_notifiarr_passthrough(payload)
    else:
        pass

    # Final decision banner
    card = get_current_card()
    if card:
        card.set_status('accepted')
        card.set_decision(f"{status_text.upper()}    root={target_root_folder}    category={best_match}    profile={profile_id}")

    # File log: decision event with key fields
    try:
        score_total = None
        try:
            # Use scored_table if available to find chosen category score
            for name, sc, wt, reasons in (locals().get('scored_table') or []):
                if name == best_match:
                    score_total = sc
                    break
        except Exception:
            score_total = None
        logger.info("decision", extra={
            "event": "decision",
            "decision": status_text,
            "category": best_match,
            "root": target_root_folder,
            "profile_id": profile_id,
            "score_total": score_total,
        })
    except Exception:
        pass

# =========================
# Main
# =========================
def init_runtime(cfg_path: str = CONFIG_PATH) -> dict:
    """Load configuration and initialise globals/clients."""
    global OVERSEERR_BASEURL, DRY_RUN, API_KEYS, TV_CATEGORIES, MOVIE_CATEGORIES
    global WEBHOOK_TOKEN, ENFORCE_WEBHOOK_TOKEN, ALLOW_AUTO_APPROVE
    global NOTIFIARR_APIKEY, NOTIFIARR_CHANNEL, NOTIFIARR_TIMEOUT
    global SERVER_HOST, SERVER_PORT, SERVER_THREADS, SERVER_CONNECTION_LIMIT
    global overseerr_client

    cfg = load_config(cfg_path)
    # Initialise logging early
    try:
        init_logging(cfg)
    except Exception:
        pass
    global RUNTIME_CFG
    RUNTIME_CFG = cfg

    OVERSEERR_BASEURL = str(cfg['OVERSEERR_BASEURL']).rstrip('/')
    DRY_RUN = bool(cfg['DRY_RUN'])
    API_KEYS = cfg['API_KEYS'] or {}
    TV_CATEGORIES = cfg['TV_CATEGORIES'] or {}
    MOVIE_CATEGORIES = cfg['MOVIE_CATEGORIES'] or {}

    # Webhook
    wcfg = cfg.get('WEBHOOK') or {}
    WEBHOOK_TOKEN = wcfg.get('TOKEN') if isinstance(wcfg, dict) else None
    ENFORCE_WEBHOOK_TOKEN = bool(WEBHOOK_TOKEN)

    # Behaviour
    ALLOW_AUTO_APPROVE = bool(cfg.get('ALLOW_AUTO_APPROVE', True))

    # Notifiarr
    ncfg = cfg.get('NOTIFIARR') or {}
    NOTIFIARR_APIKEY = ncfg.get('API_KEY')
    NOTIFIARR_CHANNEL = ncfg.get('CHANNEL')
    NOTIFIARR_TIMEOUT = int(ncfg.get('TIMEOUT', 10))

    # Server
    scfg = cfg.get('SERVER') or {}
    SERVER_HOST = scfg.get('HOST', '0.0.0.0')
    SERVER_PORT = int(scfg.get('PORT', 12210))
    SERVER_THREADS = int(scfg.get('THREADS', 15))
    SERVER_CONNECTION_LIMIT = int(scfg.get('CONNECTION_LIMIT', 500))

    # Clients
    overseerr_client = OverseerrClient(OVERSEERR_BASEURL, API_KEYS['overseerr'])

    return cfg


def main(argv: Optional[List[str]] = None) -> int:

    parser = argparse.ArgumentParser(prog='overfiltrr', description='Overseerr request filter/categoriser')
    parser.add_argument('-c', '--config', default=CONFIG_PATH, help='Path to config.yaml')
    sub = parser.add_subparsers(dest='cmd')

    # gen-webhook-token
    p_gen = sub.add_parser('gen-token', help='Generate a webhook token')
    p_gen.add_argument('--size', type=int, default=32, help='Token size for secrets.token_urlsafe')

    # list-ids
    p_ids = sub.add_parser('list-ids', help='List Radarr/Sonarr server IDs from Overseerr settings')
    p_ids.add_argument('--svc', '--service', dest='services', action='append', choices=['radarr', 'sonarr'], help='Service to list (can repeat)')

    # serve
    sub.add_parser('serve', help='Start the webhook server (default)')

    args = parser.parse_args(argv)

    if args.cmd == 'gen-token':
        try:
            import secrets
            print(secrets.token_urlsafe(args.size))
            return 0
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    if args.cmd == 'list-ids':
        try:
            cfg = load_config(args.config)
            base = str(cfg.get('OVERSEERR_BASEURL', '')).rstrip('/')
            api_key = (cfg.get('API_KEYS') or {}).get('overseerr')
            if not base or not api_key:
                print("Missing OVERSEERR_BASEURL or API_KEYS.overseerr in config.yaml", file=sys.stderr)
                return 2

            headers = {'X-Api-Key': api_key, 'accept': 'application/json'}

            def fetch_settings(svc: str):
                url = f"{base}/api/v1/settings/{svc}"
                try:
                    r = requests.get(url, headers=headers, timeout=10)
                    r.raise_for_status()
                    return r.json()
                except Exception as e:
                    print(f"Failed to fetch settings for {svc}: {e}", file=sys.stderr)
                    return None

            services = args.services or ['radarr', 'sonarr']
            for svc in services:
                data = fetch_settings(svc)
                print(f"== {svc.upper()} ==")
                if not data:
                    print("<no data>\n")
                    continue
                try:
                    for item in data:
                        _id = item.get('id')
                        name = item.get('name') or item.get('hostname') or '<unnamed>'
                        print(f"{_id}\t{name}")
                except Exception:
                    try:
                        print(json.dumps(data, indent=2))
                    except Exception:
                        print(str(data))
                print()
            return 0
        except SystemExit:
            raise
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    # default: serve
    try:
        cfg = init_runtime(args.config)
        try:
            validate_configuration()
        except SystemExit as e:
            try:
                render_startup_card(cfg=cfg, host=SERVER_HOST, port=SERVER_PORT, threads=SERVER_THREADS, connection_limit=SERVER_CONNECTION_LIMIT, ok=False, message="Invalid configuration")
            finally:
                raise

        try:
            render_startup_card(cfg=cfg, host=SERVER_HOST, port=SERVER_PORT, threads=SERVER_THREADS, connection_limit=SERVER_CONNECTION_LIMIT, ok=True)
        except Exception:
            pass
        serve(
            app,
            host=SERVER_HOST,
            port=SERVER_PORT,
            threads=SERVER_THREADS,
            connection_limit=SERVER_CONNECTION_LIMIT,
        )
    except KeyboardInterrupt:
        return 130
    except SystemExit as e:
        return int(e.code or 1)
    except Exception:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
