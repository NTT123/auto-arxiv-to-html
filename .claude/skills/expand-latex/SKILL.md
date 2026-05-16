---
name: expand-latex
description: Flatten a downloaded arXiv paper's multi-file LaTeX source into one file and expand its custom macros (\newcommand / \def aliases) into plain LaTeX, producing a clean expanded.tex for tex-to-html. Use after download-arxiv and before tex-to-html, or whenever the user asks to expand macros, flatten, de-alias, or normalize LaTeX source.
---

# expand-latex

Turn a paper's messy multi-file, macro-laden LaTeX source into one self-contained `expanded.tex` with the author's custom macros expanded away, so [[tex-to-html]] never has to guess what a `\newcommand` means.

Pipeline position: [[download-arxiv]] → **expand-latex** → [[tex-to-html]].

## How it works

Two mechanical scripts bracket one judgement step — yours:

1. `flatten_and_catalog.py` flattens every `\input`/`\include`/`\subfile` and the `.bbl` into one file, and catalogues every macro definition with a **suggested** classification.
2. **You classify.** Review the catalogue; finalise each macro as `expand`, `drop`, or `leave`. This is the one judgement step, and it is the whole point of the skill.
3. `apply_expansion.py` applies your classification mechanically: brace-balanced argument capture, fixpoint resolution of nested macros, cycle detection, comment-safe substitution.

You are the expander: every decision about what a macro means and whether expanding it is safe is yours. The scripts do only deterministic text surgery — `apply_expansion.py` expands nothing you did not mark `expand`, and anything it cannot safely handle (cycles, optional-argument macros, environments, malformed call sites) it demotes back to `leave` and reports. A surviving `\macro` is therefore always a deliberate, reported outcome.

## Inputs and outputs

Input: `workspace/<id>/source/` (from `download-arxiv`).

Artifacts, all in `workspace/<id>/`:
- `flattened.tex` — single-file source, macros not yet expanded (written by step 1)
- `macros.json` — macro catalogue with classifications (written by step 1, edited by you in step 2)
- `expanded.tex` — **the deliverable**: flattened source with macros expanded (written by step 3)

Figures and other assets stay in `source/`; this skill only rewrites `.tex` text. `tex-to-html` resolves figure paths against `source/`.

## Macro classification

`macros.json` gives every macro a `suggested` value. That one field is dual-purpose: the helper writes it as its recommendation, and `apply_expansion.py` then reads the same field as the **final decision** — so editing `suggested` *is* how you decide.

- **`expand`** — a plain alias (`\dmodel`, `\mc[1]{\mathcal{#1}}`). Its uses become its body. Most macros.
- **`drop`** — editorial (`\todo`, `\note`) or defined-but-unused. Definition removed; any live uses removed together with their arguments.
- **`leave`** — recursive, conditional/low-level, an environment, a delimited `\def`, an optional-argument macro, or a redefinition of a LaTeX internal. Definition and uses kept verbatim.

The pre-suggestions are conservative and usually correct — **default to trusting them**. The judgement you add is catching the exceptions: an editorial macro the name heuristic missed, or a `leave`-flagged macro that is in fact a safe alias worth promoting to `expand`. When genuinely unsure, choose `leave` — a surviving `\macro` is a safe, reported outcome (`tex-to-html` has a fallback); a wrongly-expanded one silently corrupts the paper.

## Playbook

### 1. Flatten and catalogue

```bash
python .claude/skills/expand-latex/scripts/flatten_and_catalog.py workspace/<id>
```

Writes `flattened.tex` + `macros.json` and prints a per-macro summary.

### 2. Review and finalise the classification

Read `macros.json`. For each macro, accept or correct its `suggested` value (see *Macro classification*). To override, edit the `suggested` string in `macros.json`. If every suggestion looks right — the common case — change nothing.

> Re-running step 1 regenerates `macros.json` from scratch and discards any edits made here. If you only need to change classifications, go straight to step 3.

### 3. Apply

```bash
python .claude/skills/expand-latex/scripts/apply_expansion.py workspace/<id>
```

Reads `flattened.tex` + `macros.json`, writes `expanded.tex`. It resolves nested macros to a fixpoint, captures brace-balanced arguments, detects cycles, and never alters comment text. It prints a report: how many macros and call sites were expanded / dropped / left, any safety demotions, and a `verify:` line.

### 4. Verify and report

- The script's `verify:` line must read `OK`. If it reports `STRAY: …`, a macro was misclassified — correct `macros.json` and re-run step 3. **Never hand-edit `expanded.tex`** to patch an expansion; fix the classification and re-run.
- Read the warnings. Each safety demotion (cycle, optional-arg, environment) is expected behaviour — relay it to the user, don't treat it as an error.
- Spot-check one expanded site in `expanded.tex` (e.g. `$\dmodel$` is now `$d_{\text{model}}$`).

Report to the user: the expanded / dropped / left counts, any reclassifications or demotions, and the path to `expanded.tex`.

## Scope discipline

This skill only de-aliases LaTeX — it does not convert to HTML, touch figures, or restyle anything (that is `tex-to-html` and later skills). If `macros.json` lists no macros, `apply_expansion.py` still writes `expanded.tex` (a faithful copy of `flattened.tex`) so the pipeline always has a consistent artifact.
