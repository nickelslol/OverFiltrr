import logging
import logging.handlers
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    # Optional console dependency
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    from rich.live import Live
    from rich.traceback import install as rich_traceback_install
    HAVE_RICH = True
except Exception:
    HAVE_RICH = False

try:
    # Optional faster JSON
    import orjson  # type: ignore
    HAVE_ORJSON = True
except Exception:
    HAVE_ORJSON = False

try:
    # Optional structured formatter
    from pythonjsonlogger import jsonlogger  # type: ignore
    HAVE_JSON_LOGGER = True
except Exception:
    HAVE_JSON_LOGGER = False

import queue
import contextvars
import json


# =========================
# Context
# =========================
cv_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("correlation_id", default=None)
cv_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
cv_media_type: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("media_type", default=None)
cv_tmdb_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tmdb_id", default=None)
cv_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("user", default=None)


def set_context(**kwargs):
    if "correlation_id" in kwargs:
        cv_correlation_id.set(kwargs.get("correlation_id"))
    if "request_id" in kwargs:
        cv_request_id.set(kwargs.get("request_id"))
    if "media_type" in kwargs:
        cv_media_type.set(kwargs.get("media_type"))
    if "tmdb_id" in kwargs:
        cv_tmdb_id.set(kwargs.get("tmdb_id"))
    if "user" in kwargs:
        cv_user.set(kwargs.get("user"))


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Inject contextvars into every record
        for name, cv in (
            ("correlation_id", cv_correlation_id),
            ("request_id", cv_request_id),
            ("media_type", cv_media_type),
            ("tmdb_id", cv_tmdb_id),
            ("user", cv_user),
        ):
            if not hasattr(record, name) or getattr(record, name) is None:
                try:
                    setattr(record, name, cv.get())
                except Exception:
                    setattr(record, name, None)
        return True


def get_logger(name: str = "overfiltrr") -> logging.Logger:
    return logging.getLogger(name)


