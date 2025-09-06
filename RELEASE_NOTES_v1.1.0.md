OverFiltrr v1.1.0 — Security & Control

Highlights

- Webhook token auth: Enforce static token when `WEBHOOK.TOKEN` is set. Accepts `X-Webhook-Token` from either the HTTP header (preferred) or JSON body at `headers.X-Webhook-Token` for clients that can’t set custom headers.
- Approval gate: New `ALLOW_AUTO_APPROVE` (default: true) lets you separate categorization from approval. When false, OverFiltrr updates the request (folder/server/profile) but leaves it Pending Approval.
- CLI helper: `python overfiltrr.py --gen-webhook-token [--size N]` prints a secure random token for use with `WEBHOOK.TOKEN`.
- Config & reliability: Added `SERVER` settings (host/port/threads/connection limit) and `NOTIFIARR.TIMEOUT`; docs and example config updated.
- Overseerr integration: Consolidated client logic; improved request processing and keyword handling.
- Housekeeping: Removed unused modules/tests; trimmed requirements; logging/docs polish.

Upgrade Notes

- Optional but recommended: set `WEBHOOK.TOKEN` in `config.yaml` and configure your Overseerr webhook to send `X-Webhook-Token` (header or JSON body field `headers.X-Webhook-Token`).
- If you want to review categorizations before approval, set `ALLOW_AUTO_APPROVE: false`.
- Review `SERVER` and `NOTIFIARR` settings in `example.config.yaml` for new tunables.

Selected Changes Since v1.0

- feat(webhook): accept token from JSON body headers as fallback when client cannot set HTTP header; docs: clarify accepted token locations (188823e)
- feat: add webhook token auth, ALLOW_AUTO_APPROVE gate, and CLI token generator (21bd035)
- Cleanup and sync with latest overfiltrr.py; config/server updates; trim requirements (335f489)
- Handle list keywords (f0c0247)
- Add Overseerr API client and use in process_request (5a99446)
- Various documentation and quality improvements

