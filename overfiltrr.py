import os
import re
import time
import logging
from flask import Flask, request
from waitress import serve
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import config
from rapidfuzz import fuzz

app = Flask(__name__)

# Configuration
OVERSEERR_BASEURL = config.OVERSEERR_BASEURL
DRY_RUN = config.DRY_RUN
API_KEYS = config.API_KEYS
TV_CATEGORIES = config.TV_CATEGORIES
MOVIE_CATEGORIES = config.MOVIE_CATEGORIES

# Ensure the logs directory exists
script_dir = os.path.dirname(os.path.abspath(__file__))
log_directory = os.path.join(script_dir, 'logs')
os.makedirs(log_directory, exist_ok=True)

# Log file path
log_file = os.path.join(log_directory, 'script.log')

# Configuration
LOG_LEVEL = "INFO"  # Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"

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
        if hasattr(record, 'is_console'):
            if record.levelno == logging.WARNING:
                return f"{Colors.WARNING}{message}{Colors.ENDC}"
            elif record.levelno in (logging.ERROR, logging.CRITICAL):
                return f"{Colors.FAIL}{message}{Colors.ENDC}"
            elif record.levelno == logging.INFO:
                return f"{Colors.OKCYAN}{message}{Colors.ENDC}"
            elif record.levelno == logging.DEBUG:
                return f"{Colors.OKBLUE}{message}{Colors.ENDC}"
        return message

# Configure logging
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
colored_formatter = ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s')

console_handler = logging.StreamHandler()
console_handler.setFormatter(colored_formatter)

file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(log_formatter)

# Add a custom attribute to log records for the console handler
class ConsoleFilter(logging.Filter):
    def filter(self, record):
        record.is_console = True
        return True

console_handler.addFilter(ConsoleFilter())

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.DEBUG),
    handlers=[console_handler, file_handler]
)

def validate_imdb_id(imdb_id):
    return re.match(r"tt\d{7,8}", imdb_id) is not None

# Setup requests session with retry logic and connection pooling
session = requests.Session()
retry = Retry(connect=3, backoff_factor=0.5)
adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
session.mount('http://', adapter)
session.mount('https://', adapter)

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
    # Get genres from Overseerr data
    genres = [g['name'] for g in overseerr_data.get('genres', [])]

    # Get keywords from Overseerr data if available
    keywords = [k['name'] for k in overseerr_data.get('keywords', [])] if 'keywords' in overseerr_data else []

    # Get IMDb ID
    if media_type == 'movie':
        imdb_id = overseerr_data.get('imdbId', '')
    elif media_type == 'tv':
        imdb_id = overseerr_data.get('externalIds', {}).get('imdbId', '')
    else:
        imdb_id = ''

    # Remove empty strings from genres and keywords
    genres = [genre for genre in genres if genre.strip()]
    keywords = [keyword for keyword in keywords if keyword.strip()]

    logging.info(f"{Colors.OKCYAN}Genres: {Colors.ENDC}{Colors.OKBLUE}{genres}{Colors.ENDC}")
    logging.info(f"{Colors.OKCYAN}Keywords: {Colors.ENDC}{Colors.OKBLUE}{keywords}{Colors.ENDC}")

    return genres, keywords, imdb_id

def categorize_media(genres, keywords, title, overseerr_data, media_type):
    # Fetch the age ratings from Overseerr data
    age_ratings = extract_age_ratings(overseerr_data, media_type)

    # Combine all ratings and find the most common or strictest rating
    age_rating = choose_common_or_strictest_rating(age_ratings)

    # Log age rating
    logging.info(f"Age ratings collected: {Colors.OKBLUE}{age_ratings}.{Colors.ENDC} {Colors.OKCYAN}Most common or strictest:{Colors.ENDC} {Colors.OKBLUE}{age_rating}{Colors.ENDC}")

    # Fuzzy matching logic for genres and keywords
    def fuzzy_match(list_to_check, possible_values, threshold=80):
        for item in list_to_check:
            for value in possible_values:
                if fuzz.ratio(item.lower(), value.lower()) >= threshold:
                    return value
        return None

    best_match = None
    highest_weight = 0

    # Determine the appropriate category based on media type
    if media_type == 'movie':
        categories = MOVIE_CATEGORIES
    elif media_type == 'tv':
        categories = TV_CATEGORIES
    else:
        categories = {}

    # Dynamically determine the best category based on genres, keywords, and weight
    for category, data in categories.items():
        if category == "default":
            continue  # Skip the default entry

        # Skip this category if the age rating is in the excluded list
        if age_rating in data.get("excluded_ratings", []):
            logging.info(f"Age rating {age_rating} excludes the category '{category}'.")
            continue

        # Fuzzy match on both genres and keywords
        matched_genre = fuzzy_match(genres, data.get("genres", []))
        matched_keyword = fuzzy_match(keywords, data.get("keywords", []))

        # If a match is found in either genres or keywords
        if matched_genre or matched_keyword:
            logging.debug(f"Potential match found: {category} (genre match: {matched_genre}, keyword match: {matched_keyword})")
            # Check if this category has a higher weight than the previous match
            if data["weight"] > highest_weight:
                best_match = category
                highest_weight = data["weight"]

    # Log the final decision
    if best_match:
        folder_data = categories[best_match]
        root_folder = folder_data["root_folder"]
        profile_id = folder_data.get("profile_id")
        return root_folder, profile_id, best_match  # Correctly return profile_id

    # If no match, default to the category specified by "default"
    default_category_key = categories.get("default")
    if default_category_key and default_category_key in categories:
        logging.info(f"Defaulting to '{default_category_key}' category")
        folder_data = categories[default_category_key]
        root_folder = folder_data["root_folder"]
        profile_id = folder_data.get("profile_id")
        return root_folder, profile_id, default_category_key
    else:
        logging.error("Default category not properly defined in configuration.")
        return None, None, None

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
    else:
        return ('Unhandled notification type', 400)

