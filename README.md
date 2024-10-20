# OverFiltrr

OverFiltrr is a webhook service that integrates with [Overseerr](https://docs.overseerr.dev/) to automatically handle and categorise media requests based on user-defined rules. It enhances media management by automatically approving and organising TV and movie requests according to configurations such as quality profiles and root folders. OverFiltrr uses genres, keywords, and age ratings gathered from various APIs (TMDB, OMDB, TVDB) to Categorise content, making media organisation smoother and more efficient.

## Features

- **Automatic Media Request Handling**: Integrates with Overseerr to automatically process media requests.
- **Dynamic Categorisation**: Categorises movies and TV shows based on genres, keywords, and age ratings.
- **Age Rating Exclusions**: Exclude certain categories based on age ratings (e.g., prevent adult-rated movies from being Categorised as "Children").
- **Quality Profile Selection**: Supports custom quality profiles for both Radarr and Sonarr.
- **API Integration**: Fetches metadata from TMDB, OMDB, and TVDB for accurate categorisation.
- **Custom Logging**: Provides detailed logs with color-coded messages for better readability.
- **Dry Run Support**: Test changes without making actual modifications by enabling DRY_RUN mode.

> [!WARNING]
> OverFiltrr is a work in progress, and there's probably bugs.
> It has been working for me with minimal problems

## Prerequisites

- **Python 3.7 or higher**
- **Access to an Overseerr instance**
- **API Keys**:
  - [Overseerr API Key](https://docs.overseerr.dev/api-reference/authentication)
  - [TMDB API Key](https://developers.themoviedb.org/3/getting-started/introduction)
  - [OMDB API Key](https://www.omdbapi.com/apikey.aspx)
  - [TVDB API Key](https://thetvdb.com/api-information)

## Installation

### Clone the Repository

```
git clone https://github.com/Nickelslol/overfiltrr.git
cd overfiltrr
```
## Install the required packages:
```
pip install -r requirements.txt
```
If a requirements.txt file is not provided, you can install the dependencies manually:
```
pip install Flask waitress requests rapidfuzz
```
## Configuration

Before running OverFiltrr, you need to configure it according to your setup.

### Edit the Configuration Variables

Open the overfiltrr.py script in a text editor and locate the configuration section at the top:
```
# Configuration
OVERSEERR_BASEURL = "http://127.0.0.1:5055"  # Replace with your Overseerr base URL
DRY_RUN = False  # Set to True to enable dry run mode

API_KEYS = {
    "overseerr": "YOUR_OVERSEERR_API_KEY",
    "tmdb": "YOUR_TMDB_API_KEY",
    "omdb": "YOUR_OMDB_API_KEY",
    "tvdb": "YOUR_TVDB_API_KEY"
}
```

- OVERSEERR_BASEURL: Replace with the base URL of your Overseerr instance (e.g., http://your-overseerr-domain.com).
- DRY_RUN: Set to True to test without making actual changes.
- API_KEYS: Replace with your actual API keys.

### Configure Categories

Customise the **TV_CATEGORIES** and **MOVIE_CATEGORIES** dictionaries to define how media should be Categorised.

#### TV_CATEGORIES

Now supports profile_id for quality profile selection in Sonarr.

Example:
```
TV_CATEGORIES = {
    "Anime": {
        "genres": ["Animation"],
        "keywords": ["anime"],
        "root_folder": "/path/to/your/anime/tv",
        "profile_id": 9,  # Sonarr quality profile ID
        "server_id": 1,
        "target_server": "Anime",
        "weight": 100
    },
    "TV": {
        "genres": [],
        "keywords": [],
        "root_folder": "/path/to/your/general/tv",
        "profile_id": 17,  # Sonarr quality profile ID
        "server_id": 2,
        "target_server": "TV",
        "weight": 10
    },
    "default": "TV"
}
```
#### MOVIE_CATEGORIES

Now includes excluded_ratings to prevent certain age-rated content from being Categorised into specific categories.

Example:
```
MOVIE_CATEGORIES = {
    "Anime": {
        "genres": ["Anime"],
        "keywords": ["anime"],
        "root_folder": "/path/to/your/anime/movies",
        "profile_id": 9,
        "server_id": 1,
        "target_server": "Anime Movies",
        "weight": 100,
        "excluded_ratings": []  # No ratings excluded
    },
    "Children": {
        "genres": ["Animation", "Family"],
        "keywords": ["animation", "children", "kids", "family"],
        "root_folder": "/path/to/your/children/movies",
        "profile_id": 12,
        "server_id": 0,
        "target_server": "Movies",
        "weight": 80,
        "excluded_ratings": ["R", "NC-17", "18", "TV-MA", "PG-13"]  # Exclude adult content
    },
    "General": {
        "genres": [],
        "keywords": [],
        "root_folder": "/path/to/your/general/movies",
        "profile_id": 12,
        "server_id": 0,
        "target_server": "Movies",
        "weight": 10,
        "excluded_ratings": []  # No ratings excluded
    },
    "default": "General"
}
```
### Key Parameters:

- **genres:** A list of genres to match.
- **keywords:** A list of keywords to match.
- **root_folder:** The path where the media should be saved.
- **profile_id:** The quality profile ID to use (for Radarr and Sonarr).
- **server_id:** The ID of the server in Overseerr/Sonarr/Radarr.
- **target_server:** A name identifier for the server.
- **weight:** A numerical value to determine priority when multiple categories match.
- **excluded_ratings (Movies only):** A list of age ratings to exclude from this category.

Note: Ensure that the paths, IDs, and profile IDs correspond to your actual setup.


### Running the Script

Start the OverFiltrr webhook server by running the script:
```
python overfiltrr.py
```

The server will start and listen for webhook notifications from Overseerr on port 12210 by default.

### Configuring Overseerr Webhook

To enable Overseerr to communicate with OverFiltrr:

- Navigate to Overseerr Settings: Go to Settings > Notifications > Webhooks.
- Configure the Webhook:
  - Webhook URL: http://your-overfiltrr-domain-or-ip:12210/webhook
  - Payload:
  - ```{
    "notification_type": "{{notification_type}}",
    "event": "{{event}}",
    "subject": "{{subject}}",
    "message": "{{message}}",
    "image": "{{image}}",
    "{{media}}": {
        "media_type": "{{media_type}}",
        "tmdbId": "{{media_tmdbid}}",
        "tvdbId": "{{media_tvdbid}}",
        "status": "{{media_status}}",
        "status4k": "{{media_status4k}}"
    },
    "{{request}}": {
        "request_id": "{{request_id}}",
        "requestedBy_email": "{{requestedBy_email}}",
        "requestedBy_username": "{{requestedBy_username}}",
        "requestedBy_avatar": "{{requestedBy_avatar}}"
    },
    "{{issue}}": {
        "issue_id": "{{issue_id}}",
        "issue_type": "{{issue_type}}",
        "issue_status": "{{issue_status}}",
        "reportedBy_email": "{{reportedBy_email}}",
        "reportedBy_username": "{{reportedBy_username}}",
        "reportedBy_avatar": "{{reportedBy_avatar}}"
    },
    "{{comment}}": {
        "comment_message": "{{comment_message}}",
        "commentedBy_email": "{{commentedBy_email}}",
        "commentedBy_username": "{{commentedBy_username}}",
        "commentedBy_avatar": "{{commentedBy_avatar}}"
    },
    "{{extra}}": []}
    ```
  - Enable the **Request Pending Approval** notification type.
  - You must turn off Auto-Approve for users
  - Save: Click Save to add the webhook.
 
> [!IMPORTANT]
> I repeat you must turn off Auto-Approve for users

Ensure that OverFiltrr is accessible from the Overseerr server. If running on different machines or networks, you may need to configure firewall settings or port forwarding.

### How It Works

When a new media request is made in Overseerr:

- **Webhook Triggered:** Overseerr sends a MEDIA_PENDING notification to OverFiltrr.
- **Data Fetching:** OverFiltrr fetches additional metadata from TMDB, OMDB, and TVDB using provided API keys.
- **Categorisation:**
  - **Movies:**
    - Checks genres, keywords, and age ratings.
    - Excludes categories based on excluded_ratings.
    - Determines the best match based on weight.
  - **TV Shows:**
    - Uses genres and keywords.
    - Now supports profile_id for quality profile selection.
- **Request Update:** OverFiltrr updates the request in Overseerr with the appropriate server, root folder, and quality profile.
- **Auto-Approval:** If not in DRY_RUN mode, the request is automatically approved.


### Usage

Once set up, OverFiltrr will automatically process new media requests received from Overseerr, Categorise them based on your configurations, and update the requests accordingly. If DRY_RUN is set to False, it will also approve the requests.

### Dry Run Mode

- **Enable Dry Run:** Set DRY_RUN = True in the configuration.
- **Purpose:** Test your configurations without making any changes to Overseerr or your download clients.
- **Output:** The script will log what it would have done without actually performing any actions.
