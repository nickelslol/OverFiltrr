import json
import logging
import logging.config
import logging.handlers
import operator
import os
import sys
import uuid
from datetime import datetime
import time

import requests
import yaml
from flask import Flask, request
from rapidfuzz import fuzz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from waitress import serve

app = Flask(__name__)

# Constants
# LOG_LEVEL will be loaded from config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIRECTORY = os.path.join(SCRIPT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIRECTORY, "script.log")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")

REQUIRED_KEYS = ["OVERSEERR_BASEURL", "DRY_RUN", "API_KEYS", "TV_CATEGORIES", "MOVIE_CATEGORIES"]

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

os.makedirs(LOG_DIRECTORY, exist_ok=True)

# Global variables populated in main()
config = {}
OVERSEERR_BASEURL = None
DRY_RUN = None
API_KEYS = {}
TV_CATEGORIES = {}
MOVIE_CATEGORIES = {}
NOTIFIARR_APIKEY = None
NOTIFIARR_CHANNEL = None
NOTIFIARR_SOURCE = None
NOTIFIARR_TIMEOUT = 10


class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


import re


class JSONFormatter(logging.Formatter):
    """Structured JSON formatter with safe handling for missing extras and UTC timestamps."""

    def __init__(self) -> None:
        super().__init__()
        # Ensure UTC time in asctime-like field
        self.converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        base: dict = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Known contextual fields we want to surface explicitly if present
        for key in [
            "request_id",
            "media_type",
            "media_title",
            "tmdb_id",
            "user",
            "category",
            "profile_id",
        ]:
            if hasattr(record, key):
                base[key] = getattr(record, key)

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(base, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    colon_pattern = re.compile(r"^(.*?):\s(.*)$")

    def format(self, record):
        # Use UTC time in asctime
        self.converter = time.gmtime
        base_message = super().format(record)
        # Append contextual identifiers if available for quick scanning
        context_parts = []
        for key in [
            "request_id",
            "media_type",
            "media_title",
            "tmdb_id",
            "user",
            "category",
            "profile_id",
        ]:
            value = getattr(record, key, None)
            if value is not None:
                context_parts.append(f"{key}={value}")
        if context_parts:
            base_message = f"{base_message} | " + " ".join(context_parts)
        if getattr(record, "is_console", False):
            media_label = getattr(record, "media_label", None)
            media_value = getattr(record, "media_value", None)
            if media_label is not None and media_value is not None:
                colored_label = f"{Colors.OKCYAN}{media_label}{Colors.ENDC}"
                colored_value = f"{Colors.OKBLUE}{media_value}{Colors.ENDC}"
                plain_substring = f"{media_label}: {media_value}"
                colored_substring = f"{colored_label}: {colored_value}"
                base_message = base_message.replace(plain_substring, colored_substring)

            match = self.colon_pattern.match(base_message)
            if match:
                label_part = match.group(1)
                value_part = match.group(2)

                colored_label = f"{Colors.OKCYAN}{label_part}{Colors.ENDC}"
                colored_value = f"{Colors.OKBLUE}{value_part}{Colors.ENDC}"
                base_message = f"{colored_label}: {colored_value}"

        return base_message


def build_logging_config(resolved_config: dict) -> dict:
    """Build dictConfig dynamically using options from YAML with sensible defaults."""
    log_options = resolved_config.get("LOG", {}) if isinstance(resolved_config, dict) else {}

    # Defaults
    file_enabled: bool = bool(log_options.get("FILE_ENABLED", True))
    file_path: str = log_options.get("FILE_PATH", LOG_FILE)
    rotate_max_bytes: int = int(log_options.get("ROTATE_MAX_BYTES", 5 * 1024 * 1024))
    rotate_backups: int = int(log_options.get("ROTATE_BACKUPS", 5))
    console_color: bool = bool(log_options.get("COLOR", True))
    file_format: str = str(log_options.get("FORMAT", "json")).lower()

    # Resolve level (already validated in load_config)
    root_level: str = resolved_config.get("LOG_LEVEL", "INFO").upper()

    formatter_module_prefix = f"{__name__}"

    formatters = {
        "standard": {
            "format": "%(asctime)sZ - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "colored": {
            "()": f"{formatter_module_prefix}.ColoredFormatter",
            "format": "%(asctime)sZ - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
        "json": {
            "()": f"{formatter_module_prefix}.JSONFormatter",
        },
    }

    handlers = {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "colored" if console_color else "standard",
            "filters": ["console_filter"],
        }
    }

    if file_enabled:
        handlers["file"] = {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": file_path,
            "formatter": "json" if file_format == "json" else "standard",
            "maxBytes": rotate_max_bytes,
            "backupCount": rotate_backups,
        }

    root_handlers = ["console"] + (["file"] if file_enabled else [])

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "filters": {"console_filter": {"()": f"{formatter_module_prefix}.ConsoleFilter"}},
        "handlers": handlers,
        "root": {"level": root_level, "handlers": root_handlers},
    }


