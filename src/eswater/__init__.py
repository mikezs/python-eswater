"""eswater: async client for Essex & Suffolk Water smart meter data."""

from __future__ import annotations

from .client import ESWaterClient
from .exceptions import (
    ApiError,
    ESWaterError,
    InvalidAuth,
    NotAuthenticated,
    ServiceUnavailable,
)
from .models import Account, Granularity, Meter, UsageReading

__version__ = "0.1.3"

__all__ = [
    "ESWaterClient",
    "Account",
    "Meter",
    "UsageReading",
    "Granularity",
    "ESWaterError",
    "InvalidAuth",
    "NotAuthenticated",
    "ServiceUnavailable",
    "ApiError",
]
