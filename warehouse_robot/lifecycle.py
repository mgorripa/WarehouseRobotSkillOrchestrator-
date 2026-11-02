"""Explicit mission finite-state machine with validated state transitions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from .model import Context


class Phase(str, Enum):
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    RECOVERING = "RECOVERING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


_ALLOWED = {
    Phase.PENDING: {Phase.EXECUTING},
    Phase.EXECUTING: {Phase.RECOVERING, Phase.SUCCEEDED, Phase.FAILED},
    Phase.RECOVERING: {Phase.EXECUTING, Phase.FAILED},
    Phase.SUCCEEDED: set(),
    Phase.FAILED: set(),
}


@dataclass
class MissionLifecycle:
    phase: Phase = Phase.PENDING

    def transition(self, target: Phase, ctx: Context, reason: str = "") -> None:
        if target not in _ALLOWED[self.phase]:
            raise ValueError(f"invalid transition: {self.phase.value} -> {target.value}")
        ctx.emit("MISSION_PHASE", previous=self.phase.value, current=target.value, reason=reason)
        self.phase = target
