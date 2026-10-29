"""Persisted first-run setup state (survives restarts and reboots).

One JSON file, written atomically. Only REAL hardware assessments are stored here: the
Compatibility Lab never writes to it, so a simulated profile cannot drive an installation.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from airgap.capacity.planner import Assessment


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_ACTION = "needs_action"
    FAILED = "failed"
    VERIFIED = "verified"


class Step(StrEnum):
    SCAN = "scan"
    PREREQUISITES = "prerequisites"
    MODELS = "models"
    INSTALL = "install"
    VERIFY = "verify"


class StepState(BaseModel):
    status: StepStatus = StepStatus.PENDING
    detail: str | None = None
    updated_at: datetime | None = None


class SetupState(BaseModel):
    version: int = 1
    steps: dict[Step, StepState] = Field(default_factory=lambda: {s: StepState() for s in Step})
    assessment: Assessment | None = None  # last REAL scan
    selected_model_ids: list[str] = Field(default_factory=list)
    active_model_id: str | None = None
    retrieval_profile_id: str | None = None
    index_version: str | None = None
    completed_at: datetime | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.completed_at is not None


class SimulatedAssessmentRefused(ValueError):
    pass


_MAX_EVENTS = 200


class SetupStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> SetupState:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return SetupState()
        return SetupState.model_validate_json(raw)

    def _save(self, state: SetupState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def update(self, fn: Any) -> SetupState:
        """Apply ``fn(state)`` under a lock and persist. ``fn`` mutates in place."""
        with self._lock:
            state = self.load()
            fn(state)
            self._save(state)
            return state

    def set_step(self, step: Step, status: StepStatus, detail: str | None = None) -> SetupState:
        def apply(state: SetupState) -> None:
            now = datetime.now(UTC)
            state.steps[step] = StepState(status=status, detail=detail, updated_at=now)
            state.events.append(
                {
                    "at": now.isoformat(),
                    "step": step.value,
                    "status": status.value,
                    "detail": detail,
                }
            )
            del state.events[:-_MAX_EVENTS]

        return self.update(apply)

    def record_scan(self, assessment: Assessment) -> SetupState:
        if assessment.simulated:
            raise SimulatedAssessmentRefused("simulated assessments are never stored as setup")

        def apply(state: SetupState) -> None:
            state.assessment = assessment
            state.retrieval_profile_id = assessment.retrieval_profile_id

        self.update(apply)
        return self.set_step(Step.SCAN, StepStatus.VERIFIED, f"tier {assessment.tier.value}")

    def reset(self) -> None:
        with self._lock:
            if self._path.exists():
                self._path.unlink()

    def to_public(self, state: SetupState) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(state.model_dump_json())
        data["complete"] = state.complete
        return data
