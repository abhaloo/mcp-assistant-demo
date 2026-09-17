"""Process state a test must drop between cases.

A module that holds a process-lifetime singleton registers how to drop it; the
test suite calls ``reset_process_state()`` once per test instead of importing a
private hook from each module. Registration happens at import, so a module that
was never imported cannot be holding a dirty singleton either.
"""

from __future__ import annotations

from collections.abc import Callable

_RESETTABLE: list[Callable[[], None]] = []


def register_resettable(fn: Callable[[], None]) -> None:
    _RESETTABLE.append(fn)


def reset_process_state() -> None:
    for fn in _RESETTABLE:
        fn()
