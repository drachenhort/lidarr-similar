"""Web UI: browse discovered candidates and trigger a fresh discovery run from a browser.

Runs the same discovery pipeline as the preview CLI. Results are persisted
in CandidateStore so the page still shows the last run after a restart -
useful in a long-running Docker container where you check back periodically
rather than re-running discovery every time.

A full run can take minutes (Discogs enrichment alone is rate-limited to
60 req/min and makes ~2 calls per candidate), so /refresh kicks the run off
as a background task and returns immediately instead of blocking the HTTP
request - a synchronous multi-minute request would trip browser and reverse
proxy timeouts. Discovery reports progress via an on_progress callback that
writes each partial snapshot to the store as it goes, so the auto-refreshing
index page fills in with results as they're found instead of staying empty
until the whole run finishes.

Each row offers "Add to Lidarr" (requires LIDARR_URL/API_KEY/ROOT_FOLDER/
QUALITY_PROFILE_ID) and "Ignore" (persisted in IgnoreList and excluded from
future discovery runs). Ignored artists stay visible rather than vanishing -
they're tagged "ignored" and pushed to the bottom of the list, with an
"Unignore" button, so a previously-ignored artist that resurfaces in a new
run is called out instead of silently disappearing.

Whole genres can be banned too (GenreIgnoreList): each genre tag in a row
has an inline "x" to ban it with one click, and a manual "ignore a genre"
form covers genres not yet visible. Matching is a case-insensitive substring
check, since genres only become known after enrichment and different
sources use different granularity (e.g. Discogs "Hip Hop" vs Deezer
"Rap/Hip Hop"). Genre-banned candidates are tagged like artist-ignores but
have no per-row Unignore, since undoing them means un-banning the genre.

Two independent popularity signals are shown: Deezer fan count and
ListenBrainz distinct-listener count. ListenBrainz needs a candidate's
MBID, so it's only populated when Last.fm supplied one.
"""

from __future__ import annotations

import asyncio
import html
import os
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from lidarr_similar.cache import Cache
from lidarr_similar.config import OVERRIDABLE_KEYS, Config, ConfigItem, describe_config, get_effective
from lidarr_similar.deezer import DeezerClient
from lidarr_similar.discogs import DiscogsEnricher
from lidarr_similar.lastfm import LastFmClient
from lidarr_similar.lidarr import LidarrClient
from lidarr_similar.listenbrainz import ListenBrainzClient
from lidarr_similar.models import Candidate
from lidarr_similar.pipeline import discover_candidates
from lidarr_similar.store import CandidateStore, GenreIgnoreList, IgnoreList, SettingsStore

app = FastAPI(title="lidarr-similar")

PAGE_SIZE = 50

