#!/usr/bin/env python3
r"""apply_expansion.py -- mechanical expansion stage for the expand-latex skill.

Reads <paper-dir>/flattened.tex and <paper-dir>/macros.json (whose `suggested`
field carries Claude's finalized expand / drop / leave decision) and writes
<paper-dir>/expanded.tex.

This script performs ONLY mechanical work: brace-balanced argument capture,
fixpoint resolution of nested macros, cycle detection, and substitution. It
makes no classification decisions -- those are Claude's, encoded in macros.json.
Anything it cannot safely auto-expand (optional-argument macros, environments,
cycles, malformed call sites) it demotes to `leave` and reports, rather than
guessing. So a surviving `\macro` is always a deliberate, reported outcome.

Usage: apply_expansion.py <paper-dir>
"""
from __future__ import annotations

import json
import string
import sys
from pathlib import Path

LETTERS = set(string.ascii_letters)


def skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def read_group(s: str, i: int):
    """s[i] == '{'. Return (inner, end_after_close) with brace balance; escapes skipped."""
    depth = 0
    j = i
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


def read_arg(s: str, i: int):
    """Read one TeX argument: a {group}, a \\control-sequence, or a single char."""
    i = skip_ws(s, i)
    if i >= len(s):
        return None
    if s[i] == "{":
        g = read_group(s, i)
        return None if g is None else g
    if s[i] == "\\":
        j = i + 1
        if j < len(s) and s[j] in LETTERS:
            while j < len(s) and s[j] in LETTERS:
                j += 1
        else:
            j = min(j + 1, len(s))
        return s[i:j], j
    return s[i], i + 1


def read_args(s: str, i: int, nargs: int):
    """Capture nargs arguments starting at i. Return (args, end) or None."""
    args = []
    for _ in range(nargs):
        r = read_arg(s, i)
        if r is None:
            return None
        args.append(r[0])
        i = r[1]
    return args, i


def substitute_params(body: str, args: list[str]) -> str:
    """Replace #1..#9 in a macro body with args; ## becomes a literal #."""
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "#" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "#":
                out.append("#")
                i += 2
                continue
            if nxt in "123456789":
                idx = int(nxt) - 1
                out.append(args[idx] if idx < len(args) else "")
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


class Cycle(Exception):
    def __init__(self, name: str):
        self.name = name