def setup_logging(resolved_config: dict):
    # Enforce UTC timestamps globally for std formatters
    logging.Formatter.converter = time.gmtime
    logging.config.dictConfig(build_logging_config(resolved_config))


LOGGING_CONFIG = {}


class ConsoleFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.is_console = True
        return True


# Load configuration from YAML file
def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        logging.critical(f"Configuration file 'config.yaml' not found at {path}.")
        sys.exit(1)
    except yaml.YAMLError as e:
        logging.critical(f"Error parsing 'config.yaml': {e}")
        sys.exit(1)

    # Check for missing required keys
    missing_keys = [key for key in REQUIRED_KEYS if key not in config]
    if missing_keys:
        logging.critical(f"Missing required configuration keys: {', '.join(missing_keys)}")
        sys.exit(1)

    # Get LOG_LEVEL from config, default to "INFO"
    log_level = config.get("LOG_LEVEL", "INFO").upper()
    if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        logging.warning(f"Invalid LOG_LEVEL '{log_level}' in config. Defaulting to 'INFO'.")
        log_level = "INFO"
    config["LOG_LEVEL"] = log_level

    return config


# Setup requests session with retry logic and connection pooling
def setup_requests_session() -> requests.Session:
    """Return a requests session configured with retries and connection pooling."""
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=0.5, total=5)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


session = setup_requests_session()


def get_logger(context: dict | None = None) -> logging.LoggerAdapter:
    """Return a LoggerAdapter with contextual extras for structured logging."""
    base_logger = logging.getLogger()
    return logging.LoggerAdapter(base_logger, context or {})


def choose_common_or_strictest_rating(ratings):
    """Return the most common rating or, if tied, the strictest."""
    rating_priority = ["G", "PG", "PG-13", "R", "NC-17", "18", "TV-MA"]
    rating_count = {}
    for rating in ratings:
        if rating in rating_priority:
            rating_count[rating] = rating_count.get(rating, 0) + 1

    if not rating_count:
        return None

    sorted_ratings = sorted(
        rating_count.items(), key=lambda x: (-x[1], rating_priority.index(x[0]))
    )
    return sorted_ratings[0][0]


def extract_age_ratings(overseerr_data, media_type):
    """Extract age ratings from Overseerr data for the given media type."""
    age_ratings = []
    if media_type == "movie":
        releases = overseerr_data.get("releases", {}).get("results", [])
        for country in releases:
            if country.get("iso_3166_1") == "US":
                for release in country.get("release_dates", []):
                    certification = release.get("certification")
                    if certification:
                        age_ratings.append(certification)
    elif media_type == "tv":
        content_ratings = overseerr_data.get("contentRatings", {}).get("results", [])
        for rating in content_ratings:
            if rating.get("iso_3166_1") == "US":
                certification = rating.get("rating")
                if certification:
                    age_ratings.append(certification)
    return age_ratings


def log_rule_match(rule: dict, profile_id: int):
    """Log details when a quality profile rule matches."""
    logging.info("Rule Matched")
    logging.info("-" * 60)

    priority = rule.get("priority", "N/A")
    logging.info("Priority: %s", priority)

    condition = rule.get("condition", {})
    if condition:
        logging.info("Condition:")
        for cond_key, cond_value in condition.items():
            logging.info("  %s: %s", cond_key, cond_value)
    else:
        logging.info("Condition: None")

    logging.info("Profile ID: %s", profile_id)
    logging.info("=" * 60)


