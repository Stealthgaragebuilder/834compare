#!/usr/bin/env python3
"""
sanitize834.py - produce a structurally identical, PII-free copy of an EDI 834 file.

Standalone helper for the 834 Benefit File Compare Tool (INT5151). Not part of the app.

WHAT IT KEEPS (so the copy is still a usable test fixture)
  * segment order and loop nesting
  * EMPTY ELEMENTS - these are what break naive parsers, so they must survive
  * qualifier codes (IL, 17, 34, HLT, D8, ...) - they carry no PII
  * delimiters read from ISA (element, component, terminator) and the line-break style
  * segment counts, control numbers, envelope dates

WHAT IT REPLACES (deterministic: same input value -> same fake value, every time)
  * person names (NM1, PER), member IDs / SSNs, employee numbers, addresses,
    cities, postal codes, birth dates, phone numbers, e-mail addresses, reference IDs

WHY DETERMINISTIC: each fake value is derived from SHA256(salt + original). No mapping
table is written to disk, so nothing sensitive can leak through a side file - yet two
files sanitized with the same salt still share the same fake person, which is exactly
what diff testing needs.

WHAT IT NEVER DOES
  * never prints or logs a field value (reports contain counts only)
  * never modifies the input file
  * never overwrites an existing output unless --force is given

USAGE
  py sanitize834.py FILE --stats-only               # structure report, writes nothing
  py sanitize834.py FILE --out OUT                  # full sanitized copy
  py sanitize834.py FILE --excerpt 3 --out OUT      # ISA/header + 3 subscribers + trailer
  py sanitize834.py A B --outdir DIR                # several files, ONE consistent mapping
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

DEFAULT_SALT = "int5151-sanitizer-v1"

# REF*<qualifier> whose value is an identifier or PII. Trim this set if something
# business-relevant gets scrambled, or add to it if you spot a new identifier.
SCRUB_REF_QUALIFIERS = {
    "17",  # client reporting number / employee number
    "0F",  # subscriber number
    "SY",  # SSN
    "ZZ",  # mutually defined
    "23",  # client number
    "1W",  # member identification number
    "3H",  # case number
    "6P",  # group / person number
    "F6",  # provider id
    "DX",  # department
    "CE",  # class of contract
}

# Name pools. Stable per value, and distinct enough to be obviously synthetic.
PERSON_NAMES = (
    "Anders", "Bennett", "Cavanaugh", "Doyle", "Ellery", "Fischer", "Grant", "Huxley",
    "Ivanov", "Jensen", "Keller", "Lockhart", "Moreau", "Novak", "Okafor", "Patel",
    "Quinn", "Ramos", "Schulz", "Tanaka", "Ueda", "Vargas", "Weber", "Zhang",
)
GIVEN_NAMES = (
    "Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kerry", "Logan", "Marlow", "Noel", "Oakley", "Parker",
    "Quincy", "Riley", "Sage", "Tatum", "Umber", "Vale", "Wren", "Yuki",
)

STATES = ("CA", "NY", "TX", "WA", "IL", "MA", "OR", "CO", "FL", "MN")


# --------------------------------------------------------------------------- helpers


def _stream(salt: str, value: str, size: int) -> bytes:
    """Deterministic pseudorandom bytes for (salt, value)."""
    out = bytearray()
    block = f"{salt}|{value}".encode("utf-8", "replace")
    while len(out) < size:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:size])


def _pick(salt: str, value: str, table: tuple[str, ...]) -> str:
    return table[_stream(salt, "pick|" + value, 1)[0] % len(table)]


def scramble(value: str, salt: str) -> str:
    """Replace characters with same-class fakes. Length and shape are preserved.

    A leading '0' stays '0' so leading-zero handling bugs in the parser stay visible.
    Retries deterministically if the result would equal the original, so a short value
    cannot pass through unchanged.
    """
    if not value:
        return value
    for attempt in range(8):
        stream = _stream(salt, f"{attempt}|" + value if attempt else value, len(value))
        chars = []
        for i, ch in enumerate(value):
            if ch.isdigit():
                chars.append("0" if (i == 0 and ch == "0") else str(stream[i] % 10))
            elif ch.isalpha():
                letter = chr(ord("A") + stream[i] % 26)
                chars.append(letter.lower() if ch.islower() else letter)
            else:
                chars.append(ch)
        result = "".join(chars)
        if result != value:
            return result
    return result


def fake_name(value: str, salt: str, table: tuple[str, ...]) -> str:
    """Pick a fake name that is guaranteed to differ from the original.

    Without the difference guard, a real name that happens to appear in the pool
    could map to itself - a genuine PII leak. Retry deterministically on collision.
    """
    if not value:
        return value
    for attempt in range(8):
        start = _stream(salt, f"name{attempt}|" + value, 1)[0]
        for offset in range(len(table)):
            fake = table[(start + offset) % len(table)]
            if fake.lower() != value.lower():
                break
        else:  # pragma: no cover - only if the pool has a single entry
            fake = value
            break
        if value.isupper() and len(value) > 1:
            fake = fake.upper()
        elif value.islower():
            fake = fake.lower()
        if fake.lower() != value.lower():
            return fake
    return fake


def fake_date(value: str, salt: str) -> str:
    """Map a date (YYYYMMDD or YYMMDD) to a plausible fake date of the same format."""
    if not re.fullmatch(r"\d{6}|\d{8}", value):
        return value
    days = int.from_bytes(_stream(salt, "date|" + value, 2), "big") % (365 * 70)
    fake = date(1945, 1, 1) + timedelta(days=days)
    return fake.strftime("%Y%m%d") if len(value) == 8 else fake.strftime("%y%m%d")


def fake_email(value: str, salt: str) -> str:
    if not value:
        return value
    n = int.from_bytes(_stream(salt, "mail|" + value, 2), "big") % 10000
    return f"user{n:04d}@example.com"


# ------------------------------------------------------------------------ delimiters


class Layout:
    """Delimiter set and body offset for one file."""

    def __init__(self, element: bytes, component: bytes,
                 terminator: bytes | None, newline: bytes, body_start: int) -> None:
        self.element = element
        self.component = component
        self.terminator = terminator
        self.newline = newline
        self.body_start = body_start

    @property
    def element_text(self) -> str:
        return self.element.decode("latin-1")


def detect_layout(data: bytes) -> Layout:
    """Read every delimiter from the ISA segment (FR5) - nothing is hard-coded."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.startswith(b"ISA"):
        raise ValueError("no ISA segment at the start of the file")
    if len(data) < 106:
        raise ValueError("file is too short to contain a complete ISA segment")

    element = data[3:4]
    isa = data[:106]

    pos = 0
    for _ in range(16):  # 16 element separators sit between ISA and ISA16
        found = isa.find(element, pos)
        if found == -1:
            raise ValueError("malformed ISA segment (fewer than 16 elements)")
        pos = found + 1
    if pos >= len(isa):
        raise ValueError("malformed ISA segment (no component separator)")

    component = isa[pos:pos + 1]
    after = isa[pos + 1:pos + 2]

    if after in (b"\r", b"\n"):
        # No segment terminator character: segments are separated by line breaks alone.
        newline = b"\r\n" if data[105:107] == b"\r\n" else b"\n"
        return Layout(element, component, None, newline, 105 + len(newline))

    return Layout(element, component, after, b"", 106)


