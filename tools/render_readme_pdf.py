"""Render README.md to README.pdf (A4, with screenshots) using Chromium.

    pip install markdown-it-py playwright && python -m playwright install chromium
    python tools/render_readme_pdf.py            # writes README.pdf next to README.md

Set KC_CHROMIUM_PATH to use an existing Chromium instead of Playwright's download.
Regenerate the PDF whenever README.md changes, and commit both together.
"""

import os
import pathlib
import sys

from markdown_it import MarkdownIt
from playwright.sync_api import sync_playwright

repo = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[1]
out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "README.pdf"
body = MarkdownIt("commonmark").enable("table").render((repo / "README.md").read_text())
css = """
@page { size: A4; margin: 16mm 15mm 18mm; }
body { font-family: "Segoe UI", "DejaVu Sans", system-ui, sans-serif; font-size: 10pt; line-height: 1.5; color: #17202e; }
h1 { font-size: 22pt; margin: 0 0 6pt; color: #1d4fb8; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 1.5px solid #dde2ea; color: #1d4fb8; break-after: avoid; }
p, li { margin: 3pt 0; }
li > ul { margin: 2pt 0; }
li pre { margin: 4pt 0; }
ul { padding-left: 16pt; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt; background: #eef1f6; padding: 0 3px; border-radius: 3px; }
pre { background: #eef1f6; padding: 8pt 10pt; border-radius: 6px; white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 9pt; break-inside: avoid; }
th, td { border: 1px solid #dde2ea; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #f4f6fa; }
img { max-width: 100%; border: 1px solid #dde2ea; border-radius: 6px; }
table img { max-height: 120mm; }
p > img { display: block; margin: 6pt auto; }
a { color: #2f6fde; text-decoration: none; }
"""
html = f"<!doctype html><html><head><meta charset='utf-8'><base href='{repo.as_uri()}/'><style>{css}</style></head><body>{body}</body></html>"
tmp = out.with_suffix(".html")
tmp.write_text(html)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("KC_CHROMIUM_PATH") or None)
    pg = b.new_page(); pg.goto(tmp.as_uri()); pg.wait_for_load_state("networkidle")
    pg.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
           header_template="<span></span>",
           footer_template="<div style='font-size:8px;width:100%;text-align:center;color:#5c6879'>Kidputer Control — README · page <span class='pageNumber'></span> of <span class='totalPages'></span></div>",
           margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"})
    b.close()
tmp.unlink()
print(out)