_BASE_STYLE = """
    :root {
      --ink: #14151f;
      --panel: #1b1d2b;
      --panel-head: #171826;
      --panel-hover: #21243680;
      --panel-hover-solid: #262a3d;
      --line: #2c2f42;
      --paper: #ece9f5;
      --dim: #8d8aa3;
      --amber: #e2a03f;
      --amber-soft: rgba(226, 160, 63, 0.14);
      --teal: #5ec2ac;
      --teal-soft: rgba(94, 194, 172, 0.14);
      --rose: #e07a8c;
      --rose-soft: rgba(224, 122, 140, 0.14);
      --font-display: ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", "URW Palladio L", serif;
      --font-body: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Menlo, Consolas, monospace;
    }
    * { box-sizing: border-box; }
    body {
      font-family: var(--font-body);
      background: var(--ink);
      color: var(--paper);
      max-width: 1120px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4rem;
      line-height: 1.5;
    }
    a { color: var(--amber); }
    :focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }

    .site-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      border-bottom: 1px solid var(--line);
      padding-bottom: 1rem;
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
    }
    .wordmark {
      font-family: var(--font-display);
      font-size: 1.9rem;
      font-weight: 500;
      letter-spacing: -0.01em;
      margin: 0;
      color: var(--paper);
    }
    .wordmark .accent { color: var(--amber); }
    .nav a { text-decoration: none; font-size: 0.85em; letter-spacing: 0.02em; text-transform: uppercase; color: var(--dim); }
    .nav a:hover { color: var(--amber); }

    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; background: var(--panel); font-size: 0.92rem; }
    th, td { text-align: left; padding: 0.55rem 0.7rem; border-bottom: 1px solid var(--line); white-space: nowrap; }
    th {
      background: var(--panel-head);
      color: var(--dim);
      font-size: 0.72rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-weight: 600;
    }
    tbody tr:hover { background: var(--panel-hover); }
    tbody tr:last-child td { border-bottom: none; }
    /* Wide table (many data columns) can overflow the viewport on typical windows;
       keep the action buttons reachable without hunting for the horizontal scrollbar. */
    th.actions-col, td.actions {
      position: sticky; right: 0;
      background: var(--panel-head);
      box-shadow: -8px 0 8px -8px rgba(0, 0, 0, 0.6);
    }
    td.actions { background: var(--panel); }
    tbody tr:hover td.actions { background: var(--panel-hover-solid); }
    td.mono, .score-num { font-family: var(--font-mono); }
    .score-cell { min-width: 5.5rem; }
    .score-num { font-size: 0.88rem; color: var(--paper); }
    .meter { display: block; width: 4.5rem; height: 4px; background: var(--line); border-radius: 2px; margin-top: 0.3rem; overflow: hidden; }
    .meter-fill { display: block; height: 100%; background: linear-gradient(90deg, var(--teal), var(--amber)); border-radius: 2px; }

    form { display: inline; }
    button, .btn {
      font-family: var(--font-body);
      background: transparent;
      color: var(--paper);
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 0.4rem 0.8rem;
      font-size: 0.85rem;
      cursor: pointer;
      transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
    }
    button:hover { border-color: var(--amber); color: var(--amber); }
    button:disabled { color: var(--dim); border-color: var(--line); cursor: not-allowed; }
    .toolbar button[type="submit"]:not(:disabled) { background: var(--amber); border-color: var(--amber); color: var(--ink); font-weight: 600; }
    .toolbar button[type="submit"]:not(:disabled):hover { background: var(--paper); border-color: var(--paper); }
    .actions button { font-size: 0.78rem; margin-right: 0.35rem; padding: 0.3rem 0.55rem; }

    .in-library { color: var(--dim); }
    .ignored-row { color: var(--dim); }
    .badge { background: var(--teal-soft); color: var(--teal); border-radius: 999px; padding: 0.1rem 0.55rem; font-size: 0.78em; white-space: nowrap; }
    .badge-ignored { background: var(--rose-soft); color: var(--rose); }

    .banner { padding: 0.6rem 0.9rem; border-radius: 6px; border-left: 3px solid; margin: 0 0 0.9rem; font-size: 0.9rem; }
    .banner.error { background: var(--rose-soft); color: var(--rose); border-color: var(--rose); }
    .banner.ok { background: var(--teal-soft); color: var(--teal); border-color: var(--teal); }
    .hint { color: var(--dim); font-size: 0.85em; }

    .toolbar { display: flex; justify-content: space-between; align-items: center; margin: 1.25rem 0 1rem; gap: 1rem; flex-wrap: wrap; }
    .sort-form { display: flex; align-items: center; gap: 0.4rem; font-size: 0.88rem; color: var(--dim); }
    .sort-form select { background: var(--panel); color: var(--paper); border: 1px solid var(--line); border-radius: 6px; padding: 0.3rem 0.5rem; }
    .pagination { margin-top: 1.1rem; display: flex; gap: 0.9rem; align-items: center; font-size: 0.88rem; color: var(--dim); }
    .pagination a { text-decoration: none; color: var(--amber); }
    .pagination a.disabled { pointer-events: none; color: var(--line); }

    .ignore-list { margin-bottom: 1rem; border: 1px solid var(--line); border-radius: 8px; padding: 0.7rem 1rem; background: var(--panel); }
    .ignore-list summary {
      cursor: pointer; font-weight: 600; font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--dim);
    }
    .ignore-list[open] summary { color: var(--amber); }
    .ignore-list ul { list-style: none; padding: 0; margin: 0.7rem 0 0; }
    .ignore-list li { display: flex; justify-content: space-between; align-items: center; padding: 0.3rem 0; border-bottom: 1px solid var(--line); }
    .ignore-list li:last-child { border-bottom: none; }
    .genre-tag {
      display: inline-flex; align-items: center; gap: 0.3rem; background: var(--amber-soft); color: var(--amber);
      border-radius: 999px; padding: 0.05rem 0.15rem 0.05rem 0.55rem; font-size: 0.82em; margin: 0.1rem 0.2rem 0.1rem 0;
    }
    .genre-ignore-btn { border: none; background: none; color: var(--rose); cursor: pointer; font-size: 1em; padding: 0 0.4rem; line-height: 1; }
    .genre-form { margin-top: 0.7rem; display: flex; gap: 0.5rem; }
    .genre-form input { padding: 0.35rem 0.6rem; }

    input[type="text"], input[type="password"], select {
      font-family: var(--font-body); background: var(--panel-head); color: var(--paper);
      border: 1px solid var(--line); border-radius: 5px; padding: 0.4rem 0.6rem; font-size: 0.85rem;
    }
    input::placeholder { color: var(--dim); }

    .status-ok { color: var(--teal); font-weight: 600; }
    .status-missing { color: var(--dim); }
    .status-invalid { color: var(--rose); font-weight: 600; }

    @media (prefers-reduced-motion: reduce) {
      button { transition: none; }
    }
    @media (max-width: 640px) {
      body { padding: 1.25rem 0.9rem 3rem; }
      .wordmark { font-size: 1.5rem; }
    }
"""