def split_segments(body: bytes, layout: Layout) -> tuple[list[bytes], bool, bytes]:
    """Split the body into trimmed segments. Returns (segments, ended, newline)."""
    if layout.terminator is None:
        parts = re.split(rb"\r\n|\r|\n", body)
        ended = bool(re.search(rb"(?:\r\n|\r|\n)\s*$", body))
        segments = [part.strip() for part in parts if part.strip()]
        return segments, ended, layout.newline

    newline = layout.newline
    if not newline:
        total = max(body.count(layout.terminator), 1)
        for candidate in (b"\r\n", b"\n"):
            if body.count(layout.terminator + candidate) >= 0.8 * total:
                newline = candidate
                break

    parts = body.split(layout.terminator)
    ended = bool(parts) and parts[-1].strip() == b""
    segments = [part.strip() for part in parts if part.strip()]
    return segments, ended, newline


def join_segments(segments: list[bytes], layout: Layout, newline: bytes,
                  ended: bool) -> bytes:
    if layout.terminator is None:
        body = newline.join(segments)
        return body + newline if ended else body
    body = (layout.terminator + newline).join(segments)
    return body + layout.terminator + newline if ended else body


# ------------------------------------------------------------------------- scrubbing


class Scrubber:
    """Applies deterministic replacements and counts them by category."""

    def __init__(self, salt: str, element: str, scrub_org: bool,
                 scrub_envelope_ids: bool) -> None:
        self.salt = salt
        self.element = element
        self.scrub_org = scrub_org
        self.scrub_envelope_ids = scrub_envelope_ids
        self.counts: Counter[str] = Counter()

    def _bump(self, kind: str, n: int = 1) -> None:
        self.counts[kind] += n

    def scrub_segment(self, raw: bytes) -> bytes:
        text = raw.decode("latin-1")
        els = text.split(self.element)
        handler = getattr(self, f"_do_{els[0]}", None)
        if handler is not None:
            handler(els)
        return self.element.join(els).encode("latin-1")

    # -- envelope ------------------------------------------------------------

    def _do_ISA(self, els: list[str]) -> None:
        if not self.scrub_envelope_ids:
            return
        for i in (5, 6, 7, 8):  # ISA05/06 sender, ISA07/08 receiver
            if i < len(els) and els[i]:
                els[i] = scramble(els[i], self.salt)
                self._bump("envelope IDs")

    def _do_GS(self, els: list[str]) -> None:
        if not self.scrub_envelope_ids:
            return
        for i in (2, 3):  # GS02 sender application, GS03 receiver application
            if i < len(els) and els[i]:
                els[i] = scramble(els[i], self.salt)
                self._bump("envelope IDs")

    def _do_BGN(self, els: list[str]) -> None:
        if len(els) > 2 and els[2]:
            els[2] = scramble(els[2], self.salt)
            self._bump("reference IDs")

    # -- people --------------------------------------------------------------

    def _do_NM1(self, els: list[str]) -> None:
        is_person = len(els) > 2 and els[2] == "1"
        if is_person:
            if len(els) > 3 and els[3]:
                els[3] = fake_name(els[3], self.salt, PERSON_NAMES)
                self._bump("last names")
            if len(els) > 4 and els[4]:
                els[4] = fake_name(els[4], self.salt, GIVEN_NAMES)
                self._bump("first names")
            if len(els) > 5 and els[5]:
                els[5] = fake_name(els[5], self.salt, GIVEN_NAMES)
            if len(els) > 7 and els[7]:
                els[7] = scramble(els[7], self.salt)

        # NM108 = ID qualifier, NM109 = ID value. For persons this is the member
        # ID / SSN and is always scrubbed; for organisations only on request.
        if len(els) > 9 and els[9]:
            if is_person:
                els[9] = scramble(els[9], self.salt)
                self._bump("member IDs")
            elif self.scrub_org:
                els[9] = scramble(els[9], self.salt)
                self._bump("org IDs")

    def _do_N1(self, els: list[str]) -> None:
        if not self.scrub_org:
            return
        if len(els) > 2 and els[2]:
            els[2] = scramble(els[2], self.salt)
            self._bump("org names")
        if len(els) > 4 and els[4]:
            els[4] = scramble(els[4], self.salt)
            self._bump("org IDs")

    def _do_REF(self, els: list[str]) -> None:
        if len(els) > 2 and els[1] in SCRUB_REF_QUALIFIERS and els[2]:
            els[2] = scramble(els[2], self.salt)
            self._bump("reference IDs")

    def _do_PER(self, els: list[str]) -> None:
        if len(els) > 2 and els[2]:
            els[2] = fake_name(els[2], self.salt, PERSON_NAMES)
            self._bump("contact names")
        for i in range(3, len(els) - 1, 2):
            qualifier, value = els[i], els[i + 1]
            if not value:
                continue
            if qualifier == "EM" or "@" in value:
                els[i + 1] = fake_email(value, self.salt)
                self._bump("e-mail addresses")
            else:
                els[i + 1] = scramble(value, self.salt)
                self._bump("phone numbers")

    def _do_N3(self, els: list[str]) -> None:
        for i in (1, 2):  # N301 street, N302 additional street
            if i < len(els) and els[i]:
                els[i] = scramble(els[i], self.salt)
                self._bump("street addresses")

    def _do_N4(self, els: list[str]) -> None:
        if len(els) > 1 and els[1]:  # N401 city
            els[1] = scramble(els[1], self.salt)
            self._bump("cities")
        if len(els) > 2 and els[2]:  # N402 state: replaced from a fixed list
            els[2] = _pick(self.salt, "state|" + els[2], STATES)
        if len(els) > 3 and els[3]:  # N403 postal code
            els[3] = scramble(els[3], self.salt)
            self._bump("postal codes")

    def _do_DMG(self, els: list[str]) -> None:
        if len(els) > 2 and els[2]:  # DMG02 date of birth
            els[2] = fake_date(els[2], self.salt)
            self._bump("birth dates")


