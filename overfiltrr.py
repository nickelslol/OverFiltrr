import os
import sys
import logging
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

app = Flask(__name__)

# Constants
LOG_LEVEL = "INFO"  # Set to DEBUG to capture detailed logs
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIRECTORY = os.path.join(SCRIPT_DIR, 'logs')
LOG_FILE = os.path.join(LOG_DIRECTORY, 'script.log')
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')
REQUIRED_KEYS = ['OVERSEERR_BASEURL', 'DRY_RUN', 'API_KEYS', 'TV_CATEGORIES', 'MOVIE_CATEGORIES']

# Ensure the logs directory exists
os.makedirs(LOG_DIRECTORY, exist_ok=True)

# Define custom color formatter
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class ColoredFormatter(logging.Formatter):
    def format(self, record):
        message = super().format(record)
        if getattr(record, 'is_console', False):
            if record.levelno == logging.WARNING:
                return f"{Colors.WARNING}{message}{Colors.ENDC}"
            elif record.levelno >= logging.ERROR:
                return f"{Colors.FAIL}{message}{Colors.ENDC}"
            elif record.levelno == logging.INFO:
                return f"{Colors.OKCYAN}{message}{Colors.ENDC}"
            elif record.levelno == logging.DEBUG:
                return f"{Colors.OKBLUE}{message}{Colors.ENDC}"
        return message

# Configure logging
def setup_logging():
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    colored_formatter = ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s')

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(colored_formatter)
    console_handler.addFilter(lambda record: setattr(record, 'is_console', True) or True)

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(log_formatter)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.DEBUG),
        handlers=[console_handler, file_handler]
    )

setup_logging()

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
    
    missing_keys = [key for key in REQUIRED_KEYS if key not in config]
    if missing_keys:
        logging.critical(f"Missing required configuration keys: {', '.join(missing_keys)}")
        sys.exit(1)
    
    return config

config = load_config(CONFIG_PATH)
OVERSEERR_BASEURL = config['OVERSEERR_BASEURL']
DRY_RUN = config['DRY_RUN']
API_KEYS = config['API_KEYS']
TV_CATEGORIES = config['TV_CATEGORIES']
MOVIE_CATEGORIES = config['MOVIE_CATEGORIES']

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
            logging.info(f"Release Date: {release_date.strftime('%Y-%m-%d')}")
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

    logging.info(f"Streaming Providers: {providers}")
    logging.info(f"Genres: {genres}")
    logging.info(f"Keywords: {keywords}")
    logging.info(f"Production Companies: {production_companies}")
    logging.info(f"Networks: {networks}")
    logging.info(f"Original Language: {original_language}")
    logging.info(f"Status: {status}")

    return genres, keywords, release_year, providers, production_companies, networks, original_language, status

def validate_categories(categories, media_type):
    valid = True
    default_category_key = categories.get("default")

    for category_name, category_data in categories.items():
        if not isinstance(category_data, dict):
            continue

        filters = category_data.get("filters", {})
        apply = category_data.get("apply", {})

        if "weight" not in category_data:
            logging.error(f"Category '{category_name}' must have 'weight'.")
            valid = False

        if "root_folder" not in apply:
            logging.error(f"Category '{category_name}' must have 'root_folder' in 'apply'.")
            valid = False

        required_id = "sonarr_id" if media_type == 'tv' else "radarr_id"
        id_key = "sonarr_id" if media_type == 'tv' else "radarr_id"
        if required_id not in apply:
            logging.error(f"Category '{category_name}' must have '{id_key}' in 'apply' for {media_type.upper()} categories.")
            valid = False

        if category_name != default_category_key:
            genres = filters.get("genres", [])
            keywords = filters.get("keywords", [])
            if not genres and not keywords:
                logging.error(f"Category '{category_name}' must have at least one of 'genres' or 'keywords' in 'filters'.")
                valid = False

    if not default_category_key or default_category_key not in categories:
        logging.error(f"The 'default' category '{default_category_key}' is not properly defined in the configuration for {media_type.upper()}_CATEGORIES.")
        valid = False

    return valid

