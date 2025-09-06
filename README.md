# OverFiltrr

OverFiltrr is a small webhook service that listens for Overseerr request events and automatically categorises requests into your Radarr/Sonarr destinations based on simple, configurable rules. It can also send a rich Discord notification via Notifiarr.

## What’s Included

- Single entrypoint: `overfiltrr.py` (runs a small Flask+waitress server)
- Config-driven category rules for TV (`Sonarr`) and Movies (`Radarr`)
- Deterministic anime routing plus flexible scoring and rule matching
- Optional Notifiarr notification payloads (Discord)

## Requirements

- Python 3.9+
- An Overseerr instance and API key

Install dependencies:

```
pip install -r requirements.txt
```

## Configure

1) Copy the example config and edit:

```
cp example.config.yaml config.yaml
```

2) Fill in the required keys (these are validated at startup):

- `OVERSEERR_BASEURL` (e.g., `http://127.0.0.1:5055`)
- `DRY_RUN` (`true` or `false`)
- `ALLOW_AUTO_APPROVE` (`true` or `false`) — if false, OverFiltrr updates the request but leaves it Pending
- `API_KEYS.overseerr` (your Overseerr API key)
- `TV_CATEGORIES` (category map)
- `MOVIE_CATEGORIES` (category map)

Optional Notifiarr settings:

- `NOTIFIARR.API_KEY`
- `NOTIFIARR.CHANNEL`
- `NOTIFIARR.SOURCE` (defaults to `Overseerr` if not set)
- `NOTIFIARR.TIMEOUT` (defaults to `10` seconds)

Notes:

- Server settings are configurable via `SERVER` in `config.yaml` (host, port, threads, connection limit). Defaults: `0.0.0.0:12210`, threads `15`, connection limit `500`.
- Console log level can be set via environment variable `LOG_LEVEL` (e.g., `DEBUG`, `INFO`).
- A JSON log file is written to `logs/script.log`.

Optional webhook security:

- Set `WEBHOOK.TOKEN` to a non-empty value to enforce a static token check on `POST /webhook`.
  - Clients (e.g., Overseerr webhook) must send header: `X-Webhook-Token: <TOKEN>`.
  - If `WEBHOOK.TOKEN` is empty or omitted, token auth is disabled.

## Run

```
python overfiltrr.py
```

Generate a secure webhook token (prints to stdout):

```
python overfiltrr.py --gen-webhook-token [--size 32]
```

Health check:

- `GET /health` → `{ "ok": true }`

Webhook endpoint:

- `POST /webhook` (expects Overseerr-style webhook payloads)

## Configure Overseerr Webhook

In Overseerr → Settings → Notifications → Webhooks:

- URL: `http://<host>:12210/webhook`
- Enable “Request Pending Approval” notifications (OverFiltrr processes `MEDIA_PENDING`).
- Use a JSON payload that includes the fields referenced by OverFiltrr (subject, request.request_id, media.tmdbId, media.media_type, etc.). Example template (placeholders are Overseerr’s):

```
{
  "notification_type": "{{notification_type}}",
  "event": "{{event}}",
  "subject": "{{subject}}",
  "message": "{{message}}",
  "image": "{{image}}",
  "headers": {
    "X-Webhook-Token": "<YOUR_TOKEN>"
  },
  "{{media}}": {
    "media_type": "{{media_type}}",
    "tmdbId": "{{media_tmdbid}}"
  },
  "{{request}}": {
    "request_id": "{{request_id}}",
    "requestedBy_username": "{{requestedBy_username}}"
  },
  "{{extra}}": []
}
```

Important: turn off user auto-approve in Overseerr if you want OverFiltrr to apply category and profile decisions before approval.

## Categories Overview

Define categories under `TV_CATEGORIES` and `MOVIE_CATEGORIES`. Each category can specify:

- `filters` (optional): simple cues like `genres`, `keywords`, `providers`, `networks` that add score towards a category
- `ratings` (optional): rating helpers `{ ceiling: "PG-13", prefer: "PG" }`
- `apply` (required): destination `root_folder`, Sonarr/Radarr server id, and `default_profile_id`
- `quality_profile_rules` (optional): ordered rules to pick a specific profile id based on fields like `release_year`, `original_language`, `providers`, `networks`, `genres`, `keywords`, `status`, and `final_rating` (with operators like `rating_lte`)
- `weight` (required): priority for tie-breaking when multiple categories could match

Example snippet (TV):

```
TV_CATEGORIES:
  Anime:
    is_anime: true
    filters:
      keywords: ["anime"]
      genres: ["Animation"]
    ratings:
      ceiling: "R"
      prefer: "PG-13"
    apply:
      root_folder: "/mnt/media/sonarr/Anime"
      sonarr_id: 3
      default_profile_id: 10
      app_name: "Anime TV"
    weight: 100
```

Example snippet (Movies):

```
MOVIE_CATEGORIES:
  Action:
    filters:
      genres: ["Action"]
    ratings:
      ceiling: "NC-17"
    apply:
      root_folder: "/mnt/media/radarr/Action"
      radarr_id: 2
      default_profile_id: 20
      app_name: "Action Movies"
    weight: 60
```

## DRY RUN

Set `DRY_RUN: true` in `config.yaml` to log all decisions without updating or approving requests in Overseerr.

## Logs

- Console logs have readable colored output.
- File logs are JSON: `logs/script.log`.
- Set `LOG_LEVEL` env var to control verbosity (e.g., `LOG_LEVEL=DEBUG`).

## Notes

- The older `overseerr_api.py` client and legacy tests have been removed; all logic now lives in `overfiltrr.py`.
