#!/usr/bin/env python3
r"""render_compare.py -- Phase B (visual) helper for verify-conversion.

Renders paper.html in headless Chromium and paper.pdf with pdftoppm, saves
both as PNG images in <paper-dir>/verify_shots/, and runs programmatic checks
for rendering faults: broken math (.katex-error), broken images, raw-LaTeX
leaks in the rendered text, and horizontal overflow at several viewport widths.

It also writes one tight screenshot per figure / table / code-listing /
algorithm as verify_shots/elem-*.png, so each element can be scrutinised on
its own (a fixed-height page slice is too coarse to catch per-element layout
faults such as a mis-aligned code block).

It produces evidence; it does not judge. Claude then reads the html-*.png and
pdf-*.png images for the top-to-bottom walk, and dispatches a sub-agent per
elem-*.png for per-element scrutiny (see SKILL.md Phase B).

Requires Playwright:  pip install playwright && playwright install chromium
Requires pdftoppm (poppler-utils) on PATH.

Usage: render_compare.py <paper-dir>
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

SLICE_H = 1400      # px tall per HTML screenshot slice (readable detail)
VIEW_W = 820        # px viewport width

LEAK_PATTERNS = {
    "begin/end environment": r"\\(?:begin|end)\{[a-zA-Z*]+\}",
    "LaTeX command": r"\\[a-zA-Z]{2,}",
    "stray $ (unrendered math delimiter)": r"\$",
}


def render_pdf(pdf: Path, outdir: Path) -> list[Path]:
    if not pdf.is_file():
        print(f"  ! {pdf.name} not found -- skipping PDF render")
        return []
    subprocess.run(["pdftoppm", "-png", "-r", "110", str(pdf), str(outdir / "pdf")],
                   check=True, capture_output=True)
    return sorted(outdir.glob("pdf-*.png"))


def render_html(html: Path, outdir: Path) -> tuple[list[Path], dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("error: Playwright not installed -- "
              "pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(2)

    shots: list[Path] = []
    facts: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": VIEW_W, "height": SLICE_H})
        page.goto(html.as_uri(), wait_until="networkidle", timeout=60000)

        # Let KaTeX finish. The page renders math on DOMContentLoaded and refits
        # on document.fonts.ready; wait for fonts to settle, then a short beat.
        try:
            page.wait_for_function(
                "document.fonts && document.fonts.status === 'loaded'", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(500)

        # Programmatic fault checks.
        facts["katex_present"] = page.evaluate("() => !!window.katex")
        facts["math_nodes"] = page.locator(".katex").count()
        facts["math_errors"] = page.locator(".katex-error").count()
        facts["math_error_text"] = page.eval_on_selector_all(
            ".katex-error", "els => els.map(e => e.textContent.trim())")
        # KaTeX records the parse error in the title attribute of the
        # .katex-error span; capture it -- more informative than the raw source.
        facts["math_error_msgs"] = page.eval_on_selector_all(
            ".katex-error",
            "els => [...new Set(els.map(e => e.getAttribute('title') || ''))]")
        facts["images"] = page.locator("img").count()
        facts["broken_images"] = page.eval_on_selector_all(
            "img", "els => els.filter(e => !e.complete || e.naturalWidth === 0)"
                   ".map(e => e.getAttribute('src'))")
        body_text = page.evaluate("() => document.body.innerText")
        facts["leaks"] = {}
        for label, pat in LEAK_PATTERNS.items():
            hits = sorted(set(re.findall(pat, body_text)))
            if hits:
                facts["leaks"][label] = hits

        # Horizontal-overflow check. A fixed-width render cannot see a page
        # that overflows on a narrow window (a long URL with no break, a wide
        # table not in a scroll container, an unfit display equation). Resize
        # to several widths and measure the document's own horizontal overflow.
        facts["overflow"] = []
        for vw in (1280, 768, 414):
            page.set_viewport_size({"width": vw, "height": 1000})
            page.wait_for_timeout(500)
            facts["overflow"].append((vw, page.evaluate(
                "() => document.documentElement.scrollWidth"
                " - document.documentElement.clientWidth")))
        page.set_viewport_size({"width": VIEW_W, "height": SLICE_H})
        page.wait_for_timeout(300)

        # Sliced screenshots, top to bottom.
        total = page.evaluate("() => document.body.scrollHeight")
        n = max(1, math.ceil(total / SLICE_H))
        for k in range(n):
            page.evaluate(f"window.scrollTo(0, {k * SLICE_H})")
            page.wait_for_timeout(150)
            dest = outdir / f"html-{k + 1:02d}.png"
            page.screenshot(path=str(dest))
            shots.append(dest)

        # Per-element screenshots for focused per-element verification.
        # Every figure, table, code listing and algorithm is wrapped in a
        # <figure>; shoot each one whole -- caption, body and any notes.
        # Widen the viewport and lift the page width cap and overflow
        # clipping first, so a wide table or long code line is captured in
        # full rather than scroll-clipped.
        page.set_viewport_size({"width": 1980, "height": 1400})
        page.add_style_tag(content=("body{max-width:1850px!important}"
                                    "figure [style*=overflow],pre{overflow:visible!important}"))
        page.wait_for_timeout(200)
        elements: list[dict] = []
        figs = page.locator("figure")
        for k in range(figs.count()):
            el = figs.nth(k)
            dest = outdir / f"elem-{k + 1:02d}.png"
            try:
                el.scroll_into_view_if_needed(timeout=5000)
                el.screenshot(path=str(dest))
            except Exception as e:
                facts.setdefault("element_errors", []).append(f"{dest.name}: {e}")
                continue
            info = el.evaluate(
                "el => {"
                "  const c = el.querySelector('figcaption') || el.querySelector('.algo-head');"
                "  const kind = el.classList.contains('table') ? 'table'"
                "    : el.classList.contains('algorithm') ? 'algorithm'"
                "    : (el.querySelector('pre') ? 'code' : 'figure');"
                "  return {id: el.id || '', kind: kind,"
                "    label: ((c && c.innerText) || '').replace(/\\s+/g, ' ').trim().slice(0, 90)};"
                "}")
            elements.append({"file": dest.name, **info})
        facts["elements"] = elements
        browser.close()
    return shots, facts


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    paper = Path(sys.argv[1]).resolve()
    html = paper / "paper.html"
    pdf = paper / "paper.pdf"
    if not html.is_file():
        print(f"error: {html} not found -- run tex-to-html first", file=sys.stderr)
        return 2

    outdir = paper / "verify_shots"
    if outdir.exists():
        shutil.rmtree(outdir)
    outdir.mkdir()

    html_shots, facts = render_html(html, outdir)
    pdf_shots = render_pdf(pdf, outdir)

    print(f"HTML render : {len(html_shots)} slice(s) -> {outdir}/html-*.png")
    print(f"PDF  render : {len(pdf_shots)} page(s)  -> {outdir}/pdf-*.png")
    print()
    print(f"math nodes rendered : {facts['math_nodes']}")
    em = facts["math_errors"]
    # "0 errors" is only meaningful once math has actually rendered. If KaTeX
    # never loaded, or loaded but typeset nothing, that is itself broken math --
    # never report it as OK.
    if not facts.get("katex_present"):
        print("math render errors  : KaTeX NOT detected on page -- "
              "math did not render  [BROKEN -- inspect]")
    elif facts["math_nodes"] == 0:
        print("math render errors  : KaTeX present but 0 .katex nodes typeset  "
              "[BROKEN -- inspect]")
    else:
        print(f"math render errors  : {em}  "
              f"[{'OK' if em == 0 else 'BROKEN MATH -- inspect'}]")
    for t in facts["math_error_text"][:10]:
        print(f"    merror text : {t!r}")
    for m in facts.get("math_error_msgs", [])[:10]:
        print(f"    error cause : {m}")
    bi = facts["broken_images"]
    print(f"images              : {facts['images']}  broken: "
          f"{len(bi)}  [{'OK' if not bi else 'MISSING IMAGES -- inspect'}]")
    for src in bi[:10]:
        print(f"    {src}")

    if facts["leaks"]:
        print("\nraw-LaTeX leak candidates (review -- some may be legitimate, "
              "e.g. $ in code, URLs):")
        for label, hits in facts["leaks"].items():
            print(f"  {label}: {len(hits)} -> {', '.join(hits[:15])}")
    else:
        print("\nraw-LaTeX leak candidates: none")

    print("\nhorizontal overflow (document width beyond the window):")
    for vw, diff in facts.get("overflow", []):
        tag = "OK" if diff <= 0 else "OVERFLOW -- inspect"
        print(f"  viewport {vw:>5} px : {diff:+d} px  [{tag}]")

    els = facts.get("elements", [])
    print(f"\nper-element shots   : {len(els)}  -> {outdir}/elem-*.png")
    for e in els:
        print(f"  {e['file']}  [{e['kind']:9}] {(e['id'] or '-'):24} {e['label']}")
    for err in facts.get("element_errors", []):
        print(f"  ! {err}")

    print(f"\nNext: Claude (a) reads {outdir}/html-*.png + pdf-*.png for the "
          f"top-to-bottom walk, then (b) dispatches a sub-agent per "
          f"{outdir}/elem-*.png for per-element scrutiny (see SKILL.md Phase B).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