def log_media_details(details: dict, header: str = "Media Details"):
    """Log formatted media details for debugging."""
    logging.info("=" * 60)
    logging.info(header)
    logging.info("-" * 60)

    for key, value in details.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)

        if key == "Overview" and isinstance(value, str):
            max_length = 50
            if len(value) > max_length:
                value = value[: max_length - 3] + "..."

        logging.info("%s: %s", key, value, extra={"media_label": key, "media_value": value})

    logging.info("=" * 60)


def get_media_data(overseerr_data, media_type):
    """Return parsed media details and log them."""
    genres = [g["name"] for g in overseerr_data.get("genres", [])]
    keywords_data = overseerr_data.get("keywords", [])
    keywords = [
        k["name"]
        for k in (
            keywords_data if isinstance(keywords_data, list) else keywords_data.get("results", [])
        )
    ]

    release_date_str = overseerr_data.get("releaseDate") or overseerr_data.get("firstAirDate")
    release_year = None
    if release_date_str:
        try:
            release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
            release_year = release_date.year
        except ValueError:
            logging.error(f"Invalid release date format: {release_date_str}")

    providers = []
    watch_providers_data = overseerr_data.get("watchProviders", [])
    if isinstance(watch_providers_data, list):
        for provider_entry in watch_providers_data:
            if provider_entry.get("iso_3166_1") == "US":
                flatrate = provider_entry.get("flatrate", [])
                providers.extend(
                    [
                        p.get("name") or p.get("provider_name")
                        for p in flatrate
                        if p.get("name") or p.get("provider_name")
                    ]
                )
    elif isinstance(watch_providers_data, dict):
        us_providers = watch_providers_data.get("results", {}).get("US", {})
        flatrate = us_providers.get("flatrate", [])
        providers.extend([p.get("provider_name") for p in flatrate if p.get("provider_name")])

    production_companies = [pc["name"] for pc in overseerr_data.get("productionCompanies", [])]
    networks = [n["name"] for n in overseerr_data.get("networks", [])] if media_type == "tv" else []
    original_language = overseerr_data.get("originalLanguage", "")
    status = overseerr_data.get("status", "")

    overview = overseerr_data.get("overview", "No overview available.")
    imdbId = overseerr_data.get("imdbId", "")
    posterPath = overseerr_data.get("posterPath", "")

    # Extract age ratings here
    age_ratings = extract_age_ratings(overseerr_data, media_type)
    age_rating = choose_common_or_strictest_rating(age_ratings)

    media_details = {
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
        "Age Ratings Collected": age_ratings if age_ratings else "None",
        "Final Age Rating": age_rating if age_rating else "None",
    }

    log_media_details(media_details, header="Fetched Media Details From Overseerr")

    return (
        genres,
        keywords,
        release_year,
        providers,
        production_companies,
        networks,
        original_language,
        status,
        overview,
        imdbId,
        posterPath,
        age_rating,
    )


def validate_categories(categories, media_type):
    """Validate category configuration for the given media type."""
    valid = True
    default_category_key = categories.get("default")

    if default_category_key is None:
        logging.error(
            f"No default category specified in the configuration for {media_type.upper()}_CATEGORIES."
        )
        valid = False

    for category_name, category_data in categories.items():
        if not isinstance(category_data, dict):
            continue

        apply = category_data.get("apply", {})
        weight = category_data.get("weight")
        if weight is None:
            logging.error(f"Category '{category_name}' must have 'weight'.")
            valid = False

        # Validate quality_profile_rules and default_profile_id
        quality_profile_rules = category_data.get("quality_profile_rules")
        if not quality_profile_rules:  # This covers both missing and empty list
            default_profile_id = apply.get("default_profile_id")
            if default_profile_id is None:
                logging.error(
                    f"Category '{category_name}' must have 'default_profile_id' in 'apply' when 'quality_profile_rules' are missing or empty."
                )
                valid = False

        root_folder = apply.get("root_folder")
        if root_folder is None:
            logging.error(f"Category '{category_name}' must have 'root_folder' in 'apply'.")
            valid = False

        required_id_key = "sonarr_id" if media_type == "tv" else "radarr_id"
        id_value = apply.get(required_id_key)
        if id_value is None:
            logging.error(
                f"Category '{category_name}' must have '{required_id_key}' in 'apply' for {media_type.upper()} categories."
            )
            valid = False

        filters = category_data.get("filters", {})
        if filters:
            genres = filters.get("genres", [])
            keywords = filters.get("keywords", [])
            if not isinstance(genres, list) or not isinstance(keywords, list):
                logging.error(
                    f"Filters in category '{category_name}' must have 'genres' and 'keywords' as lists."
                )
                valid = False

    if default_category_key and default_category_key not in categories:
        logging.error(
            f"The 'default' category '{default_category_key}' is not properly defined in the configuration for {media_type.upper()}_CATEGORIES."
        )
        valid = False

    return valid


