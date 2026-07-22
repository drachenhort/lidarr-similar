# lidarr-similar

Discovers artists similar to your Last.fm listening history and adds them to Lidarr.

Candidates are gathered from Last.fm's `artist.getSimilar` and Deezer's related-artist
data (artists found by both sources are boosted), then optionally enriched with
Discogs genre/style metadata before being handed to Lidarr.

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
| `LIDARR_URL` | yes | — | Base URL of your Lidarr instance, e.g. `http://localhost:8686` |
| `LIDARR_API_KEY` | yes | — | Lidarr API key |
| `DISCOGS_TOKEN` | no | — | Discogs personal access token; enrichment is skipped if unset |
| `DISCOGS_ENABLED` | no | `true` | Set to `false` to disable Discogs enrichment |
| `DEEZER_ENABLED` | no | `true` | Set to `false` to disable the Deezer similarity source |
| `CACHE_PATH` | no | `lidarr_similar.sqlite3` | Path to the local SQLite cache used for enrichment lookups |

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

## Development

```bash
pytest              # run the full test suite
pytest tests/test_pipeline.py            # run one test file
pytest tests/test_pipeline.py::test_name # run a single test
```
