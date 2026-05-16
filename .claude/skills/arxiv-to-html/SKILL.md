---
name: arxiv-to-html
description: End-to-end orchestrator that converts an arXiv paper into a faithful, verified, well-designed single HTML page. Runs the whole pipeline — download, macro expansion, LaTeX-to-HTML conversion, fidelity verification, and design polish — so the user does not drive each stage by hand. Use when the user runs /arxiv-to-html, or gives an arXiv id or URL (e.g. 2506.13131, hep-th/9901001, an arxiv.org link) and wants the paper converted or rendered to an HTML web page.
---

# arxiv-to-html

Turn an arXiv paper into a faithful, verified, well-designed single HTML page — end to end.

**Trigger:** `/arxiv-to-html <arxiv-id-or-URL>` — e.g. `/arxiv-to-html 2506.13131`, `/arxiv-to-html https://arxiv.org/abs/2506.13131`, `/arxiv-to-html hep-th/9901001`. Also use it whenever the user supplies an arxiv id/URL and asks for an HTML or web version of the paper.

## Pipeline

Five stages, run strictly in order; each reads the previous stage's output from `workspace/<id>/`:

**[[download-arxiv]] → [[expand-latex]] → [[tex-to-html]] → [[verify-conversion]] → frontend-design**

Each stage is its own skill — when you reach a stage, follow that skill's `SKILL.md`. This skill is the connective tissue: ordering, stage-gates, the cross-stage fixes, and the final verdict. It ships no scripts and does no conversion work itself.

## Operating rules

- Track the five stages with `TaskCreate` so progress is visible.
- **Stage-gate:** after each stage, confirm its output exists and looks sane *before* starting the next.
- **Stop on failure:** if a stage cannot produce a usable output, STOP and tell the user which stage, which skill, and why — do not limp into the next stage.
- `<id>` is the arxiv id as normalised by `download-arxiv`; every artifact lives under `workspace/<id>/`.
- Pass the user's id/URL through verbatim — `download-arxiv` normalises it; do not pre-validate.

## Stage 1 — download-arxiv

```
python .claude/skills/download-arxiv/scripts/download.py <id-or-url>
```

**Gate:** `workspace/<id>/metadata.json` and `source/` exist. Read `metadata.json.source_status` — if it is `pdf-only` or `withdrawn-or-unavailable`, **STOP**: there is no LaTeX source and the rest of the pipeline cannot run. Otherwise continue.

## Stage 2 — expand-latex

```
python .claude/skills/expand-latex/scripts/flatten_and_catalog.py workspace/<id>
# review workspace/<id>/macros.json  — the one judgement step
python .claude/skills/expand-latex/scripts/apply_expansion.py workspace/<id>
```

Default to trusting the `suggested` classifications. The exception worth catching: **bibliography / preamble boilerplate mislabelled "simple alias"** — `\url`, `\doi`, file-dependency helpers — reclassify those `expand → leave` (expanding `\url` to `\texttt` destroys hyperlinks; `\doi` often carries a conditional double-definition).

**Gate:** `expanded.tex` exists and the script's `verify:` line reads `OK`; spot-check one expanded macro. Report the expanded / dropped / left counts, any reclassifications, and any cycles.

## Stage 3 — tex-to-html

Follow the [[tex-to-html]] playbook to build `workspace/<id>/paper.html` from `expanded.tex`. Two additions proven necessary beyond that skill's current steps:

- **Prose rendered inside `<pre>`** (e.g. `minted` `escapeinside=||` regions emitted as spans) needs `white-space: pre-wrap`, or long lines clip silently.
- **Confirm figure / equation numbers against `paper.pdf`** before resolving `\Cref`/`\ref` — `\addtocounter` and `\renewcommand{\thefigure}` defeat counter arithmetic. (KaTeX has no equation counter at all; the tex-to-html skill numbers display equations with an explicit `\tag{N}` read from the PDF.)

For a paper with 100+ references or hundreds of `\cite`s, writing a fresh, reviewed one-off script to convert the bibliography and resolve citation / cross-reference tokens is acceptable — the conversion *judgement* stays yours; the script only applies it mechanically.

**Gate:** `paper.html` exists with non-trivial size; `Figures/` is populated; a headless render shows 0 console errors and 0 `.katex-error`.

## Stage 4 — verify-conversion

Run both phases per [[verify-conversion]]:

```
python .claude/skills/verify-conversion/scripts/inventory.py workspace/<id>                                   # Phase A
workspace/.testenv/bin/python .claude/skills/verify-conversion/scripts/render_compare.py workspace/<id>        # Phase B
```

Phase B needs Playwright — use `workspace/.testenv/bin/python` if that env exists, otherwise install per the skill's `SKILL.md`. Many Phase A `MISSING`/`EXTRA` flags are **normalisation artifacts** (section-number prefixes, "Figure N |" caption prefixes, differing equation/comment counting methods) — verify each is an artifact and not a real drop, and do the prose spot-check. For Phase B, dispatch the per-element sub-agents as the skill directs.

**Any genuine fault → trace it to the stage that caused it, fix it there, and re-verify.** A rendering bug introduced in stage 3 is fixed in stage 3, not cosmetically patched in `paper.html`.

## Stage 5 — frontend-design

Apply the **frontend-design** skill (the official Anthropic plugin) to make `paper.html` distinctive and polished. **Preserve every correctness layer from stage 3** — the KaTeX assets and render script, `fitWideEquations`, the overflow CSS (`.table-wrap`, `.equation`, `.figpair`, `overflow-wrap`), `white-space:pre-wrap` on prose, the diff colorizer. Re-skinning is welcome; removing the correctness layer is a regression.

After the design pass, re-run `render_compare.py` (or at least sweep horizontal overflow across ~320–1440 px) — a design that re-introduces a horizontal scrollbar fails the verdict.

If the `frontend-design` skill is unavailable, deliver the verified functional `paper.html` and tell the user the design stage was skipped.

## Final verdict

Report to the user:

- whether the conversion **succeeded** or failed;
- **Phase A** — coverage OK, or the specific drop / mismatch;
- **Phase B** — **PASS** or **issues found**; list each issue with the stage + skill responsible;
- the path to the final `workspace/<id>/paper.html`.

## Scope

This skill only sequences and gates the existing pipeline skills — it owns no conversion mechanics. Each stage's skill owns its own playbook and scripts. If a stage's skill is missing, say so plainly rather than improvising its work.
