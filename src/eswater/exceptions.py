"""Typed exceptions for the Essex & Suffolk Water client.

These let consumers (e.g. a Home Assistant integration) react precisely:
trigger a re-auth flow on ``InvalidAuth``, back off on ``ServiceUnavailable``,
etc.
"""

from __future__ import annotations


class ESWaterError(Exception):
    """Base class for all eswater errors."""


class InvalidAuth(ESWaterError):
    """Credentials were rejected, or the token could not be refreshed."""


class NotAuthenticated(ESWaterError):
    """A request was attempted before ``authenticate()`` succeeded."""


class ServiceUnavailable(ESWaterError):
    """The upstream API is unreachable, timed out, or returned a 5xx."""


class ApiError(ESWaterError):
    """The API returned an unexpected status or an unparseable response."""
