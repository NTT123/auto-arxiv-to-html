#!/usr/bin/env python3
r"""flatten_and_catalog.py -- mechanical prep for the expand-latex skill.

Given a downloaded arXiv paper dir (workspace/<id>/ with a source/ subdir),
this produces two artifacts and NOTHING ELSE -- it never expands a macro:

  flattened.tex : the main .tex with every \input/\include/\subfile and the
                  .bbl bibliography inlined, so the document is one file.
  macros.json   : a catalogue of every macro definition found, each with a
                  *suggested* classification (expand / drop / leave). Claude
                  reviews these; pre-classifying here keeps runs deterministic.

Macro expansion itself is Claude's job -- see SKILL.md.

Usage: flatten_and_catalog.py <paper-dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ@"

# renewcommand of one of these is a layout *setting*, not an alias -> leave.
LATEX_INTERNALS = {
    "baselinestretch", "arraystretch", "thefootnote", "thesection",
    "thesubsection", "theequation", "thetable", "thefigure", "labelitemi",
    "labelitemii", "familydefault", "rmdefault", "sfdefault", "ttdefault",
    "abstractname", "refname", "bibname", "contentsname", "figurename",
    "tablename", "today", "and", "thanks",
}
EDITORIAL = {"todo", "fixme", "note", "comment", "draft", "xxx", "change",
             "revise", "tocheck", "highlight", "hl", "marginnote"}
LOWLEVEL = (r"\if", r"\else", r"\fi", r"\loop", r"\repeat", r"\csname",
            r"\expandafter", r"\noexpand", r"\@ifnextchar", r"\newif", r"\let")


# --- comment-aware scanning -------------------------------------------------

def is_commented(text: str, pos: int) -> bool:
    """True if pos sits after an unescaped % on its own line."""
    start = text.rfind("\n", 0, pos) + 1
    i = start
    while i < pos:
        if text[i] == "%" and (i == start or text[i - 1] != "\\"):
            return True
        i += 1
    return False


def skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def read_group(text: str, i: int):
    """text[i] == '{'. Return (inner, index_after_close) with brace balance."""
    depth = 0
    j = i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return None


def read_optarg(text: str, i: int):
    """text[i] == '['. Return (inner, index_after_']') tracking brace depth."""
    depth = 0
    j = i + 1
    while j < len(text):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "]" and depth == 0:
            return text[i + 1:j], j + 1
        j += 1
    return None


# --- flattening -------------------------------------------------------------

INCLUDE_RE = re.compile(r"\\(input|include|subfile)\b\s*(?:\{([^}]*)\}|([^\s{}\\%]+))")


def resolve(name: str, base: Path):
    name = name.strip()
    for cand in (base / name, base / (name + ".tex")):
        if cand.is_file():
            return cand
    return None


def flatten(main: Path, base: Path, seen=None, depth=0) -> str:
    if seen is None:
        seen = set()
    main = main.resolve()
    if main in seen or depth > 30:
        return ""  # cycle / runaway guard
    seen.add(main)
    text = main.read_text(encoding="utf-8", errors="replace")
    out, last = [], 0
    for m in INCLUDE_RE.finditer(text):
        if is_commented(text, m.start()):
            continue
        target = m.group(2) or m.group(3)
        f = resolve(target, base)
        out.append(text[last:m.start()])
        if f:
            out.append(f"% >>> inlined {f.name}\n")
            out.append(flatten(f, base, seen, depth + 1))
            out.append(f"\n% <<< end {f.name}\n")
        else:
            out.append(m.group(0))  # unresolved -- leave verbatim
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def inline_bbl(text: str, base: Path) -> str:
    m = re.search(r"\\bibliography\s*\{[^}]*\}", text)
    if not m or is_commented(text, m.start()):
        return text
    bbls = sorted(base.glob("*.bbl"))
    if not bbls:
        return text
    bbl = bbls[0].read_text(encoding="utf-8", errors="replace")
    return text[:m.start()] + f"% >>> inlined {bbls[0].name}\n" + bbl + text[m.end():]


# --- macro extraction -------------------------------------------------------

DEF_RE = re.compile(
    r"\\(newcommand|renewcommand|providecommand|DeclareMathOperator"
    r"|newenvironment|renewenvironment|def)\b")


def read_cmd_name(text: str, i: int):
    """Read a macro name written as {\\name} or bare \\name. Return (name, end)."""
    i = skip_ws(text, i)
    if i < len(text) and text[i] == "{":
        g = read_group(text, i)
        if g and g[0].strip().startswith("\\"):
            return g[0].strip()[1:], g[1]
        return None
    if i < len(text) and text[i] == "\\":
        j = i + 1
        while j < len(text) and text[j] in LETTERS:
            j += 1
        if j > i + 1:
            return text[i + 1:j], j
    return None


def extract_macros(text: str):
    """Return a list of macro dicts parsed from the flattened source."""
    macros = []
    for m in DEF_RE.finditer(text):
        if is_commented(text, m.start()):
            continue
        kind = m.group(1)
        j = m.end()
        star = j < len(text) and text[j] == "*"
        if star:
            j += 1
        j = skip_ws(text, j)
        rec = {"kind": kind, "star": star, "def_start": m.start()}

        if kind == "def":
            # \def\name<paramtext>{body}
            if j >= len(text) or text[j] != "\\":
                continue
            k = j + 1
            while k < len(text) and text[k] in LETTERS:
                k += 1
            rec["name"] = text[j + 1:k]
            params = text[k:text.find("{", k)] if "{" in text[k:] else ""
            body_g = read_group(text, text.find("{", k))
            if not body_g or not rec["name"]:
                continue
            rec["body"] = body_g[0]
            simple = re.fullmatch(r"(#\d)*", params.strip())
            rec["nargs"] = len(re.findall(r"#\d", params))
            rec["def_complex"] = not simple
            rec["raw"] = text[m.start():body_g[1]]

        elif kind in ("newenvironment", "renewenvironment"):
            name_g = read_group(text, j) if j < len(text) and text[j] == "{" else None
            if not name_g:
                continue
            rec["name"] = name_g[0].strip()
            j = skip_ws(text, name_g[1])
            nargs, default = 0, None
            if j < len(text) and text[j] == "[":
                o = read_optarg(text, j)
                if o:
                    nargs = int(o[0]) if o[0].strip().isdigit() else 0
                    j = skip_ws(text, o[1])
            if j < len(text) and text[j] == "[":
                o = read_optarg(text, j)
                if o:
                    default = o[0]
                    j = skip_ws(text, o[1])
            beg = read_group(text, j) if j < len(text) and text[j] == "{" else None
            if not beg:
                continue
            end = read_group(text, skip_ws(text, beg[1]))
            rec["nargs"] = nargs
            rec["optional_default"] = default
            rec["body"] = beg[0]
            rec["end_body"] = end[0] if end else ""
            rec["raw"] = text[m.start():(end[1] if end else beg[1])]

        else:  # newcommand / renewcommand / providecommand / DeclareMathOperator
            nm = read_cmd_name(text, j)
            if not nm:
                continue
            rec["name"], j = nm[0], skip_ws(text, nm[1])
            if kind == "DeclareMathOperator":
                op = read_group(text, j) if j < len(text) and text[j] == "{" else None
                if not op:
                    continue
                rec["nargs"] = 0
                rec["optional_default"] = None
                star_op = "*" if star else ""
                rec["body"] = f"\\operatorname{star_op}{{{op[0]}}}"
                rec["raw"] = text[m.start():op[1]]
            else:
                nargs, default = 0, None
                if j < len(text) and text[j] == "[":
                    o = read_optarg(text, j)
                    if o:
                        nargs = int(o[0]) if o[0].strip().isdigit() else 0
                        j = skip_ws(text, o[1])
                if j < len(text) and text[j] == "[":
                    o = read_optarg(text, j)
                    if o:
                        default = o[0]
                        j = skip_ws(text, o[1])
                body = read_group(text, j) if j < len(text) and text[j] == "{" else None
                if not body:
                    continue
                rec["nargs"] = nargs
                rec["optional_default"] = default
                rec["body"] = body[0]
                rec["raw"] = text[m.start():body[1]]
        macros.append(rec)
    return macros


def classify(rec: dict, usage: int):
    """Return (suggested, reason). Conservative; Claude may override."""
    name, kind = rec["name"], rec["kind"]
    body = rec.get("body", "") + rec.get("end_body", "")
    if usage == 0:
        return "drop", "defined but never used"
    if name.lower() in EDITORIAL:
        return "drop", "editorial macro (not reader-facing)"
    if re.search(r"\\" + re.escape(name) + r"(?![a-zA-Z])", body):
        return "leave", "recursive (body references itself)"
    if any(tok in body for tok in LOWLEVEL):
        return "leave", "conditional / low-level TeX in body"
    if kind in ("renewcommand", "providecommand") and name in LATEX_INTERNALS:
        return "leave", "redefines a LaTeX internal (a setting, not an alias)"
    if kind == "def" and rec.get("def_complex"):
        return "leave", "\\def with delimited parameters"
    if rec.get("optional_default") is not None:
        return "leave", "optional-argument default -- expand only if confident"
    if kind in ("newenvironment", "renewenvironment"):
        return "leave", "environment -- expand by hand only if simple"
    return "expand", "simple alias"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    paper = Path(sys.argv[1]).resolve()
    source = paper / "source"
    if not source.is_dir():
        print(f"error: {source} not found", file=sys.stderr)
        return 2

    mains = [f for f in source.glob("*.tex")
             if "\\documentclass" in f.read_text("utf-8", "replace")]
    if not mains:
        print("error: no .tex with \\documentclass in source/", file=sys.stderr)
        return 2
    main_tex = next((f for f in mains
                     if "\\begin{document}" in f.read_text("utf-8", "replace")),
                    mains[0])

    flat = inline_bbl(flatten(main_tex, source), source)
    (paper / "flattened.tex").write_text(flat, encoding="utf-8")

    macros = extract_macros(flat)
    catalog = []
    for rec in macros:
        name = rec["name"]
        if rec["kind"] in ("newenvironment", "renewenvironment"):
            pat = r"\\begin\{" + re.escape(name) + r"\}"
        else:
            pat = r"\\" + re.escape(name) + r"(?![a-zA-Z])"
        # Count only occurrences in live source -- commented-out uses don't
        # count -- then drop one for the definition's own name token.
        live = [mm.start() for mm in re.finditer(pat, flat)
                if not is_commented(flat, mm.start())]
        usage = max(0, len(live) - 1)
        suggested, reason = classify(rec, usage)
        entry = {
            "name": name,
            "kind": rec["kind"] + ("*" if rec.get("star") else ""),
            "nargs": rec.get("nargs", 0),
            "optional_default": rec.get("optional_default"),
            "body": rec.get("body", ""),
            "usage_count": usage,
            "suggested": suggested,
            "reason": reason,
            "raw": rec.get("raw", ""),
        }
        if "end_body" in rec:
            entry["end_body"] = rec["end_body"]
        catalog.append(entry)

    (paper / "macros.json").write_text(
        json.dumps({"main_tex": main_tex.name,
                    "flattened_chars": len(flat),
                    "macros": catalog}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    by = {"expand": 0, "drop": 0, "leave": 0}
    for e in catalog:
        by[e["suggested"]] += 1
    print(f"main .tex   : {main_tex.name}")
    print(f"flattened   : {paper/'flattened.tex'}  ({len(flat)} chars)")
    print(f"macros.json : {paper/'macros.json'}  ({len(catalog)} definitions)")
    print(f"suggested   : {by['expand']} expand, {by['drop']} drop, {by['leave']} leave")
    for e in catalog:
        print(f"  [{e['suggested']:6}] \\{e['name']}  "
              f"({e['kind']}, {e['nargs']} args, used {e['usage_count']}x) "
              f"-- {e['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
