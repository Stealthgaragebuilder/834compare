"""
parser834.py - parse an 834 EDI file into a structured model.

Responsibilities
----------------
* Read every delimiter from the ISA segment (element, component, terminator,
  line-break style) - nothing about the delimiter set is hard-coded.
* Split the body into segments, preserving raw segment order.
* Walk the segments and group them into:
    - a Header  (envelope + BGN + any pre-INS segments)
    - Subscribers (one per INS*Y), each with its dependents (INS*N) and
      its coverage segments (HD/DTP/REF).

Confirmed real-world rules (from the validated INT5151 samples)
--------------------------------------------------------------
* A subscriber group is: INS*Y, then identifying segments (REF*17, NM1*IL,
  PER, N3, N4 in any order), then zero or more dependent blocks (INS*N + NM1 +
  DMG + ...), then coverage (HD, DTP, REF). HD marks the start of coverage.
* Subscriber key = REF*17 value; fallback = NM1*IL identity (last/first/member
  id). See models.py and the README for the exact fallback order.
* Dependent key = NM1*IL identity.
* Loop 2700 / LX is intentionally NOT modelled (the real data has none).
* Empty elements are preserved (split keeps "" entries).

ASSUMPTIONS / EDGE CASES (also in README)
-----------------------------------------
* Coverage is assumed to follow all dependent blocks; the first HD starts it.
  If a file put coverage before dependents, the trailing DTP/REF would be
  mis-attributed. The validated samples always put HD last.
* A file may contain several ST transaction sets. Subscribers are collected
  across all of them; the comparator matches by key globally.
* A dependent that appears before any INS*Y (malformed) is attached to a
  synthetic "<orphan>" subscriber and a warning is recorded.
* If a subscriber has neither REF*17 nor an NM1*IL, it is given an anonymous
  key and will not match across files (treated as added/removed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from models import (
    Dependent,
    Header,
    HeaderField,
    ParseResult,
    Segment,
    Subscriber,
)


class ParseError(ValueError):
    """Raised when a file cannot be parsed as an 834."""


# --------------------------------------------------------------------------- layout


@dataclass(frozen=True)
class Layout:
    """Delimiter set and body offset for one file (all read from the ISA)."""

    element: bytes
    component: bytes
    terminator: bytes | None
    newline: bytes
    body_start: int

    @property
    def element_text(self) -> str:
        return self.element.decode("latin-1")


def detect_layout(data: bytes) -> Layout:
    """Read every delimiter from the ISA segment (FR5) - nothing is hard-coded."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.startswith(b"ISA"):
        raise ParseError("no ISA segment at the start of the file")
    if len(data) < 106:
        raise ParseError("file is too short to contain a complete ISA segment")

    element = data[3:4]
    isa = data[:106]

    pos = 0
    for _ in range(16):  # 16 element separators sit between ISA and ISA16
        found = isa.find(element, pos)
        if found == -1:
            raise ParseError("malformed ISA segment (fewer than 16 elements)")
        pos = found + 1
    if pos >= len(isa):
        raise ParseError("malformed ISA segment (no component separator)")

    component = isa[pos:pos + 1]
    after = isa[pos + 1:pos + 2]

    if after in (b"\r", b"\n"):
        # No segment terminator character: segments are separated by line breaks alone.
        newline = b"\r\n" if data[105:107] == b"\r\n" else b"\n"
        return Layout(element, component, None, newline, 105 + len(newline))

    return Layout(element, component, after, b"", 106)


def split_segments(body: bytes, layout: Layout) -> list[bytes]:
    """Split the body into trimmed, order-preserving segment bytes."""
    if layout.terminator is None:
        parts = re.split(rb"\r\n|\r|\n", body)
        return [part.strip() for part in parts if part.strip()]

    parts = body.split(layout.terminator)
    return [part.strip() for part in parts if part.strip()]


# --------------------------------------------------------------------------- helpers


