# OverFiltrr

**OverFiltrr** is a webhook service that integrates with Overseerr to automatically handle and categorise media requests based on user-defined rules. It enhances media management by automatically approving and organising TV and movie requests according to configurations such as quality profiles and root folders. OverFiltrr uses genres, keywords, and age ratings gathered from Overseerr to Categorise content, making media organisation smoother and more efficient.

## Features

- **Dynamic Categorisation**
  - Automatically categorises media based on genres, keywords, age ratings, and more.
  - Exclude certain categories based on age ratings (e.g., prevent adult-rated movies from being put with childrens)
  - Customisable rules for TV and movie categorisation.

- **Quality Profile Management**
  - Applies quality profiles dynamically based on streaming services, release year, language, networks, and more.
  - Supports fallback to default profiles.

- **Notification Integration**
  - Sends rich Discord notifications using Notifiarr for approved and declined requests.

- **Customisable**
  - Easily adjust root folders, quality profiles, and filters to suit your needs.
  - Supports additional rules for niche requirements like stand-up comedy or anime.


> [!WARNING]
> - OverFiltrr is a work in progress, it has been working for me with minimal problems.
> - It has vastly been developed with ChatGPT 

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
```
# Configuration
OVERSEERR_BASEURL = "http://127.0.0.1:5055"  # Replace with your Overseerr base URL
DRY_RUN = False  # Set to True to enable dry run mode

API_KEYS = {
    "overseerr": "YOUR_OVERSEERR_API_KEY",

}

# Notifiarr (Optional)
NOTIFIARR:
  API_KEY: "YOUR_NOTIFIARR_KEY_HERE"
  CHANNEL: 123456789012345678  
  SOURCE: "OverFiltrr"
```

- OVERSEERR_BASEURL: Replace with the base URL of your Overseerr instance (e.g., http://your-overseerr-domain.com).
- DRY_RUN: Set to True to test without making actual changes.
- API_KEYS: Replace with your actual API keys.

### Configure Categories

Customise the **TV_CATEGORIES** and **MOVIE_CATEGORIES** to define how media should be Categorised and Rules for the Profile ID.

#### TV_CATEGORIES

- **`TV_CATEGORIES`** 
  - Configurations for categorising TV shows.
    - **`default`** : The default TV category if no filters match.
    - **Category Name** :
      - **`filters`** : Filters for genres, keywords, and excluded ratings.
      - **`apply`** :
        - **`root_folder`** : The destination root folder.
        - **`default_profile_id`** : Default quality profile ID.
        - **`sonarr_id`** : The Sonarr server ID for the category.
        - **`app_name`** : A descriptive name for logging purposes.
        - **`quality_profile_rules`** : Rules for dynamic quality profile selection.
      - **`weight`** : Priority weight for this category.

Example:
```
TV_CATEGORIES:
  Anime:
    filters:
      keywords:
        - "anime"
      excluded_ratings: # (Optional)
        - "NC-17"
        - "TV-MA"
        - "R"

    apply:
      root_folder: "/mnt/media/sonarr/Anime"
      default_profile_id: 10 # (Optional)
      sonarr_id: 3
      app_name: "Anime TV" # (Optional)
    
    weight: 100

    quality_profile_rules: # (Optional)
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
        - **`root_folder`** : The destination root folder.
        - **`default_profile_id`** : Default quality profile ID.
        - **`radarr_id`** : The Radarr server ID for the category.
        - **`app_name`** : A descriptive name for logging purposes.
        - **`quality_profile_rules`** : Rules for dynamic quality profile selection.
      - **`weight`** : Priority weight for this category.

Example:
```
MOVIE_CATEGORIES:

  KidMovies:
    filters:
      genres:
        - "Animation"
        - "Family"
      keywords:
        - "kids"
        - "child"
        - "animated"
      excluded_ratings:
        - "R"
        - "NC-17"
        - "TV-MA"
    apply:
      root_folder: "/mnt/media/radarr/KidMovies"
      default_profile_id: 15
      radarr_id: 1
      app_name: "Kid Movies"
    weight: 80

    quality_profile_rules:
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
  - Supported attributes:
    - **`release_year`**: Matches the media's release year.
    - **`original_language`**: Matches the media's original language.
    - **`streaming_service`**: Matches the media's streaming provider.
    - **`network`** *(TV only)*: Matches the media's originating network.
  - Supported operators:
    - `<`, `<=`, `>`, `>=`, `==`, `!=`, `in`, `not in`
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


### Usage

Once set up, OverFiltrr will automatically process new media requests received from Overseerr, Categorise them based on your configurations, and update the requests accordingly. If DRY_RUN is set to False, it will also approve the requests.

### Dry Run Mode

- **Enable Dry Run:** Set DRY_RUN = True in the configuration.
- **Purpose:** Test your configurations without making any changes to Overseerr or your download clients.
- **Output:** The script will log what it would have done without actually performing any actions.