def validate_configuration():
    """Ensure both movie and TV category configurations are valid."""
    tv_valid = validate_categories(TV_CATEGORIES, "tv")
    movie_valid = validate_categories(MOVIE_CATEGORIES, "movie")

    if not (tv_valid and movie_valid):
        logging.critical(
            "Configuration validation failed. Please fix the errors and restart the script."
        )
        sys.exit(1)

    logging.info(f"Configuration loaded and validated successfully.")


def fuzzy_match(list_to_check, possible_values, threshold=80):
    """Return the first value from possible_values that fuzzily matches."""
    for item in list_to_check:
        for value in possible_values:
            if fuzz.ratio(item.lower(), value.lower()) >= threshold:
                return value
    return None


def categorize_media(genres, keywords, title, age_rating, media_type):
    """Determine the best category for the media based on filters."""
    best_match = None
    highest_weight = -1
    categories = MOVIE_CATEGORIES if media_type == "movie" else TV_CATEGORIES
    default_category_key = categories.get("default")

    for category, data in categories.items():
        if not isinstance(data, dict) or category == default_category_key:
            continue

        filters = data.get("filters", {})
        genres_filters = filters.get("genres", [])
        keywords_filters = filters.get("keywords", [])
        excluded_ratings = filters.get("excluded_ratings", [])

        if age_rating in excluded_ratings:
            logging.info(f"Age rating {age_rating} excludes the category '{category}'.")
            continue

        # If no filters are provided, this category matches everything (except excluded ratings)
        if not genres_filters and not keywords_filters and not excluded_ratings:
            logging.debug(f"No filters provided for category '{category}'. It matches all media.")
            if data["weight"] > highest_weight:
                best_match = category
                highest_weight = data["weight"]
            continue

        matched_genre = fuzzy_match(genres, genres_filters) if genres_filters else None
        matched_keyword = fuzzy_match(keywords, keywords_filters) if keywords_filters else None

        if matched_genre or matched_keyword:
            logging.debug(
                f"Potential match found: {category} (genre match: {matched_genre}, keyword match: {matched_keyword})"
            )
            if data["weight"] > highest_weight:
                best_match = category
                highest_weight = data["weight"]

    if not best_match and default_category_key in categories:
        folder_data = categories[default_category_key]
        filters = folder_data.get("filters", {})
        excluded_ratings = filters.get("excluded_ratings", [])

        if age_rating in excluded_ratings:
            logging.error(
                f"Age rating {age_rating} excludes the default category '{default_category_key}'."
            )
            return None, None

        root_folder = folder_data["apply"]["root_folder"]
        return root_folder, default_category_key
    elif best_match:
        folder_data = categories[best_match]
        root_folder = folder_data["apply"]["root_folder"]
        return root_folder, best_match
    else:
        logging.error("No matching category found for media.")
        return None, None


