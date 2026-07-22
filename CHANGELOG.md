# Changelog

All notable changes to this project are documented here, in reverse chronological order.

## Unreleased

### Added
- Web UI now updates incrementally instead of only showing results once a full run finishes: `discover_candidates()` takes an `on_progress` callback, invoked once after the initial merge and again after each candidate is enriched, and the web UI persists each snapshot to the store immediately. The page shows an "N/M enriched" counter while a run is in progress. Verified live: results (217 candidates) appeared within ~16s of starting a run, well before the ~60s full run completed.
- Pagination on the web UI (50 candidates/page, Prev/Next controls) so large result sets don't render as one huge table.
- "Add to Lidarr" button per candidate row in the web UI - looks the artist up and adds it via `LidarrClient` without leaving the page, then marks it `already_in_library`. Requires `LIDARR_URL`, `LIDARR_API_KEY`, `LIDARR_ROOT_FOLDER`, and `LIDARR_QUALITY_PROFILE_ID` (new config fields); shows an explanatory hint instead of the button when any are missing.
- "Ignore" button per candidate row - persists to a new `IgnoreList` (SQLite, same store file), excluded from enrichment API calls on future `discover_candidates()` runs via a new `ignored_names` parameter, but kept visible rather than hidden: `Candidate.ignored` tags the row "ignored" and pushes it to the bottom of the list (regardless of score) with an **Unignore** button, so a previously-ignored artist that resurfaces is called out instead of silently disappearing. Verified live: an ignored artist stayed tagged and excluded from enrichment across a subsequent full discovery run, and unignoring correctly restored it.

### Fixed
- `Config.from_env()` crashed the whole app (500 on every request) if `LIDARR_QUALITY_PROFILE_ID` was set to a non-numeric value, e.g. a profile name like "Standard" instead of its numeric ID - found live when testing the Add-to-Lidarr feature. Now treated as unset, same as every other optional config field, with a docstring note on `_parse_int` clarifying it must be the numeric ID.
- Ignoring (or adding) an artist while a discovery run was still in progress got silently reverted by the run's next progress snapshot, since `discover_candidates()` computes `already_in_library`/`ignored` once from a snapshot taken before the run started, and each `on_progress` call overwrote the store with that stale computation. Found via a live test: ignoring an artist mid-run, then waiting for the run to finish, showed it un-ignored again. Fixed by having the web UI's `on_progress` handler preserve any `already_in_library`/`ignored` flags already set in the store before each snapshot write.
- `lidarr_similar/naming.py`: extracted `normalize_name()` out of `pipeline.py` into a shared module so `store.py`'s `IgnoreList` can use the same case/diacritic-insensitive matching without a pipeline->store dependency; `pipeline.normalize_name` still re-exports it for backward compatibility.
- `Candidate.already_in_library` and a "Last Release" column: candidates already in the connected Lidarr library are now kept in the results (both CLIs and the web UI) and flagged with a notice, instead of being silently dropped. Last-release info comes from Discogs' release search sorted by year descending (`discogs_latest_release_year`), reusing the existing genre/style enrichment call rather than adding a new one; verified against the live API.
- Web UI (`lidarr_similar/web.py`, FastAPI): a browsable dashboard showing discovered candidates, persisted across restarts via `CandidateStore` (SQLite). Includes a "Run discovery now" button; since a full run can take minutes (mostly Discogs' 60 req/min rate limit), refresh runs as a background task and the page polls itself while in progress rather than blocking the request - verified by a live run that took ~70s and returned 217 candidates. Supports `?min_score=` filtering like the preview CLI.
- `Dockerfile` and `docker-compose.yml` for running the web UI in a container (e.g. on Unraid); built and smoke-tested locally. `STORE_PATH`/`CACHE_PATH` default under `/data`, meant to be volume-mounted for persistence across container updates.

### Changed
- Discovery no longer silently excludes artists already in your Lidarr library - they're returned like any other candidate but marked `already_in_library=True`, so the CLIs and web UI can surface a notice instead of hiding them.

### Fixed
- Candidate merge and Lidarr-library dedupe now match names case- and diacritic-insensitively (`normalize_name()`), so e.g. Last.fm's "L'Âme Immortelle" and Deezer's "L'âme Immortelle" merge into one entry instead of appearing twice. Found via a live preview run against a real library.

### Added
- `--no-min-score` flag on the preview CLI to reset `--min-score` back to 0.0 (show every candidate), while `--limit` keeps its default of 25.
- `--min-score` flag on `python -m lidarr_similar.preview` to drop candidates below a similarity threshold before display.
- `DeezerClient.enrich_genre()` — genre enrichment from Deezer, no API token required. Deezer artist objects carry no genre field, so this resolves an artist's top album's `genre_id` to a genre name; cached and best-effort like `DiscogsEnricher`. Wired into `discover_candidates()` and shown alongside Discogs genres in both CLIs.
- `python -m lidarr_similar.preview` — terminal preview CLI showing a ranked table of candidates (score, contributing sources, Discogs genres) without ever calling Lidarr's add-artist endpoint. Lidarr credentials are now optional; when unset, preview shows all candidates without library dedupe. Supports `--limit`, `--seed-artists`, `--similar-per-artist`, `--no-deezer`, `--no-discogs`, `--no-lidarr`.
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
