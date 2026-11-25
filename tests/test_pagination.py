from datetime import UTC, datetime, timedelta, timezone

import pytest

from jobwatch.francetravail import ContentRange, format_api_datetime, parse_content_range


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("offres 0-149/1234", ContentRange(0, 149, 1234)),
        ("offres 150-299/1234", ContentRange(150, 299, 1234)),
        ("0-0/1", ContentRange(0, 0, 1)),
        ("  offres  1000 - 1149 / 5000 ", ContentRange(1000, 1149, 5000)),
        ("offres 0-149/*", ContentRange(0, 149, None)),
        ("offres */0", None),
        ("n'importe quoi", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_content_range(header: str | None, expected: ContentRange | None) -> None:
    assert parse_content_range(header) == expected


def test_format_api_datetime_is_utc_without_fraction() -> None:
    paris = timezone(timedelta(hours=1))
    moment = datetime(2026, 11, 25, 6, 30, 12, 987654, tzinfo=paris)
    assert format_api_datetime(moment) == "2026-11-25T05:30:12Z"
    assert format_api_datetime(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05Z"


def test_format_api_datetime_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naïf"):
        format_api_datetime(datetime(2026, 1, 1))  # noqa: DTZ001
