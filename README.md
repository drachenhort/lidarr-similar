# lidarr-similar

Discovers artists similar to your Last.fm listening history and adds them to Lidarr.

Candidates are gathered from Last.fm's `artist.getSimilar` and Deezer's related-artist
data (artists found by both sources are boosted), then optionally enriched with Discogs
genre/style metadata and two independent popularity signals - Deezer fan count and
ListenBrainz distinct-listener count - before being handed to Lidarr.

See [CHANGELOG.md](CHANGELOG.md) for what's been built so far.

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes runtime deps + pytest, respx
```

(Use `pip install -r requirements.txt` instead if you only need to run the tool, not test it.)

### 2. Get API credentials

- **Last.fm**: create an API key at https://www.last.fm/api/account/create
- **Discogs** (optional): create a personal access token at https://www.discogs.com/settings/developers
- **Deezer**: no key needed, it's a public API
- **Lidarr**: an API key from Settings → General in your Lidarr instance

### 3. Configure environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LASTFM_API_KEY` | yes | — | Last.fm API key |
| `LASTFM_USERNAME` | yes | — | Last.fm username to read scrobbles from |
| `LIDARR_URL` | for `python -m lidarr_similar` | — | Base URL of your Lidarr instance, e.g. `http://localhost:8686`. Optional for the preview tool (see below). |
| `LIDARR_API_KEY` | for `python -m lidarr_similar` | — | Lidarr API key. Optional for the preview tool. |
| `DISCOGS_TOKEN` | no | — | Discogs personal access token; enrichment is skipped if unset |
| `DISCOGS_ENABLED` | no | `true` | Set to `false` to disable Discogs enrichment |
| `DEEZER_ENABLED` | no | `true` | Set to `false` to disable the Deezer similarity source |
| `LISTENBRAINZ_ENABLED` | no | `true` | Set to `false` to disable ListenBrainz popularity enrichment |
| `CACHE_PATH` | no | `lidarr_similar.sqlite3` | Path to the local SQLite cache used for enrichment lookups |
| `STORE_PATH` | no | `lidarr_similar_store.sqlite3` | Path to the SQLite store the web UI persists discovery results and the ignore list in |
| `LIDARR_ROOT_FOLDER` | for the web UI's "Add to Lidarr" button | — | Root folder path Lidarr should use for newly added artists, e.g. `/music` |
| `LIDARR_QUALITY_PROFILE_ID` | for the web UI's "Add to Lidarr" button | — | Numeric quality profile ID from Lidarr's Settings → Profiles (set via `/config`'s dropdown, not by hand) |
| `LIDARR_METADATA_PROFILE_ID` | for the web UI's "Add to Lidarr" button | — | Numeric metadata profile ID; Lidarr's add-artist API rejects the request without one (set via `/config`'s dropdown) |

Example:

```bash
export LASTFM_API_KEY=your_lastfm_key
export LASTFM_USERNAME=your_lastfm_username
export LIDARR_URL=http://localhost:8686
export LIDARR_API_KEY=your_lidarr_key
export DISCOGS_TOKEN=your_discogs_token   # optional
```

### 4. Run it

```bash
python -m lidarr_similar
```

This prints discovered candidates (name, similarity score, contributing sources, Discogs
genres) sorted by similarity. Artists already in your Lidarr library are skipped.

### Preview mode

To see what would be added without touching Lidarr at all, use the preview CLI:

```bash
python -m lidarr_similar.preview
```

It runs the same discovery pipeline and prints a ranked table:

```
  #  Artist              Score  Sources         Genres
---------------------------------------------------------
  1  Aphex Twin           1.00  lastfm,deezer   Electronic, IDM
  2  Boards of Canada     0.87  lastfm          Electronic

2 candidate(s) shown.
```

`LIDARR_URL` / `LIDARR_API_KEY` are optional here — if set, they're used only to filter
out artists already in your library; if unset, every discovered candidate is shown.
Options:

```bash
python -m lidarr_similar.preview --limit 10          # show fewer results
python -m lidarr_similar.preview --no-deezer          # Last.fm only
python -m lidarr_similar.preview --no-discogs          # skip genre enrichment
python -m lidarr_similar.preview --no-listenbrainz     # skip ListenBrainz popularity enrichment
python -m lidarr_similar.preview --no-lidarr           # ignore Lidarr even if configured
python -m lidarr_similar.preview --help                # full option list
```

### Web UI

For a persistent dashboard you can check back on (e.g. running in a Docker container on
Unraid), use the web UI instead of the CLIs:

```bash
uvicorn lidarr_similar.web:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. A "⚙ Configuration status" link at the top goes to
`/config`, an editable settings page — for LASTFM_API_KEY, LASTFM_USERNAME, DISCOGS_TOKEN,
the DISCOGS/DEEZER/LISTENBRAINZ enabled toggles, and all five LIDARR_* variables, you can
enter values directly in the browser and click "Save configuration" instead of restarting
the container with different environment variables. Saved values are stored in `STORE_PATH`
(SQLite) and take priority over the equivalent environment variable when both are set.
`CACHE_PATH`/`STORE_PATH` themselves stay environment-only, since that's where the saved
settings live.

The page also shows, for every variable, whether it's set, whether it's valid where
checkable, and what feature it's needed for — secret values (API keys, tokens) are never
shown or pre-filled, only their presence; leave a secret field blank to keep its current
value. `LIDARR_QUALITY_PROFILE_ID` and `LIDARR_METADATA_PROFILE_ID` are dropdowns of your
Lidarr instance's actual profiles (fetched live) once `LIDARR_URL`/`LIDARR_API_KEY` are
set, instead of asking you to know the numeric IDs — Lidarr's UI only shows profile names
like "Standard", not their ID, which is an easy mistake to make by hand. Both are required:
Lidarr's add-artist API rejects the request outright if the metadata profile is missing.

A "Test Lidarr connection" button next to "Save configuration" checks whatever's
currently in the LIDARR_URL/LIDARR_API_KEY fields (it doesn't need to be saved first)
against Lidarr's `/api/v1/system/status` endpoint, and reports either the connected
Lidarr version or the specific reason it failed (wrong URL, bad API key, unreachable
host, etc.) - useful for catching a typo before running a full discovery.

The index page itself shows the most recent discovery results (persisted in `STORE_PATH`
so they survive restarts) and has a "Run discovery now" button. A full run can take a
few minutes — Discogs enrichment alone is rate-limited to 60 requests/min and
makes about two calls per candidate — so refresh runs in the background, and results fill
in as they're found rather than only appearing once the whole run finishes (the page
polls itself every 5s while a run is in progress and shows an "N/M enriched" counter).
Results are paginated at 50 per page. Add `?min_score=0.5` to the URL to filter the table,
same as the preview CLI's `--min-score`.

`LIDARR_URL` / `LIDARR_API_KEY` are optional here too, same as preview mode.

Each row has actions depending on its state:
- **Add to Lidarr** — looks the artist up in Lidarr and adds it directly, no need to leave
  the page. Only shown once `LIDARR_URL`, `LIDARR_API_KEY`, `LIDARR_ROOT_FOLDER` (a root
  folder path Lidarr should use, e.g. `/music`), and `LIDARR_QUALITY_PROFILE_ID` (the
  numeric ID from Lidarr's Settings → Profiles — not the profile's name) are all set;
  otherwise a hint explains what's missing.
- **Ignore** — permanently excludes the artist from future discovery runs. Ignored artists
  aren't hidden: they stay visible, tagged "ignored" and pushed to the bottom of the list
  regardless of score, with an **Unignore** button to bring them back.

An **"Ignored artists"** panel at the top of the page (above the discovery controls) lists
everything on the ignore list, each with its own Unignore button, so you can review or undo
past ignores without hunting through the results table.

You can also ban whole genres, e.g. if you never want Rap suggested: an **"Ignored genres"**
panel next to the ignored-artists one lets you type a genre and ignore it, and every genre
tag shown in a row's Genres column has a small **×** to ban it with one click. Matching is a
case-insensitive substring check (so banning "Rap" also catches Deezer's "Rap/Hip Hop"),
since genre data varies in granularity between Discogs and Deezer. Genre-banned candidates
are tagged like artist-ignores, but have no per-row Unignore — undo via the "Ignored genres"
panel instead, since it affects every artist in that genre at once.

#### Docker / Unraid

A prebuilt image is published (publicly, no login required) at
`ghcr.io/drachenhort/lidarr-similar:latest` - no need to clone the repo or build
anything yourself:

```bash
docker pull ghcr.io/drachenhort/lidarr-similar:latest
```

A `Dockerfile` and `docker-compose.yml` are also included if you'd rather build from
source.

Using the published image with `docker compose` (default in `docker-compose.yml`):

```bash
cp .env.example .env   # create this yourself, or export the vars directly
docker compose up -d
```

Or with `docker run` directly:

```bash
docker run -d \
  --name lidarr-similar \
  -p 8000:8000 \
  -e LASTFM_API_KEY=your_lastfm_key \
  -e LASTFM_USERNAME=your_lastfm_username \
  -e DISCOGS_TOKEN=your_discogs_token \
  -e LIDARR_URL=http://your-lidarr-host:8686 \
  -e LIDARR_API_KEY=your_lidarr_key \
  -v /path/on/unraid/appdata/lidarr-similar:/data \
  ghcr.io/drachenhort/lidarr-similar:latest
```

To build from source instead, swap `ghcr.io/drachenhort/lidarr-similar:latest` for
`build: .` in `docker-compose.yml` (already there, commented out), or run
`docker build -t lidarr-similar .` and use `lidarr-similar` as the image name above.

On Unraid specifically: add this as a container via the Docker tab, pointing the
repository field at `ghcr.io/drachenhort/lidarr-similar:latest`, mount an appdata path
to `/data` so the SQLite store and cache persist across container updates, and set the
same environment variables under the container's config. The web UI will be reachable
on the port you map to `8000`.

#### Unraid Community Applications template

[`unraid-template.xml`](unraid-template.xml) is a ready-made Community Applications
container template (image, port, `/data` path, and all `LASTFM_*`/`LIDARR_*`/enrichment
toggle env vars pre-declared with descriptions). Two ways to use it:

- **Quickest**: in Unraid's Docker tab, click "Add Container", switch to
  "advanced view", and paste
  `https://raw.githubusercontent.com/drachenhort/lidarr-similar/master/unraid-template.xml`
  into the template field.
- **As a CA template repo**: in Community Applications' settings, add
  `https://github.com/drachenhort/lidarr-similar` as a template repository - CA will
  pick up `unraid-template.xml` automatically.

Getting it listed in the public CA app store itself requires a separate PR against
[Community Applications' template list](https://github.com/Squidly271/AppFeed) once the
project is stable enough to submit.

## Development

```bash
pytest              # run the full test suite
pytest tests/test_pipeline.py            # run one test file
pytest tests/test_pipeline.py::test_name # run a single test
```
