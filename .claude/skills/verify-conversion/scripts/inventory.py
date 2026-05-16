#!/usr/bin/env python3
r"""inventory.py -- Phase A (source-coverage) helper for verify-conversion.

Extracts a structural inventory from the LaTeX source (expanded.tex, else
flattened.tex, else the main file in source/) and from paper.html, then diffs
them so Claude can see what the conversion may have dropped or garbled.

It reports; it does not judge. Counts are a cheap first pass. The *named*
lists -- section titles, captions, bib keys -- catch typos and reordering
that counts miss. Claude reviews every flag.

Commented-out LaTeX (a `\section` etc. behind a `%`) is NOT counted -- only
live source. The number of comments is reported separately.

Usage: inventory.py <paper-dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EQ_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath")


def read_group(s: str, i: int):
    """s[i] == '{'. Return (inner, end) with brace balance; escapes skipped."""
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    return None


def grouped(text: str, command: str):
    """Yield the brace-balanced {arg} of every \\command[opt]{arg} occurrence."""
    for m in re.finditer(r"\\" + command + r"\*?\s*", text):
        i = m.end()
        while i < len(text) and text[i] == "[":   # skip optional [..] args
            close = text.find("]", i)
            if close < 0:
                break
            i = close + 1
            while i < len(text) and text[i] in " \t\n":
                i += 1
        if i < len(text) and text[i] == "{":
            g = read_group(text, i)
            if g:
                yield g[0]


def count_comments(tex: str) -> int:
    return sum(1 for i, ch in enumerate(tex)
               if ch == "%" and (i == 0 or tex[i - 1] != "\\"))


def strip_comments(tex: str) -> str:
    """Cut each line at its first unescaped % so commented LaTeX is ignored."""
    out = []
    for line in tex.split("\n"):
        cut = None
        for i, ch in enumerate(line):
            if ch == "%" and (i == 0 or line[i - 1] != "\\"):
                cut = i
                break
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def strip_latex(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)               # html tags
    s = re.sub(r"\\[a-zA-Z]+\*?\s?", " ", s)     # control words
    return s.replace("{", " ").replace("}", " ").replace("~", " ")


def normalize(s: str) -> str:
    """Canonical form for comparing a title/caption across tex and html."""
    s = strip_latex(s)
    s = re.sub(r"&[a-zA-Z#0-9]+;", " ", s)                       # html entities
    s = re.sub(r"^\s*(figure|table)\s*\d+\s*[:.]\s*", "", s, flags=re.I)
    s = re.sub(r"^\s*\d+(\.\d+)*\.?\s*", "", s)                  # section number
    s = re.sub(r"[—–]", "-", s)                        # em/en dash
    s = re.sub(r"[\"'`‘’“”]", "", s)         # quotes
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.rstrip(" .,;:")                           # trailing punctuation


def inventory_tex(raw: str) -> dict:
    comments = count_comments(raw)
    tex = strip_comments(raw)   # all extraction below sees live source only
    sections = [normalize(t) for t in grouped(tex, "(?:sub)?(?:sub)?section")]
    captions = [normalize(t) for t in grouped(tex, "caption")]
    bib_keys = sorted({m.group(1) for m in
                       re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", tex)})
    graphics = [Path(p.strip()).stem for p in grouped(tex, "includegraphics")]
    cites: set[str] = set()
    for m in re.finditer(r"\\[cC]ite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}", tex):
        cites.update(k.strip() for k in m.group(1).split(","))
    eqs = sum(len(re.findall(r"\\begin\{" + e + r"\*?\}", tex)) for e in EQ_ENVS)
    eqs += tex.count(r"\[") + tex.count("$$") // 2
    return {"sections": sections, "captions": captions, "bib_keys": bib_keys,
            "graphics": sorted(graphics), "n_graphics": len(graphics),
            "cites": sorted(cites), "equations": eqs, "comments": comments}


def inventory_html(html: str) -> dict:
    # Drop <script>/<style> first: the math-render script can contain literal
    # "$$" / "\[" delimiters that must not be miscounted as document equations.
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    sections = [normalize(m.group(1))
                for m in re.finditer(r"<h[234][^>]*>(.*?)</h[234]>", html, re.S)]
    captions = [normalize(m.group(1))
                for m in re.finditer(r"<figcaption[^>]*>(.*?)</figcaption>", html, re.S)]
    bib_keys = sorted({m.group(1) for m in
                       re.finditer(r'<li[^>]*id="ref-([^"]+)"', html)})
    graphics = [Path(m.group(1)).stem
                for m in re.finditer(r'<img[^>]*src="([^"]+)"', html)]
    cites = sorted({m.group(1) for m in re.finditer(r'href="#ref-([^"]+)"', html)})
    # tex-to-html wraps each display equation in <div class="equation">; count those.
    eqs = len(re.findall(r'<div class="equation"', html))
    if not eqs:                                  # fallback for other conventions
        eqs = html.count("$$") // 2 + html.count(r"\[")
    return {"sections": sections, "captions": captions, "bib_keys": bib_keys,
            "graphics": sorted(graphics), "n_graphics": len(graphics),
            "cites": cites, "equations": eqs, "comments": html.count("<!--")}


def diff_named(label: str, tex: list, html: list) -> list[str]:
    ts, hs = set(tex), set(html)
    miss = [x for x in tex if x not in hs]
    extra = [x for x in html if x not in ts]
    out = [f"{label}: tex={len(tex)}  html={len(html)}"]
    if miss:
        out.append(f"  MISSING in html ({len(miss)}): " + " | ".join(miss[:10]))
    if extra:
        out.append(f"  EXTRA in html ({len(extra)}): " + " | ".join(extra[:10]))
    if not miss and not extra:
        out.append("  OK" if tex == html else "  same set, ORDER differs -- check sequence")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    paper = Path(sys.argv[1]).resolve()
    html_path = paper / "paper.html"
    if not html_path.is_file():
        print(f"error: {html_path} not found -- run tex-to-html first", file=sys.stderr)
        return 2
    tex_path = next((paper / n for n in ("expanded.tex", "flattened.tex")
                     if (paper / n).is_file()), None)
    if tex_path is None:
        mains = [f for f in (paper / "source").glob("*.tex")
                 if "\\documentclass" in f.read_text("utf-8", "replace")]
        if not mains:
            print("error: no LaTeX source found", file=sys.stderr)
            return 2
        tex_path = mains[0]

    tex = inventory_tex(tex_path.read_text("utf-8", "replace"))
    html = inventory_html(html_path.read_text("utf-8", "replace"))

    print(f"source : {tex_path.name}")
    print(f"html   : {html_path.name}\n")

    lines: list[str] = []
    lines += diff_named("section headings", tex["sections"], html["sections"])
    lines.append("  (note: 'abstract' / 'references' legitimately appear only in html)")
    lines += diff_named("captions (figures + tables)", tex["captions"], html["captions"])
    lines += diff_named("bibliography keys", tex["bib_keys"], html["bib_keys"])

    fmark = "OK" if tex["n_graphics"] == html["n_graphics"] else "DIFFERS -- review"
    lines.append(f"figures (\\includegraphics vs <img>): "
                 f"tex={tex['n_graphics']}  html={html['n_graphics']}  [{fmark}]")

    unresolved = [c for c in tex["cites"] if c not in set(tex["bib_keys"])]
    cited_missing = [c for c in tex["cites"] if c not in set(html["cites"])]
    lines.append(f"citations: {len(tex['cites'])} distinct keys cited")
    if unresolved:
        lines.append("  UNRESOLVED (no \\bibitem): " + " | ".join(unresolved[:10]))
    if cited_missing:
        lines.append("  cited in tex, no link in html: " + " | ".join(cited_missing[:10]))
    if not unresolved and not cited_missing:
        lines.append("  OK")

    for label, key in (("display equations", "equations"), ("comments", "comments")):
        t, h = tex[key], html[key]
        lines.append(f"{label}: tex={t}  html={h}  [{'OK' if t == h else 'DIFFERS -- review'}]")

    print("\n".join(lines))
    (paper / "verify_inventory.json").write_text(
        json.dumps({"tex": tex, "html": html}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"\nfull inventory -> {paper/'verify_inventory.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
