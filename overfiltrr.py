import os
import sys
import logging
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback
from datetime import datetime
from flask import Flask, request
from waitress import serve
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yaml
from rapidfuzz import fuzz
import json
import operator
import uuid  
import logging.config

app = Flask(__name__)
install_rich_traceback()

# Constants
# LOG_LEVEL will be loaded from config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIRECTORY = os.path.join(SCRIPT_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIRECTORY, 'script.log')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')

REQUIRED_KEYS = [
    'OVERSEERR_BASEURL',
    'DRY_RUN',
    'API_KEYS',
    'TV_CATEGORIES',
    'MOVIE_CATEGORIES'
]

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

os.makedirs(LOG_DIRECTORY, exist_ok=True)

def setup_logging():
    # LOG_LEVEL is now set in load_config before setup_logging is called
    logging.config.dictConfig(LOGGING_CONFIG)

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,

    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(levelname)s - %(message)s'
        },
        'rich': {
            'format': '%(message)s'
        }
    },

    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'rich.logging.RichHandler',
            'formatter': 'rich',
            'show_path': False,
            'markup': True,
            'log_time_format': '[%x %X]'
        },
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': LOG_FILE,
            'formatter': 'standard'
        }
    },

    'root': {
        # This will be updated by load_config before logging is setup
        'level': "INFO",
        'handlers': ['console', 'file']
    }
}

# Load configuration from YAML file
def load_config(path: str) -> dict:
    try:
        with open(path, 'r') as f:
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
    log_level = config.get('LOG_LEVEL', "INFO").upper()
    if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
        logging.warning(f"Invalid LOG_LEVEL '{log_level}' in config. Defaulting to 'INFO'.")
        log_level = "INFO"
    LOGGING_CONFIG['root']['level'] = log_level

    return config

config = load_config(CONFIG_PATH) # This will also set LOGGING_CONFIG['root']['level']

# Initialize logging ASAP after config is loaded and LOGGING_CONFIG is updated
setup_logging()

OVERSEERR_BASEURL = config['OVERSEERR_BASEURL']
DRY_RUN = config['DRY_RUN']
API_KEYS = config['API_KEYS']
TV_CATEGORIES = config['TV_CATEGORIES']
MOVIE_CATEGORIES = config['MOVIE_CATEGORIES']

# Try to load Notifiarr config, but don't fail if it doesn't exist
NOTIFIARR_CONFIG = config.get('NOTIFIARR')
if NOTIFIARR_CONFIG:
    NOTIFIARR_APIKEY = NOTIFIARR_CONFIG.get('API_KEY')
    NOTIFIARR_CHANNEL = NOTIFIARR_CONFIG.get('CHANNEL')
    NOTIFIARR_SOURCE = NOTIFIARR_CONFIG.get('SOURCE', 'Overseerr')
    NOTIFIARR_TIMEOUT = NOTIFIARR_CONFIG.get('TIMEOUT', 10)  # Get TIMEOUT, default to 10
else:
    NOTIFIARR_APIKEY = None
    NOTIFIARR_CHANNEL = None
    NOTIFIARR_SOURCE = None
    NOTIFIARR_TIMEOUT = 10  # Default to 10 if NOTIFIARR_CONFIG is not present

# Setup requests session with retry logic and connection pooling
def setup_requests_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(connect=3, backoff_factor=0.5, total=5)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

session = setup_requests_session()

def choose_common_or_strictest_rating(ratings):
    rating_priority = ["G", "PG", "PG-13", "R", "NC-17", "18", "TV-MA"]
    rating_count = {}
    for rating in ratings:
        if rating in rating_priority:
            rating_count[rating] = rating_count.get(rating, 0) + 1

    if not rating_count:
        return None

    sorted_ratings = sorted(rating_count.items(), key=lambda x: (-x[1], rating_priority.index(x[0])))
    return sorted_ratings[0][0]

def extract_age_ratings(overseerr_data, media_type):
    age_ratings = []
    if media_type == 'movie':
        releases = overseerr_data.get('releases', {}).get('results', [])
        for country in releases:
            if country.get('iso_3166_1') == 'US':
                for release in country.get('release_dates', []):
                    certification = release.get('certification')
                    if certification:
                        age_ratings.append(certification)
    elif media_type == 'tv':
        content_ratings = overseerr_data.get('contentRatings', {}).get('results', [])
        for rating in content_ratings:
            if rating.get('iso_3166_1') == 'US':
                certification = rating.get('rating')
                if certification:
                    age_ratings.append(certification)
    return age_ratings
    
