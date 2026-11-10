"""
models.py - small, immutable data objects shared across the 834 compare tool.

Everything here is deliberately plain and serialisable (dataclasses) so the parser,
comparison engine, UI and Excel exporter can all agree on the same shapes without
importing each other.

Design notes / assumptions
--------------------------
* A "record" is one EDI segment. We keep the raw element list (split on the file's
  element delimiter) so empty elements survive - e.g. ``NM1*IL*1*L*F****34*ID``
  keeps its two empty elements between the name and the ID qualifier.
* A "subscriber" is one INS*Y group: the subscriber itself plus its dependents and
  its coverage segments (HD/DTP/REF).
* A "dependent" is one INS*N block nested under a subscriber.
* Comparison is done on *normalised* element lists, never on raw text, so that
  cosmetic differences (extra trailing empty elements, etc.) do not cause noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- enums


class Side:
    """Which file a value came from. Used to label comparison cells."""

    BASE = "A"
    COMP = "B"

    ALL = (BASE, COMP)


class Status:
    """Comparison outcome for a subscriber (or a record block)."""

    UNCHANGED = "Unchanged"
    CHANGED = "Changed"
    ADDED = "Added"      # present in B, absent in A
    REMOVED = "Removed"  # present in A, absent in B


# --------------------------------------------------------------------------- records


@dataclass(frozen=True)
class Segment:
    """One EDI segment, already split into normalised elements.

    ``elements`` is the list of element values (the segment id is ``elements[0]``).
    Empty elements are preserved as ``""``. ``raw`` is the original text for display.
    """

    id: str
    elements: tuple[str, ...]
    raw: str
    line: int  # 1-based line number in the source file (best effort)

    @property
    def element_count(self) -> int:
        return len(self.elements)

    def get(self, index: int, default: str = "") -> str:
        """Element at ``index`` (0-based, id is 0), or ``default`` if absent."""
        if index < len(self.elements):
            return self.elements[index]
        return default

    def key(self) -> str:
        """Segment id plus a stable identity for matching (see compare.py)."""
        return self.id


# --------------------------------------------------------------------------- members


@dataclass
class Dependent:
    """One dependent member (INS*N block) under a subscriber."""

    key: str                      # stable identity, see parser834.build_dependent_key
    label: str                    # human readable, e.g. "Last First (member 0426...)"
    segments: list[Segment] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.segments


@dataclass
class Subscriber:
    """One subscriber group (INS*Y) with its dependents and coverage segments.

    ``key`` is the primary subscriber key (REF*17 value, or the NM1*IL fallback).
    ``key_source`` records which rule produced it so the UI can explain it.
    """

    key: str
    key_source: str               # "REF*17" | "NM1*IL" | "anonymous"
    label: str
    st_index: int                 # 1-based position of the ST set this came from
    segments: list[Segment] = field(default_factory=list)   # subscriber-level segments
    dependents: list[Dependent] = field(default_factory=list)
    coverage: list[Segment] = field(default_factory=list)   # HD/DTP/REF after members

    @property
    def dependent_count(self) -> int:
        return len(self.dependents)


# --------------------------------------------------------------------------- header


@dataclass
class HeaderField:
    """One labelled value pulled out of the envelope/header for the diff view."""

    label: str
    value: str


@dataclass
class Header:
    """Envelope + BGN header, flattened into labelled fields for easy diffing.

    Only the fields that are meaningful to a human comparing two files are captured.
    Control numbers and dates live here too because they are the most common
    "envelope-only" change between two runs of the same extract.
    """

    fields: list[HeaderField] = field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        return {f.label: f.value for f in self.fields}


# --------------------------------------------------------------------------- results


@dataclass
class SegmentDiff:
    """A difference between two aligned segments (or a missing/extra one)."""

    kind: str                     # "changed" | "added" | "removed"
    context: str                  # e.g. "Dependent 2 (Last First)" or "Coverage"
    seg_a: Optional[str]          # raw text on side A, or None
    seg_b: Optional[str]          # raw text on side B, or None
    detail: str = ""              # which element(s) differed, for the detail pane


@dataclass
class SubscriberDiff:
    """Comparison result for one subscriber key."""

    key: str
    label: str
    status: str                   # one of Status.*
    key_source: str = ""
    seg_a_count: int = 0
    seg_b_count: int = 0
    diffs: list[SegmentDiff] = field(default_factory=list)
    # Full logical segment lists for the "show all segments" view.
    # Each entry is a (context, Segment) row in logical order.
    rows_a: list[tuple[str, Segment]] = field(default_factory=list)
    rows_b: list[tuple[str, Segment]] = field(default_factory=list)

    @property
    def is_changed(self) -> bool:
        return self.status in (Status.CHANGED, Status.ADDED, Status.REMOVED)


@dataclass
class HeaderDiff:
    """A single header field that differs between the two files."""

    label: str
    value_a: str
    value_b: str


@dataclass
class CompareResult:
    """Full output of compare.compare_files()."""

    header_diffs: list[HeaderDiff] = field(default_factory=list)
    subscribers: list[SubscriberDiff] = field(default_factory=list)
    file_a_name: str = ""
    file_b_name: str = ""
    warnings: list[str] = field(default_factory=list)

    # -- convenience ---------------------------------------------------------
    @property
    def changed(self) -> list[SubscriberDiff]:
        return [s for s in self.subscribers if s.is_changed]

    @property
    def added(self) -> list[SubscriberDiff]:
        return [s for s in self.subscribers if s.status == Status.ADDED]

    @property
    def removed(self) -> list[SubscriberDiff]:
        return [s for s in self.subscribers if s.status == Status.REMOVED]

    @property
    def unchanged(self) -> list[SubscriberDiff]:
        return [s for s in self.subscribers if s.status == Status.UNCHANGED]

    @property
    def total_diffs(self) -> int:
        return sum(len(s.diffs) for s in self.subscribers)

    @property
    def is_identical(self) -> bool:
        return not self.header_diffs and not self.changed


@dataclass
class ParseResult:
    """Output of parser834.parse()."""

    header: Header
    subscribers: list[Subscriber]
    file_name: str = ""
    warnings: list[str] = field(default_factory=list)
    segment_count: int = 0

    @property
    def subscriber_count(self) -> int:
        return len(self.subscribers)
