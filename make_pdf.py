#!/usr/bin/env python3
"""
make_pdf.py - render USER_GUIDE.md to a styled PDF (USER_GUIDE.pdf).

Usage:
    python make_pdf.py                 # uses USER_GUIDE.md -> USER_GUIDE.pdf
    python make_pdf.py INPUT.md OUT.pdf

Pure-Python (markdown + xhtml2pdf); no pandoc or system tools required.
The markdown is converted to HTML, wrapped in a small stylesheet, and rendered
to PDF. Tables, code blocks, headers, and lists are all supported.
"""

from __future__ import annotations

from pathlib import Path

import markdown
from xhtml2pdf import pisa

ROOT = Path(__file__).resolve().parent

# Fonts with good Unicode coverage, copied into ./fonts so xhtml2pdf (which is
# sandboxed to the project directory) can embed them. This keeps arrows, dashes,
# bullets and the ASCII diagram rendering correctly. Missing files fall back to
# the built-in fonts.
_SANS_TTF = str((ROOT / "fonts" / "segoeui.ttf")).replace("\\", "/")
_MONO_TTF = str((ROOT / "fonts" / "couri.ttf")).replace("\\", "/")

# The xhtml2pdf built-in fonts lack glyphs for box-drawing, some arrows and
# emoji. We sanitize the source for the PDF only (the .md keeps the pretty
# Unicode) so the document renders cleanly in any font.
_BOX_DRAWING = {
    "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+",
    "─": "-", "│": "|",
}
_ARROWS = {"→": "->", "←": "<-", "▶": ">"}


def _sanitize_for_pdf(text: str) -> str:
    for src, dst in _BOX_DRAWING.items():
        text = text.replace(src, dst)
    for src, dst in _ARROWS.items():
        text = text.replace(src, dst)
    # Drop pictographic emoji (status dots) - the colour word is already present.
    out = []
    for ch in text:
        cp = ord(ch)
        if 0x1F000 <= cp <= 0x1FAFF or cp == 0x2B1C:
            continue
        out.append(ch)
    return "".join(out)


_CSS_TEMPLATE = """
@font-face { font-family: SansFont; src: url("__SANS__"); }
@font-face { font-family: MonoFont; src: url("__MONO__"); }
@page {
    size: A4;
    margin: 20mm 16mm 18mm 16mm;
    @frame footer {
        -pdf-frame-content: footer;
        bottom: 8mm; left: 16mm; right: 16mm; height: 6mm;
    }
}
#footer {
    font-family: SansFont, Helvetica, Arial, sans-serif;
    font-size: 8px; color: #888; text-align: center;
    border-top: 0.5pt solid #ccc; padding-top: 2px;
}
body { font-family: SansFont, Helvetica, Arial, sans-serif; font-size: 10px; color: #1a1a1a; line-height: 1.45; }
h1 { font-size: 20px; color: #1f3864; border-bottom: 2pt solid #1f3864; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 15px; color: #1f3864; margin-top: 16px; border-bottom: 0.75pt solid #b4c6e7; padding-bottom: 2px; }
h3 { font-size: 12px; color: #2e5395; margin-top: 12px; }
h4 { font-size: 11px; color: #2e5395; }
p { margin: 6px 0; }
a { color: #1f3864; text-decoration: none; }
code { font-family: MonoFont, Courier, monospace; background: #f2f2f2; padding: 0 2px; font-size: 9px; }
pre { font-family: MonoFont, Courier, monospace; background: #f5f5f5; border: 0.5pt solid #ddd;
      border-left: 3pt solid #1f3864; padding: 6px 8px; font-size: 8.5px;
      white-space: pre-wrap; word-wrap: break-word; }
pre code { background: transparent; padding: 0; }
blockquote { border-left: 3pt solid #b4c6e7; margin: 8px 0; padding: 2px 10px;
             color: #444; background: #f4f7fb; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9px; }
th, td { border: 0.5pt solid #b4c6e7; padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #1f3864; color: #fff; font-weight: bold; }
tr:nth-child(even) td { background: #eef2f9; }
ul, ol { margin: 6px 0 6px 18px; padding: 0; }
li { margin: 2px 0; }
hr { border: none; border-top: 0.75pt solid #ccc; margin: 14px 0; }
strong { color: #111; }
"""
CSS = _CSS_TEMPLATE.replace("__SANS__", _SANS_TTF).replace("__MONO__", _MONO_TTF)


def render(input_md: Path, output_pdf: Path) -> None:
    text = _sanitize_for_pdf(input_md.read_text(encoding="utf-8"))

    html_body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
    )

    # Add a running footer with the document title and page number.
    footer = ('<div id="footer">834 EDI File Compare - User Guide'
              '&nbsp;&nbsp;|&nbsp;&nbsp;Page <pdf:pagenumber/></div>')
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body>
{html_body}
{footer}
</body></html>"""

    # xhtml2pdf prints harmless warnings to stderr (e.g. "border-collapse not
    # implemented"). Silence them so the script exits cleanly on Windows.
    import os
    import sys

    with output_pdf.open("wb") as out:
        real_stderr = sys.stderr
        try:
            sys.stderr = open(os.devnull, "w")
            status = pisa.CreatePDF(html, dest=out, encoding="utf-8")
        finally:
            sys.stderr = real_stderr
    if status.err:
        raise RuntimeError(f"PDF generation reported {status.err} error(s)")


def main() -> None:
    import sys

    if len(sys.argv) == 1:
        src, out = ROOT / "USER_GUIDE.md", ROOT / "USER_GUIDE.pdf"
    elif len(sys.argv) == 3:
        src, out = Path(sys.argv[1]), Path(sys.argv[2])
    else:
        print(__doc__)
        raise SystemExit(2)

    if not src.exists():
        print(f"ERROR: input not found: {src}")
        raise SystemExit(1)

    render(src, out)
    size = out.stat().st_size
    print(f"Wrote {out}  ({size:,} bytes)  from {src.name}")


if __name__ == "__main__":
    main()