def evaluate_quality_profile_rules(rules, context):
    """Evaluate quality profile rules against a media context."""
    if not rules:
        logging.debug("No quality profile rules provided.")
        return None

    sorted_rules = sorted(rules, key=lambda x: x.get("priority", 9999))
    for rule in sorted_rules:
        condition = rule.get("condition", {})
        profile_id = rule.get("profile_id")
        logic = rule.get("logic", "OR").upper()

        if logic not in ["AND", "OR"]:
            logging.warning(f"Unsupported logic '{logic}' in rule. Defaulting to 'OR'.")
            logic = "OR"

        if evaluate_condition(condition, context, logic):
            log_rule_match(rule, profile_id)
            return profile_id
    return None


def evaluate_condition(condition, context, logic="OR"):
    """Evaluate a condition dictionary against context data."""
    operators_map = {
        "<": operator.lt,
        "<=": operator.le,
        ">": operator.gt,
        ">=": operator.ge,
        "==": operator.eq,
        "!=": operator.ne,
        "in": lambda a, b: a in b,
        "not in": lambda a, b: a not in b,
    }

    def evaluate_single_condition(key, value):
        context_value = context.get(key)
        if context_value is None:
            logging.debug(f"Context does not contain key '{key}'.")
            return False

        if isinstance(context_value, list):
            for operator_str, target_value in value.items():
                operator_func = operators_map.get(operator_str)
                if not operator_func:
                    logging.warning(
                        f"Unsupported operator '{operator_str}' in condition for key '{key}'."
                    )
                    continue

                target_values = target_value if isinstance(target_value, list) else [target_value]
                for t_value in target_values:
                    if operator_str in ["in", "not in"]:
                        if not operator_func(t_value, context_value):
                            logging.debug(
                                f"Condition '{t_value} {operator_str} {context_value}' not met."
                            )
                            return False
                    else:
                        if operator_str in ["!=", "<", "<=", ">", ">="]:
                            comparator = all
                        else:
                            comparator = any

                        if not comparator(operator_func(item, t_value) for item in context_value):
                            logging.debug(
                                f"No match found for '{key}' with operator '{operator_str}' and target '{t_value}'."
                            )
                            return False
            return True
        else:
            for operator_str, target_value in value.items():
                operator_func = operators_map.get(operator_str)
                if not operator_func:
                    logging.warning(
                        f"Unsupported operator '{operator_str}' in condition for key '{key}'."
                    )
                    continue

                if operator_str in ["in", "not in"]:
                    if not operator_func(context_value, target_value):
                        logging.debug(
                            f"Condition '{context_value} {operator_str} {target_value}' not met."
                        )
                        return False
                else:
                    if not operator_func(context_value, target_value):
                        logging.debug(
                            f"Condition '{context_value} {operator_str} {target_value}' not met."
                        )
                        return False
            return True

    if logic == "AND":
        return all(evaluate_single_condition(k, v) for k, v in condition.items())
    elif logic == "OR":
        return any(evaluate_single_condition(k, v) for k, v in condition.items())
    else:
        logging.warning(f"Unsupported logic type: {logic}. Defaulting to 'OR'.")
        return any(evaluate_single_condition(k, v) for k, v in condition.items())


@app.route("/webhook", methods=["POST"])
def handle_request():
    """Handle incoming Overseerr webhook requests."""
    request_data = request.get_json()
    notification_type = request_data.get("notification_type", "")

    if notification_type == "TEST_NOTIFICATION":
        logging.info("Test payload received, no further processing.")
        return ("Test payload received", 200)

    if notification_type == "MEDIA_PENDING":
        process_request(request_data)
        return ("success", 202)

    return ("Unhandled notification type", 400)


