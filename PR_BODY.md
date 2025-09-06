This PR hardens the webhook endpoint and adds safer rollout controls.

Key changes:
- Optional webhook token auth: if `WEBHOOK.TOKEN` is set in config, `POST /webhook` requires header `X-Webhook-Token: <TOKEN>` (verified via constant-time compare).
- Approval behavior gate: new `ALLOW_AUTO_APPROVE` config (default: true). When false, the service updates the request (rootFolder/server/profile) but leaves it Pending Approval.
- CLI helper: `python overfiltrr.py --gen-webhook-token [--size N]` prints a random token to stdout for use with `WEBHOOK.TOKEN`.
- Docs/config: `example.config.yaml` and `README.md` updated with usage and security notes.

Behavior notes:
- Token check is enforced only when a token is configured to avoid breaking existing setups.
- `DRY_RUN` continues to bypass PUT/approve calls; the log message indicates whether auto-approve would have occurred.
- Approval call is now behind `ALLOW_AUTO_APPROVE`.

Why:
- Prevent unauthorized sources from triggering updates/approvals.
- Allow teams to separate categorization from approval during rollout or ongoing operations.

Follow-up ideas:
- Optional HMAC signature verification and request size limits.
- Log rotation and stricter JSON logging.