def _nm1_identity(els: list[str]) -> tuple[str, str, str]:
    """Return (last, first, id_value) from an NM1*IL element list.

    Element indices (0-based, id is 0):
        1 = NM101 name type (IL)
        2 = NM102 name format code
        3 = NM103 last name
        4 = NM104 first name
        5 = NM105 middle name
        6 = NM106 suffix
        7 = NM107 identifier qualifier
        8 = NM108 (in this data a code, e.g. "34")
        9 = NM109  (the member ID value, e.g. "042640680")
    Per the confirmed rules, dependents are identified by NM1*IL (last/first)
    with a fallback to the identifying value. In the validated samples the
    member ID sits in NM109 (index 9), so we prefer that and fall back to
    NM108 (index 8) if it is the only value present.
    """
    if len(els) < 3 or els[1] != "IL":
        return ("", "", "")
    last = els[3] if len(els) > 3 else ""
    first = els[4] if len(els) > 4 else ""
    id_value = ""
    for idx in (9, 8):
        if len(els) > idx and els[idx]:
            id_value = els[idx]
            break
    return (last, first, id_value)


def _person_key(last: str, first: str, id_value: str) -> str:
    return f"{last}|{first}|{id_value}"


def _name(last: str, first: str) -> str:
    return " ".join(p for p in (last, first) if p) or "(unnamed)"


def _first_nm1_name(segments: list[Segment]) -> str:
    for seg in segments:
        if seg.id == "NM1" and seg.get(1) == "IL":
            last, first, _ = _nm1_identity(list(seg.elements))
            return _name(last, first)
    return ""


def _subscriber_identity(segments: list[Segment]) -> tuple[str, str]:
    """Return (key_source, key) for a subscriber's identifying segments."""
    for seg in segments:
        if seg.id == "REF" and seg.get(1) == "17" and seg.get(2):
            return ("REF*17", seg.get(2))
    for seg in segments:
        if seg.id == "NM1" and seg.get(1) == "IL":
            last, first, id_value = _nm1_identity(list(seg.elements))
            return ("NM1*IL", _person_key(last, first, id_value))
    return ("anonymous", "")


def _label(name: str, key_source: str, key: str, rel: str = "") -> str:
    """Human-readable label for the UI."""
    base = name or "(no name)"
    if key_source == "REF*17":
        return f"{base} [{key}]"
    if rel:
        return f"{base} ({rel}) [{key}]" if key else f"{base} ({rel})"
    return f"{base} [{key}]" if key else base


# --------------------------------------------------------------------------- header


def _build_header(segments: list[Segment]) -> Header:
    """Flatten the envelope + BGN + pre-INS segments into labelled fields."""
    fields: list[HeaderField] = []
    isa = next((s for s in segments if s.id == "ISA"), None)
    gs = next((s for s in segments if s.id == "GS"), None)
    st_controls = [s.get(2) for s in segments if s.id == "ST"]

    if isa is not None:
        fields.append(HeaderField("ISA date", isa.get(9)))
        fields.append(HeaderField("ISA control", isa.get(13)))
    if gs is not None:
        fields.append(HeaderField("GS sender", gs.get(2)))
        fields.append(HeaderField("GS receiver", gs.get(3)))
        fields.append(HeaderField("GS date", gs.get(4)))
        fields.append(HeaderField("GS control", gs.get(6)))
    fields.append(HeaderField("ST count", str(len(st_controls))))
    fields.append(HeaderField("ST controls", ", ".join(st_controls)))

    # Pre-INS, non-envelope segments (BGN, REF, DTP, N1, ...).
    seen_ins = False
    per_id_count: dict[str, int] = {}
    for seg in segments:
        if seg.id == "INS":
            seen_ins = True
            break
        if seg.id in ("ISA", "GS", "ST", "SE", "GE", "IEA"):
            continue
        if seg.id == "BGN":
            fields.append(HeaderField("BGN reference", seg.get(3)))
            fields.append(HeaderField("BGN date", seg.get(4)))
            continue
        n = per_id_count.get(seg.id, 0) + 1
        per_id_count[seg.id] = n
        value = ":".join(seg.elements[1:])
        fields.append(HeaderField(f"{seg.id} ({n})", value))

    return Header(fields=fields)


# --------------------------------------------------------------------------- main parse