def log_rule_match(rule: dict, profile_id: int):
    logging.info("Rule Matched")
    logging.info("-" * 60)

    priority = rule.get('priority', 'N/A')
    logging.info("Priority: %s", priority)

    condition = rule.get('condition', {})
    if condition:
        logging.info("Condition:")
        for cond_key, cond_value in condition.items():
            logging.info("  %s: %s", cond_key, cond_value)
    else:
        logging.info("Condition: None")

    logging.info("Profile ID: %s", profile_id)
    logging.info("=" * 60)
    
def log_media_details(details: dict, header: str = "Media Details", highlights=None):
    """Log media details with coloured headers and optional highlights."""
    highlights = highlights or {}

    header_colours = {
        "Streaming Providers": "magenta",
        "Genres": "cyan",
        "Keywords": "yellow",
        "Production Companies": "green",
        "Networks": "blue",
    }

    logging.info("=" * 60)
    logging.info(header)
    logging.info("-" * 60)

    for key, value in details.items():
        colour = header_colours.get(key, "bright_white")
        label = f"[bold {colour}]{key}[/]"

        highlight_values = set(highlights.get(key, []))

        if isinstance(value, list):
            formatted_items = []
            for item in value:
                if item in highlight_values:
                    formatted_items.append(f"[bold red]{item}[/]")
                else:
                    formatted_items.append(str(item))
            value_display = ", ".join(formatted_items)
        else:
            value_display = str(value)
            if value_display in highlight_values:
                value_display = f"[bold red]{value_display}[/]"

        if key == "Overview" and isinstance(value_display, str):
            max_length = 50
            if len(value_display) > max_length:
                value_display = value_display[:max_length - 3] + "..."

        logging.info(
            f"{label}: {value_display}",
            extra={"media_label": key, "media_value": value_display}
        )

    logging.info("=" * 60)

def get_media_data(overseerr_data, media_type):
    genres = [g['name'] for g in overseerr_data.get('genres', [])]
    keywords_data = overseerr_data.get('keywords', [])
    keywords = [k['name'] for k in (keywords_data if isinstance(keywords_data, list) else keywords_data.get('results', []))]

    release_date_str = overseerr_data.get('releaseDate') or overseerr_data.get('firstAirDate')
    release_year = None
    if release_date_str:
        try:
            release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
            release_year = release_date.year
        except ValueError:
            logging.error(f"Invalid release date format: {release_date_str}")

    providers = []
    watch_providers_data = overseerr_data.get('watchProviders', [])
    if isinstance(watch_providers_data, list):
        for provider_entry in watch_providers_data:
            if provider_entry.get('iso_3166_1') == 'US':
                flatrate = provider_entry.get('flatrate', [])
                providers.extend([p.get('name') or p.get('provider_name') for p in flatrate if p.get('name') or p.get('provider_name')])
    elif isinstance(watch_providers_data, dict):
        us_providers = watch_providers_data.get('results', {}).get('US', {})
        flatrate = us_providers.get('flatrate', [])
        providers.extend([p.get('provider_name') for p in flatrate if p.get('provider_name')])

    production_companies = [pc['name'] for pc in overseerr_data.get('productionCompanies', [])]
    networks = [n['name'] for n in overseerr_data.get('networks', [])] if media_type == 'tv' else []
    original_language = overseerr_data.get('originalLanguage', '')
    status = overseerr_data.get('status', '')

    overview = overseerr_data.get('overview', 'No overview available.')
    imdbId = overseerr_data.get('imdbId', '')
    posterPath = overseerr_data.get('posterPath', '')

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
        "Final Age Rating": age_rating if age_rating else "None"
    }

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
        media_details,
    )

