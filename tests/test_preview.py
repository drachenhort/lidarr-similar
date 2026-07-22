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
    print_table([], dedupe_active=True)

    assert "No candidates found." in capsys.readouterr().out


def test_print_table_lists_candidates_with_sources_and_genres(capsys):
    candidates = [
        Candidate(name="Boards of Canada", similarity=0.95, sources=["lastfm", "deezer"], discogs_genres=["Electronic"]),
        Candidate(name="Aphex Twin", similarity=0.8, sources=["deezer"]),
    ]

    print_table(candidates, dedupe_active=True)
    output = capsys.readouterr().out

    assert "Boards of Canada" in output
    assert "lastfm,deezer" in output
    assert "Electronic" in output
    assert "Aphex Twin" in output
    assert "2 candidate(s) shown." in output
    assert "Note:" not in output


def test_print_table_warns_when_dedupe_skipped(capsys):
    print_table([Candidate(name="X", similarity=0.5, sources=["lastfm"])], dedupe_active=False)

    assert "dedupe was skipped" in capsys.readouterr().out
