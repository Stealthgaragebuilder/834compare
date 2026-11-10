"""
compare.py - compare two parsed 834 files logically.

Strategy
--------
* Header: compare the labelled header fields by label.
* Subscribers: match by subscriber key (REF*17 / NM1*IL fallback).
    - key in both  -> compare in detail (Changed / Unchanged)
    - key only in B -> Added
    - key only in A -> Removed
* Within a matched subscriber, compare three "record blocks":
    1. subscriber-level segments (INS, REF, NM1, PER, N3, N4)
    2. dependents (matched by dependent key)
    3. coverage segments (HD, DTP, REF)

Segments are matched by a stable *match key* (segment id + identifying element),
NOT by position. This means a segment that moved (e.g. REF before vs after NM1)
still lines up, and only genuine value changes are reported. See README for the
exact match-key rules and their edge cases.

The engine is pure and deterministic: same inputs -> same output, no I/O, no
global state. It is O(n) over the segments, so it stays fast on files with
thousands of records.
"""

from __future__ import annotations

from models import (
    CompareResult,
    HeaderDiff,
    ParseResult,
    Segment,
    SegmentDiff,
    Status,
    Subscriber,
    SubscriberDiff,
)


# --------------------------------------------------------------------------- match keys


def _match_key(seg: Segment) -> tuple[str, ...]:
    """Stable identity used to pair a segment with its counterpart in the other file.

    We deliberately do NOT use the full element list: if we did, a changed value
    would look like a removed+added pair instead of a single "changed" segment.
    Instead we key on the segment id plus the element(s) that identify *which*
    segment it is (stable across value changes):

        REF  -> (REF, qualifier)      e.g. REF*17, REF*1L
        DTP  -> (DTP, code)           e.g. DTP*348, DTP*349
        NM1  -> (NM1, name type)      e.g. NM1*IL
        INS  -> (INS, Y/N)
        HD/PER/N3/N4/DMG/other -> (id,)

    EDGE CASE: if two segments in the same block share a match key (e.g. two
    REF*17), they are paired in order of appearance. This is rare and documented.
    """
    e = seg.elements
    sid = seg.id
    if sid in ("REF", "DTP"):
        return (sid, e[1] if len(e) > 1 else "")
    if sid == "NM1":
        return (sid, e[1] if len(e) > 1 else "")
    if sid == "INS":
        return (sid, e[1] if len(e) > 1 else "")
    return (sid,)


def _diff_detail(a_els: tuple[str, ...], b_els: tuple[str, ...]) -> str:
    """Human-readable description of which elements changed between two segments."""
    if len(a_els) != len(b_els):
        return f"element count {len(a_els)} -> {len(b_els)}"
    changed = [i for i in range(len(a_els)) if a_els[i] != b_els[i]]
    if not changed:
        return ""
    parts = [f"el{i} '{a_els[i]}' -> '{b_els[i]}'" for i in changed]
    return "; ".join(parts[:6]) + (f" (+{len(changed) - 6} more)" if len(changed) > 6 else "")


def _compare_segment_lists(
    a_segs: list[Segment],
    b_segs: list[Segment],
    context: str,
) -> list[SegmentDiff]:
    """Match two segment lists by match key and report changes/adds/removes."""
    a_by_key: dict[tuple[str, ...], list[Segment]] = {}
    for s in a_segs:
        a_by_key.setdefault(_match_key(s), []).append(s)
    b_by_key: dict[tuple[str, ...], list[Segment]] = {}
    for s in b_segs:
        b_by_key.setdefault(_match_key(s), []).append(s)

    diffs: list[SegmentDiff] = []
    all_keys = list(a_by_key.keys())
    for k in b_by_key:
        if k not in a_by_key:
            all_keys.append(k)

    for key in all_keys:
        a_list = a_by_key.get(key, [])
        b_list = b_by_key.get(key, [])
        n = min(len(a_list), len(b_list))
        for i in range(n):
            a, b = a_list[i], b_list[i]
            if a.elements != b.elements:
                diffs.append(SegmentDiff(
                    kind="changed", context=context,
                    seg_a=a.raw, seg_b=b.raw,
                    detail=_diff_detail(a.elements, b.elements)))
        for a in a_list[n:]:
            diffs.append(SegmentDiff(
                kind="removed", context=context, seg_a=a.raw, seg_b=None))
        for b in b_list[n:]:
            diffs.append(SegmentDiff(
                kind="added", context=context, seg_a=None, seg_b=b.raw))
    return diffs


# --------------------------------------------------------------------------- flatten


def _flatten(sub: Subscriber) -> list[tuple[str, Segment]]:
    """Return (context, segment) rows for a subscriber in logical order.

    Order: subscriber-level segments, then each dependent block, then coverage.
    """
    rows: list[tuple[str, Segment]] = []
    for seg in sub.segments:
        rows.append(("Subscriber", seg))
    for i, dep in enumerate(sub.dependents, 1):
        for seg in dep.segments:
            rows.append((f"Dependent {i} ({dep.label})", seg))
    for seg in sub.coverage:
        rows.append(("Coverage", seg))
    return rows