def validate_categories(categories, media_type):
    valid = True
    default_category_key = categories.get("default")

    if default_category_key is None:
        logging.error(f"No default category specified in the configuration for {media_type.upper()}_CATEGORIES.")
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
                logging.error(f"Category '{category_name}' must have 'default_profile_id' in 'apply' when 'quality_profile_rules' are missing or empty.")
                valid = False

        root_folder = apply.get("root_folder")
        if root_folder is None:
            logging.error(f"Category '{category_name}' must have 'root_folder' in 'apply'.")
            valid = False

        required_id_key = "sonarr_id" if media_type == 'tv' else "radarr_id"
        id_value = apply.get(required_id_key)
        if id_value is None:
            logging.error(f"Category '{category_name}' must have '{required_id_key}' in 'apply' for {media_type.upper()} categories.")
            valid = False

        filters = category_data.get("filters", {})
        if filters:
            genres = filters.get("genres", [])
            keywords = filters.get("keywords", [])
            if not isinstance(genres, list) or not isinstance(keywords, list):
                logging.error(f"Filters in category '{category_name}' must have 'genres' and 'keywords' as lists.")
                valid = False

    if default_category_key and default_category_key not in categories:
        logging.error(f"The 'default' category '{default_category_key}' is not properly defined in the configuration for {media_type.upper()}_CATEGORIES.")
        valid = False

    return valid

def validate_configuration():
    tv_valid = validate_categories(TV_CATEGORIES, 'tv')
    movie_valid = validate_categories(MOVIE_CATEGORIES, 'movie')

    if not (tv_valid and movie_valid):
        logging.critical("Configuration validation failed. Please fix the errors and restart the script.")
        sys.exit(1)
    
    logging.info(f"Configuration loaded and validated successfully.")

def fuzzy_match(list_to_check, possible_values, threshold=80):
    for item in list_to_check:
        for value in possible_values:
            if fuzz.ratio(item.lower(), value.lower()) >= threshold:
                return value
    return None

def categorize_media(genres, keywords, title, age_rating, media_type):
    best_match = None
    highest_weight = -1
    matched_genre = None
    matched_keyword = None
    categories = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
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

        genre_hit = fuzzy_match(genres, genres_filters) if genres_filters else None
        keyword_hit = fuzzy_match(keywords, keywords_filters) if keywords_filters else None

        if genre_hit or keyword_hit:
            logging.debug(
                f"Potential match found: {category} (genre match: {genre_hit}, keyword match: {keyword_hit})"
            )
            if data["weight"] > highest_weight:
                best_match = category
                highest_weight = data["weight"]
                matched_genre = genre_hit
                matched_keyword = keyword_hit

    if not best_match and default_category_key in categories:
        folder_data = categories[default_category_key]
        filters = folder_data.get("filters", {})
        excluded_ratings = filters.get("excluded_ratings", [])

        if age_rating in excluded_ratings:
            logging.error(f"Age rating {age_rating} excludes the default category '{default_category_key}'.")
            return None, None

        root_folder = folder_data["apply"]["root_folder"]
        return root_folder, default_category_key, matched_genre, matched_keyword
    elif best_match:
        folder_data = categories[best_match]
        root_folder = folder_data["apply"]["root_folder"]
        return root_folder, best_match, matched_genre, matched_keyword
    else:
        logging.error("No matching category found for media.")
        return None, None, None, None

def evaluate_quality_profile_rules(rules, context):
    if not rules:
        logging.debug("No quality profile rules provided.")
        return None

    sorted_rules = sorted(rules, key=lambda x: x.get('priority', 9999))
    for rule in sorted_rules:
        condition = rule.get('condition', {})
        profile_id = rule.get('profile_id')
        logic = rule.get('logic', 'OR').upper()

        if logic not in ['AND', 'OR']:
            logging.warning(f"Unsupported logic '{logic}' in rule. Defaulting to 'OR'.")
            logic = 'OR'

        if evaluate_condition(condition, context, logic):
            log_rule_match(rule, profile_id)
            return profile_id
    return None