def process_request(request_data):
    """Process a MEDIA_PENDING webhook payload."""
    try:
        request_info = request_data["request"]
        media_info = request_data["media"]
        request_username = request_info["requestedBy_username"]
        request_id = request_info["request_id"]
        media_tmdbid = media_info["tmdbId"]
        media_type = media_info["media_type"]
        media_title = request_data["subject"]
        logger = get_logger(
            {
                "request_id": request_id,
                "media_type": media_type,
                "media_title": media_title,
                "tmdb_id": media_tmdbid,
                "user": request_username,
            }
        )

        logger.info(
            "Starting processing for media request"
        )
        logger.info("Media Type detected")

        # Fetch media details from Overseerr
        get_url = f"{OVERSEERR_BASEURL}/api/v1/{media_type}/{media_tmdbid}"
        headers = {"accept": "application/json", "X-Api-Key": API_KEYS["overseerr"]}

        response = session.get(get_url, headers=headers, timeout=5)
        if response.status_code != 200:
            logger.error(
                "Error fetching media details",
                extra={"status_code": response.status_code},
            )
            return
        overseerr_data = response.json()

        # Unpack all details including age_rating now
        (
            genres,
            keywords,
            release_year,
            providers,
            production_companies,
            networks,
            original_language,
            status,
            overview,
            imdbId,
            posterPath,
            age_rating,
        ) = get_media_data(overseerr_data, media_type)

        # Categorize media
        target_root_folder, best_match = categorize_media(
            genres, keywords, media_title, age_rating, media_type
        )
        if not target_root_folder or not best_match:
            logger.error("Unable to determine target root folder or category")
            return

        context = {
            "release_year": release_year,
            "original_language": original_language,
            "providers": providers,
            "production_companies": production_companies,
            "networks": networks,
            "status": status,
            "genres": genres,
            "keywords": keywords,
            "media_type": media_type,
        }

        categories = MOVIE_CATEGORIES if media_type == "movie" else TV_CATEGORIES
        folder_data = categories.get(best_match)
        if not folder_data:
            logger.error("No configuration found for category", extra={"category": best_match})
            return

        apply_data = folder_data.get("apply", {})
        default_profile_id = apply_data.get("default_profile_id")
        quality_profile_rules = folder_data.get("quality_profile_rules", [])
        if quality_profile_rules is None:
            quality_profile_rules = []

        profile_id = (
            evaluate_quality_profile_rules(quality_profile_rules, context) or default_profile_id
        )

        if not profile_id:
            logger.error(
                "Unable to determine Quality Profile ID; no matching rules and no default configured",
                extra={"category": best_match},
            )
            # The existing "if not profile_id:" check below will still catch this,
            # but this log provides more specific context.
            # No need for an immediate return here as the next check handles it.

        if not profile_id:  # This check remains to handle the case
            logger.error(
                "Critical: profile_id is None; processing cannot continue",
                extra={"category": best_match},
            )
            return

        put_data = {}
        if media_type == "movie":
            radarr_id = apply_data.get("radarr_id")
            if radarr_id is None:
                logger.error("'radarr_id' is missing in 'apply'", extra={"category": best_match})
                return
            target_name = apply_data.get("app_name", "Unknown App")

            put_data = {
                "mediaType": media_type,
                "rootFolder": target_root_folder,
                "serverId": radarr_id,
                "profileId": profile_id,
            }

            logger.info("Using Radarr target", extra={"category": best_match})
            logger.info("Categorized", extra={"category": best_match})

        elif media_type == "tv":
            sonarr_id = apply_data.get("sonarr_id")
            if sonarr_id is None:
                logger.error("'sonarr_id' is missing in 'apply'", extra={"category": best_match})
                return
            target_name = apply_data.get("app_name", "Unknown App")

            seasons = None
            try:
                seasons_str = request_data["extra"][0]["value"]
                seasons = [int(season) for season in seasons_str.split(",")]
            except (KeyError, IndexError, ValueError) as e:
                logger.warning("Seasons information is missing or invalid", extra={"error": str(e)})
                seasons = []

            put_data = {
                "mediaType": media_type,
                "seasons": seasons,
                "rootFolder": target_root_folder,
                "serverId": sonarr_id,
                "profileId": profile_id,
            }

            logger.info("Using Sonarr target", extra={"category": best_match})
            logger.info("Categorized", extra={"category": best_match})

        headers.update({"Content-Type": "application/json"})

        if put_data:
            if DRY_RUN:
                logger.warning(
                    "[DRY RUN] No changes made. Would update request",
                    extra={
                        "category": best_match,
                        "profile_id": profile_id,
                        "target_root": put_data.get("rootFolder"),
                        "target_name": target_name,
                    },
                )
            else:
                put_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}"
                response = session.put(put_url, headers=headers, json=put_data, timeout=5)
                if response.status_code == 200:
                    logger.info(
                        "Request updated",
                        extra={
                            "category": best_match,
                            "profile_id": profile_id,
                            "target_root": put_data.get("rootFolder"),
                            "target_name": target_name,
                        },
                    )
                    # Auto approve request
                    approve_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}/approve"
                    approve_response = session.post(approve_url, headers=headers, timeout=5)

                    if approve_response.status_code == 200:
                        logger.info("Request approved successfully")
                    else:
                        logger.error(
                            "Error auto-approving request",
                            extra={"status_code": approve_response.status_code},
                        )
                else:
                    logger.error(
                        "Error updating request",
                        extra={"status_code": response.status_code},
                    )
        else:
            logger.error("Unable to determine appropriate service for the request")

        # After processing, get the updated request status
        request_status_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}"
        request_status_response = session.get(request_status_url, headers=headers, timeout=5)

        if request_status_response.status_code == 200:
            request_status_data = request_status_response.json()
            status_code = request_status_data.get("status")
            status_map = {1: "Pending Approval", 2: "Approved", 3: "Declined"}
            status_text = status_map.get(status_code, "Unknown Status")
        else:
            logger.error(
                "Failed to get request status",
                extra={"status_code": request_status_response.status_code},
            )
            status_text = "Status Unknown"

        if NOTIFIARR_APIKEY:
            if media_type == "movie":
                payload = construct_movie_payload(
                    media_title=media_title,
                    request_username=request_username,
                    status_text=status_text,
                    target_root_folder=target_root_folder,
                    request_id=request_id,
                    overview=overview,
                    imdbId=imdbId,
                    posterPath=posterPath,
                    best_match=best_match,
                )
            elif media_type == "tv":
                payload = construct_tv_payload(
                    media_title=media_title,
                    request_username=request_username,
                    status_text=status_text,
                    target_root_folder=target_root_folder,
                    request_id=request_id,
                    seasons=seasons,
                    overview=overview,
                    imdbId=imdbId,
                    posterPath=posterPath,
                    best_match=best_match,
                )
            else:
                logging.error(
                    f"Unsupported media type '{media_type}'. No notification will be sent."
                )
                return

            send_notifiarr_passthrough(payload)
        else:
            logger.debug("No Notifiarr API key found; not sending notifications")

    except Exception as e:
        # Use a base logger to ensure traceback is included even if adapter failed earlier
        get_logger().error("Exception occurred during request processing", exc_info=True, extra={"error": str(e)})


