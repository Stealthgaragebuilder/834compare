"""Tests for the compare engine against the confirmed real-world rules."""

from __future__ import annotations

from pathlib import Path

import compare
import parser834
from tests.edi_builder import (build_file, coverage, dependent, subscriber)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _compare(a_segs: list[str], b_segs: list[str]):
    a = parser834.parse_bytes(build_file(a_segs))
    b = parser834.parse_bytes(build_file(b_segs))
    return compare.compare_results(a, b)


def _by_key(result):
    return {s.key: s for s in result.subscribers}


# --------------------------------------------------------------------------- sample pair


def test_sample_pair_statuses():
    r = compare.compare_files(SAMPLES / "sample_a.834", SAMPLES / "sample_b.834")
    m = _by_key(r)
    assert m["EMP00123"].status == "Changed"
    assert m["EMP00124"].status == "Changed"
    assert m["EMP00125"].status == "Removed"
    assert m["Okafor|Ada|042640685"].status == "Unchanged"
    assert m["EMP00127"].status == "Unchanged"
    assert m["EMP00126"].status == "Added"


def test_sample_pair_plan_change_detected():
    r = compare.compare_files(SAMPLES / "sample_a.834", SAMPLES / "sample_b.834")
    m = _by_key(r)
    diffs = m["EMP00123"].diffs
    assert any(d.kind == "changed" and "PLAN1" in d.detail and "PLAN2" in d.detail
               for d in diffs)


def test_sample_pair_new_dependent_detected():
    r = compare.compare_files(SAMPLES / "sample_a.834", SAMPLES / "sample_b.834")
    m = _by_key(r)
    diffs = m["EMP00124"].diffs
    assert any(d.kind == "added" and "Mateo" in d.context for d in diffs)


def test_sample_pair_header_diffs():
    r = compare.compare_files(SAMPLES / "sample_a.834", SAMPLES / "sample_b.834")
    labels = {h.label for h in r.header_diffs}
    assert "GS control" in labels
    assert "ST controls" in labels


def test_segment_reorder_is_not_a_diff():
    # B reorders REF*17 after NM1; content is identical -> Unchanged.
    a = subscriber("E1", "Last", "First", "000000001", ref_first=True) + coverage("P")
    b = subscriber("E1", "Last", "First", "000000001", ref_first=False) + coverage("P")
    r = _compare(a, b)
    assert r.subscribers[0].status == "Unchanged"
    assert r.subscribers[0].diffs == []


def test_identical_files():
    segs = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    r = _compare(segs, list(segs))
    assert r.is_identical
    assert r.subscribers[0].status == "Unchanged"


def test_value_change_in_subscriber_segment():
    a = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    b = subscriber("E1", "Last", "First", "000000009") + coverage("P")  # member id
    r = _compare(a, b)
    assert r.subscribers[0].status == "Changed"
    assert any(d.context == "Subscriber" for d in r.subscribers[0].diffs)


def test_removed_subscriber():
    a = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    b = subscriber("E2", "Other", "Guy", "000000002") + coverage("P")
    r = _compare(a, b)
    m = _by_key(r)
    assert m["E1"].status == "Removed"
    assert m["E2"].status == "Added"


def test_dependent_changed_dob():
    a = subscriber("E1", "Last", "First", "000000001") \
        + dependent("19", "Kid", "Sam", "000000002", "20200304") + coverage("P")
    b = subscriber("E1", "Last", "First", "000000001") \
        + dependent("19", "Kid", "Sam", "000000002", "20200305") + coverage("P")
    r = _compare(a, b)
    assert r.subscribers[0].status == "Changed"
    assert any("20200304" in d.detail and "20200305" in d.detail
               for d in r.subscribers[0].diffs)


def test_dependent_added():
    a = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    b = subscriber("E1", "Last", "First", "000000001") \
        + dependent("19", "Kid", "Sam", "000000002", "20200304") + coverage("P")
    r = _compare(a, b)
    assert r.subscribers[0].status == "Changed"
    assert any(d.kind == "added" and "Kid" in d.context
               for d in r.subscribers[0].diffs)


def test_coverage_term_date_added():
    a = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    b = subscriber("E1", "Last", "First", "000000001") + coverage("P", term="20261231")
    r = _compare(a, b)
    assert r.subscribers[0].status == "Changed"
    assert any(d.context == "Coverage" and d.kind == "added"
               for d in r.subscribers[0].diffs)


def test_rows_populated_for_show_all():
    segs = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    r = _compare(segs, list(segs))
    sub = r.subscribers[0]
    assert len(sub.rows_a) > 0
    assert len(sub.rows_b) > 0
    # rows are (context, Segment) tuples
    ctx, seg = sub.rows_a[0]
    assert ctx == "Subscriber"
    assert seg.id == "INS"


def test_multi_st_overlap_not_collapsed():
    # The same person (E1) appears in two ST sets. Comparing a file to itself
    # must keep BOTH occurrences (both Unchanged), not collapse to one.
    body = (subscriber("E1", "A", "B", "000000001") + coverage("P")
            + ["SE*6*0001", "ST*834*0002"]
            + subscriber("E1", "A", "B", "000000001") + coverage("P"))
    r = _compare(body, list(body))
    e1 = [s for s in r.subscribers if s.key == "E1"]
    assert len(e1) == 2
    assert all(s.status == "Unchanged" for s in e1)
