"""
overrides.py — Typed what-if override structures.

Replaces raw dicts throughout the codebase with proper dataclasses
that validate fields, support serialisation, and can be safely passed
to model functions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReactorOverride:
    """Override settings for a single reactor."""
    retirement_year:      int   | None = None
    capacity_mw:          float | None = None
    status:               str   | None = None
    restart_date:         str   | None = None
    pipeline_probability: float | None = None
    expected_online_year: int   | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to the raw dict format consumed by the projection engine."""
        d: dict[str, Any] = {}
        if self.retirement_year      is not None: d["retirement_year"]      = self.retirement_year
        if self.capacity_mw          is not None: d["capacity_mw"]          = self.capacity_mw
        if self.status               is not None: d["status"]               = self.status
        if self.restart_date         is not None: d["restart_date"]         = self.restart_date
        if self.pipeline_probability is not None: d["pipeline_probability"] = self.pipeline_probability
        if self.expected_online_year is not None: d["expected_online_year"] = self.expected_online_year
        return d

    @property
    def is_empty(self) -> bool:
        return all(v is None for v in [
            self.retirement_year, self.capacity_mw, self.status,
            self.restart_date, self.pipeline_probability, self.expected_online_year,
        ])


@dataclass
class SyntheticBatch:
    """A batch of synthetic new-build reactor additions."""
    region:      str
    capacity_mw: float
    per_year:    int
    start_year:  int
    n_years:     int

    def to_dict(self) -> dict:
        return {
            "region":      self.region,
            "capacity_mw": self.capacity_mw,
            "per_year":    self.per_year,
            "start_year":  self.start_year,
            "n_years":     self.n_years,
        }


@dataclass
class WhatIfOverrides:
    """
    All what-if overrides for a single projection run.

    Provides a typed interface over the raw ``{reactor_id: {field: value}}``
    dicts the projection engine consumes. Construct via ``from_session_state()``
    and convert back to the engine format with ``to_raw_dict()``.
    """
    reactor_overrides: dict[str, ReactorOverride] = field(default_factory=dict)
    synthetic_batches: list[SyntheticBatch]        = field(default_factory=list)

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def from_session_state(
        cls,
        overrides_list: list[dict],
        synthetic_list: list[dict],
    ) -> WhatIfOverrides:
        """Build from the Streamlit session-state format.

        Each entry in ``overrides_list`` is::

            {"reactor_id": "CA-22", "field": "retirement_year", "value": 2033}
        """
        reactor_overrides: dict[str, ReactorOverride] = {}
        for o in overrides_list:
            rid   = o["reactor_id"]
            fname = o["field"]
            val   = o["value"]
            if rid not in reactor_overrides:
                reactor_overrides[rid] = ReactorOverride()
            ov = reactor_overrides[rid]
            if   fname == "retirement_year":      ov.retirement_year      = int(val)
            elif fname == "capacity_mw":          ov.capacity_mw          = float(val)
            elif fname == "status":               ov.status               = str(val)
            elif fname == "restart_date":         ov.restart_date         = str(val)
            elif fname == "pipeline_probability": ov.pipeline_probability = float(val)
            elif fname == "expected_online_year": ov.expected_online_year = int(val)

        synthetic_batches = [
            SyntheticBatch(
                region      = b["region"],
                capacity_mw = float(b["capacity_mw"]),
                per_year    = int(b["per_year"]),
                start_year  = int(b["start_year"]),
                n_years     = int(b["n_years"]),
            )
            for b in synthetic_list
        ]
        return cls(reactor_overrides=reactor_overrides, synthetic_batches=synthetic_batches)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_raw_dict(self) -> dict:
        """
        Convert to the raw dict format expected by the projection engine::

            {
                "CA-22": {"retirement_year": 2033},
                "__synthetic__": [{"region": "China", ...}],
            }
        """
        result: dict = {
            rid: ov.to_dict()
            for rid, ov in self.reactor_overrides.items()
            if not ov.is_empty
        }
        if self.synthetic_batches:
            result["__synthetic__"] = [b.to_dict() for b in self.synthetic_batches]
        return result

    # ── Inspection ────────────────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        return not self.reactor_overrides and not self.synthetic_batches

    def reactor_count(self) -> int:
        return len(self.reactor_overrides)

    def retirement_year_overrides(self) -> list[tuple[str, int]]:
        """Return (reactor_id, retirement_year) pairs for all retirement overrides."""
        return [
            (rid, ov.retirement_year)
            for rid, ov in self.reactor_overrides.items()
            if ov.retirement_year is not None
        ]
