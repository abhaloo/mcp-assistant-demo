"""One typed view of a ledger's spend state, however it was shaped."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LedgerView"]


@dataclass(frozen=True)
class LedgerView:
    """A ledger's settled spend and whether a reservation is still open.

    The one normalisation of a foreign ledger shape: :meth:`from_any` accepts
    a dict spelled either ``open_reservation`` or ``has_open_reservation``, an
    object carrying ``has_open_reservation`` as a plain attribute or a
    zero-arg method, or an existing ``LedgerView``. Every caller downstream
    reads this typed shape only -- no ``getattr``, no ``callable()``, no
    ``isinstance`` probing.
    """

    settled_usd: float
    has_open_reservation: bool

    @classmethod
    def from_any(cls, obj: object) -> LedgerView:
        if isinstance(obj, LedgerView):
            return obj
        if isinstance(obj, dict):
            open_reservation = bool(obj.get("open_reservation") or obj.get("has_open_reservation"))
            settled = obj.get("settled_usd")
            return cls(
                settled_usd=float(settled) if settled is not None else 0.0,
                has_open_reservation=open_reservation,
            )
        open_reservation_attr = getattr(obj, "has_open_reservation", False)
        if callable(open_reservation_attr):
            open_reservation_attr = open_reservation_attr()
        settled = getattr(obj, "settled_usd", None)
        return cls(
            settled_usd=float(settled) if settled is not None else 0.0,
            has_open_reservation=bool(open_reservation_attr),
        )