def evaluate_condition(condition, context, logic='OR'):
    operators_map = {
        '<': operator.lt,
        '<=': operator.le,
        '>': operator.gt,
        '>=': operator.ge,
        '==': operator.eq,
        '!=': operator.ne,
        'in': lambda a, b: a in b,
        'not in': lambda a, b: a not in b,
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
                    logging.warning(f"Unsupported operator '{operator_str}' in condition for key '{key}'.")
                    continue

                target_values = target_value if isinstance(target_value, list) else [target_value]
                for t_value in target_values:
                    if operator_str in ['in', 'not in']:
                        if not operator_func(t_value, context_value):
                            logging.debug(f"Condition '{t_value} {operator_str} {context_value}' not met.")
                            return False
                    else:
                        if operator_str in ['!=', '<', '<=', '>', '>=']:
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
                    logging.warning(f"Unsupported operator '{operator_str}' in condition for key '{key}'.")
                    continue

                if operator_str in ['in', 'not in']:
                    if not operator_func(context_value, target_value):
                        logging.debug(f"Condition '{context_value} {operator_str} {target_value}' not met.")
                        return False
                else:
                    if not operator_func(context_value, target_value):
                        logging.debug(f"Condition '{context_value} {operator_str} {target_value}' not met.")
                        return False
            return True

    if logic == 'AND':
        return all(evaluate_single_condition(k, v) for k, v in condition.items())
    elif logic == 'OR':
        return any(evaluate_single_condition(k, v) for k, v in condition.items())
    else:
        logging.warning(f"Unsupported logic type: {logic}. Defaulting to 'OR'.")
        return any(evaluate_single_condition(k, v) for k, v in condition.items())

@app.route('/webhook', methods=['POST'])
def handle_request():
    request_data = request.get_json()
    notification_type = request_data.get('notification_type', '')

    if notification_type == 'TEST_NOTIFICATION':
        logging.info("Test payload received, no further processing.")
        return ('Test payload received', 200)

    if notification_type == 'MEDIA_PENDING':
        process_request(request_data)
        return ('success', 202)

    return ('Unhandled notification type', 400)

def process_request(request_data):
    try:
        request_info = request_data['request']
        media_info = request_data['media']
        request_username = request_info['requestedBy_username']
        request_id = request_info['request_id']
        media_tmdbid = media_info['tmdbId']
        media_type = media_info['media_type']
        media_title = request_data['subject']

        logging.info(f"Starting processing for: {media_title} (Request ID: {request_id}, User: {request_username})")
        logging.info(f"Media Type: {media_type}")

        # Fetch media details from Overseerr
        get_url = f"{OVERSEERR_BASEURL}/api/v1/{media_type}/{media_tmdbid}"
        headers = {'accept': 'application/json', 'X-Api-Key': API_KEYS['overseerr']}

        response = session.get(get_url, headers=headers, timeout=5)
        if response.status_code != 200:
            logging.error(f"Error fetching media details from {get_url}. Status: {response.status_code}, Response: {response.text if response.text else response.content}")
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
            media_details,
        ) = get_media_data(overseerr_data, media_type)

        # Categorize media
        (
            target_root_folder,
            best_match,
            matched_genre,
            matched_keyword,
        ) = categorize_media(genres, keywords, media_title, age_rating, media_type)
        if not target_root_folder or not best_match:
            logging.error("Unable to determine target root folder or category.")
            return

        highlight_map = {
            "Genres": [matched_genre] if matched_genre else [],
            "Keywords": [matched_keyword] if matched_keyword else [],
        }
        log_media_details(media_details, header="Fetched Media Details From Overseerr", highlights=highlight_map)

        context = {
            'release_year': release_year,
            'original_language': original_language,
            'providers': providers,
            'production_companies': production_companies,
            'networks': networks,
            'status': status,
            'genres': genres,
            'keywords': keywords,
            'media_type': media_type
        }

        categories = MOVIE_CATEGORIES if media_type == 'movie' else TV_CATEGORIES
        folder_data = categories.get(best_match)
        if not folder_data:
            logging.error(f"No configuration found for category '{best_match}'.")
            return

        apply_data = folder_data.get("apply", {})
        default_profile_id = apply_data.get('default_profile_id')
        quality_profile_rules = folder_data.get('quality_profile_rules', [])
        if quality_profile_rules is None:
            quality_profile_rules = []

        profile_id = evaluate_quality_profile_rules(quality_profile_rules, context) or default_profile_id

        if not profile_id:
            logging.error(f"Unable to determine Quality Profile ID for media '{media_title}' (Request ID: {request_id}). No matching rules and no default_profile_id configured for category '{best_match}'.")
            # The existing "if not profile_id:" check below will still catch this,
            # but this log provides more specific context.
            # No need for an immediate return here as the next check handles it.

        if not profile_id: # This check remains to handle the case
            logging.error(f"Critical: profile_id is None for media '{media_title}' (Request ID: {request_id}). Processing cannot continue.") # Added more specific message for the existing check
            return

        put_data = {}
        if media_type == 'movie':
            radarr_id = apply_data.get("radarr_id")
            if radarr_id is None:
                logging.error(f"'radarr_id' is missing in 'apply' for category '{best_match}'.")
                return
            target_name = apply_data.get("app_name", "Unknown App")

            put_data = {
                "mediaType": media_type,
                "rootFolder": target_root_folder,
                "serverId": radarr_id,
                "profileId": profile_id
            }

            logging.info(f"Using Radarr for: {target_name}")
            logging.info(f"Categorized as: {best_match}")

        elif media_type == 'tv':
            sonarr_id = apply_data.get("sonarr_id")
            if sonarr_id is None:
                logging.error(f"'sonarr_id' is missing in 'apply' for category '{best_match}'.")
                return
            target_name = apply_data.get("app_name", "Unknown App")

            seasons = None
            try:
                seasons_str = request_data['extra'][0]['value']
                seasons = [int(season) for season in seasons_str.split(',')]
            except (KeyError, IndexError, ValueError) as e:
                logging.warning(f"Seasons information is missing or invalid: {e}")
                seasons = []

            put_data = {
                "mediaType": media_type,
                "seasons": seasons,
                "rootFolder": target_root_folder,
                "serverId": sonarr_id,
                "profileId": profile_id
            }

            logging.info(f"Using Sonarr for: {target_name}")
            logging.info(f"Categorized as: {best_match}")

        headers.update({'Content-Type': 'application/json'})

        if put_data:
            if DRY_RUN:
                logging.warning(
                    f"[DRY RUN] No changes made. Would update request {request_id} "
                    f"to use {target_name}, root folder {put_data['rootFolder']}, "
                    f"and quality profile {profile_id}."
                )
            else:
                put_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}"
                response = session.put(put_url, headers=headers, json=put_data, timeout=5)
                if response.status_code == 200:
                    logging.info(
                        f"Request updated: {target_name}, root folder {put_data['rootFolder']}, "
                        f"and quality profile {profile_id}."
                    )
                    # Auto approve request
                    approve_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}/approve"
                    approve_response = session.post(approve_url, headers=headers, timeout=5)

                    if approve_response.status_code == 200:
                        logging.info(f"Request {request_id} approved successfully.")
                    else:
                        logging.error(f"Error auto-approving request {approve_url}. Status: {approve_response.status_code}, Response: {approve_response.text if approve_response.text else approve_response.content}")
                else:
                    logging.error(f"Error updating request {put_url}. Status: {response.status_code}, Response: {response.text if response.text else response.content}")
        else:
            logging.error("Error: Unable to determine appropriate service for the request.")

        # After processing, get the updated request status
        request_status_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}"
        request_status_response = session.get(request_status_url, headers=headers, timeout=5)

        if request_status_response.status_code == 200:
            request_status_data = request_status_response.json()
            status_code = request_status_data.get('status')
            status_map = {1: 'Pending Approval', 2: 'Approved', 3: 'Declined'}
            status_text = status_map.get(status_code, 'Unknown Status')
        else:
            logging.error(f"Failed to get request status from {request_status_url}. Status: {request_status_response.status_code}, Response: {request_status_response.text if request_status_response.text else request_status_response.content}")
            status_text = 'Status Unknown'

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
            elif media_type == 'tv':
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
                    best_match=best_match
                )
            else:
                logging.error(f"Unsupported media type '{media_type}'. No notification will be sent.")
                return
                
            send_notifiarr_passthrough(payload)
        else:
            logging.debug("No Notifiarr API key found; not sending notifications.")

    except Exception as e:
        logging.error(f"Exception occurred during request processing: {str(e)}", exc_info=True)