def construct_movie_payload(
    media_title,
    request_username,
    status_text,
    target_root_folder,
    best_match,
    request_id,
    overview,
    imdbId,
    posterPath,
):
    """
    Constructs a Discord notification payload for movies.
    """
    unique_event = str(uuid.uuid4())

    payload = {
        "notification": {
            "update": False,
            "name": "OverFiltrr",
            "event": f"Movie Request {status_text} - {request_id}",
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
                "footer": "Overseerr Notification",
            },
            "ids": {"channel": NOTIFIARR_CHANNEL},
        },
    }

    if status_text != "Approved":
        payload["discord"]["text"]["fields"].append(
            {
                "title": "NOT APPROVED",
                "text": "Something unexpected happened. This was not approved, so check the logs or settings.",
                "inline": False,
            }
        )

    if imdbId:
        imdb_link = f"https://www.imdb.com/title/{imdbId}/"
        payload["notification"]["url"] = imdb_link
    else:
        logging.warning(
            f"No IMDb ID found for '{media_title}'. Title will not be a clickable link."
        )

    if posterPath:
        poster_url = f"{TMDB_IMAGE_BASE_URL}{posterPath}"
        payload["discord"]["images"]["thumbnail"] = poster_url
    else:
        logging.warning(f"No posterPath found for '{media_title}'. Icon will not be set.")

    return payload