# =========================
# JSON formatter (fallback if python-json-logger is absent)
# =========================
class NDJSONFormatter(logging.Formatter):
    """Simple JSON Line formatter with stable keys."""

    def __init__(self, *, use_orjson: bool = False):
        super().__init__()
        self.use_orjson = use_orjson and HAVE_ORJSON

    def format(self, record: logging.LogRecord) -> str:
        base: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            # context
            "correlation_id": getattr(record, "correlation_id", None),
            "request_id": getattr(record, "request_id", None),
            "media_type": getattr(record, "media_type", None),
            "tmdb_id": getattr(record, "tmdb_id", None),
            "user": getattr(record, "user", None),
        }
        # Common optional fields
        for k in (
            "event",
            "step",
            "ok",
            "delta_ms",
            "cumulative_ms",
            "decision",
            "category",
            "root",
            "profile_id",
            "score_total",
            "scores",
            "exc_type",
            "exc_msg",
            "exc_stack",
        ):
            v = getattr(record, k, None)
            if v is not None:
                base[k] = v

        try:
            if self.use_orjson:
                return orjson.dumps(base).decode("utf-8")
            return json.dumps(base, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            # Fallback to a minimal, safe line
            return f"{base.get('ts')} {record.levelname} {record.getMessage()}"


# =========================
# Console Card Renderer (minimal, live-capable)
# =========================
@dataclass
class Step:
    name: str
    ok: Optional[bool] = None
    delta_ms: Optional[int] = None
    cumulative_ms: Optional[int] = None


@dataclass
class RequestCard:
    cfg: Dict[str, Any]
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    title: str = ""
    status: str = "running"  # accepted|failed|aborted|rejected|running
    steps: List[Step] = field(default_factory=list)
    decision_line: Optional[str] = None
    media_type: Optional[str] = None
    dry_run: bool = False
    correlation_id: Optional[str] = None
    request_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    user: Optional[str] = None
    scored_table: List[Tuple[str, int, int, List[str]]] = field(default_factory=list)
    scoring_note: Optional[str] = None

    _last_step_time: Optional[datetime] = None
    _console: Optional[Console] = None
    _live: Optional[Live] = None

    def attach_console(self, console: Optional[Console]) -> None:
        self._console = console

    def _now_ms(self) -> int:
        return int((datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000)

    @contextmanager
    def step(self, name: str):
        s = Step(name=name)
        self.steps.append(s)
        start = datetime.now(timezone.utc)
        try:
            self.refresh()
            yield s
            ok = True
        except Exception:
            ok = False
            raise
        finally:
            end = datetime.now(timezone.utc)
            delta_ms = int((end - start).total_seconds() * 1000)
            cumulative_ms = int((end - self.started_at).total_seconds() * 1000)
            s.ok = ok
            s.delta_ms = delta_ms
            s.cumulative_ms = cumulative_ms
            self._last_step_time = end
            self.refresh()

    def set_meta(self, *, title: Optional[str] = None, media_type: Optional[str] = None,
                 correlation_id: Optional[str] = None, request_id: Optional[str] = None,
                 tmdb_id: Optional[str] = None, user: Optional[str] = None,
                 dry_run: Optional[bool] = None):
        if title is not None:
            self.title = title
        if media_type is not None:
            self.media_type = media_type
        if correlation_id is not None:
            self.correlation_id = correlation_id
        if request_id is not None:
            self.request_id = request_id
        if tmdb_id is not None:
            self.tmdb_id = tmdb_id
        if user is not None:
            self.user = user
        if dry_run is not None:
            self.dry_run = bool(dry_run)
        self.refresh()

    def set_status(self, status: str):
        # accepted|failed|aborted|rejected
        self.status = status
        self.refresh()

    def set_decision(self, line: str):
        self.decision_line = line
        self.refresh()

    def refresh(self):
        if not HAVE_RICH:
            return  # No-op; plain mode prints only at end
        if not self._live:
            return
        try:
            self._live.update(self._build_panel())
        except Exception:
            pass

    def _badge_text(self) -> Text:
        t = Text()
        # Media type badge
        if self.media_type:
            color = "blue" if self.media_type == "movie" else "magenta"
            t.append(f" [ {self.media_type.upper()} ] ", style=f"bold {color}")
        # Dry run
        if self.dry_run:
            t.append(" [ DRY-RUN ] ", style="bold yellow")
        # Status
        status_color = {
            "accepted": "green",
            "running": "cyan",
            "failed": "red",
            "aborted": "yellow",
            "rejected": "red",
        }.get(self.status, "cyan")
        t.append(f" [ {self.status.upper()} ] ", style=f"bold {status_color}")
        # Corr id
        if self.correlation_id:
            t.append(f"    corr={self.correlation_id[:8]}", style="dim")
        # Clock and duration
        dur_s = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        t.append(f"    · {dur_s:.2f}s", style="dim")
        return t

    def _steps_table(self) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left")
        for s in self.steps:
            mark = "✓" if s.ok else ("✗" if s.ok is not None else "…")
            if self.cfg.get("ascii"):
                mark = "+" if s.ok else ("-" if s.ok is not None else ".")
            left = f"{mark} {s.name}"
            table.add_row(left)
        return table

    def set_scoring(self, table: List[Tuple[str, int, int, List[str]]]):
        try:
            self.scored_table = table or []
            self.refresh()
        except Exception:
            self.scored_table = []

    def set_scoring_note(self, note: Optional[str]):
        self.scoring_note = note
        self.refresh()

    def _score_color(self, ratio: float) -> str:
        if ratio >= 0.75:
            return "green"
        if ratio >= 0.4:
            return "yellow"
        return "red"

    def _score_bar(self, filled: int, total: int, style: str) -> Text:
        filled = max(0, min(total, int(filled)))
        empty = max(0, total - filled)
        if self.cfg.get("ascii"):
            bar = "#" * filled + "." * empty
            return Text(bar, style=style)
        else:
            bar = "█" * filled + "·" * empty
            return Text(bar, style=style)

    def _build_scoring_table(self, width: int) -> Table:
        tbl = Table.grid(padding=(0, 1))
        tbl.add_column("", justify="left")
        tbl.add_column("", justify="left", ratio=1)
        tbl.add_column("", justify="right", no_wrap=True)
        tbl.add_column("", justify="right", no_wrap=True)
        tbl.add_column("", justify="left")

        if not self.scored_table:
            return tbl

        rows = sorted(self.scored_table, key=lambda r: (r[1], r[2], r[0]), reverse=True)
        max_score = max(1, max((r[1] for r in rows), default=1))
        if self.scoring_note:
            note = Text(self.scoring_note, style="dim")
            tbl.add_row(note, Text(""), Text(""), Text(""), Text(""))

        for (name, score, weight, reasons) in rows:
            ratio = (score / max_score) if max_score else 0.0
            filled = int(round(ratio * 10))
            style = self._score_color(ratio)
            bar = self._score_bar(filled, 10, style)

            shown = reasons[:2]
            more = len(reasons) - len(shown)
            reason_txt = ", ".join([r for r in shown if r])
            if more > 0:
                if reason_txt:
                    reason_txt += f"  +{more} more"
                else:
                    reason_txt = f"+{more} more"

            tbl.add_row(Text(name, style="bold"), bar, Text(str(score)), Text(str(weight)), Text(reason_txt))
        return tbl

    def _meta_text(self) -> Text:
        parts = []
        if self.title:
            parts.append(f"Title: {self.title}")
        if self.tmdb_id:
            parts.append(f"tmdb={self.tmdb_id}")
        if self.request_id:
            parts.append(f"req={self.request_id}")
        if self.user:
            parts.append(f"by={self.user}")
        return Text("    ".join(parts))

    def _build_panel(self) -> Panel:
        header = self._badge_text()
        width = 100
        try:
            if self._console:
                width = max(40, int(self._console.size.width))
        except Exception:
            pass

        wide = width >= 80
        if wide:
            body = Table.grid(expand=True)
            body.add_row(self._meta_text())
            body.add_row(Text())
            columns = Table.grid(expand=True)
            columns.add_column(ratio=1)
            columns.add_column(ratio=1)

            left = Table.grid(expand=True)
            left.add_row(Text("Steps", style="bold"))
            left.add_row(self._steps_table())

            right = Table.grid(expand=True)
            right.add_row(Text("Scoring", style="bold"))
            right.add_row(self._build_scoring_table(width // 2))

            columns.add_row(left, right)
            body.add_row(columns)

            if self.decision_line:
                body.add_row(Text())
                body.add_row(Text("─ Decision ─", style="bold"))
                body.add_row(Text(self.decision_line))
        else:
            body = Table.grid(expand=True)
            body.add_row(self._meta_text())
            body.add_row(Text())
            body.add_row(Text("Steps", style="bold"))
            body.add_row(self._steps_table())
            if self.scored_table:
                body.add_row(Text())
                body.add_row(Text("Scoring", style="bold"))
                body.add_row(self._build_scoring_table(width))
            if self.decision_line:
                body.add_row(Text())
                body.add_row(Text("─ Decision ─", style="bold"))
                body.add_row(Text(self.decision_line))
        status_color = {
            "accepted": "green",
            "running": "cyan",
            "failed": "red",
            "aborted": "yellow",
            "rejected": "red",
        }.get(self.status, "cyan")
        return Panel(
            body,
            title=header,
            border_style=status_color,
            box=box.ASCII if self.cfg.get("ascii") else box.ROUNDED,
        )

    @contextmanager
    def live(self):
        if HAVE_RICH and self._console is not None and self.cfg.get("enabled", True):
            panel = self._build_panel()
            with Live(panel, console=self._console, refresh_per_second=8, transient=True) as live:
                self._live = live
                try:
                    yield self
                finally:
                    self._live = None
        else:
            # Plain mode: nothing to render live
            yield self

    def final_render(self):
        if HAVE_RICH and self._console is not None and self.cfg.get("enabled", True):
            try:
                self._console.print(self._build_panel())
            except Exception:
                pass
        else:
            # Plain text fallback
            dur = (datetime.now(timezone.utc) - self.started_at).total_seconds()
            print(f"[{self.media_type or '?'}]{' [DRY-RUN]' if self.dry_run else ''} {self.status.upper()} corr={str(self.correlation_id)[:8]} · {dur:.2f}s")
            if self.title:
                bits = []
                if self.tmdb_id:
                    bits.append(f"tmdb={self.tmdb_id}")
                if self.request_id:
                    bits.append(f"req={self.request_id}")
                if self.user:
                    bits.append(f"by={self.user}")
                print(f"Title: {self.title}  {'  '.join(bits)}")
            print("Steps")
            for s in self.steps:
                mark = "+" if s.ok else ("-" if s.ok is not None else ".")
                right = ""
                if s.delta_ms is not None and s.cumulative_ms is not None:
                    right = f"  (Δ {s.delta_ms} ms  Σ {s.cumulative_ms} ms)"
                print(f"  {mark} {s.name}{right}")
            if self.decision_line:
                print("Decision")
                print(f"  {self.decision_line}")


# Track current card in context to avoid threading params through everywhere
cv_card: contextvars.ContextVar[Optional[RequestCard]] = contextvars.ContextVar("_card", default=None)


def get_current_card() -> Optional[RequestCard]:
    try:
        return cv_card.get()
    except Exception:
        return None


def new_request_card(cfg: Dict[str, Any]) -> RequestCard:
    console = Console(log_time=False, log_path=False, highlight=False) if HAVE_RICH else None
    card = RequestCard(cfg={
        "enabled": bool(cfg.get("enabled", True)),
        "ascii": bool(cfg.get("ascii", False)),
    })
    card.attach_console(console)
    cv_card.set(card)
    return card


def end_request_card():
    card = get_current_card()
    if card:
        card.final_render()
    cv_card.set(None)


# =========================
# Initialisation
# =========================
_listener: Optional[logging.handlers.QueueListener] = None


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass


def init_logging(cfg: Dict[str, Any]):
    global _listener

    log_cfg = cfg.get("LOGGING") if isinstance(cfg, dict) else None
    log_cfg = log_cfg or {}

    level_name = str(log_cfg.get("LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any pre-existing handlers to avoid duplicate logs
    for h in list(root.handlers):
        try:
            root.removeHandler(h)
        except Exception:
            pass

    # Common context filter
    ctx_filter = ContextFilter()

    # File logging (async NDJSON)
    fcfg = (log_cfg.get("FILE") or {}) if isinstance(log_cfg, dict) else {}
    if fcfg.get("enabled", True):
        path = fcfg.get("path", os.path.join("logs", "overfiltrr.log"))
        _ensure_dir(path)
        when = (fcfg.get("rotate") or {}).get("when", "midnight")
        backup_count = int((fcfg.get("rotate") or {}).get("backup_count", 7))
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=path, when=when, backupCount=backup_count, encoding="utf-8"
        )

        # Structured formatter
        encoder_choice = (log_cfg.get("JSON") or {}).get("encoder", "auto")
        use_orjson = HAVE_ORJSON if encoder_choice in ("auto", "orjson") else False

        # Prefer built-in NDJSON formatter for stable schema
        file_handler.setFormatter(NDJSONFormatter(use_orjson=use_orjson))

        file_handler.addFilter(ctx_filter)

        q: queue.Queue = queue.Queue(-1)
        qh = logging.handlers.QueueHandler(q)
        qh.addFilter(ctx_filter)
        root.addHandler(qh)

        try:
            _listener = logging.handlers.QueueListener(q, file_handler)
            _listener.daemon = True
            _listener.start()
        except Exception:
            # Fallback to direct handler if listener fails
            root.removeHandler(qh)
            root.addHandler(file_handler)

    # Console: install rich traceback if available
    ccfg = (log_cfg.get("CONSOLE") or {}) if isinstance(log_cfg, dict) else {}
    if HAVE_RICH and ccfg.get("enabled", True):
        try:
            rich_traceback_install(show_locals=False, suppress=["urllib3", "requests"])  # type: ignore
        except Exception:
            pass

    # Quiet noisy libraries a bit
    for noisy in ("werkzeug", "waitress", "urllib3"):
        try:
            logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
        except Exception:
            pass


# =========================
# Startup card
# =========================
def render_startup_card(*, cfg: Dict[str, Any], host: str, port: int, threads: int, connection_limit: int, ok: bool = True, message: Optional[str] = None) -> None:
    try:
        ccfg = (cfg.get("LOGGING") or {}).get("CONSOLE") or {}
    except Exception:
        ccfg = {}

    status_color = "green" if ok else "red"
    status_text = "READY" if ok else "ERROR"

    # Gather details
    try:
        dry_run = bool(cfg.get("DRY_RUN", False))
        allow_auto = bool(cfg.get("ALLOW_AUTO_APPROVE", True))
        overseerr = str(cfg.get("OVERSEERR_BASEURL", ""))
        wcfg = cfg.get("WEBHOOK") or {}
        token_enabled = bool(wcfg.get("TOKEN"))
        fcfg = (cfg.get("LOGGING") or {}).get("FILE") or {}
        file_enabled = fcfg.get("enabled", True)
        file_path = fcfg.get("path", os.path.join("logs", "overfiltrr.log"))
        console_enabled = (cfg.get("LOGGING") or {}).get("CONSOLE", {}).get("enabled", True)
        ascii_mode = (cfg.get("LOGGING") or {}).get("CONSOLE", {}).get("ascii", False)
    except Exception:
        dry_run = False; allow_auto = True; overseerr = ""
        token_enabled = False; file_enabled = True; file_path = "logs/overfiltrr.log"
        console_enabled = True; ascii_mode = False

    if HAVE_RICH and console_enabled:
        try:
            console = Console(log_time=False, log_path=False, highlight=False)
            table = Table.grid(padding=(0, 2))
            table.add_column(justify="left")
            table.add_column(justify="left")

            def add_row(k: str, v: str | Text):
                v_text = v if isinstance(v, Text) else Text(v)
                table.add_row(Text(k, style="dim"), v_text)

            add_row("Config", Text("OK" if ok else "INVALID", style=("bold green" if ok else "bold red")))
            if message:
                add_row("Note", message)
            add_row("Overseerr", Text(overseerr or "(unset)", style="bold blue"))
            add_row("Dry run", Text("ON" if dry_run else "OFF", style=("bold orange3" if dry_run else "bold green")))
            add_row("Auto-approve", Text("ON" if allow_auto else "OFF", style=("bold green" if allow_auto else "bold red")))
            add_row("Webhook token", Text("ENABLED" if token_enabled else "disabled", style=("bold cyan" if token_enabled else "dim")))
            add_row("Server", Text(f"{host}:{port}", style="bold blue"))
            add_row("Console logging", Text("ON" if console_enabled else "OFF", style=("bold green" if console_enabled else "bold red")))
            add_row("ASCII", Text("ON" if ascii_mode else "OFF", style=("bold magenta" if ascii_mode else "dim")))
            if file_enabled:
                add_row("File logging", Text(f"ON → {file_path}", style="bold green"))
            else:
                add_row("File logging", Text("OFF", style="bold red"))

            panel = Panel(
                table,
                title=Text(f" OverFiltrr • {status_text} ", style=f"bold {status_color}"),
                border_style=status_color,
                box=box.ASCII if ccfg.get("ascii") else box.ROUNDED,
            )
            console.print(panel)
            return
        except Exception:
            pass

    # Plain fallback
    print(f"OverFiltrr {status_text}")
    if message:
        print(f"  Note: {message}")
    print(f"  Overseerr: {overseerr}")
    print(f"  Dry run: {'ON' if dry_run else 'OFF'}; Auto-approve: {'ON' if allow_auto else 'OFF'}")
    print(f"  Server: {host}:{port}")
    print(f"  Console logging: {'ON' if console_enabled else 'OFF'}  ASCII: {'ON' if ascii_mode else 'OFF'}")
    print(f"  File logging: {'ON' if file_enabled else 'OFF'}  Path: {file_path}")
