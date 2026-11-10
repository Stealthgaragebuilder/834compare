"""Tests for parser834: ISA/delimiter handling and subscriber grouping."""

from __future__ import annotations

from pathlib import Path

import pytest

import parser834
from tests.edi_builder import (build_file, coverage, dependent, isa_segment,
                               subscriber)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


# --------------------------------------------------------------------------- real samples


def test_parse_sample_a_subscribers():
    r = parser834.parse_file(SAMPLES / "sample_a.834")
    assert r.subscriber_count == 5
    keys = [s.key for s in r.subscribers]
    assert keys == ["EMP00123", "EMP00124", "EMP00125",
                    "Okafor|Ada|042640685", "EMP00127"]


def test_parse_sample_a_ref17_key_source():
    r = parser834.parse_file(SAMPLES / "sample_a.834")
    assert r.subscribers[0].key_source == "REF*17"
    assert r.subscribers[3].key_source == "NM1*IL"  # the one without REF*17


def test_parse_sample_a_dependent_keys():
    r = parser834.parse_file(SAMPLES / "sample_a.834")
    smith = r.subscribers[0]
    assert smith.dependent_count == 1
    assert smith.dependents[0].key == "Smith|Erin|042640681"
    # Vargas's dependent has no member id -> name-only key
    vargas = r.subscribers[4]
    assert vargas.dependents[0].key == "Vargas|Sofia|"


def test_parse_sample_a_coverage_present():
    r = parser834.parse_file(SAMPLES / "sample_a.834")
    for s in r.subscribers:
        assert len(s.coverage) >= 3  # HD + DTP + REF


# --------------------------------------------------------------------------- synthetic


def test_ref17_key():
    data = build_file(subscriber("E1", "Last", "First", "000000001") + coverage("PLAN1"))
    r = parser834.parse_bytes(data)
    assert r.subscribers[0].key == "E1"
    assert r.subscribers[0].key_source == "REF*17"


def test_fallback_to_nm1_when_no_ref17():
    data = build_file(subscriber(None, "Last", "First", "000000002") + coverage("PLAN1"))
    r = parser834.parse_bytes(data)
    assert r.subscribers[0].key_source == "NM1*IL"
    assert r.subscribers[0].key == "Last|First|000000002"


def test_empty_elements_preserved():
    # NM1*IL*1*Last*First****34*ID has two empty elements (middle, suffix).
    data = build_file(subscriber("E1", "Last", "First", "000000001") + coverage("P"))
    r = parser834.parse_bytes(data)
    nm1 = next(s for s in r.subscribers[0].segments if s.id == "NM1")
    assert nm1.elements[5] == ""  # middle name empty
    assert nm1.elements[6] == ""  # suffix empty
    assert nm1.elements[8] == "34"
    assert nm1.elements[9] == "000000001"


def test_dependent_without_member_id():
    data = build_file(subscriber("E1", "Last", "First", "000000001")
                      + dependent("19", "Kid", "Sam", None, "20200304")
                      + coverage("P"))
    r = parser834.parse_bytes(data)
    dep = r.subscribers[0].dependents[0]
    assert dep.key == "Kid|Sam|"


def test_multiple_st_sets_collected():
    # Two ST sets, disjoint people.
    body = (subscriber("E1", "A", "B", "000000001") + coverage("P")
            + ["SE*9*0001", "ST*834*0002"]
            + subscriber("E2", "C", "D", "000000002") + coverage("P"))
    data = build_file(body)
    r = parser834.parse_bytes(data)
    assert {s.key for s in r.subscribers} == {"E1", "E2"}


def test_pipe_delimiters():
    data = build_file(subscriber("E1", "Last", "First", "000000001") + coverage("P"),
                      element="|", terminator="~")
    r = parser834.parse_bytes(data)
    assert r.subscribers[0].key == "E1"


def test_newline_only_terminator():
    # ISA terminator is a newline: no '~' at all in the body.
    isa = isa_segment("*", ":", "\n", "000000004")
    body = ["GS*BE*S*R*20261109*1200*4*X*005010X220A1",
            "ST*834*0004",
            "INS*Y*18*021*30*A***AE***20260101",
            "REF*17*E1",
            "NM1*IL*1*Last*First****34*000000001",
            "HD*021**HLT*P*EMP",
            "SE*9*0004", "GE*1*4", "IEA*1*000000004"]
    text = isa + "\n" + "\n".join(body) + "\n"
    r = parser834.parse_bytes(text.encode("latin-1"))
    assert r.subscribers[0].key == "E1"


def test_bad_file_raises():
    with pytest.raises(parser834.ParseError):
        parser834.parse_bytes(b"GS*BE*X*Y*20261109*1200*1*X*005010X220A1~\n")


def test_too_short_raises():
    with pytest.raises(parser834.ParseError):
        parser834.parse_bytes(b"ISA*00*")


def test_header_fields_captured():
    data = build_file(subscriber("E1", "Last", "First", "000000001") + coverage("P"),
                      control="000000009")
    r = parser834.parse_bytes(data)
    d = r.header.as_dict()
    assert d["ISA control"] == "000000009"
    assert d["ST count"] == "1"
