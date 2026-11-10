"""
export_excel.py - write a CompareResult to a formatted .xlsx workbook.

Worksheet layout
----------------
1. "Summary"               - file names, counts, warnings.
2. "Header Differences"    - one row per differing header field (A vs B).
3. "Subscriber Differences"- one row per segment-level diff, grouped by subscriber.
4. "All Segments"          - full side-by-side of every segment (the "show all" view).

The output is deterministic: given the same CompareResult it always produces the
same workbook (no timestamps, no random styling). openpyxl is the only dependency.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from models import CompareResult, Status

# --------------------------------------------------------------------------- styling

_TITLE_FONT = Font(bold=True, size=14)
_SUB_FONT = Font(bold=True, size=11)
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="305496")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_STATUS_FILL = {
    Status.CHANGED: PatternFill("solid", fgColor="FFF2CC"),  # amber
    Status.ADDED: PatternFill("solid", fgColor="C6EFCE"),    # green
    Status.REMOVED: PatternFill("solid", fgColor="FFC7CE"),  # red
    Status.UNCHANGED: PatternFill("solid", fgColor="E7E6E6"),  # grey
}
_KIND_FONT = {
    "changed": Font(color="9C6500", bold=True),
    "added": Font(color="006100", bold=True),
    "removed": Font(color="9C0006", bold=True),
}


def _style_header_row(ws, row: int, ncols: int) -> None:
    for col in range(1, ncols + 1):
        c = ws.cell(row=row, column=col)
        c.font = _HEADER_FONT
        c.fill = _HEADER_FILL
        c.border = _BORDER
        c.alignment = Alignment(vertical="center")


def _set_widths(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


# --------------------------------------------------------------------------- sheets


def _write_summary(wb: Workbook, result: CompareResult) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "834 Comparison Summary"
    ws["A1"].font = _TITLE_FONT
    ws["A2"] = f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}"

    rows = [
        ("File A", result.file_a_name),
        ("File B", result.file_b_name),
        ("", ""),
        ("Subscribers (A)", sum(1 for s in result.subscribers if s.seg_a_count)),
        ("Subscribers (B)", sum(1 for s in result.subscribers if s.seg_b_count)),
        ("Changed", sum(1 for s in result.subscribers if s.status == Status.CHANGED)),
        ("Added (only in B)", len(result.added)),
        ("Removed (only in A)", len(result.removed)),
        ("Unchanged", len(result.unchanged)),
        ("Segment-level differences", result.total_diffs),
        ("Header differences", len(result.header_diffs)),
        ("Result", "IDENTICAL" if result.is_identical else "DIFFERENT"),
    ]
    r = 4
    for label, value in rows:
        ws.cell(row=r, column=1, value=label).font = _SUB_FONT
        ws.cell(row=r, column=2, value=value)
        r += 1

    if result.warnings:
        r += 1
        ws.cell(row=r, column=1, value="Warnings").font = _SUB_FONT
        for w in result.warnings:
            r += 1
            ws.cell(row=r, column=1, value="! " + w)

    _set_widths(ws, [32, 60])


def _write_header_diffs(wb: Workbook, result: CompareResult) -> None:
    ws = wb.create_sheet("Header Differences")
    headers = ["Field", "File A", "File B"]
    ws.append(headers)
    _style_header_row(ws, 1, len(headers))
    for h in result.header_diffs:
        ws.append([h.label, h.value_a, h.value_b])
    if not result.header_diffs:
        ws.append(["(no header differences)", "", ""])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:C{max(2, ws.max_row)}"
    _set_widths(ws, [24, 40, 40])


def _write_subscriber_diffs(wb: Workbook, result: CompareResult) -> None:
    ws = wb.create_sheet("Subscriber Differences")
    headers = ["Subscriber", "Status", "Key", "Context", "Type",
               "Segment A", "Segment B", "Detail"]
    ws.append(headers)
    _style_header_row(ws, 1, len(headers))

    r = 2
    for sub in result.subscribers:
        if not sub.is_changed:
            continue
        if sub.diffs:
            for d in sub.diffs:
                ws.cell(row=r, column=1, value=sub.label)
                c = ws.cell(row=r, column=2, value=sub.status)
                c.fill = _STATUS_FILL.get(sub.status, PatternFill())
                ws.cell(row=r, column=3, value=sub.key)
                ws.cell(row=r, column=4, value=d.context)
                kc = ws.cell(row=r, column=5, value=d.kind)
                kc.font = _KIND_FONT.get(d.kind, Font())
                ws.cell(row=r, column=6, value=d.seg_a or "")
                ws.cell(row=r, column=7, value=d.seg_b or "")
                ws.cell(row=r, column=8, value=d.detail)
                r += 1
        else:
            # Added/Removed subscriber with no per-segment diffs to list.
            ws.cell(row=r, column=1, value=sub.label)
            c = ws.cell(row=r, column=2, value=sub.status)
            c.fill = _STATUS_FILL.get(sub.status, PatternFill())
            ws.cell(row=r, column=3, value=sub.key)
            ws.cell(row=r, column=4, value="(whole subscriber)")
            kc = ws.cell(row=r, column=5, value=sub.status.lower())
            kc.font = _KIND_FONT.get(sub.status.lower(), Font())
            r += 1

    if r == 2:
        ws.append(["(no differences)", "", "", "", "", "", "", ""])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:H{max(2, ws.max_row)}"
    _set_widths(ws, [28, 12, 20, 26, 10, 40, 40, 40])


def _write_all_segments(wb: Workbook, result: CompareResult,
                        only_changed: bool = False) -> None:
    """Full side-by-side of segments. ``only_changed`` limits to changed subs."""
    ws = wb.create_sheet("All Segments")
    headers = ["Subscriber", "Status", "Context", "Segment A", "Segment B"]
    ws.append(headers)
    _style_header_row(ws, 1, len(headers))

    r = 2
    for sub in result.subscribers:
        if only_changed and not sub.is_changed:
            continue
        # Align rows by context, then by position within the context.
        a_rows = _group_by_context(sub.rows_a)
        b_rows = _group_by_context(sub.rows_b)
        contexts = list(a_rows.keys()) + [c for c in b_rows if c not in a_rows]
        for ctx in contexts:
            a_list = a_rows.get(ctx, [])
            b_list = b_rows.get(ctx, [])
            for i in range(max(len(a_list), len(b_list))):
                seg_a = a_list[i].raw if i < len(a_list) else ""
                seg_b = b_list[i].raw if i < len(b_list) else ""
                ws.cell(row=r, column=1, value=sub.label)
                c = ws.cell(row=r, column=2, value=sub.status)
                c.fill = _STATUS_FILL.get(sub.status, PatternFill())
                ws.cell(row=r, column=3, value=ctx)
                ws.cell(row=r, column=4, value=seg_a)
                ws.cell(row=r, column=5, value=seg_b)
                r += 1

    if r == 2:
        ws.append(["(no segments)", "", "", "", ""])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:E{max(2, ws.max_row)}"
    _set_widths(ws, [28, 12, 26, 45, 45])


def _group_by_context(rows: list[tuple[str, object]]) -> dict[str, list]:
    out: dict[str, list] = {}
    for ctx, seg in rows:
        out.setdefault(ctx, []).append(seg)
    return out


# --------------------------------------------------------------------------- public


def export(result: CompareResult, path: str | Path,
           include_all_segments: bool = True) -> Path:
    """Write ``result`` to an .xlsx workbook at ``path``. Returns the path."""
    wb = Workbook()
    _write_summary(wb, result)
    _write_header_diffs(wb, result)
    _write_subscriber_diffs(wb, result)
    if include_all_segments:
        _write_all_segments(wb, result, only_changed=False)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def default_output_name(file_a: str, file_b: str) -> str:
    """Suggest an output filename from the two input names."""
    stem_a = Path(file_a).stem
    stem_b = Path(file_b).stem
    return f"834_compare_{stem_a}_vs_{stem_b}.xlsx"