def parse_bytes(data: bytes, file_name: str = "") -> ParseResult:
    """Parse raw 834 bytes into a ParseResult. Raises ParseError on bad input."""
    layout = detect_layout(data)
    body = data[layout.body_start:]
    if not body.strip():
        raise ParseError("nothing after the ISA segment")

    raw_segments = split_segments(body, layout)
    if not raw_segments:
        raise ParseError("no segments found after the ISA")

    element = layout.element
    warnings: list[str] = []

    # The ISA segment sits before the body offset; prepend it so the header
    # (and any ISA-level field) is available. Strip the terminator/newline.
    isa_bytes = data[:layout.body_start].strip()

    # Build Segment objects with a stable 1-based ordinal (ISA == 1).
    segments: list[Segment] = []
    ordinal = 0
    for raw in [isa_bytes, *raw_segments]:
        ordinal += 1
        els = [e.decode("latin-1", "replace") for e in raw.split(element)]
        segments.append(Segment(id=els[0], elements=tuple(els),
                                raw=raw.decode("latin-1", "replace"), line=ordinal))

    header = _build_header(segments)
    subscribers: list[Subscriber] = []

    current_sub: Subscriber | None = None
    current_dep: Dependent | None = None
    mode: str | None = None  # 'subscriber' | 'dependent' | 'coverage'
    st_count = 0
    first_ins_seen = False

    for seg in segments:
        sid = seg.id
        if sid == "ST":
            st_count += 1
            continue
        if sid in ("ISA", "GS", "SE", "GE", "IEA"):
            continue

        if sid == "INS":
            first_ins_seen = True
            if seg.get(1) == "Y":
                current_sub = Subscriber(key="", key_source="", label="", st_index=st_count)
                subscribers.append(current_sub)
                current_dep = None
                mode = "subscriber"
                current_sub.segments.append(seg)
            else:
                if current_sub is None:
                    warnings.append(
                        f"dependent (INS*{seg.get(1)}) at segment {seg.line} appears "
                        "before any subscriber; attached to a synthetic group")
                    current_sub = Subscriber(key="<orphan>", key_source="orphan",
                                             label="(orphan dependent)", st_index=st_count)
                    subscribers.append(current_sub)
                    current_sub.segments.append(seg)
                    mode = "subscriber"
                else:
                    current_dep = Dependent(key="", label="", segments=[seg])
                    current_sub.dependents.append(current_dep)
                    mode = "dependent"
            continue

        if not first_ins_seen:
            continue  # already captured into the header

        if current_sub is None:
            continue

        if sid == "HD":
            mode = "coverage"
            current_sub.coverage.append(seg)
        elif mode == "coverage":
            current_sub.coverage.append(seg)
        elif mode == "dependent" and current_dep is not None:
            current_dep.segments.append(seg)
        else:
            current_sub.segments.append(seg)

    _finalize_keys(subscribers)

    return ParseResult(
        header=header,
        subscribers=subscribers,
        file_name=file_name,
        warnings=warnings,
        segment_count=len(segments),
    )


def _finalize_keys(subscribers: list[Subscriber]) -> None:
    """Fill in stable keys and display labels for every subscriber/dependent."""
    for index, sub in enumerate(subscribers, 1):
        key_source, key = _subscriber_identity(sub.segments)
        sub.key_source = key_source
        if not key:
            key = f"<anon-{sub.st_index}-{index}>"
        sub.key = key
        sub.label = _label(_first_nm1_name(sub.segments), key_source, key)

        for dep_index, dep in enumerate(sub.dependents, 1):
            nm1 = next((s for s in dep.segments if s.id == "NM1" and s.get(1) == "IL"), None)
            rel = dep.segments[0].get(2) if dep.segments else ""
            if nm1 is not None:
                last, first, id_value = _nm1_identity(list(nm1.elements))
                dep.key = _person_key(last, first, id_value)
                dep.label = _label(_name(last, first), "NM1*IL", id_value, rel=rel)
            else:
                dep.key = f"<dep-{sub.key}-{dep_index}>"
                dep.label = f"Dependent {dep_index} (no NM1)"


def parse_file(path: str | Path) -> ParseResult:
    """Convenience wrapper: read a file and parse it."""
    path = Path(path)
    data = path.read_bytes()
    return parse_bytes(data, file_name=path.name)
