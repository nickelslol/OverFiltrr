# Configuration
OVERSEERR_BASEURL = "YOUR_OVERSEERR_URL"
DRY_RUN = False

API_KEYS = {
    "overseerr": "YOUR_OVERSEERR_API_KEY",
}

TV_CATEGORIES = {
    "Anime": {
        "genres": ["Animation"],
        "keywords": ["anime"],
        "root_folder": "/path/to/your/anime/tv/folder",
        "profile_id": 9,  
        "sonarr_id": 1,
        "app_name": "Anime",
        "weight": 100
    },
    "TV": {
        "genres": [],  
        "keywords": [],  
        "root_folder": "/path/to/your/tv/folder",
        "profile_id": 17,  
        "sonarr_id": 2,
        "app_name": "TV",
        "weight": 10  
    },
    "default": "TV"  
}

MOVIE_CATEGORIES = {
    "Anime": {
        "genres": ["Anime"],
        "keywords": ["anime"],
        "root_folder": "/path/to/your/anime/movies/folder",
        "profile_id": 9,
        "radarr_id": 1,
        "app_name": "Anime Movies",
        "weight": 100,
        "excluded_ratings": []  
    },
    "Children": {
        "genres": ["Animation", "Animated", "Family"],
        "keywords": ["animation", "animated", "children", "kids", "family"],
        "root_folder": "/path/to/your/children/movies/folder",
        "profile_id": 12,
        "radarr_id": 0,
        "app_name": "Movies",
        "weight": 80,
        "excluded_ratings": ["R", "NC-17", "18", "TV-MA", "PG-13"]  
    },
    "Documentary": {
        "genres": ["Documentary"],
        "keywords": ["documentary"],
        "root_folder": "/path/to/your/documentary/folder",
        "profile_id": 12,
        "radarr_id": 0,
        "app_name": "Movies",
        "weight": 90,
        "excluded_ratings": [] 
    },
    "StandUp": {
        "genres": ["Stand-Up", "standup", "stand up"],
        "keywords": ["stand-up", "comedy", "standup", "stand up", "stand-up comedy"],
        "root_folder": "/path/to/your/standup/folder",
        "profile_id": 11,
        "radarr_id": 0,
        "app_name": "Movies",
        "weight": 95,
        "excluded_ratings": []  
    },
    "General": {
        "genres": [],
        "keywords": [],
        "root_folder": "/path/to/your/general/movies/folder",
        "profile_id": 12,
        "radarr_id": 0,
        "app_name": "Movies",
        "weight": 10,
        "excluded_ratings": []  
    },
    "default": "General"
}