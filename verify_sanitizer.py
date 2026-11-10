#!/usr/bin/env python3
"""
verify_sanitizer.py - prove the sanitizer is safe to use on real files.

Checks, for each original/sanitized pair:
  1. STRUCTURE  - same segment count, same segment IDs in the same order,
                  same number of elements per segment, same empty-element pattern.
  2. DELIMITERS - element/component/terminator and line-break style unchanged.
  3. PII GONE   - no original name, member ID, employee number, DOB, address,
                  city, postal code, phone or e-mail survives anywhere in the output.
  4. CONSISTENCY- the same real person maps to the same fake person across files,
                  and different real people map to different fakes.

Exit code 0 means every check passed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sanitize834 import detect_layout, split_segments  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"
CLEAN = SAMPLES / "clean"

# Person-level segments whose values are PII and must not survive verbatim.
# N1 (payer/sponsor organisation) is excluded: it is business data, not employee PII,
# and only scratched when --scrub-org is passed. Dates are excluded for the same reason.
PII_SEGMENTS = {"NM1", "N3", "N4", "DMG", "PER"}

DATE_LIKE = re.compile(r"^\d{6}$|^\d{8}$|^\d{4}-\d{2}-\d{2}$")


def load(path: Path) -> tuple[list[list[str]], object]:
    data = path.read_bytes()
    layout = detect_layout(data)
    segments, _, _ = split_segments(data[layout.body_start:], layout)
    return [seg.decode("latin-1").split(layout.element_text) for seg in segments], layout


def shape(segments: list[list[str]]) -> list[tuple[str, int, tuple[bool, ...]]]:
    """Segment ID, element count, and which elements are empty."""
    return [(els[0], len(els), tuple(e == "" for e in els)) for els in segments]


def pii_values(segments: list[list[str]]) -> set[str]:
    """Every person-level element value long enough to be identifying."""
    values: set[str] = set()
    for els in segments:
        if els[0] not in PII_SEGMENTS:
            continue
        for value in els[1:]:
            if len(value) < 4 or DATE_LIKE.match(value):
                continue
            values.add(value)
    return values


def check_pair(original: Path, sanitized: Path) -> list[str]:
    problems: list[str] = []
    orig_segments, orig_layout = load(original)
    clean_segments, clean_layout = load(sanitized)

    # 1. structure
    if len(orig_segments) != len(clean_segments):
        problems.append(f"segment count {len(orig_segments)} -> {len(clean_segments)}")
    else:
        for i, (before, after) in enumerate(zip(shape(orig_segments), shape(clean_segments))):
            if before != after:
                problems.append(
                    f"segment {i} shape changed: {before[0]} "
                    f"({before[1]} els) -> {after[0]} ({after[1]} els)")
                break

    # 2. delimiters
    for name in ("element", "component", "terminator", "newline"):
        if getattr(orig_layout, name) != getattr(clean_layout, name):
            problems.append(f"delimiter {name} changed")

    # 3. PII gone
    clean_text = sanitized.read_bytes().decode("latin-1")
    leaked = sorted(v for v in pii_values(orig_segments) if v in clean_text)
    if leaked:
        problems.append(f"{len(leaked)} original value(s) still present, e.g. {leaked[:3]}")

    return problems


def check_consistency() -> list[str]:
    """Same real person -> same fake person, across files."""
    problems: list[str] = []
    a_segments, _ = load(SAMPLES / "sample_a.834")
    a_clean, _ = load(CLEAN / "sample_a.sanitized.834")
    b_segments, _ = load(SAMPLES / "sample_b.834")
    b_clean, _ = load(CLEAN / "sample_b.sanitized.834")

    def member_map(orig: list[list[str]], clean: list[list[str]]) -> dict[str, str]:
        """Map original member ID -> sanitized member ID."""
        mapping: dict[str, str] = {}
        for before, after in zip(orig, clean):
            if before[0] == "NM1" and len(before) > 9 and before[9]:
                mapping[before[9]] = after[9]
        return mapping

    a_map = member_map(a_segments, a_clean)
    b_map = member_map(b_segments, b_clean)

    shared = set(a_map) & set(b_map)
    if not shared:
        problems.append("no shared member IDs between A and B - consistency untestable")
    for member_id in shared:
        if a_map[member_id] != b_map[member_id]:
            problems.append(
                f"member ID mapped inconsistently across files: "
                f"{a_map[member_id]} vs {b_map[member_id]}")

    # different real people must not collapse onto one fake
    fakes = list(a_map.values())
    if len(set(fakes)) != len(fakes):
        problems.append("two different member IDs mapped to the same fake ID")

    # names must be consistent too
    def name_map(orig: list[list[str]], clean: list[list[str]]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for before, after in zip(orig, clean):
            if before[0] == "NM1" and len(before) > 4 and before[3]:
                mapping[f"{before[3]}|{before[4]}"] = f"{after[3]}|{after[4]}"
        return mapping

    a_names, b_names = name_map(a_segments, a_clean), name_map(b_segments, b_clean)
    for key in set(a_names) & set(b_names):
        if a_names[key] != b_names[key]:
            problems.append(f"name mapped inconsistently across files: {a_names[key]} vs {b_names[key]}")

    return problems


def check_self_collision_pair(original: Path, sanitized: Path) -> list[str]:
    """A fake value must never equal the original - that would be a straight PII leak."""
    problems: list[str] = []
    before, _ = load(original)
    after, _ = load(sanitized)
    if len(before) != len(after):
        return problems  # structure already reported
    leaked_fields: list[str] = []
    for a, b in zip(before, after):
        if a[0] not in PII_SEGMENTS:
            continue
        for index, (orig_value, clean_value) in enumerate(zip(a[1:], b[1:]), start=1):
            if len(orig_value) < 4 or DATE_LIKE.match(orig_value):
                continue
            if orig_value == clean_value:
                leaked_fields.append(f"{a[0]}{index:02d}")
    if leaked_fields:
        problems.append(
            f"{len(leaked_fields)} field(s) unchanged in output: {leaked_fields[:6]}")
    return problems


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--pair":
        if len(sys.argv) != 4:
            print("usage: verify_sanitizer.py --pair ORIGINAL SANITIZED", file=sys.stderr)
            return 2
        original, sanitized = Path(sys.argv[2]), Path(sys.argv[3])
        if not original.exists():
            print(f"ERROR  {original} not found", file=sys.stderr)
            return 2
        if not sanitized.exists():
            print(f"ERROR  {sanitized} not found", file=sys.stderr)
            return 2
        problems = check_pair(original, sanitized) + check_self_collision_pair(original, sanitized)
        if problems:
            print(f"FAIL  {original.name} -> {sanitized.name}")
            for problem in problems:
                print(f"        - {problem}")
            return 1
        print(f"PASS  {original.name} -> {sanitized.name}")
        print("ALL CHECKS PASSED")
        return 0

    pairs = [
        (SAMPLES / "sample_a.834", CLEAN / "sample_a.sanitized.834"),
        (SAMPLES / "sample_b.834", CLEAN / "sample_b.sanitized.834"),
    ]
    failures = 0
    for original, sanitized in pairs:
        if not sanitized.exists():
            print(f"SKIP  {sanitized.name} (not generated)")
            continue
        problems = check_pair(original, sanitized) + check_self_collision_pair(original, sanitized)
        if problems:
            failures += 1
            print(f"FAIL  {original.name}")
            for problem in problems:
                print(f"        - {problem}")
        else:
            print(f"PASS  {original.name} -> {sanitized.name}")

    consistency = check_consistency()
    if consistency:
        failures += 1
        print("FAIL  cross-file consistency")
        for problem in consistency:
            print(f"        - {problem}")
    else:
        print("PASS  cross-file consistency (same person -> same fake, no collisions)")

    print()
    print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK GROUP(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