def construct_tv_payload(
    media_title,
    request_username,
    status_text,
    target_root_folder,
    best_match,
    request_id,
    seasons,
    overview,
    imdbId,
    posterPath,
):
    """
    Constructs a Discord notification payload for TV shows.
    """
    unique_event = str(uuid.uuid4())
    logging.debug(f"Notification payload event identifier: {unique_event}")

    # Format seasons
    if seasons:
        seasons_formatted = ", ".join(str(season) for season in seasons)
    else:
        seasons_formatted = "All Seasons"

    payload = {
        "notification": {
            "update": False,
            "name": "OverFiltrr",
            "event": f"TV Request {status_text} - {request_id}",
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
                "footer": "Overseerr Notification",
            },
            "ids": {"channel": NOTIFIARR_CHANNEL},
        },
    }

    if status_text != "Approved":
        payload["discord"]["text"]["fields"].append(
            {
                "title": "NOT APPROVED",
                "text": "Something unexpected happened. This was not approved, so check the logs or settings.",
                "inline": False,
            }
        )

    if imdbId:
        imdb_link = f"https://www.imdb.com/title/{imdbId}/"
        payload["notification"]["url"] = imdb_link
    else:
        logging.warning(
            f"No IMDb ID found for '{media_title}'. Title will not be a clickable link."
        )

    if posterPath:
        poster_url = f"{TMDB_IMAGE_BASE_URL}{posterPath}"
        payload["discord"]["images"]["thumbnail"] = poster_url
    else:
        logging.warning(f"No posterPath found for '{media_title}'. Icon will not be set.")

    return payload


def send_notifiarr_passthrough(payload):
    """
    Sends a notification via Notifiarr (if configured).
    """
    if not NOTIFIARR_APIKEY:
        logging.debug("No Notifiarr API key present; skipping notification.")
        return

    try:
        passthrough_url = (
            f"https://notifiarr.com/api/v1/notification/passthrough/{NOTIFIARR_APIKEY}"
        )

        response = session.post(
            passthrough_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=NOTIFIARR_TIMEOUT,  # Use the configured timeout
        )

        if response.status_code == 200:
            logging.info("Notification sent via Notifiarr passthrough.")
        else:
            logging.error(
                f"Failed to send notification via Notifiarr passthrough: {response.status_code} {response.text}"
            )
    except Exception as e:
        logging.error(
            f"Exception occurred while sending notification via Notifiarr passthrough: {e}"
        )


def main() -> None:
    """Load configuration, set up logging and start the server."""
    global config, OVERSEERR_BASEURL, DRY_RUN, API_KEYS, TV_CATEGORIES, MOVIE_CATEGORIES
    global NOTIFIARR_APIKEY, NOTIFIARR_CHANNEL, NOTIFIARR_SOURCE, NOTIFIARR_TIMEOUT

    config = load_config(CONFIG_PATH)
    setup_logging(config)

    OVERSEERR_BASEURL = config["OVERSEERR_BASEURL"]
    DRY_RUN = config["DRY_RUN"]
    API_KEYS = config["API_KEYS"]
    TV_CATEGORIES = config["TV_CATEGORIES"]
    MOVIE_CATEGORIES = config["MOVIE_CATEGORIES"]

    NOTIFIARR_CONFIG = config.get("NOTIFIARR")
    if NOTIFIARR_CONFIG:
        NOTIFIARR_APIKEY = NOTIFIARR_CONFIG.get("API_KEY")
        NOTIFIARR_CHANNEL = NOTIFIARR_CONFIG.get("CHANNEL")
        NOTIFIARR_SOURCE = NOTIFIARR_CONFIG.get("SOURCE", "Overseerr")
        NOTIFIARR_TIMEOUT = NOTIFIARR_CONFIG.get("TIMEOUT", 10)
    else:
        NOTIFIARR_APIKEY = None
        NOTIFIARR_CHANNEL = None
        NOTIFIARR_SOURCE = None
        NOTIFIARR_TIMEOUT = 10

    validate_configuration()

    server_config = config.get("SERVER", {})
    host = server_config.get("HOST", "0.0.0.0")
    port = server_config.get("PORT", 12210)
    threads = server_config.get("THREADS", 5)
    connection_limit = server_config.get("CONNECTION_LIMIT", 200)

    logging.info(
        f"Configuration is valid. Starting the server on {host}:{port} with {threads} threads and connection limit {connection_limit}..."
    )
    serve(app, host=host, port=port, threads=threads, connection_limit=connection_limit)


if __name__ == "__main__":
    main()