def transaction_breakdown(segments: list[bytes], element: bytes) -> list[str]:
    """Per-ST-transaction-set detail, plus member-level segment presence.

    Answers a different question from the totals: with more than one ST in a file,
    are the transaction sets disjoint populations, or is one a subset of the other?
    Also reports how many members are missing segments we intend to rely on.
    """
    sets: list[dict] = []
    current: dict | None = None
    member_element_counts: Counter[int] = Counter()
    member_has: dict[str, int] = Counter()
    member_total = 0

    presence_segments = (b"DMG", b"PER", b"N3", b"N4", b"NM1")

    for seg in segments:
        els = seg.split(element)
        sid = els[0]
        if sid == b"ST":
            current = {"subscribers": 0, "dependents": 0, "segments": 0, "keys": set()}
            sets.append(current)
            continue
        if current is None:
            continue
        current["segments"] += 1
        if sid == b"INS":
            member_total += 1
            member_element_counts[len(els)] += 1
            if len(els) > 1 and els[1] == b"Y":
                current["subscribers"] += 1
                # subscriber identity: REF*17 (employee number) if present, else NM1*IL
                current["keys"].add(("REF17", b""))
            else:
                current["dependents"] += 1
        elif sid == b"REF" and len(els) > 2 and els[1] == b"17" and current["keys"]:
            current["keys"].discard(("REF17", b""))
            current["keys"].add(("REF17", els[2]))
        elif sid in presence_segments:
            member_has[sid.decode()] += 1

    lines = []
    if len(sets) > 1:
        lines.append("Transaction sets:")
        seen = [info["keys"] for info in sets]
        for index, info in enumerate(sets, start=1):
            lines.append(
                f"  ST #{index}: {info['segments']:,} segments, "
                f"{info['subscribers']:,} subscribers, {info['dependents']:,} dependents")

        for i in range(len(seen)):
            for j in range(i + 1, len(seen)):
                shared = {key for key in seen[i] & seen[j] if key[1]}
                if shared:
                    lines.append(
                        f"  !! ST #{i + 1} and ST #{j + 1} share {len(shared):,} "
                        f"subscriber key(s) - the same people appear in both sets")
                else:
                    lines.append(
                        f"  ST #{i + 1} and ST #{j + 1}: no shared subscriber keys")

    if member_total:
        counts_text = ", ".join(
            f"{n} elements={c:,}" for n, c in sorted(member_element_counts.items()))
        lines.append(f"INS element counts      : {counts_text}")
        missing = []
        for name in ("NM1", "DMG", "PER", "N3", "N4"):
            present = member_has.get(name, 0)
            if present != member_total:
                missing.append(f"{name} missing in {member_total - present:,}")
        if missing:
            lines.append("Members without         : " + "; ".join(missing))
        else:
            lines.append("Members without         : none "
                         "(every member has NM1, DMG, PER, N3, N4)")

    return lines