def construct_movie_payload(media_title, request_username, status_text, target_root_folder, best_match, request_id, overview, imdbId, posterPath):
    """
    Constructs a Discord notification payload for movies.
    """
    unique_event = str(uuid.uuid4())

    payload = {
        "notification": {
            "update": False,
            "name": "OverFiltrr",
            "event": f"Movie Request {status_text} - {request_id}"  
        },
        "discord": {
            "color": "377E22" if status_text == "Approved" else "D65845",
            "ping": {
                "pingUser": 0,
                "pingRole": 0
            },
            "images": {
                "thumbnail": "",
                "image": ""
            },
            "text": {
                "title": f"🎬 **{media_title}**",
                "icon": "",
                "content": "",
                "description": overview,
                "fields": [
                    {
                        "title": "Requested By",
                        "text": request_username,
                        "inline": False
                    },
                    {
                        "title": "Request Status",
                        "text": status_text,
                        "inline": True
                    },
                    {
                        "title": "Categorised As",
                        "text": best_match,
                        "inline": True
                    },
                ],
                "footer": "Overseerr Notification"
            },
            "ids": {
                "channel": NOTIFIARR_CHANNEL  
            }
        }
    }
    
    if status_text != "Approved":
        payload["discord"]["text"]["fields"].append({
            "title": "NOT APPROVED",
            "text": "Something unexpected happened. This was not approved, so check the logs or settings.",
            "inline": False
        })

    if imdbId:
        imdb_link = f"https://www.imdb.com/title/{imdbId}/"
        payload["notification"]["url"] = imdb_link
    else:
        logging.warning(f"No IMDb ID found for '{media_title}'. Title will not be a clickable link.")

    if posterPath:
        poster_url = f"{TMDB_IMAGE_BASE_URL}{posterPath}"
        payload["discord"]["images"]["thumbnail"] = poster_url
    else:
        logging.warning(f"No posterPath found for '{media_title}'. Icon will not be set.")

    return payload

