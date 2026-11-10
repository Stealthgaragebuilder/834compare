# Jira Ticket — 834 EDI File Compare Tool (INT5151)

> Draft ticket description for documenting the initial build. Copy the content
> below into the Jira ticket.

---

**Summary:** Build 834 EDI File Compare Tool (INT5151) — initial working version

## Background
We need a way to compare two 834 EDI benefit enrollment files (baseline vs. comparison) to identify subscriber and coverage changes. Manual line-by-line diffing is not viable: real files contain thousands of records, segment order is not stable between runs (e.g. `REF*17` may move relative to `NM1`), and envelope/control-number changes are expected noise. This ticket covers the first working version of a local-only desktop tool built and validated against sanitized copies of real INT5151 834 files.

## Scope — what was delivered
- **Local-only Python desktop app** (Python 3.12+, tkinter/ttk UI, no network).
- **Parser** (`parser834.py`):
  - Reads all delimiters from the ISA segment (element, component, terminator, line-break style) — nothing hard-coded; handles `*`/`|` delimiters, `~` or newline-only termination, CRLF/LF.
  - Preserves raw segment order and **empty elements**.
  - Groups records into envelope/BGN header, subscribers (`INS*Y`), dependents (`INS*N`), and coverage (`HD`/`DTP`/`REF`).
- **Comparison engine** (`compare.py`), pure and deterministic:
  - Subscriber key = `REF*17`, with fallback to `NM1*IL` (last/first/member ID).
  - Dependent key = `NM1*IL` (member ID in NM109; name-only fallback when no ID).
  - Compares logically by subscriber and record block, **not** line-by-line; segments matched by stable identity so reordered segments are not false positives.
  - Results: Changed / Added / Removed / Unchanged per subscriber, plus a separate **Header Differences** section (ISA/GS/ST control numbers, dates, BGN, other pre-`INS` fields).
- **UI** (`app.py`): file pickers, colour-coded results tree (default: changed only, with a "show all segments" option), side-by-side detail pane, Excel export. Also supports a headless CLI (`python app.py A B --out report.xlsx`) for scripted runs.
- **Excel export** (`export_excel.py`, openpyxl): sheets for Summary, Header Differences, Subscriber Differences, All Segments.
- **Tests**: 34 pytest tests covering delimiter handling, empty elements, key fallbacks, multi-`ST` files, value changes, added/removed subscribers & dependents, reorder tolerance, and export.

## Confirmed real-world rules (validated against real samples)
- Subscriber identity: `REF*17` primary; `NM1*IL` fallback when absent.
- Dependents identified by `NM1*IL`, falling back to NM109/other identifying values.
- **Loop 2700 / `LX` is intentionally out of scope** — validated real data contains none.
- Comparison must be logical (subscriber + record block), not raw text.
- Header differences displayed separately; default view shows only changed segments.

## Acceptance criteria
- [ ] Parses both files using ISA/GS/ST structure, preserving raw segment order
- [ ] Groups records by subscriber using `REF*17` (fallback `NM1*IL`)
- [ ] Detects changed, added, and removed subscribers/dependents/coverage
- [ ] Shows header differences in a separate section
- [ ] Displays only changed segments by default, with option to show all
- [ ] Exports results to Excel with a clear worksheet layout
- [ ] Deterministic and fast on real files with thousands of records
- [ ] Test suite passes (`python -m pytest`)

## Performance (measured)
- 5,000 subscribers / 45,000 segments: parse ≈ 0.13 s, compare ≈ 0.1–0.26 s.

## Assumptions & known edge cases (documented in code + README)
- Coverage is assumed to follow the dependent blocks; the first `HD` starts the coverage block (all validated samples place `HD` last).
- Files may contain multiple `ST` transaction sets; subscribers are matched globally by key. If the same person appears in more than one `ST` set (overlap case), occurrences are paired by position, not collapsed.
- Member ID is read from NM109 (index 9), falling back to index 8, per the validated data layout.
- Subscribers with neither `REF*17` nor a usable `NM1*IL` get an anonymous key and will show as Added/Removed rather than matched.
- Testing uses sanitized fixtures (see `README_sanitizer.md`); no real PII is stored in the repo.

## Deliverables
`app.py`, `parser834.py`, `compare.py`, `models.py`, `export_excel.py`, `tests/`, `README.md`, `requirements.txt` (project root: `834compare`).

**Run:** `python app.py` (GUI) · `python -m pytest` (tests)
