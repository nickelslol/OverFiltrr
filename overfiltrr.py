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

def get_tmdb_movie_certification(tmdb_id):
    if not API_KEYS.get('tmdb'):
        logging.warning(f"{Colors.WARNING}TMDB API key not provided, skipping TMDB certification lookup.{Colors.ENDC}")
        return []  # Return an empty list if TMDB key is not provided

    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/release_dates"
    params = {'api_key': API_KEYS['tmdb']}
    response = session.get(url, params=params, timeout=5)
    if response.status_code == 200:
        data = response.json()
        certifications = []
        for country_data in data['results']:
            if country_data['iso_3166_1'] == 'US':
                for release in country_data['release_dates']:
                    certification = release.get('certification', '')
                    if certification:
                        certifications.append(certification)
        return certifications
    return []

def get_omdb_movie_certification(imdb_id):
    if not API_KEYS.get('omdb'):
        logging.warning(f"{Colors.WARNING}OMDB API key not provided, skipping OMDB certification lookup.{Colors.ENDC}")
        return []  # Return an empty list if OMDB key is not provided

    url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={API_KEYS['omdb']}"
    response = session.get(url, timeout=5)
    if response.status_code == 200:
        omdb_data = response.json()
        return [omdb_data.get('Rated', '')] if omdb_data.get('Rated') else []
    return []

def get_tvdb_show_details(tvdb_id):
    if not API_KEYS.get('tvdb'):
        logging.warning(f"{Colors.WARNING}TVDB API key not provided, skipping TVDB show details lookup.{Colors.ENDC}")
        return None  # Return None if TVDB key is not provided

    url = f"https://api.thetvdb.com/series/{tvdb_id}"
    headers = {'Authorization': f'Bearer {API_KEYS["tvdb"]}'}
    response = session.get(url, headers=headers, timeout=5)
    if response.status_code == 200:
        return response.json()
    else:
        logging.error(f"Error fetching TV show details from TVDB: {response.content}")
        return None

def get_omdb_movie_details(imdb_id):
    if not validate_imdb_id(imdb_id):
        logging.error(f"{Colors.FAIL}Invalid IMDb ID: {Colors.ENDC}{Colors.OKBLUE}{imdb_id}{Colors.ENDC}")
        return None

    url = f"http://www.omdbapi.com/?i={imdb_id}&apikey={API_KEYS['omdb']}"
    response = session.get(url, timeout=5)
    if response.status_code == 200:
        omdb_data = response.json()
        if omdb_data.get("Response", "False") == "True":
            return omdb_data
        else:
            logging.error(f"{Colors.FAIL}OMDB returned an error: {Colors.ENDC}{Colors.OKBLUE}{omdb_data.get('Error')}{Colors.ENDC}")
            return None
    else:
        logging.error(f"{Colors.FAIL}Error fetching OMDB details: {Colors.ENDC}{Colors.OKBLUE}{response.content}{Colors.ENDC}")
        return None

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

def get_tmdb_movie_details_and_keywords(tmdb_id):
    if not API_KEYS.get('tmdb'):
        logging.warning(f"{Colors.WARNING}TMDB API key not provided, skipping TMDB movie details and keywords lookup.{Colors.ENDC}")
        return None, [], ''  # Return default values if TMDB key is not provided

    details_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    keywords_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/keywords"
    params = {'api_key': API_KEYS['tmdb'], 'language': 'en-US'}

    details_response = session.get(details_url, params=params, timeout=5)
    keywords_response = session.get(keywords_url, params=params, timeout=5)

    if details_response.status_code == 200 and keywords_response.status_code == 200:
        details_data = details_response.json()
        keywords_data = keywords_response.json().get('keywords', [])
        keywords = [k['name'] for k in keywords_data]
        imdb_id = details_data.get('imdb_id', '')
        return details_data, keywords, imdb_id
    else:
        logging.error("Error fetching TMDB details or keywords")
        return None, [], ''