@dataclass
class RefreshStatus:
    running: bool = False
    error: str | None = None
    enriched: int = 0
    total: int = 0


_status = RefreshStatus()


def _store_path() -> str:
    return os.environ.get("STORE_PATH", "lidarr_similar_store.sqlite3")


def _describe(error: Exception) -> str:
    """str(error) is empty for some exceptions (e.g. httpx.ReadTimeout with no message) -
    confirmed live, this silently hid the error banner entirely, since the template only
    renders it when status.error is truthy. Always fall back to the exception's type name."""
    return str(error) or type(error).__name__


def _is_lidarr_add_enabled() -> bool:
    """Whether every value the Add to Lidarr button needs is both present and valid -
    via describe_config() rather than raw os.environ, so a UI-saved override (SettingsStore)
    counts, and an invalid LIDARR_QUALITY_PROFILE_ID/LIDARR_METADATA_PROFILE_ID (e.g. a
    profile name, not its ID) correctly disables the button instead of being treated as
    merely "truthy"."""
    required = {
        "LIDARR_URL",
        "LIDARR_API_KEY",
        "LIDARR_ROOT_FOLDER",
        "LIDARR_QUALITY_PROFILE_ID",
        "LIDARR_METADATA_PROFILE_ID",
    }
    items = {item.name: item for item in describe_config() if item.name in required}
    return all(item.present and item.valid for item in items.values())


_SORT_KEYS = {
    "score": lambda c: (-c.similarity,),
    "deezer_fans": lambda c: (c.popularity is None, -(c.popularity or 0)),
    "lb_listeners": lambda c: (c.listenbrainz_listeners is None, -(c.listenbrainz_listeners or 0)),
}


@app.get("/", response_class=HTMLResponse)
async def index(
    min_score: float = 0.0, page: int = 1, sort: str = "score", message: str | None = None, error: str | None = None
) -> str:
    store = CandidateStore(_store_path())
    try:
        candidates = [c for c in store.load_all() if c.similarity >= min_score]
        candidates.sort(key=lambda c: (c.ignored, *_SORT_KEYS.get(sort, _SORT_KEYS["score"])(c)))
        last_updated = store.last_updated()
    finally:
        store.close()

    ignore_list = IgnoreList(_store_path())
    try:
        ignored_names = ignore_list.list_ordered()
    finally:
        ignore_list.close()

    genre_ignore_list = GenreIgnoreList(_store_path())
    try:
        ignored_genres = genre_ignore_list.list_ordered()
    finally:
        genre_ignore_list.close()

    lidarr_add_enabled = _is_lidarr_add_enabled()
    return render_page(
        candidates,
        last_updated,
        min_score,
        page,
        sort,
        _status,
        lidarr_add_enabled,
        message,
        error,
        ignored_names,
        ignored_genres,
    )


@app.get("/config", response_class=HTMLResponse)
async def config_status(message: str | None = None, error: str | None = None) -> str:
    profiles = await _fetch_lidarr_profiles()
    return render_config_page(describe_config(), profiles, message, error)


@app.post("/config")
async def save_config(request: Request) -> RedirectResponse:
    form = await request.form()
    settings = SettingsStore(_store_path())
    try:
        for key in OVERRIDABLE_KEYS:
            raw = form.get(key)
            if raw is None:
                continue
            value = str(raw).strip()
            if not value:
                continue  # blank means "leave unchanged" - important for secret fields,
                # which are never pre-filled, so a blank submit must not wipe them out
            settings.set(key, value)
    finally:
        settings.close()
    return RedirectResponse(f"/config?{urlencode({'message': 'Configuration saved.'})}", status_code=303)


