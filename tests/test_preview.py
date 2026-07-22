from __future__ import annotations

from lidarr_similar.models import Candidate
from lidarr_similar.preview import filter_by_min_score, parse_args, print_table


def test_parse_args_defaults():
    args = parse_args([])

    assert args.limit == 25
    assert args.min_score == 0.0
    assert args.no_min_score is False
    assert args.seed_artists == 20
    assert args.similar_per_artist == 10
    assert args.no_deezer is False
    assert args.no_discogs is False
    assert args.no_lidarr is False


def test_parse_args_overrides():
    args = parse_args(["--limit", "5", "--min-score", "0.5", "--no-deezer", "--no-lidarr"])

    assert args.limit == 5
    assert args.min_score == 0.5
    assert args.no_deezer is True
    assert args.no_lidarr is True


def test_parse_args_no_min_score():
    args = parse_args(["--min-score", "0.5", "--no-min-score"])

    assert args.min_score == 0.5
    assert args.no_min_score is True


def test_filter_by_min_score_drops_low_scores():
    candidates = [
        Candidate(name="High", similarity=0.8, sources=["lastfm"]),
        Candidate(name="Borderline", similarity=0.5, sources=["lastfm"]),
        Candidate(name="Low", similarity=0.3, sources=["lastfm"]),
    ]

    result = filter_by_min_score(candidates, 0.5)

    assert [c.name for c in result] == ["High", "Borderline"]


def test_print_table_handles_empty_candidates(capsys):
    print_table([], library_check_active=True)

    assert "No candidates found." in capsys.readouterr().out


def test_print_table_lists_candidates_with_sources_and_genres(capsys):
    candidates = [
        Candidate(
            name="Boards of Canada",
            similarity=0.95,
            sources=["lastfm", "deezer"],
            discogs_genres=["Electronic"],
            discogs_latest_release_year="2022",
            popularity=12345,
        ),
        Candidate(name="Aphex Twin", similarity=0.8, sources=["deezer"], already_in_library=True),
    ]

    print_table(candidates, library_check_active=True)
    output = capsys.readouterr().out

    assert "Boards of Canada" in output
    assert "lastfm,deezer" in output
    assert "Electronic" in output
    assert "2022" in output
    assert "12,345" in output
    assert "Aphex Twin" in output
    assert "2 candidate(s) shown." in output
    assert "Note:" not in output
    # "In Library" column: Boards of Canada is not in library, Aphex Twin is
    boards_line = next(line for line in output.splitlines() if "Boards of Canada" in line)
    aphex_line = next(line for line in output.splitlines() if "Aphex Twin" in line)
    assert "yes" not in boards_line
    assert "yes" in aphex_line


def test_print_table_warns_when_library_check_inactive(capsys):
    print_table([Candidate(name="X", similarity=0.5, sources=["lastfm"])], library_check_active=False)

    assert 'no Lidarr connection' in capsys.readouterr().out
