# 834 EDI File Compare

A local-only desktop tool for comparing two **834 EDI** (benefit enrollment) files.
It parses both files, groups records by subscriber, compares them **logically**
(not line-by-line), shows the differences in a tkinter window, and can export the
result to Excel.

Built for the INT5151 834 files. It is fast and deterministic: a file with
thousands of records parses in well under a second and compares in a few hundred
milliseconds.

> **New user?** Read the step-by-step **[User Guide](USER_GUIDE.md)** (or the
> **[PDF version](USER_GUIDE.pdf)**) — it walks you through installing, running,
> using the GUI, reading results, and exporting.

## Requirements

* Python **3.12+** (built and tested on 3.12/3.15)
* `openpyxl` (Excel export)
* `pytest` (tests)
* `tkinter` (standard library, ships with Python)

```
pip install -r requirements.txt
```

## Run the app

```
python app.py
```

1. Pick **File A (baseline)** and **File B (comparison)**.
2. Press **Compare**.
3. The results tree shows a *Header Differences* group (when present) and one row
   per subscriber, colour-coded:
   * amber = **Changed**
   * green = **Added** (only in B)
   * red = **Removed** (only in A)
   * grey = **Unchanged**
4. Select a subscriber to see its segments side-by-side in the detail pane.
   * By default only changed subscribers are expanded and their diffs are shown.
   * Tick **Show all segments** to see every segment for the selected subscriber.
5. Press **Export to Excel…** to write a formatted `.xlsx` report.

## Project layout

```
app.py            tkinter/ttk UI (thin layer over the engine)
parser834.py      ISA/GS/ST parsing + subscriber/dependent grouping
compare.py        logical comparison engine (pure, no I/O)
models.py         dataclasses shared by all modules
export_excel.py   openpyxl workbook writer
tests/            pytest suite (parser, compare, export)
conftest.py       makes the project root importable in tests
pytest.ini        scopes pytest to tests/
samples/          synthetic fixtures (no real PII)
```

The root-level `sanitize834.py`, `verify_sanitizer.py`, `make_fixtures.py`,
`test_helpers.py`, `test_multist.py` are **standalone helpers for the sanitizer**
and are not part of the compare app.

## How parsing works

* Every delimiter is read from the **ISA** segment (element, component, segment
  terminator, line-break style) — nothing is hard-coded. This means the tool
  handles `*`/`|` element delimiters, `~` or newline-only terminators, and CRLF/LF.
* The body is split into segments **preserving raw order**.
* **Empty elements are preserved** (splitting keeps `""`), so
  `NM1*IL*1*Last*First****34*ID` keeps its two empty elements.
* A **subscriber** is one `INS*Y` group: the subscriber's identifying segments
  (`REF*17`, `NM1*IL`, `PER`, `N3`, `N4`), its dependents (`INS*N` blocks with
  `NM1`/`DMG`), and its coverage (`HD`, `DTP`, `REF`). The first `HD` marks the
  start of coverage.
* A file may hold **several ST transaction sets**; subscribers are collected
  across all of them.

## How comparison works

* **Subscriber key** = `REF*17` value. If absent, it falls back to the `NM1*IL`
  identity (last / first / member ID). A subscriber with neither gets an
  anonymous key and will not match across files.
* **Dependent key** = `NM1*IL` identity (last / first / member ID). Dependents
  with no ID fall back to name-only.
* Subscribers are matched by key:
  * in both files → compared in detail (**Changed** / **Unchanged**)
  * only in B → **Added**
  * only in A → **Removed**
* Within a matched subscriber, three **record blocks** are compared:
  subscriber-level segments, dependents (matched by dependent key), and coverage.
* Segments are matched by a **stable match key** (segment id + identifying
  element), *not* by position. So a segment that moved (e.g. `REF*17` before vs
  after `NM1`) still lines up, and only genuine value changes are reported.
* **Header differences** (ISA/GS/ST control numbers, dates, BGN, and other
  pre-`INS` segments) are shown separately.
* **Loop 2700 / `LX` is intentionally not modelled** — the real data has none.

## Excel report layout

| Sheet | Contents |
|---|---|
| Summary | file names, subscriber/added/removed/changed counts, warnings |
| Header Differences | one row per differing header field (A vs B) |
| Subscriber Differences | one row per segment-level diff, grouped by subscriber |
| All Segments | full side-by-side of every segment (the "show all" view) |

## Assumptions & edge cases

* **Coverage comes last.** The first `HD` starts the coverage block and everything
  after it (until the next `INS`) is treated as coverage. The validated samples
  always place `HD` after the dependent blocks. If a file interleaved coverage
  before dependents, trailing `DTP`/`REF` would be mis-attributed.
* **Multiple `ST` sets.** Subscribers are matched by key *globally* across all ST
  sets. If the **same person appears in more than one ST set** (the overlap case
  the sanitizer warns about), the occurrences are paired by position within the
  key group rather than collapsed, so no data is lost.
* **Duplicate match keys within a block** (e.g. two `REF*17` in one subscriber)
  are paired in order of appearance. This is rare.
* **NM1 member ID location.** In the validated data the member ID sits in
  **NM109 (index 9)**; index 8 holds a code (e.g. `34`). The parser prefers
  index 9 and falls back to index 8. If a future file stores the ID elsewhere,
  adjust `_nm1_identity` in `parser834.py`.
* **Anonymous subscribers** (no `REF*17`, no usable `NM1*IL`) are given a
  synthetic key and will show as Added/Removed rather than matched.
* **Orphan dependents** (an `INS*N` before any `INS*Y`) are attached to a
  synthetic group and a warning is recorded.
* **Deterministic output.** Same inputs always produce the same comparison and
  the same workbook (no timestamps in the data, no random styling).

## Tests

```
python -m pytest
```

The suite covers ISA/delimiter handling, empty-element preservation, key
fallbacks, multi-ST sets, value changes, added/removed subscribers and
dependents, segment reordering (not a diff), header diffs, overlap handling, and
Excel export. Fixtures are generated by `tests/edi_builder.py` (synthetic, no
real data).

## Using the sanitized samples

The `samples/` folder contains synthetic 834 files. `samples/sample_a.834` and
`samples/sample_b.834` are a matched pair with deliberate differences (plan
change, new dependent, added/removed subscriber, segment reorder, and
envelope-only changes) — a good end-to-end test:

```
python app.py          # pick sample_a.834 and sample_b.834, then Compare
```

To generate fresh fixtures (no real data): `python make_fixtures.py`.
