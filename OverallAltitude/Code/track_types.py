"""Types shared by the analysis pipeline and its elevation backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackPoint:
    latitude: float
    longitude: float
    elevation_m: float | None


class ElevationServiceError(RuntimeError):
    """Raised when the external elevation source cannot satisfy a request."""