def process_request(request_data):
    try:
        request_username = request_data['request']['requestedBy_username']
        request_id = request_data['request']['request_id']
        media_tmdbid = request_data['media']['tmdbId']
        media_type = request_data['media']['media_type']
        media_title = request_data['subject']

        logging.info(f"{Colors.HEADER}{Colors.BOLD}Starting processing for: {Colors.ENDC}{Colors.OKBLUE}{media_title} (Request ID: {request_id}, User: {request_username}){Colors.ENDC}")
        logging.info(f"{Colors.OKCYAN}Media Type: {Colors.ENDC}{Colors.OKBLUE}{media_type}{Colors.ENDC}")

        get_url = f"{OVERSEERR_BASEURL}/api/v1/{media_type}/{media_tmdbid}?language=en"
        headers = {'accept': 'application/json', 'X-Api-Key': API_KEYS['overseerr']}

        response = session.get(get_url, headers=headers, timeout=5)
        overseerr_data = response.json()

        logging.info(f"{Colors.OKCYAN}Fetched media details from Overseerr{Colors.ENDC}")

        # Initialize put_data as an empty dictionary
        put_data = {}

        # Define the PUT URL for updating the request
        put_url = f"{OVERSEERR_BASEURL}/api/v1/request/{request_id}"

        # Get genres, keywords, and IMDb ID
        genres, keywords, imdb_id = get_media_data(overseerr_data, media_type)

        # Use the categorize_media function to determine the root folder, profile ID, and category name
        target_root_folder, profile_id, best_match = categorize_media(genres, keywords, media_title, overseerr_data, media_type)

        if not target_root_folder or not best_match:
            logging.error("Unable to determine target root folder or category.")
            return

        # Depending on media_type, get the appropriate app and IDs
        if media_type == 'movie':
            categories = MOVIE_CATEGORIES
            folder_data = categories.get(best_match)
            if not folder_data:
                logging.error(f"No configuration found for category '{best_match}'.")
                return

            radarr_id = folder_data.get("radarr_id")
            target_name = folder_data.get("app_name")

            put_data = {
                "mediaType": media_type,
                "rootFolder": target_root_folder,
                "radarrId": radarr_id,
            }

            if profile_id is not None:
                put_data["profileId"] = profile_id

            logging.info(f"{Colors.OKCYAN}Using Radarr for: {Colors.ENDC}{Colors.OKBLUE}{target_name}{Colors.ENDC}")
            logging.info(f"{Colors.OKCYAN}Categorized as: {Colors.ENDC}{Colors.OKBLUE}{best_match}{Colors.ENDC}")

        elif media_type == 'tv':
            categories = TV_CATEGORIES
            folder_data = categories.get(best_match)
            if not folder_data:
                logging.error(f"No configuration found for category '{best_match}'.")
                return

            sonarr_id = folder_data.get("sonarr_id")
            target_name = folder_data.get("app_name")
            profile_id = folder_data.get("profile_id", None)

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
                "sonarrId": sonarr_id,
            }

            if profile_id is not None:
                put_data["profileId"] = profile_id

            logging.info(f"{Colors.OKCYAN}Using Sonarr for: {Colors.ENDC}{Colors.OKBLUE}{target_name}{Colors.ENDC}")
            logging.info(f"{Colors.OKCYAN}Categorized as: {Colors.ENDC}{Colors.OKBLUE}{best_match}{Colors.ENDC}")

        headers.update({'Content-Type': 'application/json'})

        if put_data:
            if DRY_RUN:
                logging.warning(f"{Colors.WARNING}[DRY RUN] No changes made. Would update request {request_id} to use {target_name}, root folder {put_data['rootFolder']}, and quality profile {put_data.get('profileId', 'N/A')}.{Colors.ENDC}")
            else:
                response = session.put(put_url, headers=headers, json=put_data, timeout=5)
                if response.status_code == 200:
                    logging.info(f"{Colors.OKGREEN}Request updated: {Colors.ENDC}{Colors.OKBLUE}{target_name}, root folder {put_data['rootFolder']}, and quality profile {put_data.get('profileId', 'N/A')}.{Colors.ENDC}")
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
    serve(app, host='0.0.0.0', port=12210, threads=5, connection_limit=200)