# Sanitizer for INT5151 834 files

Produces a **structurally identical, PII-free copy** of a real 834 file so it can be
shared for testing the compare tool without exposing employee data.

This is a standalone helper. It is **not** part of the compare app and must not be
shipped with it.

## Files

| File | Purpose |
|---|---|
| `sanitize834.py` | the sanitizer |
| `verify_sanitizer.py` | proves the output is safe (structure, delimiters, no PII, consistency) |
| `test_helpers.py` | unit checks for the replacement helpers |
| `make_fixtures.py` | generates fake 834 files for testing, no real data involved |
| `samples/` | generated fixtures |
| `samples/clean/` | sanitized output |

## Recommended workflow

**Step 1 — report first, write nothing.** This answers the open questions and prints
counts only, never a field value:

```
py sanitize834.py "C:\path\to\int5151_file.txt" --stats-only
```

Read these lines:

- `Loop 2700 (LX segments)` — 0 means loop 2700 can be skipped entirely.
- `Duplicate REF*17 values` — non-zero means a subscriber appears twice in one file.
- `Transaction sets (ST)` — more than 1 means the file holds several transaction sets.
- `REF*17 present` vs `subscribers without REF*17` — tells you how often the
  fallback key (NM1*IL NM109) is actually needed.

**Step 2 — verify the report looks right**, then produce a copy:

```
py sanitize834.py "C:\path\to\int5151_file.txt" --out "C:\temp\sample.sanitized.txt"
```

**Step 3 — prove it is safe:**

```
py verify_sanitizer.py --pair "C:\path\to\original.txt" "C:\temp\sample.sanitized.txt"
```

Exit code 0 and `ALL CHECKS PASSED` means no original value survived. Do this before
sharing anything, every time.

**Step 4 — sanity-check by eye.** Open the sanitized file and skim one subscriber
block. Names, IDs, addresses and dates should look obviously fake while the segments,
qualifiers and empty elements look untouched.

## Working with a pair of files

Pass both files in one run so the same person maps to the same fake person:

```
py sanitize834.py baseline.txt comparison.txt --outdir C:\temp\clean
```

## Smaller excerpts

For sharing a sample rather than a whole file, keep only the header plus N subscribers
(the envelope trailer is preserved so the file still parses):

```
py sanitize834.py baseline.txt --excerpt 3 --out C:\temp\sample.txt
```

## Options

| Option | Effect |
|---|---|
| `--stats-only` | structure report, writes nothing |
| `--out PATH` | explicit output path (single input only) |
| `--outdir DIR` | output directory, one output per input |
| `--excerpt N` | header + first N subscriber groups + envelope trailer |
| `--scrub-org` | also scramble payer/sponsor names and IDs (`N1`, `NM1` type 2) |
| `--scrub-envelope-ids` | also scramble ISA/GS sender and receiver IDs |
| `--salt TEXT` | mapping seed; **keep it constant**, or cross-file mappings change |
| `--force` | overwrite an existing output file |

## What is preserved and what is replaced

**Preserved** — segment order, loop nesting, **empty elements**, qualifier codes,
delimiters read from ISA (element / component / terminator, and the line-break style),
segment counts, control numbers and envelope dates.

**Replaced** — person names, member IDs / SSNs, employee numbers, street addresses,
cities, postal codes, birth dates, phone numbers, e-mail addresses, and the
identifier-style `REF` qualifiers listed in `SCRUB_REF_QUALIFIERS`.

## Design notes

- **Deterministic, no mapping file.** Each fake value is `SHA256(salt + original)`.
  The same real value always yields the same fake, so two files keep their
  correspondence, while nothing sensitive is ever written to a side file.
- **Leading zeros survive.** `042640680` keeps its leading `0`, so leading-zero
  handling bugs in the compare tool stay visible instead of being masked.
- **No value can map to itself.** Names and IDs are re-derived deterministically if the
  first candidate equals the original, so a real name that happens to sit in the fake
  name pool cannot leak through unchanged.
- **Shape preserved.** Element counts and empty elements are untouched. Code that
  breaks on empty elements will still break — which is the point.
- **Lengths preserved** for IDs and numbers, so column widths and fixed-width
  assumptions behave the same.
- **Never logs a value.** All output is counts, file paths and byte sizes.
- **Never touches the input.** Refuses to write onto the source file, and refuses to
  overwrite an existing output without `--force`.

## Before you share anything

1. Run `--stats-only` and confirm it reads the file correctly.
2. Produce the copy.
3. Run `verify_sanitizer.py --pair` and confirm `ALL CHECKS PASSED`.
4. Skim one block by eye.

`--scrub-org` is worth turning on if payer/sponsor names are commercially sensitive,
and `--scrub-envelope-ids` if the ISA/GS sender IDs identify your organisation.
