"""Terminal progress display for running campaigns."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, TextIO

from app.experiments.core.secret_redaction import redact_secrets

__all__ = [
    "CONSOLE_MODES",
    "DASHBOARD_HEIGHT",
    "CampaignConsole",
    "DashboardRenderer",
    "LineRenderer",
]

CONSOLE_MODES = ("auto", "lines", "dashboard", "off")

#: Repeated progress inside one stage prints at most this often.
DEFAULT_MIN_INTERVAL_S = 10.0

#: Stages whose payload is a terminal event: never throttled, never merged.
_ERROR_STAGE = "error"
_COMPLETE_STAGE = "complete"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProgressRenderer(Protocol):
    """What ``CampaignConsole`` drives. Kept structural so a test double is just a
    class with two methods, and so C2's dashboard is a peer of the line renderer
    rather than a subclass of it."""

    def on_progress(self, progress: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...


def _money(value: Any) -> str | None:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return None


def _failures_token(failure_counts: Any) -> str | None:
    """``{"operational": 4, "route": 0}`` -> ``operational:4``.

    Unlabelled on purpose: the line renderer prefixes ``fail=`` because its rows have
    no columns, and the dashboard does not because its label column already says
    "failures". Baking the prefix in here printed ``failures  fail=operational:4``.

    Zero-count kinds are dropped rather than printed: a steady ``route:0`` on every
    line trains the eye to skip the failure column, which is the one column that must
    keep its salience.
    """
    if not isinstance(failure_counts, Mapping):
        return None
    parts = [f"{key}:{count}" for key, count in sorted(failure_counts.items()) if count]
    return ",".join(parts) if parts else None


def _eta_token(*, completed: int, total: int, elapsed_s: float) -> str | None:
    if completed <= 0 or total <= 0 or completed >= total or elapsed_s <= 0:
        return None
    remaining_s = (elapsed_s / completed) * (total - completed)
    if remaining_s < 90:
        return f"eta={round(remaining_s)}s"
    return f"eta={round(remaining_s / 60)}m"


class _ThrottleGate:
    """Throttle repeated progress events within a stage."""

    def __init__(self, *, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._last_stage: str | None = None
        self._last_at: datetime | None = None

    def should_emit(self, *, stage: str, completed: int, total: int, now: datetime) -> bool:
        if stage != self._last_stage:
            return True
        if total > 0 and completed >= total:
            return True
        if self._last_at is None:
            return True
        return (now - self._last_at).total_seconds() >= self._min_interval_s

    def record(self, *, stage: str, now: datetime) -> None:
        self._last_stage = stage
        self._last_at = now


class _BaseRenderer:
    """Shared progress filtering for line and dashboard renderers."""

    def __init__(
        self,
        stream: TextIO,
        *,
        clock: Callable[[], datetime],
        min_interval_s: float,
    ) -> None:
        self._stream = stream
        self._clock = clock
        self._gate = _ThrottleGate(min_interval_s=min_interval_s)
        self._started_at: datetime | None = None

    def on_progress(self, progress: Mapping[str, Any]) -> None:
        now = self._clock()
        if self._started_at is None:
            self._started_at = now

        stage = progress.get("stage")
        if stage is None:
            # A bare liveness tick from ``heartbeat_ticker`` -- the only periodic event
            # in the system, and the only one carrying no stage.
            self._on_liveness_tick(now)
            return

        stage = str(stage)
        completed = _as_int(progress.get("completed"))
        total = _as_int(progress.get("total"))

        if not self._gate.should_emit(stage=stage, completed=completed, total=total, now=now):
            return

        self._gate.record(stage=stage, now=now)
        self._render(progress, stage=stage, completed=completed, total=total, now=now)

    def _on_liveness_tick(self, now: datetime) -> None:
        """Default: ignore it."""

    def _render(
        self,
        progress: Mapping[str, Any],
        *,
        stage: str,
        completed: int,
        total: int,
        now: datetime,
    ) -> None:
        raise NotImplementedError

    def _elapsed_s(self, now: datetime) -> float:
        return (now - self._started_at).total_seconds() if self._started_at else 0.0

    def close(self) -> None:
        return


class LineRenderer(_BaseRenderer):
    """Append-only line renderer for piped or non-TTY output."""

    def __init__(
        self,
        stream: TextIO,
        *,
        clock: Callable[[], datetime] = _utc_now,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    ) -> None:
        super().__init__(stream, clock=clock, min_interval_s=min_interval_s)

    def _render(
        self,
        progress: Mapping[str, Any],
        *,
        stage: str,
        completed: int,
        total: int,
        now: datetime,
    ) -> None:
        self._write(self._compose(progress, stage=stage, completed=completed, total=total, now=now))

    def close(self) -> None:
        """Nothing to restore -- this renderer never took the terminal over."""

    # ------------------------------------------------------------------ internals

    def _compose(
        self,
        progress: Mapping[str, Any],
        *,
        stage: str,
        completed: int,
        total: int,
        now: datetime,
    ) -> str:
        stamp = now.strftime("%H:%M:%S")
        if stage == _ERROR_STAGE:
            return f"{stamp}  {'ERROR':<11}{progress.get('error')}"
        if stage == _COMPLETE_STAGE:
            return f"{stamp}  {'VERDICT':<11}" + "  ".join(
                token
                for token in (
                    str(progress.get("status") or "done"),
                    _total_and_cap(progress),
                )
                if token
            )

        spend = _money(progress.get("spend_usd"))
        elapsed_s = self._elapsed_s(now)
        tokens = [
            f"{completed}/{total}" if total > 0 else f"{completed}",
            f"spend={spend}" if spend else None,
            (
                f"fail={failures}"
                if (failures := _failures_token(progress.get("failure_counts")))
                else None
            ),
            _eta_token(completed=completed, total=total, elapsed_s=elapsed_s),
        ]
        return f"{stamp}  {stage:<11}" + "  ".join(token for token in tokens if token)

    def _write(self, line: str) -> None:
        # Redacted as a WHOLE LINE, not field by field. ``error`` is free text built by
        # whatever raised, and this stream is usually piped to a log file that outlives
        # the run -- so the console must not become the one place a credential is
        # printed in the clear. Numbers and stage tokens are not key-shaped, so a
        # whole-line pass costs nothing but closes every field at once, including any
        # field added here later.
        self._stream.write((redact_secrets(line) or line) + "\n")
        flush = getattr(self._stream, "flush", None)
        if flush is not None:
            flush()


def _total_and_cap(progress: Mapping[str, Any]) -> str | None:
    total_spend = _money(progress.get("spend_usd"))
    if total_spend is None:
        return None
    cap = _money(progress.get("cap_usd"))
    return f"total={total_spend} / cap {cap}" if cap else f"total={total_spend}"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


#: Rows in one dashboard frame. The rewind is ``\x1b[{DASHBOARD_HEIGHT}A``, so this
#: number and the number of rows ``_frame()`` returns must agree exactly -- disagree and
#: the panel either walks down the screen leaving a trail, or eats a line of scrollback
#: per redraw. ``_frame`` pads and truncates to guarantee it for every payload shape.
DASHBOARD_HEIGHT = 7

#: Rows are hard-truncated to this. A row that exceeds the terminal width WRAPS onto a
#: second physical line, and then the rewind is short by one for every wrapped row --
#: the same tearing, arriving only for operators with narrow terminals or long errors.
DASHBOARD_WIDTH = 78

_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_ERASE_LINE = "\x1b[2K"

_GLYPHS_UNICODE = {"full": "█", "empty": "░", "rule": "─", "dot": "·"}
_GLYPHS_ASCII = {"full": "#", "empty": ".", "rule": "-", "dot": "-"}


def _fit_frame(rows: list[str], *, height: int, width: int) -> list[str]:
    """Force a row list into the exact rectangle the rewind assumes.

    Both conditions are the redraw's correctness, not cosmetics:

    * HEIGHT -- the rewind is ``\\x1b[{height}A``. Draw fewer rows than that and the
      panel eats a line of scrollback per frame; draw more and it walks down the
      screen leaving a trail.
    * WIDTH -- a row longer than the terminal WRAPS onto a second physical line, so
      the rewind is short by one for every wrapped row. That arrives only for
      operators with narrow terminals or long error strings, which is exactly when
      they are reading the panel most carefully.

    Extracted and tested directly because ``_frame`` happens to build the right number
    of rows today, which made both guards invisible to mutation testing -- they defend
    against a row added later without a matching height bump, and a defence nothing
    measures is the thing that quietly stops working.
    """
    return [row[:width] for row in (rows + [""] * height)[:height]]


def _glyphs_for(stream: TextIO) -> dict[str, str]:
    """Block characters, or ASCII if this stream cannot carry them.

    A raw Windows console is often cp1252, where writing U+2588 raises
    UnicodeEncodeError -- from inside a progress renderer, in the middle of a paid
    campaign. Asking the stream's own codec is the only reliable test; guessing from
    the platform gets Windows Terminal (UTF-8) wrong.
    """
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(_GLYPHS_UNICODE.values()).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return _GLYPHS_ASCII
    return _GLYPHS_UNICODE


class DashboardRenderer(_BaseRenderer):
    """A fixed-height panel redrawn in place with cursor-up + erase-line.

    No new dependency: ``rich`` is not in ``pyproject.toml`` and this repo pins
    dependencies deliberately (``tests/test_dependency_pins.py``). Adding a rendering
    library to a lockfile that guards a paid campaign is a worse trade than writing
    ~80 lines of escape codes.

    Every escape sequence here is one this renderer has PROVEN the terminal accepts --
    see ``_enable_virtual_terminal``. Emitting VT codes that were not enabled leaves an
    operator reading ``[2K[7A`` while the run they are watching spends money.
    """

    def __init__(
        self,
        stream: TextIO,
        *,
        clock: Callable[[], datetime] = _utc_now,
        title: str = "campaign",
        min_interval_s: float = 0.5,
        width: int = DASHBOARD_WIDTH,
    ) -> None:
        super().__init__(stream, clock=clock, min_interval_s=min_interval_s)
        self._title = title
        self._width = width
        self._glyphs = _glyphs_for(stream)
        self._drawn = False
        #: The last real event, kept so a liveness tick can redraw it with a fresh clock.
        self._last: tuple[Mapping[str, Any], str, int, int] | None = None

    def _render(
        self,
        progress: Mapping[str, Any],
        *,
        stage: str,
        completed: int,
        total: int,
        now: datetime,
    ) -> None:
        self._last = (progress, stage, completed, total)
        self._draw(self._frame(progress, stage=stage, completed=completed, total=total, now=now))

    def _on_liveness_tick(self, now: datetime) -> None:
        """Redraw the last frame with a fresh clock.

        The line renderer is right to drop this; the dashboard is not. Its elapsed
        counter and its ETA only move when an event arrives, so during a quiet stretch
        -- a long judge stage, a stalled provider -- the panel freezes at whatever time
        the last real event carried. A frozen clock and "is it hung?" are the same
        picture, and this is the only periodic event in the system that can tell them
        apart. It is already flowing and it costs a redraw.
        """
        if self._last is None:
            return
        progress, stage, completed, total = self._last
        self._draw(self._frame(progress, stage=stage, completed=completed, total=total, now=now))

    def _draw(self, rows: list[str]) -> None:
        chunks = [] if self._drawn else [_HIDE_CURSOR]
        if self._drawn:
            chunks.append(f"\x1b[{DASHBOARD_HEIGHT}A")
        for row in rows:
            chunks.append(f"{_ERASE_LINE}{row}\n")
        self._stream.write("".join(chunks))
        self._drawn = True
        flush = getattr(self._stream, "flush", None)
        if flush is not None:
            flush()

    def close(self) -> None:
        """Give the cursor back.

        Skipped entirely when nothing was drawn -- a campaign that dies during freeze
        never emitted progress, and printing a show-cursor code into a stream this
        renderer never touched is noise in someone's log.
        """
        if not self._drawn:
            return
        self._stream.write(_SHOW_CURSOR)

    # ------------------------------------------------------------------ internals

    def _frame(
        self,
        progress: Mapping[str, Any],
        *,
        stage: str,
        completed: int,
        total: int,
        now: datetime,
    ) -> list[str]:
        g = self._glyphs
        elapsed_s = self._elapsed_s(now)
        rule = g["rule"] * min(self._width, 48)

        spend = _money(progress.get("spend_usd"))
        reserved = _money(progress.get("reserved_usd"))
        cap = _money(progress.get("cap_usd"))
        money = "  ".join(
            token
            for token in (
                spend,
                f"reserved {reserved}" if reserved else None,
                f"cap {cap}" if cap else None,
            )
            if token
        )

        last = (
            f"error  {progress.get('error')}"
            if stage == _ERROR_STAGE
            else f"verdict  {progress.get('status')}"
            if stage == _COMPLETE_STAGE
            else (_eta_token(completed=completed, total=total, elapsed_s=elapsed_s) or g["dot"])
        )

        rows = [
            f"{self._title} {g['dot']} {_hms(elapsed_s)}",
            rule,
            f"{'stage':<10}{stage}",
            f"{'progress':<10}{self._bar(completed, total)}",
            f"{'spend':<10}{money or g['dot']}",
            f"{'failures':<10}{_failures_token(progress.get('failure_counts')) or 'none'}",
            f"{'':<10}{last}",
        ]
        # Redacted per ROW, and BEFORE fitting. Redaction is a substitution, and
        # `<redacted:openrouter_api_key>` is longer than the fragment of a key it
        # replaces -- so sweeping the assembled frame afterwards could push a row past
        # the width _fit_frame had just guaranteed, wrap it, and tear the panel on the
        # next rewind. That arrives on an auth error at the start of a paid campaign on
        # an 80-column terminal: exactly when the panel is being read most carefully.
        return _fit_frame(
            [redact_secrets(row) or row for row in rows],
            height=DASHBOARD_HEIGHT,
            width=self._width,
        )

    def _bar(self, completed: int, total: int) -> str:
        g = self._glyphs
        if total <= 0:
            return f"{completed}"
        filled = max(0, min(18, round(18 * completed / total)))
        pct = round(100 * completed / total)
        return f"{g['full'] * filled}{g['empty'] * (18 - filled)}  {completed}/{total}  {pct}%"


def _hms(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


class _NullRenderer:
    """``--console off``. A real object rather than a ``None`` check at every call
    site, so the "is there a console" question is answered once, at construction."""

    def on_progress(self, progress: Mapping[str, Any]) -> None:
        return

    def close(self) -> None:
        return


class CampaignConsole:
    """Owns renderer selection, terminal restoration, thread safety, and never raising.

    Not a pass-through wrapper -- it holds two guarantees the renderers must not each
    re-implement:

    * **It cannot raise.** ``Campaign`` calls ``_progress_fn`` from inside the try block
      that decides whether the attempt failed, so an exception escaping a renderer is
      indistinguishable from the campaign itself failing.
    * **It serialises.** Progress arrives from worker threads; renderers hold mutable
      state and must not assume single-threaded callers. One lock is cheaper than
      interleaved terminal output from concurrent progress posts.
    """

    def __init__(self, renderer: ProgressRenderer, *, renderer_name: str) -> None:
        self._renderer = renderer
        self._lock = threading.Lock()
        self.renderer_name = renderer_name

    @classmethod
    def create(
        cls,
        *,
        mode: str = "auto",
        stream: TextIO | None = None,
        clock: Callable[[], datetime] = _utc_now,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        title: str = "campaign",
        enable_vt: Callable[[TextIO], bool] | None = None,
    ) -> CampaignConsole:
        if mode not in CONSOLE_MODES:
            # The one place this class is allowed to raise. A typo in ``--console``
            # happens before the campaign exists, so nothing is in flight; silently
            # disabling the view the operator asked for would be worse.
            raise ValueError(f"unknown console mode {mode!r}; expected one of {CONSOLE_MODES}")

        if mode == "off":
            return cls(_NullRenderer(), renderer_name="off")

        target = stream if stream is not None else sys.stdout
        if mode == "auto":
            vt = enable_vt if enable_vt is not None else _enable_virtual_terminal
            mode = "dashboard" if _dashboard_is_safe(target, vt) else "lines"

        # An EXPLICIT --console dashboard skips detection entirely. The flag exists to
        # override a wrong answer, and an operator inside a multiplexer or a container
        # that detection cannot see must be able to say so.
        if mode == "dashboard":
            return cls(
                DashboardRenderer(target, clock=clock, title=title),
                renderer_name="dashboard",
            )
        return cls(
            LineRenderer(target, clock=clock, min_interval_s=min_interval_s),
            renderer_name="lines",
        )

    def on_progress(self, progress: Mapping[str, Any]) -> None:
        try:
            with self._lock:
                self._renderer.on_progress(progress)
        except Exception:  # noqa: BLE001 - a status line must never abort a paid run
            return

    def close(self) -> None:
        try:
            with self._lock:
                self._renderer.close()
        except Exception:  # noqa: BLE001 - teardown of a view, never of the campaign
            return

    def tee(
        self, *sinks: Callable[[Mapping[str, Any]], Any]
    ) -> Callable[[Mapping[str, Any]], None]:
        """Console first, then each sink -- with the SINKS' exceptions propagating.

        The composition both runners were writing by hand, identically, docstring and
        all. An earlier draft had a module-level ``fan_out`` that swallowed every
        consumer's failure; deleting it was right, because wrapping ``write_heartbeat``
        in the same swallow silently changes whether a failed heartbeat write aborts a
        paid campaign -- and whether losing observability should forfeit a 372-cell
        matrix is a real question that adding a VIEW does not get to answer.

        A helper does not have to swallow. This one does not: the console's guarantee
        is the console's, and every sink's failure behaviour is exactly what it was
        when the runners called it directly. Console first so a sink that raises has
        still had its status line drawn.
        """

        def report(progress: Mapping[str, Any]) -> None:
            self.on_progress(progress)
            for sink in sinks:
                sink(progress)

        return report


def _dashboard_is_safe(stream: TextIO, enable_vt: Callable[[TextIO], bool]) -> bool:
    """Is this stream one we can PROVE takes a redraw?

    Fails CLOSED to ``lines`` at every step: emitting escape codes we have not proven
    work leaves an operator reading ``[2K[7A`` instead of a progress bar, and the run
    they are watching is the one paying for it.
    """
    if os.environ.get("NO_COLOR") or os.environ.get("CI"):
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:  # noqa: BLE001 - a stream that cannot answer is not a terminal
        return False
    try:
        return bool(enable_vt(stream))
    except Exception:  # noqa: BLE001 - could not enable it, so we have not proven it
        return False


def _enable_virtual_terminal(stream: TextIO) -> bool:
    """Turn on VT processing for a Windows console handle; a no-op elsewhere.

    Windows Terminal normally has this already; a raw ``cmd.exe`` may not, and there
    the escape codes render as literal text. This ASKS rather than assumes, and a
    refusal routes the caller to ``LineRenderer``.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        enable_virtual_terminal_processing = 0x0004
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if handle in (0, -1):
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & enable_virtual_terminal_processing:
            return True
        return bool(
            kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing)
        )
    except Exception:  # noqa: BLE001 - no console, no ctypes, no VT: fall back to lines
        return False
