---
name: verify-conversion
description: Verify a converted arXiv paper — check paper.html against the LaTeX source for dropped or garbled content, and against the original PDF for rendering errors. Use after tex-to-html, or when the user asks to verify, check, validate, QA, or proofread the converted HTML.
---

# verify-conversion

The final pipeline stage: confirm `paper.html` is a faithful, error-free rendering of the paper. Two phases, run in order.

Pipeline position: [[download-arxiv]] → [[expand-latex]] → [[tex-to-html]] → **verify-conversion**.

- **Phase A — source coverage**: did the conversion drop or garble anything? Compares `paper.html` against the LaTeX source.
- **Phase B — visual fidelity**: does the HTML render without errors and show the same content as the PDF?

Each phase pairs a mechanical helper script (gathers evidence) with your judgement (interprets it). The scripts never decide pass/fail — you do.

## Requirements

- Phase A: none — pure stdlib.
- Phase B: **Playwright** (`pip install playwright && playwright install chromium`) and `pdftoppm` (poppler-utils) on `PATH`. If Playwright is missing, `render_compare.py` exits with an install hint — install it and re-run.

## Phase A — source coverage

### 1. Run the inventory

```bash
python .claude/skills/verify-conversion/scripts/inventory.py workspace/<id>
```

It extracts a structural inventory from the LaTeX source (`expanded.tex`, else `flattened.tex`, else `source/`) and from `paper.html`, then diffs them: section headings, figure/table captions, bibliography keys, figure count, citations, display-equation count, comment count. Commented-out LaTeX is excluded from the source side.

### 2. Review every flag

The script reports; you judge. For each flagged line:

- **`MISSING in html`** — in the source but not the HTML. Real drops are bugs: open `paper.html` and the source and confirm. (`abstract` / `references` showing as html-only headings is expected — they are not `\section`s.)
- **`EXTRA in html`** — usually harmless added structure; check it is not duplicated content.
- **`ORDER differs`** — same items, resequenced. Check against source order.
- **`DIFFERS — review`** on a count (equations, comments) — a small gap is often a counting artifact (an `align` block counts once; an inline `$` miscounts); a large gap is a real drop. Cross-check in Phase B.
- **`UNRESOLVED`** citation — a `\cite` key with no `\bibitem`. Note it; may be a source-side error.

Then **spot-check prose**: pick 2–3 sections, read them in the source and in `paper.html`, and confirm no sentences or paragraphs were silently dropped *within* a section. Counts cannot catch a missing paragraph inside a section that is itself present.

## Phase B — visual fidelity

### 3. Render both

```bash
python .claude/skills/verify-conversion/scripts/render_compare.py workspace/<id>
```

It renders `paper.html` (headless Chromium) into `verify_shots/html-NN.png` slices and `paper.pdf` into `verify_shots/pdf-NN.png` pages, **plus one tight `verify_shots/elem-NN.png` screenshot per figure / table / code-listing / algorithm** (with a printed manifest of each). It also runs programmatic checks: rendered-math count, **`.katex-error`** (broken math — KaTeX renders an unparseable formula as a red `.katex-error` span), broken images, **raw-LaTeX-leak candidates** (e.g. a stray `\command` surviving into the rendered page), and **horizontal overflow at several viewport widths**. Read its report first — `.katex-error > 0` and broken images are definite faults; so is a `BROKEN -- inspect` math verdict — KaTeX absent, or present but 0 `.katex` nodes typeset. `math render errors: 0` is only meaningful once math has actually rendered (`math nodes rendered` > 0); a zero-node page has *not* passed the math check.

Any `OVERFLOW` line in the horizontal-overflow report is a definite fault too: the page is wider than the window at that width, so the reader gets a horizontal scrollbar or clipped content. The usual causes are a long URL that does not wrap, a wide table not wrapped in a scroll container, or a display equation that was not fit to the column — all `tex-to-html` bugs. A fixed-width render alone cannot see this, which is why the script resizes; never judge overflow from one width.

The leak-candidate list and the `.katex-error` count are **independent signals — neither implies the other.** `.katex-error` flags only math KaTeX *tried* to render and choked on. LaTeX that never reached KaTeX at all — a macro `expand-latex` left in the prose, a display equation not wrapped in `<div class="equation">`, a stray delimiter — renders as **literal `\command` text** on the page and produces *no* `.katex-error`. So `.katex-error: 0` does **not** mean "no leaks", and a non-empty leak list is never benign-by-default — it is unverified until step 4 resolves it.

