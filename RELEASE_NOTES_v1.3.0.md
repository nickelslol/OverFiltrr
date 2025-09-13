# v1.3.0 – Refactor: argparse CLI, early logging, safe JSON logs, runtime init

Highlights

- Refactor to a single argparse-powered CLI in `overfiltrr.py`.
- Early logging initialization with colored console output and safe JSON file logs.
- Runtime config initialization (`init_runtime`) and stricter config validation.
- Improved webhook handling with optional static token validation.
- Notifiarr payload handling hardened and JSON field handling made safer.
- Consolidation/cleanup of unused legacy items and helpers.

Notes

- Console log level can be controlled via `LOG_LEVEL`.
- JSON logs are written to `logs/script.log`.
- See README for updated usage examples (list-ids, token generation, server run).

