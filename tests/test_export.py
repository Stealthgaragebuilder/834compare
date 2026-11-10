"""Tests for the Excel export (uses openpyxl to read the workbook back)."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

import compare
import export_excel
from tests.edi_builder import coverage, subscriber

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture()
def result():
    return compare.compare_files(SAMPLES / "sample_a.834", SAMPLES / "sample_b.834")


def test_export_creates_workbook(result, tmp_path):
    out = tmp_path / "out.xlsx"
    export_excel.export(result, out)
    assert out.exists()
    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "Header Differences",
                             "Subscriber Differences", "All Segments"]


def test_summary_sheet(result, tmp_path):
    out = tmp_path / "out.xlsx"
    export_excel.export(result, out)
    wb = load_workbook(out)
    ws = wb["Summary"]
    # Find the "Result" row and confirm it says DIFFERENT.
    values = {(ws.cell(row=r, column=1).value): ws.cell(row=r, column=2).value
              for r in range(1, ws.max_row + 1)}
    assert values.get("Result") == "DIFFERENT"
    assert values.get("Changed") == 2


def test_header_diffs_sheet(result, tmp_path):
    out = tmp_path / "out.xlsx"
    export_excel.export(result, out)
    ws = load_workbook(out)["Header Differences"]
    labels = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    assert "GS control" in labels


def test_subscriber_diffs_includes_added_removed(result, tmp_path):
    out = tmp_path / "out.xlsx"
    export_excel.export(result, out)
    ws = load_workbook(out)["Subscriber Differences"]
    labels = {ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)}
    # The removed (Chen) and added (Patel) subscribers must be present.
    assert any(l and "EMP00125" in str(l) for l in labels)
    assert any(l and "EMP00126" in str(l) for l in labels)


def test_all_segments_sheet(result, tmp_path):
    out = tmp_path / "out.xlsx"
    export_excel.export(result, out)
    ws = load_workbook(out)["All Segments"]
    assert ws.max_row > 1


def test_identical_export(tmp_path):
    segs = subscriber("E1", "Last", "First", "000000001") + coverage("P")
    import parser834
    from tests.edi_builder import build_file
    r = compare.compare_results(
        parser834.parse_bytes(build_file(segs)),
        parser834.parse_bytes(build_file(list(segs))))
    out = tmp_path / "same.xlsx"
    export_excel.export(r, out)
    ws = load_workbook(out)["Summary"]
    values = {(ws.cell(row=rr, column=1).value): ws.cell(row=rr, column=2).value
              for rr in range(1, ws.max_row + 1)}
    assert values.get("Result") == "IDENTICAL"


def test_default_output_name():
    name = export_excel.default_output_name("a/sample_a.834", "b/sample_b.834")
    assert name == "834_compare_sample_a_vs_sample_b.xlsx"
