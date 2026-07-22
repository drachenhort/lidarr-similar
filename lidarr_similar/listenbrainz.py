"""ListenBrainz client: a second, independent popularity signal alongside Deezer's fan count.

ListenBrainz's dedicated /1/popularity/artist endpoint is disabled
server-side ("high load", confirmed live), so this uses its per-artist
stats endpoint instead, which returns the same kind of data (distinct
listener count, total play count) for a given MusicBrainz artist ID.
No auth token required.

Requires a candidate's MBID, which isn't always known (only when
Last.fm supplied one) - candidates without one are left unchanged,
same best-effort, non-blocking contract as DiscogsEnricher.
"""

from __future__ import annotations

import httpx

from lidarr_similar.cache import Cache
from lidarr_similar.models import Candidate

API_ROOT = "https://api.listenbrainz.org"
CACHE_SOURCE = "listenbrainz"


class ListenBrainzClient:
    def __init__(self, cache: Cache | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self._cache = cache
        self._http = http_client or httpx.AsyncClient(base_url=API_ROOT)

    async def enrich_popularity(self, candidate: Candidate) -> Candidate:
        """Best-effort listener-count attach; returns candidate unchanged on miss/error/no MBID."""
        if candidate.mbid is None:
            return candidate

        if self._cache is not None:
            cached = self._cache.get(CACHE_SOURCE, candidate.mbid)
            if cached is not None:
                candidate.listenbrainz_listeners = cached
                return candidate

        try:
            response = await self._http.get(f"/1/stats/artist/{candidate.mbid}/listeners")
            response.raise_for_status()
            # 204 (no content) means ListenBrainz has no listener data for this artist at
            # all - a real, common outcome given its much smaller user base than Last.fm/
            # Deezer, not an error. Its empty body would otherwise crash response.json().
            if response.status_code == 204:
                return candidate
            listeners = response.json().get("payload", {}).get("total_user_count")
        except (httpx.HTTPError, ValueError):
            return candidate

        if listeners is None:
            return candidate

        if self._cache is not None:
            self._cache.set(CACHE_SOURCE, candidate.mbid, listeners)
        candidate.listenbrainz_listeners = listeners
        return candidate

    async def aclose(self) -> None:
        await self._http.aclose()
