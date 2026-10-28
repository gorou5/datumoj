"""Small synthetic parser/state tests; no page or network access."""

import sqlite3
from datetime import UTC, datetime

import pytest

from datumoj.eltiro.page_cases import MAX_CASES, MAX_DEPTH, MAX_PAGE_BYTES, parse_cases, record_cases


def page(number="V07", hypothesis="假设", title="标题"):
    return (f'<article class="case"><span class="no">{number}</span><h3>{title}</h3>'
            f'<p class="hyp">{hypothesis}</p><div class="srcs"><a class="src" href="https://example.invalid/a">'
            '来源</a></div><p class="method">方法</p><p class="signal">信号</p></article>')


def parse(html):
    return parse_cases(html.encode(), "verify")


def record(html, tmp_path, day=1):
    return record_cases(parse(html), data_dir=tmp_path, seen_at=datetime(2026, 1, day, tzinfo=UTC))


def test_normalization_ignores_markup_labels_script_style_and_outside_content():
    clean = parse(page()).cases[0]
    noisy = parse('<h1>outside</h1><script><article class="case">ignored</article></script>' + page(
        hypothesis='<span class="lab">标签</span>\n 假设 <script>register()</script><style>ignore</style>')).cases[0]
    assert clean == noisy
    assert clean.content_hash == noisy.content_hash
    assert parse(page(hypothesis="假设修改")).cases[0].content_hash != clean.content_hash


@pytest.mark.parametrize("html", [page().replace('<span class="no">V07</span>', ''),
                                  page().replace('<p class="method">方法</p>', ''),
                                  page().replace('href="https://example.invalid/a"', ''),
                                  page().replace('V07', '../../escape'),
                                  page().replace('</h3>', '</h3><h3>other</h3>'), page() + page(),
                                  page().replace('</article>', '')])
def test_invalid_cases_are_failures(html):
    result = parse(html)
    assert not result.cases
    assert result.failures


@pytest.mark.parametrize("payload", [b'\xff', b'<h1>no articles</h1>', b' ' * (MAX_PAGE_BYTES + 1),
                                     (page() * (MAX_CASES + 1)).encode(),
                                     ('<div>' * (MAX_DEPTH + 1)).encode()])
def test_whole_page_limits(payload):
    with pytest.raises(ValueError):
        parse_cases(payload, "verify")


def test_partial_failure_updates_valid_and_preserves_failed(tmp_path):
    assert record(page() + page("V08"), tmp_path).added == ("V07", "V08")
    broken = page("V08").replace('<p class="method">方法</p>', '')
    changed = record(page(hypothesis="变化") + broken, tmp_path, 2)
    assert changed.changed == ("V07",)
    assert changed.disappeared == ()
    assert changed.failures[0].number == "V08"
    with sqlite3.connect(tmp_path / "eltiro/page_cases.sqlite") as db:
        rows = db.execute('SELECT number, first_seen, last_seen, present FROM page_cases ORDER BY number').fetchall()
    assert rows[0][1:3] == ('2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')
    assert rows[1][1:3] == ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
    assert rows[1][3] == 1


def test_changes_use_content_only_and_absence_is_not_repeated(tmp_path):
    record(page() + page("V08"), tmp_path)
    change = record(page("V07") + page("V09"), tmp_path, 2)
    assert change.added == ("V09",)
    assert change.unchanged == ("V07",)
    assert change.disappeared == ("V08",)
    assert record(page() + page("V09"), tmp_path, 3).disappeared == ()
    # Reappearance with identical content is unchanged, even after a disappearance.
    assert record(page("V08"), tmp_path, 4).unchanged == ("V08",)


def test_unknown_failed_identity_cannot_mark_old_cases_missing(tmp_path):
    record(page() + page("V08"), tmp_path)
    result = record(page() + page().replace('V07', '???'), tmp_path, 2)
    assert result.disappeared == ()
    assert result.failures[0].number is None


def test_invalid_page_and_naive_time_cannot_touch_state(tmp_path):
    with pytest.raises(ValueError):
        record_cases(parse(page()), data_dir=tmp_path, seen_at=datetime(2026, 1, 1))
    assert list(tmp_path.iterdir()) == []


def test_database_symlink_is_refused(tmp_path):
    (tmp_path / 'eltiro').mkdir()
    target = tmp_path / 'keep'
    target.write_text('untouched')
    (tmp_path / 'eltiro/page_cases.sqlite').symlink_to(target)
    with pytest.raises(OSError):
        record(page(), tmp_path)
    assert target.read_text() == 'untouched'


def test_ambiguous_number_preserves_all_unmatched_records(tmp_path):
    record(page() + page('V08'), tmp_path)
    ambiguous = page().replace('<span class="no">V07</span>', '<span class="no">V07</span><span class="no">V08</span>')
    result = record(ambiguous, tmp_path, 2)
    assert not result.disappeared
    assert result.failures[0].number is None