### 4. Compare the screenshots

Read the `verify_shots/html-*.png` and `verify_shots/pdf-*.png` images. Walk the paper top to bottom and answer these two questions — **ask them exactly, every run**:

1. **Are there any rendering errors visible?** — broken math, missing figures, raw LaTeX text showing, overlapping or cut-off content, misplaced labels.
2. **Does the HTML present the same content as the PDF?** — same sections in order, same figures, same tables, same equations, no missing or garbled paragraphs — **accepting that the PDF is two-column and the HTML is single-column**. This is a *content* check, not a pixel or layout comparison.

**Resolve every raw-LaTeX-leak candidate by looking, not by reasoning.** The list from step 3 is *candidates*, not verdicts — but each one is settled by *finding it in a screenshot*, never by argument ("KaTeX surely supports that macro", "it's inside math so it's fine"). For each candidate: open the `html-*.png` slice where the token would appear and look. A leak renders as a raw `\command` glyph or a red `.katex-error` span in the page; if you see that, it is a fault — trace it to `tex-to-html` (commonly: a macro `expand-latex` left behind, or a display equation that was not wrapped in `<div class="equation">` so KaTeX never rendered it). It is benign only if the token is genuinely confined to source — a `$` in a URL, a backslash in a verbatim block — *and* the rendered slice is clean. A candidate you did not visually check is an open fault, not a pass.

### 5. Scrutinise every element with a sub-agent

The step-4 walk is coarse: fixed-height slices viewed at thumbnail scale hide per-element layout faults — a centered code block, a clipped table column, a caption baked into a figure crop. So check every element in isolation.

`render_compare.py` has written one tight screenshot per figure / table / code-listing / algorithm to `verify_shots/elem-NN.png` and printed a manifest (`file`, `kind`, `id`, caption). **Spawn one sub-agent per `elem-NN.png`** — issue all the `Agent` calls in a single message so they run in parallel. Give each sub-agent its one image path, the element's `kind` and caption, and the checklist below; it reads only that image, runs the checklist, and reports `clean` or the specific faults. Fold every verdict into the Phase B findings.

Per-element checklist:

- **Alignment** — code and algorithm bodies flush-left; display math and figure images centered; table columns consistent. (Centered code/algorithm text is the classic fault.)
- **Clipping / overflow** — nothing cut off at the element's edges; over-wide content scrolls, never silently truncates or breaks the page layout.
- **Completeness** — the whole element rendered; no blank panels or half-drawn regions.
- **Figure** — image loaded (not a broken icon or bare alt text); it is the *right* image for the caption; labels and legends sharp and legible; aspect ratio undistorted; for a PDF-cropped figure the crop is tight — no caption baked in, no neighbouring text bleeding in, nothing cut at the crop edge.
- **Table** — row/column structure intact; `rowspan`/`colspan` merges land correctly; data sits in the right cells (no off-by-one column shift); header row distinct; table notes present.
- **Code** — indentation preserved; monospace; no literal `&lt;` / `&gt;` / `&amp;` escaping artifacts showing.
- **Algorithm** — steps ordered; nested `for`/`if`/`else` indented; comments distinguishable from statements.
- **Math** — equations typeset; a red `.katex-error` span is a formula KaTeX could not parse — a fault; raw `\command` or `$` showing as literal text is a leak (LaTeX that never reached KaTeX) — also a fault; equation numbers present where expected; not clipped.
- **Caption & emphasis** — caption present, correct text, correct "Figure/Table N" number, attached to this element; bold, italic and meaningful colour preserved.

A sub-agent judges rendering from the HTML element alone — enough for every fault above. Content fidelity against the PDF (right data, nothing dropped) stays with the step-4 walk; hand a sub-agent the matching `pdf-NN.png` only if an element needs element-level content checking.

## Report

Give the user a verdict:

- **Phase A** — each category: OK, or the specific drop / mismatch.
- **Phase B** — rendering errors found (name the slice/page or `elem-NN.png`), the per-element sub-agent results, and a content-correspondence verdict.
- **Overall** — **PASS** (faithful, no errors) or **issues found** (list them, each with where it is).

If an issue traces to a specific upstream skill — a dropped section is a `tex-to-html` bug, a stray `\macro` is an `expand-latex` miss — say so, so the fix lands in the right place.

## Scope

This skill judges *correctness*, not visual design. "The HTML looks plain" is not a finding — polish is a separate concern. Report only faults: dropped content, rendering errors, mistranslation.
