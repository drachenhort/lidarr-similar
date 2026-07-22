# Changelog

All notable changes to this project are documented here, in reverse chronological order.

## Unreleased

### Added
- Initial project scaffold: async, typed Python package (`lidarr_similar`) discovering artists similar to your Last.fm listening history and adding them to Lidarr.
- `LastFmClient` — pulls top artists from scrobble history and Last.fm's `artist.getSimilar` candidates.
- `DeezerClient` — second, independent similar-artist source via Deezer's public (unofficial) API; synthesizes a rank-decayed similarity score since Deezer has no numeric match value.
- Candidate merge logic (`pipeline.merge_candidates`) — unions candidates from Last.fm and Deezer by artist name, keeps the higher similarity score, and applies a `+0.15` boost (capped at 1.0) when an artist is surfaced by more than one source.
- `DiscogsEnricher` — optional, non-blocking enrichment stage that attaches genre/style metadata to candidates after merge; a miss or API error leaves the candidate unchanged so the core discovery flow is unaffected.
- SQLite-backed `Cache` for enrichment lookups, shared across sources, to avoid repeat API calls across runs.
- `LidarrClient` — dedupes candidates against the existing library and adds new artists.
- `discover_candidates()` pipeline wiring: seed artists → Last.fm + Deezer candidates → merge/boost → Discogs enrichment → sort by similarity.
- CLI entrypoint (`python -m lidarr_similar`) reading configuration from environment variables (`LASTFM_API_KEY`, `LASTFM_USERNAME`, `DISCOGS_TOKEN`, `DISCOGS_ENABLED`, `DEEZER_ENABLED`, `LIDARR_URL`, `LIDARR_API_KEY`, `CACHE_PATH`).
- Test suite (`pytest`, `respx` for HTTP mocking) covering the cache, Discogs enrichment (exact match, fuzzy match, no match, API error, caching), Deezer client, and merge/dedupe logic.

### Notes / future ideas
- A "music genome"-style audio-feature/embedding stage (e.g. Essentia, OpenL3) was discussed as a longer-term idea for acoustic similarity based on the actual audio in the library, rather than listener co-occurrence — parked as a someday item, not scheduled.