class Expander:
    def __init__(self, macros: list[dict]):
        self.meta = {m["name"]: m for m in macros}
        self.warnings: list[str] = []
        self.expand: set[str] = set()
        self.drop: set[str] = set()
        # Finalize decisions, with mechanical safety demotions.
        for m in macros:
            name, d = m["name"], m.get("suggested", "leave")
            if d == "expand":
                kind = m.get("kind", "")
                if kind.startswith(("newenvironment", "renewenvironment")):
                    self.warnings.append(f"\\{name}: environment cannot be auto-expanded -- left")
                    d = "leave"
                elif m.get("optional_default") is not None:
                    self.warnings.append(f"\\{name}: optional-argument macro -- left (expand by hand if needed)")
                    d = "leave"
            if d == "expand":
                self.expand.add(name)
            elif d == "drop":
                self.drop.add(name)
        self.resolved: dict[str, str] = {}
        self.expand_sites = 0
        self.drop_sites = 0

    def resolve(self, name: str, stack: frozenset) -> str:
        """Return name's body with all nested expand/drop macros applied (fixpoint)."""
        if name in self.resolved:
            return self.resolved[name]
        if name in stack:
            raise Cycle(name)
        body = self.meta[name].get("body", "")
        out = self.transform(body, stack | {name}, comment_aware=False, count=False)
        self.resolved[name] = out
        return out

    def resolve_all(self) -> None:
        """Resolve every expand macro's body; break cycles by demoting to leave."""
        while True:
            try:
                # Iterate in catalog order (not set order) so that, when a
                # cycle must be broken, the demoted macro is deterministic.
                for name in self.meta:
                    if name in self.expand:
                        self.resolve(name, frozenset())
                return
            except Cycle as cyc:
                self.warnings.append(f"\\{cyc.name}: macro cycle detected -- demoted to leave")
                self.expand.discard(cyc.name)
                self.resolved.clear()

    def transform(self, text: str, stack: frozenset, comment_aware: bool,
                  count: bool = True) -> str:
        out: list[str] = []
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if comment_aware and c == "%":
                eol = text.find("\n", i)
                eol = n if eol < 0 else eol + 1
                out.append(text[i:eol])
                i = eol
                continue
            if c == "\\":
                j = i + 1
                if j < n and text[j] in LETTERS:
                    while j < n and text[j] in LETTERS:
                        j += 1
                    name = text[i + 1:j]
                else:
                    # control symbol (\%, \{, \\, ...) -- emit as one unit
                    out.append(text[i:min(j + 1, n)])
                    i = min(j + 1, n)
                    continue
                if name in self.expand:
                    nargs = self.meta[name].get("nargs", 0) or 0
                    r = read_args(text, j, nargs)
                    if r is None:
                        self.warnings.append(
                            f"\\{name}: could not capture {nargs} argument(s) at one "
                            f"call site -- left as-is")
                        out.append(text[i:j])
                        i = j
                        continue
                    args, end = r
                    # An argument may itself contain macros (\f{\g{x}}); expand
                    # it before substituting into the (already-resolved) body.
                    args = [self.transform(a, stack, comment_aware=False, count=count)
                            for a in args]
                    out.append(substitute_params(self.resolve(name, stack), args))
                    if count:
                        self.expand_sites += 1
                    i = end
                    continue
                if name in self.drop:
                    nargs = self.meta[name].get("nargs", 0) or 0
                    r = read_args(text, j, nargs)
                    end = r[1] if r else j
                    if count:
                        self.drop_sites += 1
                    i = end
                    continue
                # leave macro, or not a custom macro -- emit verbatim
                out.append(text[i:j])
                i = j
                continue
            out.append(c)
            i += 1
        return "".join(out)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    paper = Path(sys.argv[1]).resolve()
    flat_path = paper / "flattened.tex"
    cat_path = paper / "macros.json"
    if not flat_path.is_file() or not cat_path.is_file():
        print(f"error: run flatten_and_catalog.py first -- need {flat_path} and {cat_path}",
              file=sys.stderr)
        return 2

    text = flat_path.read_text(encoding="utf-8")
    macros = json.loads(cat_path.read_text(encoding="utf-8")).get("macros", [])

    exp = Expander(macros)
    exp.resolve_all()

    # Remove the definitions of every expand/drop macro (final sets, post-cycle).
    for m in macros:
        if m["name"] in exp.expand or m["name"] in exp.drop:
            raw = m.get("raw", "")
            if raw and raw in text:
                text = text.replace(raw, "", 1)
            elif raw:
                exp.warnings.append(f"\\{m['name']}: definition not found in source to remove")

    result = exp.transform(text, frozenset(), comment_aware=True)
    (paper / "expanded.tex").write_text(result, encoding="utf-8")

    leave = [m["name"] for m in macros
             if m["name"] not in exp.expand and m["name"] not in exp.drop]
    print(f"expanded.tex written ({len(result)} chars)")
    print(f"  expand : {len(exp.expand)} macros -> {exp.expand_sites} call sites substituted")
    print(f"  drop   : {len(exp.drop)} macros -> {exp.drop_sites} call sites removed")
    print(f"  leave  : {len(leave)} macros untouched" + (f" ({', '.join(leave)})" if leave else ""))
    for w in exp.warnings:
        print(f"  ! {w}")

    # Self-check: no live expand/drop token should survive (comments excepted).
    stray = []
    for name in sorted(exp.expand | exp.drop):
        token = "\\" + name
        i, hits = 0, 0
        while True:
            i = result.find(token, i)
            if i < 0:
                break
            after = i + len(token)
            if after >= len(result) or result[after] not in LETTERS:
                line_start = result.rfind("\n", 0, i) + 1
                if "%" not in result[line_start:i]:
                    hits += 1
            i = after
        if hits:
            stray.append(f"{name}({hits})")
    print(f"  verify : {'OK -- no live macro tokens remain' if not stray else 'STRAY: ' + ', '.join(stray)}")
    return 0 if not stray else 1


if __name__ == "__main__":
    sys.exit(main())
