# OverFiltrr

OverFiltrr is a webhook service that integrates with [Overseerr](https://docs.overseerr.dev/) to automatically handle and categorise media requests based on user-defined rules. It enhances media management by automatically approving and organising TV and movie requests according to configurations such as quality profiles and root folders. OverFiltrr uses genres, keywords, and age ratings gathered from Overseerr to Categorise content, making media organisation smoother and more efficient.

## Features

- **Automatic Media Request Handling**: Integrates with Overseerr to automatically process media requests.
- **Categorisation**: Categorises movies and TV shows based on genres, keywords, and age ratings.
- **Age Rating Exclusions**: Exclude certain categories based on age ratings (e.g., prevent adult-rated movies from being Categorised as "Children").
- **Quality Profile Selection**: Supports custom quality profiles for both Radarr and Sonarr.
  - **Default**: Select a Default if nothing is matched.
  - **Rule Based**: Set rules to chose a specific Quality Profile
- **API Integration**: Fetches metadata from Overseerr for categorisation.
- **Dry Run Support**: Test changes without making actual modifications by enabling DRY_RUN mode.

> [!WARNING]
> OverFiltrr is a work in progress, and there's probably bugs.
> It has been working for me with minimal problems

## Prerequisites

- **Python 3.7 or higher**
- **Access to an Overseerr instance**
- **API Keys**:
    - [Overseerr API Key](https://docs.overseerr.dev/api-reference/authentication)

## Installation

### Clone the Repository

```
git clone https://github.com/nickelslol/overfiltrr.git
cd overfiltrr
```
## Install the required packages:
```
pip install -r requirements.txt
```

## Configuration

Before running OverFiltrr, you need to configure it according to your setup.

### Edit the Configuration Variables

Edit the config.yaml:
```
# Base URL for your Overseerr instance (e.g., "http://localhost:5055")
OVERSEERR_BASEURL: "http://127.0.0.1:5055"

# Set to 'true' to enable dry run mode (no changes will be made), 'false' to apply changes
DRY_RUN: false

# API keys for accessing Overseerr and other services
API_KEYS:
  # **Mandatory**: Overseerr API key (replace with your actual API key)
  overseerr: "YOUR_OVERSEERR_API_KEY"
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
TV_CATEGORIES:
  KidsTV:
    apply:
      root_folder: "/path/to/your/kids/tv/shows"
      default_profile_id: 3
      sonarr_id: 0
      app_name: "Kids TV Shows"
    weight: 100
    filters:
      genres:
        - "Animation"
        - "Family"
      # **Optional**: Exclude TV shows with certain age ratings
      excluded_ratings:
        - "TV-MA"
```
#### MOVIE_CATEGORIES

Now includes excluded_ratings to prevent certain age-rated content from being Categorised into specific categories.

Example:
```
MOVIE_CATEGORIES:
  FamilyMovies:
    apply:
      root_folder: "/path/to/your/family/movies"
      default_profile_id: 4
      radarr_id: 0
      app_name: "Family Movies"
    weight: 100
    filters:
      genres:
        - "Family"
        - "Animation"
      excluded_ratings:
        - "R"
        - "NC-17"
```

### **Category Configuration Parameters**

Within each category under **TV_CATEGORIES** or **MOVIE_CATEGORIES**:

filters: # (Optional)
  - **genres**: A list of genres to match.
  - **keywords**: A list of keywords to match.
  - **excluded_ratings** (Movies only): A list of age ratings to exclude from this category.

apply:
  - **root_folder**: The path where the media should be saved.
  - **sonarr_id** (TV only): The Sonarr server ID to use.
  - **radarr_id** (Movies only): The Radarr server ID to use.
  - **default_profile_id**: The default quality profile ID to use.
  - **quality_profile_rules** (Optional): Advanced rules to select quality profiles based on media attributes.
  - **app_name** (Optional): A name identifier for the server, used in logging.

weight:
  - **weight**: A numerical value to determine priority when multiple categories match (higher takes precedence).

default:
  - **default**: Specifies the default category to use when no other categories match.

## **Advanced Configuration Parameters (Within quality_profile_rules)**

### Within **quality_profile_rules** in the apply section:
quality_profile_rules:
  - **priority**: Determines the order in which rules are evaluated (lower numbers first).
  - **condition**: Conditions to match against media attributes.
    - **Operators Supported**: ==, !=, >, <, >=, <=, in, not in.
  - **profile_id**: The quality profile ID to apply if the condition matches.
  - **logic** (Optional): Logical operator to combine conditions ('AND' or 'OR', default is 'OR').

### **Media Attributes Available for Conditions:**

  - **release_year**: The year the media was released.
  - **original_language**: The original language of the media (e.g., 'en' for English).
  - **providers**: Streaming providers associated with the media.
  - **production_companies**: Production companies involved.
  - **networks** (TV only): Networks for TV shows.
  - **status**: Current status (e.g., 'Ended', 'Returning Series').
  - **genres**: List of genres.
  - **keywords**: List of keywords.
  - **media_type**: 'movie' or 'tv'.

### By setting these parameters in your **config.yaml**, you can customize how the script processes and categorizes media requests.


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

- **Webhook Triggered:** Overseerr sends a `MEDIA_PENDING` notification to OverFiltrr.
- **Data Fetching:** OverFiltrr fetches additional metadata from Overseerr to help with categorisation.
- **Categorisation:**
  - **Movies:**
    - **Genre and Keyword Matching:** Checks the genres and keywords of the movie to match against configured filters.
    - **Rating Exclusion:** Filters out movies that match any age ratings specified in `excluded_ratings`.
    - **Quality Profile Selection:** Uses quality profile rules within `quality_profile_rules` to select an appropriate quality profile based on movie attributes.
    - **Weight-Based Matching:** Determines the best matching category based on the weight parameter to prioritise categories.
  - **TV Shows:**
    - **Genre and Keyword Matching:** Similar to movies, it checks genres and keywords to match the best category.
    - **Profile Selection:** Uses `sonarr_id` for server selection and `profile_id` for quality profile matching.
    - **Network and Status Filters:** Allows categorisation based on TV network and show status (e.g., `Returning Series` or `Ended`).
- **Request Update:** OverFiltrr updates the request in Overseerr with the appropriate `server_id`, `root_folder`, and `quality_profile`.
- **Auto-Approval:** If not in `DRY_RUN` mode, OverFiltrr automatically approves the request once categorised.

By configuring parameters like `filters`, `apply`, and `quality_profile_rules` in `config.yaml`, OverFiltrr can handle diverse criteria for categorising media requests and applying quality settings.


### Usage

Once set up, OverFiltrr will automatically process new media requests received from Overseerr, Categorise them based on your configurations, and update the requests accordingly. If DRY_RUN is set to False, it will also approve the requests.

### Dry Run Mode

- **Enable Dry Run:** Set DRY_RUN = True in the configuration.
- **Purpose:** Test your configurations without making any changes to Overseerr or your download clients.
- **Output:** The script will log what it would have done without actually performing any actions.
