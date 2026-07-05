"""
Torrent Resolver — converts magnet links / torrent files into direct HTTP(S)
download URLs using configurable external services.

No libtorrent dependency.  This module provides a pluggable resolver that
delegates the actual BitTorrent download to an external service and returns
a direct download link.

Supported backends (configurable via ``config.yaml``):
  - ``none``       : no resolution (only direct links supported)
  - ``generic-api``: a configurable HTTP endpoint (see config for details)

Users can also implement custom resolvers by extending ``BaseResolver``.
"""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseResolver(ABC):
    """Abstract base for a torrent-to-direct-link resolver."""

    @abstractmethod
    def resolve(self, source: str) -> List[Dict[str, Any]]:
        """Convert *source* (magnet link, torrent file path/URL) into a list
        of direct-download entries.

        Each entry has at least:
            ``{"url": "...", "filename": "...", "size": int}``
        """
        ...


# ---------------------------------------------------------------------------
# Built-in resolvers
# ---------------------------------------------------------------------------


class NullResolver(BaseResolver):
    """A resolver that does nothing — returns an empty list.

    Use this when you only want to download from direct URLs and skip any
    torrent-to-link conversion.
    """

    def __init__(self, config: Optional[dict] = None):
        pass

    def resolve(self, source: str) -> List[Dict[str, Any]]:
        logger.debug("NullResolver: skipping %s", source)
        return []


class GenericApiResolver(BaseResolver):
    """Generic HTTP API resolver.

    Sends a POST request to a configurable endpoint with the torrent source
    and expects a JSON response containing direct-download URLs.

    Configuration (under ``torrent_resolver.generic_api``):

        endpoint      : str   – API URL (required)
        api_key       : str   – optional API key sent as ``Authorization: Bearer <key>``
        param_name    : str   – JSON field name for the source (default ``"source"``)
        response_path : str   – dot-separated path to the array of results in the response
                                (e.g. ``"data.files"``).  If empty, the whole response is
                                treated as the array.
        timeout       : int   – request timeout (default 120)
    """

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint", "")
        self.api_key = config.get("api_key", "")
        self.param_name = config.get("param_name", "source")
        self.response_path = config.get("response_path", "")
        self.timeout = config.get("timeout", 120)

    def resolve(self, source: str) -> List[Dict[str, Any]]:
        if not self.endpoint:
            logger.warning("GenericApiResolver: no endpoint configured")
            return []

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {self.param_name: source}

        try:
            resp = requests.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            if self.response_path:
                for key in self.response_path.split("."):
                    data = data.get(key, [])
                    if data is None:
                        return []

            if not isinstance(data, list):
                data = [data]

            # Normalise entries
            results = []
            for entry in data:
                if isinstance(entry, dict) and "url" in entry:
                    results.append(entry)
                elif isinstance(entry, str):
                    results.append({"url": entry, "filename": "", "size": 0})

            logger.info(
                "GenericApiResolver: resolved %d link(s) from %s",
                len(results),
                source[:80],
            )
            return results

        except requests.RequestException as exc:
            logger.error("GenericApiResolver: request failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Resolver registry + factory
# ---------------------------------------------------------------------------

RESOLVERS: Dict[str, type] = {
    "none": NullResolver,
    "generic-api": GenericApiResolver,
}


def create_resolver(config: dict) -> BaseResolver:
    """Create a resolver instance from the ``torrent_resolver`` config block.

    The config must contain a ``backend`` key whose value is one of the
    registered resolver names.  Additional keys are passed as kwargs to the
    resolver constructor.
    """
    backend = config.get("backend", "none")
    resolver_cls = RESOLVERS.get(backend)
    if resolver_cls is None:
        logger.warning(
            "Unknown torrent_resolver backend '%s'; falling back to 'none'",
            backend,
        )
        resolver_cls = NullResolver

    resolver_config = config.get(backend, {}) if isinstance(config, dict) else {}
    return resolver_cls(resolver_config)


def register_resolver(name: str, resolver_cls: type) -> None:
    """Register a custom resolver class."""
    RESOLVERS[name] = resolver_cls
