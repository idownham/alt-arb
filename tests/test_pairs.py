from datetime import date, datetime, timezone

import pytest

from altarb.mapping.pairs import COLUMNS, DEFAULT_PATH, PairsFileError, load_pairs, verified_pairs

HEADER = ",".join(COLUMNS)
VERIFIED = "KXA,pm-a,false,A wins,,2026-10-17,true,Ian,2026-10-12"
UNVERIFIED = "KXB,pm-b,true,B wins,PM voids on postponement?,2026-10-18T05:00:00Z,false,,"


def write(tmp_path, *rows: str):
    path = tmp_path / "pairs.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def test_loads_rows_with_types(tmp_path):
    pairs = load_pairs(write(tmp_path, VERIFIED, UNVERIFIED))

    a, b = pairs
    assert a.kalshi_ticker == "KXA" and a.polymarket_slug == "pm-a"
    assert a.inverted is False and a.verified is True
    assert a.expiry == date(2026, 10, 17)
    assert a.verified_on == date(2026, 10, 12)
    assert b.inverted is True and b.verified is False
    assert b.expiry == datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
    assert b.resolution_notes == "PM voids on postponement?"


def test_verified_pairs_filters_unverified(tmp_path):
    pairs = load_pairs(write(tmp_path, VERIFIED, UNVERIFIED))

    assert [p.kalshi_ticker for p in verified_pairs(pairs)] == ["KXA"]


def test_header_only_file_is_empty(tmp_path):
    assert load_pairs(write(tmp_path)) == []


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("KXA,pm-a,false,,,,true,,", "need verified_by and verified_on"),
        ("KXA,pm-a,false,,,,true,Ian,", "need verified_by and verified_on"),
        ("KXA,pm-a,false,,,,ture,,", "verified must be true or false"),
        ("KXA,pm-a,yes,,,,false,,", "inverted must be true or false"),
        ("KXA,pm-a,,,,,false,,", "inverted must be true or false"),
        (",pm-a,false,,,,false,,", "kalshi_ticker is empty"),
        ("KXA,,false,,,,false,,", "polymarket_slug is empty"),
        ("KXA,pm-a,false,,,10/17/2026,false,,", "expiry must be an ISO"),
        ("KXA,pm-a,false,,,,true,Ian,Oct 12", "verified_on must be YYYY-MM-DD"),
    ],
)
def test_rejects_bad_rows_with_line_number(tmp_path, row, message):
    with pytest.raises(PairsFileError, match=message) as err:
        load_pairs(write(tmp_path, UNVERIFIED, row))
    assert ":3:" in str(err.value)  # header is line 1, the bad row is line 3


def test_rejects_duplicate_pairs(tmp_path):
    with pytest.raises(PairsFileError, match="duplicate"):
        load_pairs(write(tmp_path, VERIFIED, VERIFIED))


def test_rejects_wrong_header(tmp_path):
    path = tmp_path / "pairs.csv"
    path.write_text("kalshi_ticker,polymarket_id\nKXA,1\n", encoding="utf-8")

    with pytest.raises(PairsFileError, match="header must be"):
        load_pairs(path)


def test_repo_pairs_file_is_valid():
    # Guards the real data/pairs.csv: a bad hand edit fails the test suite.
    load_pairs(DEFAULT_PATH)