async def _fetch_lidarr_profiles() -> dict[str, list[dict]]:
    """Live quality- and metadata-profile lists for the config page's dropdowns, keyed by
    the env var each one fills in. Missing/unreachable Lidarr just yields empty lists -
    the form falls back to a plain numeric input in that case."""
    url, api_key = get_effective("LIDARR_URL"), get_effective("LIDARR_API_KEY")
    if not url or not api_key:
        return {"LIDARR_QUALITY_PROFILE_ID": [], "LIDARR_METADATA_PROFILE_ID": []}
    lidarr = LidarrClient(url, api_key)
    try:
        quality = await lidarr.quality_profiles()
        metadata = await lidarr.metadata_profiles()
        return {"LIDARR_QUALITY_PROFILE_ID": quality, "LIDARR_METADATA_PROFILE_ID": metadata}
    except Exception:  # noqa: BLE001 - best-effort; any failure just falls back to text inputs
        return {"LIDARR_QUALITY_PROFILE_ID": [], "LIDARR_METADATA_PROFILE_ID": []}
    finally:
        await lidarr.aclose()


@app.post("/refresh")
async def refresh() -> RedirectResponse:
    if _status.running:
        return RedirectResponse("/", status_code=303)

    try:
        config = Config.from_env()
    except RuntimeError as error:
        _status.error = _describe(error)
        return RedirectResponse("/", status_code=303)

    _status.running = True
    _status.error = None
    _status.enriched = 0
    _status.total = 0
    asyncio.create_task(_run_discovery(config))
    return RedirectResponse("/", status_code=303)


@app.post("/ignore")
async def ignore(name: str = Form(...)) -> RedirectResponse:
    ignore_list = IgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        ignore_list.add(name)
        store.mark_ignored(name, ignored=True)
    finally:
        ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Ignored {name}. It will be excluded from future runs.'})}", status_code=303)


@app.post("/unignore")
async def unignore(name: str = Form(...)) -> RedirectResponse:
    ignore_list = IgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        ignore_list.remove(name)
        store.mark_ignored(name, ignored=False)
    finally:
        ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Unignored {name}.'})}", status_code=303)


@app.post("/ignore-genre")
async def ignore_genre(genre: str = Form(...)) -> RedirectResponse:
    genre_ignore_list = GenreIgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        genre_ignore_list.add(genre)
        for candidate in store.load_all():
            combined = candidate.discogs_genres + candidate.discogs_styles
            if candidate.deezer_genre:
                combined.append(candidate.deezer_genre)
            if any(genre.casefold() in g.casefold() for g in combined):
                store.mark_ignored(candidate.name, ignored=True, ignored_genre=genre)
    finally:
        genre_ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Ignoring genre {genre}.'})}", status_code=303)


@app.post("/unignore-genre")
async def unignore_genre(genre: str = Form(...)) -> RedirectResponse:
    genre_ignore_list = GenreIgnoreList(_store_path())
    store = CandidateStore(_store_path())
    try:
        genre_ignore_list.remove(genre)
        for candidate in store.load_all():
            if candidate.ignored_genre and candidate.ignored_genre.casefold() == genre.casefold():
                store.mark_ignored(candidate.name, ignored=False, ignored_genre=None)
    finally:
        genre_ignore_list.close()
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Unignored genre {genre}.'})}", status_code=303)