def construct_tv_payload(media_title, request_username, status_text, target_root_folder, best_match, request_id, seasons, overview, imdbId, posterPath):
    """
    Constructs a Discord notification payload for TV shows.
    """
    unique_event = str(uuid.uuid4())
    logging.debug(f"Notification payload event identifier: {unique_event}")

    # Format seasons
    if seasons:
        seasons_formatted = ', '.join(str(season) for season in seasons)
    else:
        seasons_formatted = 'All Seasons'

    payload = {
        "notification": {
            "update": False,
            "name": "OverFiltrr",
            "event": f"TV Request {status_text} - {request_id}"
        },
        "discord": {
            "color": "377E22" if status_text == "Approved" else "D65845",
            "ping": {
                "pingUser": 0,
                "pingRole": 0
            },
            "images": {
                "thumbnail": "",
                "image": ""
            },
            "text": {
                "title": f"📺 **{media_title}**",
                "icon": "",
                "content": "",
                "description": overview,
                "fields": [
                    {
                        "title": "Requested By",
                        "text": request_username,
                        "inline": False
                    },
                    {
                        "title": "Request Status",
                        "text": status_text,
                        "inline": True
                    },
                    {
                        "title": "Seasons",
                        "text": seasons_formatted,
                        "inline": True
                    },
                    {
                        "title": "Categorised As",
                        "text": best_match,
                        "inline": True
                    }
                ],
                "footer": "Overseerr Notification"
            },
            "ids": {
                "channel": NOTIFIARR_CHANNEL  
            }
        }
    }
    
    if status_text != "Approved":
        payload["discord"]["text"]["fields"].append({
            "title": "NOT APPROVED",
            "text": "Something unexpected happened. This was not approved, so check the logs or settings.",
            "inline": False
        })

    if imdbId:
        imdb_link = f"https://www.imdb.com/title/{imdbId}/"
        payload["notification"]["url"] = imdb_link
    else:
        logging.warning(f"No IMDb ID found for '{media_title}'. Title will not be a clickable link.")

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
        passthrough_url = f"https://notifiarr.com/api/v1/notification/passthrough/{NOTIFIARR_APIKEY}"

        response = session.post(
            passthrough_url,
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            timeout=NOTIFIARR_TIMEOUT  # Use the configured timeout
        )

        if response.status_code == 200:
            logging.info("Notification sent via Notifiarr passthrough.")
        else:
            logging.error(f"Failed to send notification via Notifiarr passthrough: {response.status_code} {response.text}")
    except Exception as e:
        logging.error(f"Exception occurred while sending notification via Notifiarr passthrough: {e}")

if __name__ == '__main__':
    # setup_logging() is already called after load_config()
    validate_configuration()

    server_config = config.get('SERVER', {})
    host = server_config.get('HOST', '0.0.0.0')
    port = server_config.get('PORT', 12210)
    threads = server_config.get('THREADS', 5)
    connection_limit = server_config.get('CONNECTION_LIMIT', 200)

    logging.info(f"Configuration is valid. Starting the server on {host}:{port} with {threads} threads and connection limit {connection_limit}...")
    serve(app, host=host, port=port, threads=threads, connection_limit=connection_limit)