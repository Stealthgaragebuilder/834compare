"""
app.py - tkinter/ttk front end for the 834 compare tool.

Local-only desktop app. No network, no external services.

Layout
------
* Top bar: pick File A (baseline) and File B (comparison), a "Show all segments"
  checkbox (default off = only changed), and Compare / Export buttons.
* Results tree: a "Header Differences" group (when present) followed by one row
  per subscriber, each expandable to its segment-level diffs.
* Detail pane: side-by-side segments for the selected subscriber, honouring the
  "show all" toggle.

The UI is a thin layer over parser834 + compare + export_excel. All the logic
lives in those modules; this file only wires widgets to them.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import compare
import export_excel
import parser834
from models import CompareResult, ParseResult, Status, SubscriberDiff

# Colours (tkinter hex) for status tags.
_TAG_COLORS = {
    Status.CHANGED: "#fff2cc",
    Status.ADDED: "#c6efce",
    Status.REMOVED: "#ffc7ce",
    Status.UNCHANGED: "#f2f2f2",
    "header": "#d9e1f2",
}


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("834 EDI File Compare")
        self.root.geometry("1080x720")
        self.root.minsize(820, 520)

        self.result: Optional[CompareResult] = None
        self.parse_a: Optional[ParseResult] = None
        self.parse_b: Optional[ParseResult] = None

        self.var_a = tk.StringVar()
        self.var_b = tk.StringVar()
        self.show_all = tk.BooleanVar(value=False)

        self._build_ui()

    # ------------------------------------------------------------------ layout

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="File A (baseline):").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_a).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="Browse…", command=self._browse_a).grid(row=0, column=2)

        ttk.Label(top, text="File B (comparison):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(top, textvariable=self.var_b).grid(row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(top, text="Browse…", command=self._browse_b).grid(row=1, column=2, pady=(6, 0))

        top.columnconfigure(1, weight=1)

        opts = ttk.Frame(self.root)
        opts.pack(fill="x", **pad)
        ttk.Checkbutton(opts, text="Show all segments (not just changed)",
                        variable=self.show_all, command=self._repaint).pack(side="left")
        ttk.Button(opts, text="Compare", command=self._compare).pack(side="left", padx=8)
        ttk.Button(opts, text="Export to Excel…", command=self._export).pack(side="left")
        self.status_var = tk.StringVar(value="Pick two 834 files and press Compare.")
        ttk.Label(opts, textvariable=self.status_var, foreground="#555").pack(side="right")

        # Results tree.
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, **pad)
        cols = ("status", "name", "key", "summary")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (
            ("status", "Status", 110),
            ("name", "Subscriber / Field", 300),
            ("key", "Key", 220),
            ("summary", "Detail", 360),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        for tag, color in _TAG_COLORS.items():
            self.tree.tag_configure(tag, background=color)

        # Detail pane.
        det_frame = ttk.LabelFrame(self.root, text="Detail")
        det_frame.pack(fill="both", expand=True, **pad)
        self.detail = tk.Text(det_frame, wrap="word", height=10,
                              font=("Consolas", 9))
        d_sb = ttk.Scrollbar(det_frame, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=d_sb.set)
        self.detail.pack(side="left", fill="both", expand=True)
        d_sb.pack(side="right", fill="y")
        self.detail.configure(state="disabled")

    # ------------------------------------------------------------------ actions

    def _browse_a(self) -> None:
        self._browse(self.var_a)

    def _browse_b(self) -> None:
        self._browse(self.var_b)

    def _browse(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Select an 834 file",
            filetypes=[("834 / EDI files", "*.834 *.txt *.edi"), ("All files", "*.*")])
        if path:
            var.set(path)

    def _compare(self) -> None:
        a, b = self.var_a.get().strip(), self.var_b.get().strip()
        if not a or not b:
            messagebox.showwarning("834 Compare", "Choose both File A and File B.")
            return
        try:
            self.parse_a = parser834.parse_file(a)
            self.parse_b = parser834.parse_file(b)
        except (parser834.ParseError, OSError) as exc:
            messagebox.showerror("834 Compare", f"Could not read a file:\n{exc}")
            return

        self.result = compare.compare_results(self.parse_a, self.parse_b)
        self._populate_tree()
        self._set_status(
            f"{len(self.result.subscribers)} subscribers · "
            f"{len(self.result.changed)} changed · "
            f"{len(self.result.added)} added · "
            f"{len(self.result.removed)} removed · "
            f"{len(self.result.header_diffs)} header diffs")
        # Auto-expand changed subscribers so the user sees the differences first.
        for item in self.tree.get_children():
            if self.tree.set(item, "status") in (
                    Status.CHANGED, Status.ADDED, Status.REMOVED):
                self.tree.item(item, open=True)

    def _export(self) -> None:
        if self.result is None:
            messagebox.showinfo("834 Compare", "Run a comparison first.")
            return
        default = export_excel.default_output_name(self.var_a.get(), self.var_b.get())
        path = filedialog.asksaveasfilename(
            title="Export comparison to Excel",
            defaultextension=".xlsx",
            initialfile=default,
            filetypes=[("Excel workbook", "*.xlsx")])
        if not path:
            return
        try:
            export_excel.export(self.result, path,
                                include_all_segments=self.show_all.get())
        except Exception as exc:  # noqa: BLE001 - surface any export failure
            messagebox.showerror("834 Compare", f"Export failed:\n{exc}")
            return
        self._set_status(f"Exported to {path}")
        messagebox.showinfo("834 Compare", f"Saved:\n{path}")

    # ------------------------------------------------------------------ painting

    def _populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.result is None:
            return

        if self.result.header_diffs:
            hdr = self.tree.insert(
                "", "end", text="Header Differences",
                values=(f"{len(self.result.header_diffs)} diffs", "", "",
                        "envelope / BGN fields that differ"),
                tags=("header",))
            for h in self.result.header_diffs:
                self.tree.insert(hdr, "end", text=h.label,
                                 values=("", "", f"{h.value_a!r}  ->  {h.value_b!r}"),
                                 tags=("header",))

        for sub in self.result.subscribers:
            summary = self._sub_summary(sub)
            item = self.tree.insert(
                "", "end", text=sub.label,
                values=(sub.status, sub.key, summary),
                tags=(sub.status,))
            for d in sub.diffs:
                detail = d.detail or (d.seg_a or d.seg_b or "")
                self.tree.insert(item, "end", text=d.context,
                                 values=(d.kind, "", detail), tags=(d.kind,))

    def _sub_summary(self, sub: SubscriberDiff) -> str:
        if not sub.is_changed:
            return "identical"
        kinds: dict[str, int] = {}
        for d in sub.diffs:
            kinds[d.kind] = kinds.get(d.kind, 0) + 1
        parts = [f"{n} {k}" for k, n in sorted(kinds.items())]
        base = ", ".join(parts) if parts else "whole subscriber"
        return f"A={sub.seg_a_count} segs, B={sub.seg_b_count} segs · {base}"

    def _on_select(self, _event: object) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        node = sel[0]
        # A leaf node is a diff child (or a header field); show its parent
        # subscriber's detail. A subscriber row (has children) shows itself.
        target = node
        if not self.tree.get_children(node):
            parent = self.tree.parent(node)
            if parent:
                target = parent
        values = self.tree.item(target, "values")
        if values and values[0] in (Status.CHANGED, Status.ADDED,
                                    Status.REMOVED, Status.UNCHANGED):
            self._show_subscriber_detail(self.tree.item(target, "text"))
        else:
            self._show_text("Select a subscriber row to see its segments.")

    def _show_subscriber_detail(self, label: str) -> None:
        self._set_detail("")
        if self.result is None:
            return
        sub = next((s for s in self.result.subscribers if s.label == label), None)
        if sub is None:
            return
        lines = [f"{sub.label}   [{sub.status}]   key={sub.key} ({sub.key_source})", ""]
        if sub.diffs:
            lines.append("Differences:")
            for d in sub.diffs:
                lines.append(f"  [{d.kind}] {d.context}")
                if d.seg_a:
                    lines.append(f"     A: {d.seg_a}")
                if d.seg_b:
                    lines.append(f"     B: {d.seg_b}")
                if d.detail:
                    lines.append(f"     {d.detail}")
            lines.append("")
        if self.show_all.get() or sub.is_changed:
            lines.append("All segments (A | B):")
            a_rows = _group(sub.rows_a)
            b_rows = _group(sub.rows_b)
            contexts = list(a_rows) + [c for c in b_rows if c not in a_rows]
            for ctx in contexts:
                a_list = a_rows.get(ctx, [])
                b_list = b_rows.get(ctx, [])
                for i in range(max(len(a_list), len(b_list))):
                    sa = a_list[i].raw if i < len(a_list) else ""
                    sb = b_list[i].raw if i < len(b_list) else ""
                    lines.append(f"  {ctx}")
                    lines.append(f"     A: {sa}")
                    lines.append(f"     B: {sb}")
        self._set_detail("\n".join(lines))

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _show_text(self, text: str) -> None:
        self._set_detail(text)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _repaint(self) -> None:
        # Re-render the detail pane when the show-all toggle changes.
        sel = self.tree.selection()
        if sel:
            self._on_select(None)


def _group(rows: list[tuple[str, object]]) -> "dict[str, list]":
    out: dict[str, list] = {}
    for ctx, seg in rows:
        out.setdefault(ctx, []).append(seg)
    return out


def _run_cli(argv: list[str]) -> int:
    """Headless comparison: ``app.py FILE_A FILE_B [--out X.xlsx]``.

    Prints a short summary to stdout and optionally writes the Excel report.
    Returns a process exit code (0 ok, 1 error).
    """
    import argparse
    parser = argparse.ArgumentParser(description="Compare two 834 EDI files.")
    parser.add_argument("file_a", help="baseline 834 file")
    parser.add_argument("file_b", help="comparison 834 file")
    parser.add_argument("--out", help="write the Excel report to this path")
    args = parser.parse_args(argv)

    try:
        a = parser834.parse_file(args.file_a)
        b = parser834.parse_file(args.file_b)
    except (parser834.ParseError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    result = compare.compare_results(a, b)
    print(f"File A : {a.file_name}  ({a.subscriber_count} subscribers)")
    print(f"File B : {b.file_name}  ({b.subscriber_count} subscribers)")
    print(f"Header diffs : {len(result.header_diffs)}")
    print(f"Changed      : {sum(1 for s in result.subscribers if s.status == Status.CHANGED)}")
    print(f"Added        : {len(result.added)}")
    print(f"Removed      : {len(result.removed)}")
    print(f"Unchanged    : {len(result.unchanged)}")
    print(f"Segment diffs: {result.total_diffs}")
    for s in result.subscribers:
        if s.is_changed:
            print(f"  [{s.status:9}] {s.label}  ({len(s.diffs)} diffs)")

    if args.out:
        out = export_excel.export(result, args.out)
        print(f"Excel report: {out}")
    return 0


def main() -> None:
    if len(sys.argv) > 1:
        raise SystemExit(_run_cli(sys.argv[1:]))
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
