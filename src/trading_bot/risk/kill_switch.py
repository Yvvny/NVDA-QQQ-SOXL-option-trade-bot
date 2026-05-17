from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KillSwitchState:
    active: bool = False
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def triggered(cls, *reason_codes: str) -> KillSwitchState:
        return cls(active=True, reason_codes=tuple(reason_codes) or ("kill_switch_active",))
