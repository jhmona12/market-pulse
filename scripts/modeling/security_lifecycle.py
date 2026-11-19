"""Research contracts for index eligibility and security terminal outcomes.

Not an automatic corporate-action parser. Only reviewed, sourced events can
resolve a label; missing vendor history is never proof of a zero-value outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite


@dataclass(frozen=True)
class MembershipInterval:
    security_id: str
    start: date
    end_exclusive: date | None
    source_url: str
    verified: bool

    def eligible(self, signal_date: date) -> bool:
        if not self.verified or not self.security_id or not self.source_url:
            raise ValueError("Membership requires a verified security identity and dated source")
        if self.end_exclusive is not None and self.end_exclusive <= self.start:
            raise ValueError("Invalid membership interval")
        return self.start <= signal_date and (self.end_exclusive is None or signal_date < self.end_exclusive)


@dataclass(frozen=True)
class TerminalEvent:
    security_id: str
    kind: str
    effective_date: date
    value_known_at: date
    cash_equivalent_per_old_share: float | None
    source_url: str
    verified: bool = False
    price_basis: str = "raw_last_traded_share"
    included_in_adjusted_history: bool = False


def resolve_terminal_outcome(*, security_id: str, total_return_to_last: float,
                             last_raw_close: float, last_trade_date: date,
                             exit_date: date, as_of: date, event: TerminalEvent | None) -> dict:
    """Liquidate verified consideration at the event, hold cash to label exit.

    Cash equivalents for stock/mixed deals must already value the contractual
    exchange ratio at the event's verified realizable successor price. This
    function must not receive an adjusted close as the raw-share denominator.
    """
    if not isfinite(total_return_to_last) or total_return_to_last < -1 or not isfinite(last_raw_close) or last_raw_close <= 0:
        raise ValueError("Invalid pre-event wealth or raw close")
    if event is None or not event.verified or not event.source_url:
        return {"status": "unresolved", "return": None, "reason": "Missing reviewed terminal evidence"}
    if event.security_id != security_id:
        raise ValueError("Security identity mismatch; ticker reuse is not continuity")
    if event.price_basis != "raw_last_traded_share" or event.included_in_adjusted_history:
        raise ValueError("Incompatible share units or terminal consideration already counted")
    if not last_trade_date <= event.effective_date <= exit_date:
        raise ValueError("Terminal event is outside the holding outcome window")
    if event.kind not in {"cash_acquisition", "stock_acquisition", "mixed_acquisition", "worthless_cancellation"}:
        return {"status": "unresolved", "return": None, "reason": "Filing, halt or missing quote is not a terminal value"}
    value = event.cash_equivalent_per_old_share
    if value is None or not isfinite(value) or value < 0:
        return {"status": "unresolved", "return": None, "reason": "Missing verified consideration"}
    if event.kind == "worthless_cancellation" and value != 0:
        raise ValueError("Worthless cancellation cannot have positive recovery")
    available_at = max(exit_date, event.effective_date, event.value_known_at)
    if as_of < available_at:
        return {"status": "not_mature", "return": None, "available_at": available_at.isoformat()}
    result = (1 + total_return_to_last) * value / last_raw_close - 1
    return {"status": "resolved", "return": result, "available_at": available_at.isoformat(),
            "policy": "verified_event_liquidation_then_zero_interest_cash_to_original_exit", "source_url": event.source_url}