# --------------------------------------------------------------------------- report


def build_report(segments: list[bytes], layout: Layout, path: Path, size: int) -> str:
    """Structural report. Counts only - never a field value."""
    element = layout.element
    ids = [seg.split(element)[0].decode("latin-1", "replace") for seg in segments]
    counts = Counter(ids)

    subscribers = dependents = 0
    no_ref17 = 0
    ref17_values: list[bytes] = []
    in_subscriber = False
    member_has_ref17 = False
    first_ins: int | None = None

    for index, seg in enumerate(segments):
        els = seg.split(element)
        name = els[0]
        if name == b"INS":
            if first_ins is None:
                first_ins = index
            if in_subscriber and not member_has_ref17:
                no_ref17 += 1
            in_subscriber = len(els) > 1 and els[1] == b"Y"
            member_has_ref17 = False
            if in_subscriber:
                subscribers += 1
            else:
                dependents += 1
        elif name == b"REF" and len(els) > 1 and els[1] == b"17":
            member_has_ref17 = True
            if len(els) > 2 and els[2]:
                ref17_values.append(els[2])
    if in_subscriber and not member_has_ref17:
        no_ref17 += 1

    duplicates = sum(1 for _, n in Counter(ref17_values).items() if n > 1)

    if layout.terminator is None:
        term_text = "newline only (" + repr(layout.newline.decode("latin-1")) + ")"
    else:
        term_text = repr(layout.terminator.decode("latin-1"))
        if layout.newline:
            term_text += " + " + repr(layout.newline.decode("latin-1"))

    lines = [
        f"File                    : {path.name}  ({size:,} bytes)",
        f"Element / component     : {element.decode('latin-1')!r} / "
        f"{layout.component.decode('latin-1')!r}",
        f"Segment terminator      : {term_text}",
        f"Segments                : {len(segments):,}",
        f"Functional groups (GS)  : {counts.get('GS', 0):,}",
        f"Transaction sets (ST)   : {counts.get('ST', 0):,}",
        f"Subscribers (INS*Y)     : {subscribers:,}",
        f"Dependents  (INS*N)     : {dependents:,}",
        f"Loop 2700 (LX segments) : {counts.get('LX', 0):,}   <-- question A",
        f"REF*17 present          : {len(ref17_values):,}"
        f"   subscribers without REF*17: {no_ref17:,}",
        f"Duplicate REF*17 values : {duplicates:,}   <-- question D",
        f"Header segs before INS  : {first_ins if first_ins is not None else len(segments):,}",
        "Segment IDs (top 12)    : " + ", ".join(f"{k}={v}" for k, v in counts.most_common(12)),
    ]
    lines.extend(transaction_breakdown(segments, element))
    return "\n".join(lines)


