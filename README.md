# OverFiltrr

**OverFiltrr** is a webhook service that integrates with Overseerr to automatically handle and categorise media requests based on user-defined rules. It enhances media management by automatically approving and organising TV and movie requests according to configurations such as quality profiles and root folders. OverFiltrr uses genres, keywords, and age ratings gathered from Overseerr to categorise content, making media organisation smoother and more efficient.

## Features

- **Dynamic Categorisation**
  - Automatically categorises media based on genres, keywords, age ratings, and more.
  - Exclude certain categories based on age ratings (e.g., prevent adult-rated movies from being grouped with children)
  - Customisable rules for TV and movie categorisation.

- **Quality Profile Management**
  - Applies quality profiles dynamically based on streaming services, release year, language, networks, and more.
  - Supports fallback to default profiles.

- **Notification Integration**
  - Sends rich Discord notifications via Notifiarr when requests are approved or rejected.

- **Customisable**
  - Easily adjust root folders, quality profiles, and filters to suit your needs.
  - Supports additional rules for niche requirements like stand-up comedy or anime.


> [!WARNING]
> - OverFiltrr is a work in progress; it has been working for me with minimal problems.
> - This project was largely developed with help from ChatGPT

## Prerequisites

- **Python 3.8 or higher**
- **Access to an Overseerr instance**
- **API Keys**: [Overseerr API Key](https://docs.overseerr.dev/using-overseerr/settings#api-key)

## Installation

### Clone the Repository

```
git clone https://github.com/nickelslol/overfiltrr.git
cd overfiltrr
```
### Install the required packages:
```
pip install -r requirements.txt
```

## Configuration

Before running OverFiltrr, you need to configure it according to your setup.

### Make a copy of the config:
```
cp example.config.yaml config.yaml
```

### Edit the Configuration Variables

Open the config.yaml in a text editor:
```yaml
# Overall log level for the script. Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL: "INFO"

# The URL of your Overseerr instance
OVERSEERR_BASEURL: "http://127.0.0.1:5055" # Replace with your Overseerr base URL

# DRY_RUN mode
# If true, the script will log actions it would take but not actually perform them.
DRY_RUN: false

# API Keys
API_KEYS:
  overseerr: "YOUR_OVERSEERR_API_KEY"
  # Add other API keys if needed by future integrations

# Server configuration for the webhook listener
SERVER:
  HOST: "0.0.0.0"  # Host to bind the server to (e.g., "0.0.0.0" for all interfaces)
  PORT: 12210       # Port to listen on
  THREADS: 5        # Number of threads for the server
  CONNECTION_LIMIT: 200 # Maximum number of simultaneous connections

# Notifiarr (Optional)
# Configuration for sending notifications via Notifiarr
NOTIFIARR:
  API_KEY: "YOUR_NOTIFIARR_KEY_HERE"
  CHANNEL: 123456789012345678 # Your Notifiarr Channel ID
  SOURCE: "OverFiltrr" # Source label for notifications
  TIMEOUT: 10 # Timeout in seconds for Notifiarr API requests
```

- `LOG_LEVEL`: Defines the verbosity of logs. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.
- `OVERSEERR_BASEURL`: The base URL of your Overseerr instance (e.g., `http://your-overseerr-domain.com`).
- `DRY_RUN`: If `true`, the script logs actions without making changes. Default: `false`.
- `API_KEYS`: Contains API keys for services. Currently, only `overseerr` is used.
- `SERVER`:
    - `HOST`: IP address for the server to bind to. Default: `"0.0.0.0"`.
    - `PORT`: Port for the server to listen on. Default: `12210`.
    - `THREADS`: Number of worker threads for the server. Default: `5`.
    - `CONNECTION_LIMIT`: Maximum number of simultaneous connections. Default: `200`.
- `NOTIFIARR` (Optional):
    - `API_KEY`: Your Notifiarr API key.
    - `CHANNEL`: The Notifiarr channel ID for notifications.
    - `SOURCE`: A source label for notifications (e.g., "OverFiltrr"). Default: `"OverFiltrr"`.
    - `TIMEOUT`: Timeout in seconds for Notifiarr API requests. Default: `10`.

### Configure Categories

Customise the **TV_CATEGORIES** and **MOVIE_CATEGORIES** to define how media should be Categorised and Rules for the Profile ID.

#### TV_CATEGORIES

- **`TV_CATEGORIES`** 
  - Configurations for categorising TV shows.
    - **`default`** : The default TV category if no filters match.
    - **Category Name** :
      - **`filters`** : Filters for genres, keywords, and excluded ratings.
      - **`apply`** :
        - **`root_folder`** : The destination root folder (Required).
        - **`default_profile_id`** : Fallback quality profile ID. This is **required** if `quality_profile_rules` are not defined, are empty, or if no rules match.
        - **`sonarr_id`** : The Sonarr server ID (as configured in Overseerr) for this category (Required).
        - **`app_name`** : A descriptive name for logging and notification purposes (Optional).
      - **`quality_profile_rules`** (Optional): Rules for dynamic quality profile selection. If omitted or empty, `default_profile_id` must be set.
      - **`weight`** : Priority weight for this category (Required). Higher numbers mean higher priority.

Example:
```yaml
TV_CATEGORIES:
  Anime: # Name of the category
    filters: # Optional: criteria for media to fall into this category
      keywords:
        - "anime"
      excluded_ratings: # Optional: list of age ratings to exclude from this category
        - "NC-17"
        - "TV-MA"
        - "R"
    apply:
      root_folder: "/mnt/media/sonarr/Anime"
      # default_profile_id is required here if quality_profile_rules below don't match or are absent
      default_profile_id: 10
      sonarr_id: 3
      app_name: "Anime TV"
    weight: 100 # Higher weight means this category is checked first
    quality_profile_rules: # Optional: rules to dynamically select a quality profile
      - priority: 1
        condition:
          original_language:
            "==": "ja"
        profile_id: 12

      - priority: 2
        condition:
          networks:
            "in": ["Netflix"]
        profile_id: 13
```
#### MOVIE_CATEGORIES

- **`MOVIE_CATEGORIES`** 
  - Configurations for categorising movies.
    - **`default`** : The default movie category if no filters match.
    - **Category Name** :
      - **`filters`** : Filters for genres, keywords, and excluded ratings.
      - **`apply`** :
        - **`root_folder`** : The destination root folder (Required).
        - **`default_profile_id`** : Fallback quality profile ID. This is **required** if `quality_profile_rules` are not defined, are empty, or if no rules match.
        - **`radarr_id`** : The Radarr server ID (as configured in Overseerr) for this category (Required).
        - **`app_name`** : A descriptive name for logging and notification purposes (Optional).
      - **`quality_profile_rules`** (Optional): Rules for dynamic quality profile selection. If omitted or empty, `default_profile_id` must be set.
      - **`weight`** : Priority weight for this category (Required). Higher numbers mean higher priority.

Example:
```yaml
MOVIE_CATEGORIES:
  KidMovies: # Name of the category
    filters: # Optional: criteria for media to fall into this category
      genres:
        - "Animation"
        - "Family"
      keywords:
        - "kids"
        - "child"
        - "animated"
      excluded_ratings: # Optional: list of age ratings to exclude
        - "R"
        - "NC-17"
        - "TV-MA"
    apply:
      root_folder: "/mnt/media/radarr/KidMovies"
      # default_profile_id is required here if quality_profile_rules below don't match or are absent
      default_profile_id: 15
      radarr_id: 1
      app_name: "Kid Movies"
    weight: 80 # Higher weight means this category is checked first
    quality_profile_rules: # Optional: rules to dynamically select a quality profile
      - priority: 1
        condition:
          providers:
            "in": ["Netflix"]
        profile_id: 16
```
## Quality Profile Rules

The `quality_profile_rules` section within each category allows for dynamic selection of quality profiles based on custom conditions. Below is a breakdown of the structure and available options:

### Rule Structure

Each rule is defined as an object with the following keys:

- **`priority`** :
  - Determines the order in which rules are evaluated. Lower numbers have higher priority.
  - Example: `1`

- **`condition`** :
  - Specifies the criteria for applying the rule. Conditions support logical operators and can include multiple attributes.
  - Supported attributes for conditions:
    - `release_year`: Media's release year.
    - `original_language`: Media's original language (e.g., "en", "ja").
    - `providers`: List of streaming providers for the media (e.g., "Netflix", "Hulu").
    - `production_companies`: List of production companies.
    - `networks` (TV only): List of TV networks.
    - `status`: Media's status (e.g., "Released", "Ended", "Returning Series").
    - `genres`: List of genres associated with the media.
    - `keywords`: List of keywords associated with the media.
  - Supported operators:
    - `<`, `<=`, `>`, `>=`, `==`, `!=` (for single value attributes like `release_year`, `original_language`, `status`)
    - `in`, `not in` (for list attributes like `providers`, `genres`, `keywords`, `networks`, and can also be used with single value attributes if the target in the config is a list)
  - Example:
    ```yaml
    condition:
      release_year:
        "<": 2006
      original_language:
        "!=": "en"
    ```

- **`profile_id`** :
  - The ID of the quality profile to apply when the condition matches.
  - Example: `25`

- **`logic`** *(optional)*:
  - Determines how multiple conditions are evaluated. Default is `OR`.
  - Options: `AND`, `OR`
  - Example: `logic: "AND"`


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
  - Webhook URL: `http://<your_overfiltrr_host_or_ip>:<port>/webhook` (e.g., `http://localhost:12210/webhook` if running locally on the default port).
  - Notification Type: Ensure **Request Pending Approval** is checked. Other types can be enabled but OverFiltrr currently only processes `MEDIA_PENDING` which is triggered by this event.
  - JSON Payload:
  ```json
  {
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
    "{{extra}}": []
  }
  ```
  - **Crucially, you must turn off Auto-Approve for users within Overseerr's user settings.** OverFiltrr handles the approval logic. If Overseerr auto-approves, OverFiltrr might not be able to apply its detailed category and quality profile logic correctly.
  - Save: Click "Save Changes" to add the webhook.


### Usage

Once set up, OverFiltrr will automatically process new media requests received from Overseerr, Categorise them based on your configurations, and update the requests accordingly. If DRY_RUN is set to False, it will also approve the requests.

### Dry Run Mode

- **Enable Dry Run:** Set DRY_RUN = True in the configuration.
- **Purpose:** Test your configurations without making any changes to Overseerr or your download clients.
- **Output:** The script will log what it would have done without actually performing any actions.