@app.post("/add")
async def add(name: str = Form(...)) -> RedirectResponse:
    config = Config.from_env()
    if not (
        config.lidarr_url
        and config.lidarr_api_key
        and config.lidarr_root_folder
        and config.lidarr_quality_profile_id
        and config.lidarr_metadata_profile_id
    ):
        return RedirectResponse(
            f"/?{urlencode({'error': 'Lidarr is not fully configured (URL/API key/root folder/quality profile/metadata profile).'})}",
            status_code=303,
        )

    lidarr = LidarrClient(config.lidarr_url, config.lidarr_api_key)
    try:
        lookup = await lidarr.lookup_artist(name)
        if lookup is None:
            not_found_message = f"{name} was not found in Lidarr's catalog search."
            return RedirectResponse(f"/?{urlencode({'error': not_found_message})}", status_code=303)
        await lidarr.add_artist(
            Candidate(name=name, similarity=0.0),
            config.lidarr_root_folder,
            config.lidarr_quality_profile_id,
            config.lidarr_metadata_profile_id,
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the UI, not swallowed
        return RedirectResponse(f"/?{urlencode({'error': f'Failed to add {name}: {_describe(error)}'})}", status_code=303)
    finally:
        await lidarr.aclose()

    store = CandidateStore(config.store_path)
    try:
        store.mark_in_library(name)
    finally:
        store.close()
    return RedirectResponse(f"/?{urlencode({'message': f'Added {name} to Lidarr.'})}", status_code=303)


async def _run_discovery(config: Config) -> None:
    cache = Cache(config.cache_path)
    lastfm = LastFmClient(config.lastfm_api_key)
    deezer = DeezerClient(cache) if config.deezer_enabled else None
    listenbrainz = ListenBrainzClient(cache) if config.listenbrainz_enabled else None
    discogs = (
        DiscogsEnricher(config.discogs_token, cache)
        if config.discogs_enabled and config.discogs_token
        else None
    )
    lidarr = (
        LidarrClient(config.lidarr_url, config.lidarr_api_key)
        if config.lidarr_url and config.lidarr_api_key
        else None
    )
    ignore_list = IgnoreList(config.store_path)
    genre_ignore_list = GenreIgnoreList(config.store_path)
    store = CandidateStore(config.store_path)

    async def on_progress(candidates: list[Candidate]) -> None:
        _status.total = len(candidates)
        _status.enriched = sum(1 for c in candidates if c.discogs_id is not None or c.deezer_genre is not None)

        for candidate in candidates:
            if not candidate.ignored:
                combined_genres = candidate.discogs_genres + candidate.discogs_styles
                if candidate.deezer_genre:
                    combined_genres.append(candidate.deezer_genre)
                matched = genre_ignore_list.matching_genre(combined_genres)
                if matched is not None:
                    candidate.ignored = True
                    candidate.ignored_genre = matched

        # Preserve already_in_library/ignored flags set via /add, /ignore, or /ignore-genre while
        # this run was in progress - the pipeline computed already_in_library/ignored from a
        # snapshot taken before the run started, so without this a manual action mid-run gets
        # silently reverted by the next snapshot.
        previous_flags = {c.name: (c.already_in_library, c.ignored, c.ignored_genre) for c in store.load_all()}
        for candidate in candidates:
            prev = previous_flags.get(candidate.name)
            if prev is not None:
                candidate.already_in_library = candidate.already_in_library or prev[0]
                candidate.ignored = candidate.ignored or prev[1]
                candidate.ignored_genre = candidate.ignored_genre or prev[2]
        store.replace_all(candidates)

    existing_names, existing_mbids = set(), set()
    if lidarr is not None:
        try:
            existing_names, existing_mbids = await lidarr.existing_artist_identifiers()
        except Exception as error:  # noqa: BLE001 - best-effort, same as Discogs/Deezer/ListenBrainz:
            # a slow/unreachable Lidarr (confirmed live: intermittent timeouts on a 962-artist
            # library) shouldn't abort the whole run, just fall back to no known-library data.
            _status.error = f"Could not reach Lidarr for library dedupe ({_describe(error)}); continuing without it."

    try:
        await discover_candidates(
            lastfm,
            config.lastfm_username,
            discogs,
            existing_names,
            deezer=deezer,
            listenbrainz=listenbrainz,
            existing_artist_mbids=existing_mbids,
            ignored_names=ignore_list.names(),
            on_progress=on_progress,
        )
    except Exception as error:  # noqa: BLE001 - surfaced to the UI, not swallowed
        _status.error = _describe(error)
    finally:
        await lastfm.aclose()
        if deezer is not None:
            await deezer.aclose()
        if listenbrainz is not None:
            await listenbrainz.aclose()
        if discogs is not None:
            await discogs.aclose()
        if lidarr is not None:
            await lidarr.aclose()
        cache.close()
        ignore_list.close()
        genre_ignore_list.close()
        store.close()
        _status.running = False


_SORT_LABELS = {
    "score": "Similarity score",
    "deezer_fans": "Deezer fans",
    "lb_listeners": "LB listeners",
}


def render_page(
    candidates: list[Candidate],
    last_updated: str | None,
    min_score: float,
    page: int,
    sort: str,
    status: RefreshStatus,
    lidarr_add_enabled: bool,
    message: str | None,
    error: str | None,
    ignored_names: list[str] = (),
    ignored_genres: list[str] = (),
) -> str:
    total = len(candidates)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    page_items = candidates[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

    rows = "".join(
        _render_row(rank, c, lidarr_add_enabled)
        for rank, c in enumerate(page_items, start=(page - 1) * PAGE_SIZE + 1)
    )
    updated_line = f"Last updated: {html.escape(last_updated)}" if last_updated else "No discovery run yet."
    body = "<p>No candidates to show.</p>" if not candidates else f"""
    <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Artist</th><th>Score</th><th>Sources</th>
          <th>Deezer Fans</th><th>LB Listeners</th><th>Last Release</th><th>Status</th><th>Genres</th><th class="actions-col">Actions</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    </div>
    {_render_pagination(page, total_pages, min_score, sort)}
    """
    if status.running:
        progress = f" ({status.enriched}/{status.total} enriched)" if status.total else ""
        toolbar = f"<span>Discovery running{progress}, results fill in as they're found…</span>"
        auto_refresh = '<meta http-equiv="refresh" content="5">'
        button = '<button type="submit" disabled>Run discovery now</button>'
    else:
        toolbar = f"<span>{updated_line}</span>"
        auto_refresh = ""
        button = '<button type="submit">Run discovery now</button>'
    error_banner = (
        f'<p class="banner error">Last run failed: {html.escape(status.error)}</p>' if status.error else ""
    )
    action_error = f'<p class="banner error">{html.escape(error)}</p>' if error else ""
    action_message = f'<p class="banner ok">{html.escape(message)}</p>' if message else ""
    lidarr_note = (
        ""
        if lidarr_add_enabled
        else '<p class="hint">Set LIDARR_URL, LIDARR_API_KEY, LIDARR_ROOT_FOLDER, '
        "LIDARR_QUALITY_PROFILE_ID and LIDARR_METADATA_PROFILE_ID (via /config) to enable one-click adds.</p>"
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  {auto_refresh}
  <title>lidarr-similar</title>
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="site-header">
    <h1 class="wordmark">lidarr<span class="accent">‑</span>similar</h1>
    <nav class="nav"><a href="/config">Configuration status</a></nav>
  </div>
  {_render_ignore_list(ignored_names)}
  {_render_genre_ignore_list(ignored_genres)}
  {error_banner}
  {action_error}
  {action_message}
  {lidarr_note}
  <div class="toolbar">
    {toolbar}
    {_render_sort_selector(sort, min_score)}
    <form method="post" action="/refresh">
      {button}
    </form>
  </div>
  {body}
</body>
</html>"""


def render_config_page(
    items: list[ConfigItem],
    profiles: dict[str, list[dict]],
    message: str | None,
    error: str | None,
) -> str:
    rows = "".join(_render_config_row(item, profiles) for item in items)
    all_required_present = all(item.present for item in items if item.required_for == "core discovery pipeline")
    summary = (
        '<p class="banner ok">Core discovery pipeline is configured (Last.fm credentials present).</p>'
        if all_required_present
        else '<p class="banner error">Core discovery pipeline is missing required configuration - '
        "discovery runs will fail until LASTFM_API_KEY and LASTFM_USERNAME are set.</p>"
    )
    action_message = f'<p class="banner ok">{html.escape(message)}</p>' if message else ""
    action_error = f'<p class="banner error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>lidarr-similar - Configuration</title>
  <style>{_BASE_STYLE}</style>
</head>
<body>
  <div class="site-header">
    <h1 class="wordmark">Configuration</h1>
    <nav class="nav"><a href="/">&larr; Back to results</a></nav>
  </div>
  {summary}
  {action_message}
  {action_error}
  <form method="post" action="/config">
    <div class="table-wrap">
    <table>
      <thead>
        <tr><th>Variable</th><th>Status</th><th>Used for</th><th>Value</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
    </div>
    <p><button type="submit">Save configuration</button></p>
  </form>
  <p class="hint">Secret fields (API keys, tokens) are never pre-filled or echoed back - leave one blank to keep its current value. CACHE_PATH/STORE_PATH are environment-only and can't be changed here.</p>
</body>
</html>"""


def _render_config_row(item: ConfigItem, profiles: dict[str, list[dict]]) -> str:
    if not item.present:
        status = '<span class="status-missing">not set</span>'
    elif not item.valid:
        status = '<span class="status-invalid">set, but invalid</span>'
    else:
        status = '<span class="status-ok">&#10003; set</span>'
    if item.source:
        status += f'<br><span class="hint">from {html.escape(item.source)}</span>'
    note_html = f"<br><span class=\"hint\">{html.escape(item.note)}</span>" if item.note else ""

    input_html = _render_config_input(item, profiles)

    return (
        "<tr>"
        f"<td><code>{html.escape(item.name)}</code></td>"
        f"<td>{status}{note_html}</td>"
        f"<td>{html.escape(item.required_for)}</td>"
        f"<td>{input_html}</td>"
        "</tr>"
    )


_BOOLEAN_KEYS = {"DISCOGS_ENABLED", "DEEZER_ENABLED", "LISTENBRAINZ_ENABLED"}


_PROFILE_KEYS = {"LIDARR_QUALITY_PROFILE_ID", "LIDARR_METADATA_PROFILE_ID"}


def _render_config_input(item: ConfigItem, profiles: dict[str, list[dict]]) -> str:
    if not item.editable:
        value = html.escape(item.value_preview) if item.value_preview else "-"
        return f"{value} <span class=\"hint\">(env-only)</span>"

    name_attr = html.escape(item.name, quote=True)

    if item.name in _PROFILE_KEYS and profiles.get(item.name):
        current = item.value_preview
        options = "".join(
            f'<option value="{profile["id"]}"{" selected" if str(profile["id"]) == current else ""}>'
            f'{html.escape(profile["name"])} (id {profile["id"]})</option>'
            for profile in profiles[item.name]
        )
        return f'<select name="{name_attr}"><option value="">-- choose --</option>{options}</select>'

    if item.name in _BOOLEAN_KEYS:
        current = (item.value_preview or "true").split(" ")[0]
        return (
            f'<select name="{name_attr}">'
            f'<option value="true"{" selected" if current == "true" else ""}>Enabled</option>'
            f'<option value="false"{" selected" if current == "false" else ""}>Disabled</option>'
            "</select>"
        )

    if item.secret:
        placeholder = "configured, leave blank to keep" if item.present else "not set"
        return f'<input type="password" name="{name_attr}" placeholder="{html.escape(placeholder, quote=True)}" autocomplete="off">'

    current_value = html.escape(item.value_preview, quote=True) if item.value_preview else ""
    return f'<input type="text" name="{name_attr}" value="{current_value}">'


def _render_ignore_list(ignored_names: list[str]) -> str:
    items = "".join(
        f"<li><span>{html.escape(name)}</span>"
        f'<form method="post" action="/unignore"><input type="hidden" name="name" value="{html.escape(name, quote=True)}">'
        '<button type="submit">Unignore</button></form></li>'
        for name in ignored_names
    )
    summary = f"Ignored artists ({len(ignored_names)})" if ignored_names else "Ignored artists"
    open_attr = " open" if ignored_names else ""
    return f"""
    <details class="ignore-list"{open_attr}>
      <summary>{summary}</summary>
      <ul>{items or "<li>None yet.</li>"}</ul>
    </details>
    """


def _render_genre_ignore_list(ignored_genres: list[str]) -> str:
    items = "".join(
        f"<li><span>{html.escape(genre)}</span>"
        f'<form method="post" action="/unignore-genre"><input type="hidden" name="genre" value="{html.escape(genre, quote=True)}">'
        '<button type="submit">Unignore</button></form></li>'
        for genre in ignored_genres
    )
    summary = f"Ignored genres ({len(ignored_genres)})" if ignored_genres else "Ignored genres"
    open_attr = " open" if ignored_genres else ""
    return f"""
    <details class="ignore-list"{open_attr}>
      <summary>{summary}</summary>
      <ul>{items}</ul>
      <form class="genre-form" method="post" action="/ignore-genre">
        <input type="text" name="genre" placeholder="e.g. Rap" required>
        <button type="submit">Ignore genre</button>
      </form>
    </details>
    """


def _render_pagination(page: int, total_pages: int, min_score: float, sort: str) -> str:
    if total_pages <= 1:
        return ""
    prev_qs = urlencode({"min_score": min_score, "sort": sort, "page": page - 1})
    next_qs = urlencode({"min_score": min_score, "sort": sort, "page": page + 1})
    prev_class = "disabled" if page <= 1 else ""
    next_class = "disabled" if page >= total_pages else ""
    return (
        '<div class="pagination">'
        f'<a class="{prev_class}" href="/?{prev_qs}">&larr; Prev</a>'
        f"<span>Page {page} of {total_pages}</span>"
        f'<a class="{next_class}" href="/?{next_qs}">Next &rarr;</a>'
        "</div>"
    )


def _render_sort_selector(sort: str, min_score: float) -> str:
    options = "".join(
        f'<option value="{key}"{" selected" if key == sort else ""}>{html.escape(label)}</option>'
        for key, label in _SORT_LABELS.items()
    )
    min_score_attr = html.escape(str(min_score), quote=True)
    return f"""
    <form method="get" action="/" class="sort-form">
      <input type="hidden" name="min_score" value="{min_score_attr}">
      <label>Sort by <select name="sort" onchange="this.form.submit()">{options}</select></label>
    </form>
    """


def _render_row(rank: int, candidate: Candidate, lidarr_add_enabled: bool) -> str:
    all_genres = candidate.discogs_genres + ([candidate.deezer_genre] if candidate.deezer_genre else [])
    genres_cell = " ".join(_render_genre_tag(g) for g in all_genres) or "-"
    last_release = html.escape(candidate.discogs_latest_release_year) if candidate.discogs_latest_release_year else "-"
    popularity = f"{candidate.popularity:,}" if candidate.popularity is not None else "-"
    lb_listeners = f"{candidate.listenbrainz_listeners:,}" if candidate.listenbrainz_listeners is not None else "-"
    name_attr = html.escape(candidate.name, quote=True)

    badges = []
    if candidate.already_in_library:
        badges.append('<span class="badge">already in library</span>')
    if candidate.ignored and candidate.ignored_genre:
        badges.append(f'<span class="badge badge-ignored">ignored: genre "{html.escape(candidate.ignored_genre)}"</span>')
    elif candidate.ignored:
        badges.append('<span class="badge badge-ignored">ignored</span>')
    status = " ".join(badges) or "-"

    row_class = ""
    if candidate.ignored:
        row_class = ' class="ignored-row"'
    elif candidate.already_in_library:
        row_class = ' class="in-library"'

    if candidate.ignored_genre:
        # Undoing this means un-banning the genre (top-of-page section), not un-ignoring one artist.
        actions = ""
    elif candidate.ignored:
        actions = (
            f'<form method="post" action="/unignore"><input type="hidden" name="name" value="{name_attr}">'
            f'<button type="submit">Unignore</button></form>'
        )
    elif candidate.already_in_library:
        actions = ""
    else:
        add_button = (
            f'<form method="post" action="/add"><input type="hidden" name="name" value="{name_attr}">'
            f'<button type="submit">Add to Lidarr</button></form>'
            if lidarr_add_enabled
            else ""
        )
        ignore_button = (
            f'<form method="post" action="/ignore"><input type="hidden" name="name" value="{name_attr}">'
            f'<button type="submit">Ignore</button></form>'
        )
        actions = add_button + ignore_button

    meter_pct = max(0, min(100, round(candidate.similarity * 100)))
    score_cell = (
        '<td class="score-cell">'
        f'<span class="score-num">{candidate.similarity:.2f}</span>'
        f'<span class="meter"><span class="meter-fill" style="width:{meter_pct}%"></span></span>'
        "</td>"
    )

    return (
        f"<tr{row_class}>"
        f'<td class="mono">{rank}</td>'
        f"<td>{html.escape(candidate.name)}</td>"
        f"{score_cell}"
        f"<td>{html.escape(','.join(candidate.sources))}</td>"
        f'<td class="mono">{popularity}</td>'
        f'<td class="mono">{lb_listeners}</td>'
        f'<td class="mono">{last_release}</td>'
        f"<td>{status}</td>"
        f'<td class="genres-cell">{genres_cell}</td>'
        f'<td class="actions">{actions}</td>'
        "</tr>"
    )


def _render_genre_tag(genre: str) -> str:
    genre_attr = html.escape(genre, quote=True)
    return (
        '<span class="genre-tag">'
        f"{html.escape(genre)}"
        f'<form method="post" action="/ignore-genre" style="display:inline">'
        f'<input type="hidden" name="genre" value="{genre_attr}">'
        f'<button type="submit" class="genre-ignore-btn" title="Ignore this genre">×</button>'
        "</form></span>"
    )