def validate_configuration():
    tv_valid = validate_categories(TV_CATEGORIES, 'tv')
    movie_valid = validate_categories(MOVIE_CATEGORIES, 'movie')

    if not (tv_valid and movie_valid):
        logging.critical("Configuration validation failed. Please fix the errors and restart the script.")
        sys.exit(1)
    
    # Add confirmation log after successful validation
    logging.info(f"{Colors.OKGREEN}Configuration loaded and validated successfully.{Colors.ENDC}")

def fuzzy_match(list_to_check, possible_values, threshold=80):
    for item in list_to_check:
        for value in possible_values:
            if fuzz.ratio(item.lower(), value.lower()) >= threshold:
                return value
    return None

def categorize_media(genres, keywords, title, overseerr_data, media_type):
    age_ratings = extract_age_ratings(overseerr_data, media_type)
    age_rating = choose_common_or_strictest_rating(age_ratings)
    logging.info(f"Age ratings collected: {age_ratings}. Most common or strictest: {age_rating}")

    best_match = None
    highest_weight = -1  # Allow weights of 0
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

        if not genres_filters and not keywords_filters:
            continue

        matched_genre = fuzzy_match(genres, genres_filters)
        matched_keyword = fuzzy_match(keywords, keywords_filters)

        if matched_genre or matched_keyword:
            logging.debug(f"Potential match found: {category} (genre match: {matched_genre}, keyword match: {matched_keyword})")
            if data["weight"] > highest_weight:
                best_match = category
                highest_weight = data["weight"]

    if not best_match and default_category_key in categories:
        folder_data = categories[default_category_key]
        filters = folder_data.get("filters", {})
        excluded_ratings = filters.get("excluded_ratings", [])

        if age_rating in excluded_ratings:
            logging.error(f"Age rating {age_rating} excludes the default category '{default_category_key}'.")
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
    sorted_rules = sorted(rules, key=lambda x: x.get('priority', 9999))
    for rule in sorted_rules:
        condition = rule.get('condition', {})
        profile_id = rule.get('profile_id')
        logic = rule.get('logic', 'OR').upper()

        if logic not in ['AND', 'OR']:
            logging.warning(f"Unsupported logic '{logic}' in rule. Defaulting to 'OR'.")
            logic = 'OR'

        if evaluate_condition(condition, context, logic):
            logging.info(f"Rule matched: {rule}. Applying profile ID: {profile_id}")
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
                        if not any(operator_func(item, t_value) for item in context_value):
                            logging.debug(f"No match found for '{key}' with operator '{operator_str}' and target '{t_value}'.")
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

        logging.info(f"{Colors.HEADER}{Colors.BOLD}Starting processing for: {Colors.ENDC}{Colors.OKBLUE}{media_title} (Request ID: {request_id}, User: {request_username}){Colors.ENDC}")
        logging.info(f"{Colors.OKCYAN}Media Type: {Colors.ENDC}{Colors.OKBLUE}{media_type}{Colors.ENDC}")

        get_url = f"{OVERSEERR_BASEURL}/api/v1/{media_type}/{media_tmdbid}"
        headers = {'accept': 'application/json', 'X-Api-Key': API_KEYS['overseerr']}

        response = session.get(get_url, headers=headers, timeout=5)
        if response.status_code != 200:
            logging.error(f"Error fetching media details: {response.status_code} {response.text}")
            return
        overseerr_data = response.json()

        logging.debug(f"Overseerr Data: {json.dumps(overseerr_data, indent=2)}")
        logging.info(f"{Colors.OKCYAN}Fetched media details from Overseerr{Colors.ENDC}")

        genres, keywords, release_year, providers, production_companies, networks, original_language, status = get_media_data(overseerr_data, media_type)
        target_root_folder, best_match = categorize_media(genres, keywords, media_title, overseerr_data, media_type)

        if not target_root_folder or not best_match:
            logging.error("Unable to determine target root folder or category.")
            return

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
        quality_profile_rules = apply_data.get('quality_profile_rules', [])

        profile_id = evaluate_quality_profile_rules(quality_profile_rules, context) or default_profile_id
        if not profile_id:
            logging.error("Unable to determine quality profile ID.")
            return

        put_data = {}
        if media_type == 'movie':
            radarr_id = apply_data.get("radarr_id")
            if radarr_id is None:
                logging.error(f"'radarr_id' is missing in 'apply' for category '{best_match}'.")
                return
            target_name = apply_data.get("app_name")

            put_data = {
                "mediaType": media_type,
                "rootFolder": target_root_folder,
                "serverId": radarr_id,
                "profileId": profile_id
            }

            logging.info(f"{Colors.OKCYAN}Using Radarr for: {Colors.ENDC}{Colors.OKBLUE}{target_name}{Colors.ENDC}")
            logging.info(f"{Colors.OKCYAN}Categorized as: {Colors.ENDC}{Colors.OKBLUE}{best_match}{Colors.ENDC}")

        elif media_type == 'tv':
            sonarr_id = apply_data.get("sonarr_id")
            if sonarr_id is None:
                logging.error(f"'sonarr_id' is missing in 'apply' for category '{best_match}'.")
                return
            target_name = apply_data.get("app_name")

            try:
                seasons_str = request_data['extra'][0]['value']
                seasons = [int(season) for season in seasons_str.split(',')]
            except (KeyError, IndexError, ValueError) as e:
                logging.error(f"Error parsing seasons: {e}")
                return

            put_data = {
                "mediaType": media_type,
                "seasons": seasons,
                "rootFolder": target_root_folder,
                "serverId": sonarr_id,
                "profileId": profile_id
            }

            logging.info(f"{Colors.OKCYAN}Using Sonarr for: {Colors.ENDC}{Colors.OKBLUE}{target_name}{Colors.ENDC}")
            logging.info(f"{Colors.OKCYAN}Categorized as: {Colors.ENDC}{Colors.OKBLUE}{best_match}{Colors.ENDC}")

        headers.update({'Content-Type': 'application/json'})

        if put_data:
            if DRY_RUN:
                logging.warning(f"{Colors.WARNING}[DRY RUN] No changes made. Would update request {request_id} to use {target_name}, root folder {put_data['rootFolder']}, and quality profile {profile_id}.{Colors.ENDC}")
            else:
                put_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}"
                response = session.put(put_url, headers=headers, json=put_data, timeout=5)
                if response.status_code == 200:
                    logging.info(f"{Colors.OKGREEN}Request updated: {Colors.ENDC}{Colors.OKBLUE}{target_name}, root folder {put_data['rootFolder']}, and quality profile {profile_id}.{Colors.ENDC}")
                    # Auto approve request
                    approve_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}/approve"
                    approve_response = session.post(approve_url, headers=headers, timeout=5)

                    if approve_response.status_code == 200:
                        logging.info(f"{Colors.OKGREEN}Request {request_id} approved successfully.{Colors.ENDC}")
                    else:
                        logging.error(f"{Colors.FAIL}Error auto-approving request {request_id}: {Colors.ENDC}{Colors.OKBLUE}{approve_response.content}{Colors.ENDC}")
                else:
                    logging.error(f"{Colors.FAIL}Error updating request {request_id}: {Colors.ENDC}{Colors.OKBLUE}{response.content}{Colors.ENDC}")
        else:
            logging.error(f"{Colors.FAIL}Error: Unable to determine appropriate service for the request.{Colors.ENDC}")
    except Exception as e:
        logging.error(f"{Colors.FAIL}Exception occurred during request processing: {Colors.ENDC}{Colors.OKBLUE}{str(e)}{Colors.ENDC}", exc_info=True)

if __name__ == '__main__':
    validate_configuration()  # Validate configuration at startup
    
    # Add startup confirmation logs
    logging.info(f"{Colors.OKGREEN}Configuration is valid. Starting the server...{Colors.ENDC}")
    logging.info(f"{Colors.OKGREEN}Server is running on http://0.0.0.0:12210{Colors.ENDC}")
    
    serve(app, host='0.0.0.0', port=12210, threads=5, connection_limit=200)