def get_movie_data(tmdb_id, overseerr_data):
    # Use genres and keywords from Overseerr if TMDB API is not available
    overseerr_genres = [g['name'] for g in overseerr_data.get('genres', [])]
    overseerr_keywords = [k['name'] for k in overseerr_data.get('keywords', [])] if 'keywords' in overseerr_data else []

    # Try to get data from TMDB and OMDB APIs, if API keys are available
    tmdb_data, tmdb_keywords, imdb_id = get_tmdb_movie_details_and_keywords(tmdb_id) if API_KEYS.get('tmdb') else (None, [], '')

    # If no TMDB data, use Overseerr data
    tmdb_genres = [g['name'] for g in tmdb_data.get('genres', [])] if tmdb_data else []
    tmdb_keywords = [kw.strip() for kw in tmdb_keywords if kw.strip()] if tmdb_data else []

    # Combine Overseerr and TMDB data
    genres = set(overseerr_genres + tmdb_genres)
    keywords = set(overseerr_keywords + tmdb_keywords)

    # If no IMDb ID, fallback to just Overseerr data and log
    if not imdb_id:
        logging.warning(f"{Colors.WARNING}IMDb ID is missing or unavailable, using Overseerr data only.{Colors.ENDC}")

    # Remove empty strings from genres and keywords
    genres.discard('')
    keywords.discard('')

    logging.info(f"{Colors.OKCYAN}Consolidated genres: {Colors.ENDC}{Colors.OKBLUE}{list(genres)}{Colors.ENDC}")
    logging.info(f"{Colors.OKCYAN}Consolidated keywords: {Colors.ENDC}{Colors.OKBLUE}{list(keywords)}{Colors.ENDC}")

    return list(genres), list(keywords), imdb_id