# -------------------------------------------------------------------------- excerpt


def make_excerpt(segments: list[bytes], element: bytes, count: int) -> list[bytes]:
    """Keep the header, the first `count` subscriber groups, and the envelope trailer."""
    starts = []
    for i, seg in enumerate(segments):
        els = seg.split(element)
        if els[0] == b"INS" and len(els) > 1 and els[1] == b"Y":
            starts.append(i)
    if not starts or count >= len(starts):
        return segments
    end = starts[count]
    trailer = [seg for seg in segments[end:]
               if seg.split(element)[0] in (b"SE", b"GE", b"IEA")]
    return segments[:end] + trailer


# ----------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Make a PII-free, structurally identical copy of an EDI 834 file.")
    parser.add_argument("files", nargs="+", type=Path, help="input 834 file(s)")
    parser.add_argument("--out", type=Path, help="output path (single input only)")
    parser.add_argument("--outdir", type=Path,
                        help="output directory for one or more inputs")
    parser.add_argument("--excerpt", type=int, default=0, metavar="N",
                        help="keep only the header plus the first N subscriber groups")
    parser.add_argument("--stats-only", action="store_true",
                        help="print the structural report and write nothing")
    parser.add_argument("--scrub-org", action="store_true",
                        help="also scramble organisation names and IDs (N1, NM1 type 2)")
    parser.add_argument("--scrub-envelope-ids", action="store_true",
                        help="also scramble ISA/GS sender and receiver IDs")
    parser.add_argument("--salt", default=DEFAULT_SALT,
                        help="mapping salt; keep it constant or cross-file mappings differ")
    parser.add_argument("--force", action="store_true", help="overwrite existing output")
    args = parser.parse_args(argv)

    if args.out and len(args.files) > 1:
        parser.error("--out takes a single input file; use --outdir for several")

    for source in args.files:
        try:
            data = source.read_bytes()
        except OSError as exc:
            print(f"ERROR  {source}: {exc}", file=sys.stderr)
            return 1

        try:
            layout = detect_layout(data)
        except ValueError as exc:
            print(f"ERROR  {source}: {exc}", file=sys.stderr)
            return 1

        body = data[layout.body_start:]
        if not body.strip():
            print(f"ERROR  {source}: nothing after the ISA segment", file=sys.stderr)
            return 1

        segments, ended, newline = split_segments(body, layout)
        if not segments:
            print(f"ERROR  {source}: no segments found after the ISA", file=sys.stderr)
            return 1

        print(build_report(segments, layout, source, len(data)))
        if args.stats_only:
            print()
            continue

        if args.excerpt:
            segments = make_excerpt(segments, layout.element, args.excerpt)

        scrubber = Scrubber(args.salt, layout.element_text,
                            args.scrub_org, args.scrub_envelope_ids)
        cleaned = [scrubber.scrub_segment(seg) for seg in segments]
        output = data[:layout.body_start] + join_segments(cleaned, layout, newline, ended)

        if args.out:
            target = args.out
        elif args.outdir:
            target = args.outdir / f"{source.stem}.sanitized{source.suffix}"
        else:
            target = source.with_name(f"{source.stem}.sanitized{source.suffix}")

        if target.resolve() == source.resolve():
            print(f"ERROR  {source}: refusing to overwrite the input file", file=sys.stderr)
            return 1
        if target.exists() and not args.force:
            print(f"ERROR  {target} exists; use --force to overwrite", file=sys.stderr)
            return 1

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(output)

        summary = ", ".join(f"{k}={v}" for k, v in sorted(scrubber.counts.items()))
        print(f"  -> wrote {target}  ({len(output):,} bytes)")
        print(f"  -> replacements: {summary or 'none'}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