# --------------------------------------------------------------------------- subscriber


def _compare_subscriber(a: Subscriber, b: Subscriber) -> SubscriberDiff:
    diffs: list[SegmentDiff] = []

    # 1. subscriber-level segments
    diffs += _compare_segment_lists(a.segments, b.segments, "Subscriber")

    # 2. dependents, matched by dependent key
    a_deps = {d.key: d for d in a.dependents}
    b_deps = {d.key: d for d in b.dependents}
    for key in list(a_deps.keys()) + [k for k in b_deps if k not in a_deps]:
        da, db = a_deps.get(key), b_deps.get(key)
        if da is None:
            diffs.append(SegmentDiff(
                kind="added", context=f"Dependent {db.label}",
                seg_a=None, seg_b=" ".join(s.raw for s in db.segments)))
        elif db is None:
            diffs.append(SegmentDiff(
                kind="removed", context=f"Dependent {da.label}",
                seg_a=" ".join(s.raw for s in da.segments), seg_b=None))
        else:
            diffs += _compare_segment_lists(da.segments, db.segments,
                                            f"Dependent {db.label}")

    # 3. coverage segments
    diffs += _compare_segment_lists(a.coverage, b.coverage, "Coverage")

    status = Status.CHANGED if diffs else Status.UNCHANGED
    return SubscriberDiff(
        key=a.key,
        label=a.label,
        status=status,
        key_source=a.key_source,
        seg_a_count=len(_flatten(a)),
        seg_b_count=len(_flatten(b)),
        diffs=diffs,
        rows_a=_flatten(a),
        rows_b=_flatten(b),
    )


def _one_sided(sub: Subscriber, status: str) -> SubscriberDiff:
    return SubscriberDiff(
        key=sub.key,
        label=sub.label,
        status=status,
        key_source=sub.key_source,
        seg_a_count=len(_flatten(sub)) if status == Status.REMOVED else 0,
        seg_b_count=len(_flatten(sub)) if status == Status.ADDED else 0,
        rows_a=_flatten(sub) if status == Status.REMOVED else [],
        rows_b=_flatten(sub) if status == Status.ADDED else [],
    )


# --------------------------------------------------------------------------- header


def _compare_header(a, b) -> list[HeaderDiff]:
    a_map = a.as_dict()
    b_map = b.as_dict()
    diffs: list[HeaderDiff] = []
    for label in list(a_map.keys()) + [k for k in b_map if k not in a_map]:
        va, vb = a_map.get(label, ""), b_map.get(label, "")
        if va != vb:
            diffs.append(HeaderDiff(label=label, value_a=va, value_b=vb))
    return diffs


# --------------------------------------------------------------------------- public


def _group_by_key(subs: list[Subscriber]) -> "dict[str, list[Subscriber]]":
    """Group subscribers by key, preserving first-seen order of keys and the
    order of subscribers within each key (needed for the multi-ST overlap case
    where the same person appears in more than one transaction set)."""
    out: dict[str, list[Subscriber]] = {}
    for s in subs:
        out.setdefault(s.key, []).append(s)
    return out


def compare_results(a: ParseResult, b: ParseResult) -> CompareResult:
    """Compare two parsed files and return a CompareResult.

    Subscribers are matched by key. If a key occurs more than once (the
    multi-ST overlap case), the occurrences are paired by position within the
    key group rather than collapsed - so no data is silently dropped.
    """
    warnings = list(a.warnings) + list(b.warnings)

    a_groups = _group_by_key(a.subscribers)
    b_groups = _group_by_key(b.subscribers)

    # Stable order: A's key order first, then any keys only in B.
    ordered_keys: list[str] = list(a_groups.keys())
    ordered_keys += [k for k in b_groups if k not in a_groups]

    subscribers: list[SubscriberDiff] = []
    for key in ordered_keys:
        a_list = a_groups.get(key, [])
        b_list = b_groups.get(key, [])
        n = min(len(a_list), len(b_list))
        for i in range(n):
            subscribers.append(_compare_subscriber(a_list[i], b_list[i]))
        for sa in a_list[n:]:
            subscribers.append(_one_sided(sa, Status.REMOVED))
        for sb in b_list[n:]:
            subscribers.append(_one_sided(sb, Status.ADDED))

    return CompareResult(
        header_diffs=_compare_header(a.header, b.header),
        subscribers=subscribers,
        file_a_name=a.file_name,
        file_b_name=b.file_name,
        warnings=warnings,
    )


def compare_files(path_a: str, path_b: str) -> CompareResult:
    """Convenience: parse two files and compare them (used by tests/CLI)."""
    import parser834
    return compare_results(parser834.parse_file(path_a), parser834.parse_file(path_b))