def categorize_movie(genres, keywords, title, tmdb_id, imdb_id):
    # Fetch the age ratings from both TMDB and OMDB
    tmdb_ratings = get_tmdb_movie_certification(tmdb_id)
    omdb_ratings = get_omdb_movie_certification(imdb_id)

    # Combine all ratings and find the most common or strictest rating
    all_ratings = tmdb_ratings + omdb_ratings
    age_rating = choose_common_or_strictest_rating(all_ratings)

    # Log age rating
    logging.info(f"Age ratings collected: {Colors.OKBLUE}{all_ratings}.{Colors.ENDC} {Colors.OKCYAN}Most common or strictest:{Colors.ENDC} {Colors.OKBLUE}{age_rating}{Colors.ENDC}")

    # Fuzzy matching logic for genres and keywords
    def fuzzy_match(list_to_check, possible_values, threshold=80):
        for item in list_to_check:
            for value in possible_values:
                if fuzz.ratio(item.lower(), value.lower()) >= threshold:
                    return value
        return None

    best_match = None
    highest_weight = 0

    # Dynamically determine the best category based on genres, keywords, and weight
    for category, data in MOVIE_CATEGORIES.items():
        if category == "default":
            continue  # Skip the default entry

        # Skip this category if the age rating is in the excluded list
        if age_rating in data.get("excluded_ratings", []):
            logging.info(f"Age rating {age_rating} excludes the category '{category}'.")
            continue

        # Fuzzy match on both genres and keywords
        matched_genre = fuzzy_match(genres, data["genres"])
        matched_keyword = fuzzy_match(keywords, data["keywords"])

        # If a match is found in either genres or keywords
        if matched_genre or matched_keyword:
            logging.debug(f"Potential match found: {category} (genre match: {matched_genre}, keyword match: {matched_keyword})")
            # Check if this category has a higher weight than the previous match
            if data["weight"] > highest_weight:
                best_match = category
                highest_weight = data["weight"]

    # Log the final decision
    if best_match:
        folder_data = MOVIE_CATEGORIES[best_match]
        root_folder = folder_data["root_folder"]
        profile_id = folder_data["profile_id"]
        return root_folder, profile_id, best_match  # Correctly return profile_id

    # If no match, default to General
    logging.info("Defaulting to General category")
    folder_data = MOVIE_CATEGORIES["General"]
    root_folder = folder_data["root_folder"]
    profile_id = folder_data["profile_id"]
    return root_folder, profile_id, "General"

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
        tvdb_id = request_data['media'].get('tvdbId')

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

        # Movie logic
        if media_type == 'movie':
            imdb_id = overseerr_data.get('imdbId')

            if not imdb_id:
                logging.warning(f"{Colors.WARNING}IMDb ID is missing or unavailable, using Overseerr data only.{Colors.ENDC}")
                if API_KEYS.get('tmdb'):
                    tmdb_url = f"https://api.themoviedb.org/3/movie/{media_tmdbid}?api_key={API_KEYS['tmdb']}"
                    tmdb_response = session.get(tmdb_url, timeout=5)
                    if tmdb_response.status_code == 200:
                        tmdb_data = tmdb_response.json()
                        imdb_id = tmdb_data.get('imdb_id')
                        if imdb_id:
                            logging.info(f"{Colors.OKCYAN}IMDb ID fetched from TMDB: {Colors.ENDC}{Colors.OKBLUE}{imdb_id}{Colors.ENDC}")
                    else:
                        logging.error(f"{Colors.FAIL}Failed to fetch IMDb ID from TMDB.{Colors.ENDC}")

            if not imdb_id:
                logging.error(f"{Colors.FAIL}IMDb ID missing for {media_title}. Unable to categorize movie.{Colors.ENDC}")
                return

            genres, keywords, _ = get_movie_data(media_tmdbid, overseerr_data)
            best_match = MOVIE_CATEGORIES["default"]
            highest_weight = 0
            for category, data in MOVIE_CATEGORIES.items():
                if category == "default":
                    continue
                matched_genre = any(genre in genres for genre in data["genres"])
                matched_keyword = any(keyword.lower() in keywords for keyword in data["keywords"])
                if (matched_genre or matched_keyword) and data["weight"] > highest_weight:
                    best_match = category
                    highest_weight = data["weight"]

            folder_data = MOVIE_CATEGORIES[best_match]
            target_root_folder = folder_data["root_folder"]
            profile_id = folder_data.get("profile_id", None)
            radarr_id = folder_data["radarr_id"]
            target_name = folder_data["app_name"]

            put_data = {
                "mediaType": media_type,
                "rootFolder": target_root_folder,
                "radarrId": radarr_id,
            }

            if profile_id is not None:
                put_data["profileId"] = profile_id

            logging.info(f"{Colors.OKCYAN}Using Radarr for: {Colors.ENDC}{Colors.OKBLUE}{target_name}{Colors.ENDC}")
            logging.info(f"{Colors.OKCYAN}Categorized as: {Colors.ENDC}{Colors.OKBLUE}{best_match}{Colors.ENDC}")

        # TV Show logic
        elif media_type == 'tv':
            overseerr_genres = [genre['name'] for genre in overseerr_data.get('genres', [])]
            overseerr_keywords = [keyword['name'] for keyword in overseerr_data.get('keywords', [])] if 'keywords' in overseerr_data else []
            genres = overseerr_genres
            keywords = overseerr_keywords

            if tvdb_id:
                tvdb_data = get_tvdb_show_details(tvdb_id)
                if tvdb_data:
                    tvdb_genres = [genre['name'] for genre in tvdb_data.get('genres', [])]
                    tvdb_keywords = tvdb_data.get('aliases', [])
                    genres += tvdb_genres
                    keywords += tvdb_keywords
                else:
                    logging.warning(f"{Colors.WARNING}TVDB data is missing or could not be fetched.{Colors.ENDC}")

            genres = list(set(genre.strip() for genre in genres if genre.strip()))
            keywords = list(set(kw.strip() for kw in keywords if kw.strip()))

            logging.info(f"{Colors.OKCYAN}Consolidated genres: {Colors.ENDC}{Colors.OKBLUE}{genres}{Colors.ENDC}")
            logging.info(f"{Colors.OKCYAN}Consolidated keywords: {Colors.ENDC}{Colors.OKBLUE}{keywords}{Colors.ENDC}")

            best_match = TV_CATEGORIES["default"]
            highest_weight = 0
            for cat, rules in TV_CATEGORIES.items():
                if cat == "default":
                    continue
                matched_genre = any(genre in genres for genre in rules["genres"])
                matched_keyword = any(keyword.lower() in keywords for keyword in rules["keywords"])
                if (matched_genre or matched_keyword) and rules["weight"] > highest_weight:
                    best_match = cat
                    highest_weight = rules["weight"]

            folder_data = TV_CATEGORIES[best_match]
            target_root_folder = folder_data["root_folder"]
            sonarr_id = folder_data["sonarr_id"]
            target_name = folder_data["app_name"]
            profile_id = folder_data.get("profile_id", None)

            seasons = request_data['extra'][0]['value'].split(',')
            seasons = [int(season) for season in seasons]

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
        logging.error(f"{Colors.FAIL}Exception occurred during request processing: {Colors.ENDC}{Colors.OKBLUE}{str(e)}{Colors.ENDC}")

if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=12210, threads=5, connection_limit=200)