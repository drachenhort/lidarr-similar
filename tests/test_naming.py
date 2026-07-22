from __future__ import annotations

from lidarr_similar.naming import normalize_name


def test_normalize_name_matches_across_stylized_punctuation():
    # Found live: Lidarr stores "[:SITD:]" while Last.fm/Deezer return "SITD",
    # and Lidarr stores "38 Special" while Deezer returns ".38 Special" - both
    # went unmatched (no already_in_library flag) before punctuation was stripped.
    assert normalize_name("[:SITD:]") == normalize_name("SITD")
    assert normalize_name(".38 Special") == normalize_name("38 Special")


def test_normalize_name_matches_case_and_diacritics():
    assert normalize_name("L'Âme Immortelle") == normalize_name("L'âme Immortelle")


def test_normalize_name_collapses_whitespace_left_by_stripped_punctuation():
    assert normalize_name("A.B.C.") == normalize_name("ABC")
    assert normalize_name("A - B") == normalize_name("A  B")
