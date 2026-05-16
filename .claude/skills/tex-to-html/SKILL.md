---
name: tex-to-html
description: Convert a downloaded arXiv paper's LaTeX source into a single semantic HTML file with KaTeX for equations. Use after the download-arxiv skill has populated workspace/<id>/source/, or when the user asks to convert, render, or transform .tex files to HTML.
---

# tex-to-html

Convert the LaTeX source in `workspace/<id>/source/` into `workspace/<id>/paper.html` — a single semantic HTML page with KaTeX math, relative figure references, and the bibliography rendered inline. This is the second stage of the pipeline; design polish happens in a later skill.

## Inputs and outputs

Input: a paper directory `workspace/<id>/` populated by the [[download-arxiv]] skill. The directory contains `source/` with the `.tex` files (and possibly figures, `.bbl`, `.sty`).

**Prefer `expanded.tex` if it exists.** When the [[expand-latex]] skill has run, `workspace/<id>/expanded.tex` is a single flattened file with custom macros already expanded and the bibliography inlined. Use it as the sole text input — it makes steps 3 and 4 below mostly unnecessary. Figures still live in `source/`, so figure-path resolution (step 6) is unchanged.

Output:
```
workspace/<id>/
  paper.html         # this skill writes this file
  Figures/           # copied/converted from source/Figures (or wherever)
```

## Hard rules

1. **Math is preserved semantically.** Copy `$...$`, `$$...$$`, `\(...\)`, `\[...\]`, `\begin{equation}...\end{equation}`, `cases`, `matrix`, `pmatrix`, `bmatrix`, etc., verbatim into the HTML (inline math stays inline; each display equation goes in its own `<div class="equation">`, step 3a). **Do not** rewrite operators, change variable names, simplify, or expand macros (macros go in the KaTeX macro config, step 3). The following narrow adjustments are allowed because they are KaTeX-compatibility or pure-typography:
   - `eqnarray` is not supported by KaTeX — replace it with `aligned` (or `align`), which KaTeX does support.
   - Adding `\left` / `\right` around delimiters that would otherwise look small next to tall fractions or matrices.
   - Adding thin-space macros (`\,`, `\!`, `\;`, `\:`) for spacing only.
   - Removing text-size directives (`\footnotesize`, `\small`, `\scriptsize`, …) from inside display equations — they are print-only column-fitting hacks with no mathematical meaning. KaTeX supports them, so they do not break, but leave the fitting to `fitWideEquations` (step 3a) instead.

   Everything else stays as-written. If in doubt, copy verbatim.
2. **Use relative paths** for figures (`Figures/foo.png`), never base64 data URLs.
3. **Custom macros**: when working from `expanded.tex` they are already expanded away — handle only any definitions `expand-latex` left behind. When working from raw `source/`, put them in the KaTeX macro config (step 3), not expanded by hand; a missed `\newcommand` means broken-looking math.
4. **Do not invent content.** If a figure file is missing, leave the `<img>` with a clear `alt` and a TODO comment. If the bibliography section can't be found, write an empty `<section id="references">` with a comment explaining what was missing.

## Playbook

Follow these steps in order. Use Read, Bash, Edit, Write tools — no helper scripts ship with this skill; the work is Claude's.

### 1. Locate the source text

If `workspace/<id>/expanded.tex` exists, **that is your input** — it is already flat and macro-expanded. Skip step 4 entirely, and treat step 3 as a small fallback. Otherwise, find the main `.tex` — the one containing `\documentclass`:

```bash
grep -l '^\\documentclass' workspace/<id>/source/*.tex
```

If multiple match (rare), pick the one that also has `\begin{document}`.

### 2. Extract metadata

Read the main `.tex` and grab:

- **Title** from `\title{...}` (may span lines; handle `\\` and `\thanks{...}` — strip them).
- **Authors** from `\author{...}`. Authors are commonly separated by `\And`, `\and`, `\AND`, or `\\`. Strip affiliation/email lines (anything after `\\` inside an author block is usually an affiliation — keep only the name portion if obvious; otherwise keep the block as-is and rely on later polish).
- **Abstract** from `\begin{abstract}...\end{abstract}`.

If `workspace/<id>/metadata.json` already exists (from `download-arxiv`), prefer its `title`, `authors`, `abstract` fields — they came from the arXiv API and are cleaner.

### 3. Build the KaTeX setup

KaTeX renders only the math the page hands it — it does not scan the page on its own. Load three assets and add one render script (step 3a defines `fitWideEquations`, called at the end of it).

> If you are working from `expanded.tex`, custom macros are already expanded — the macro config below only needs the few definitions `expand-latex` deliberately left in (recursive, conditional, environments). Scan `expanded.tex` for any remaining `\newcommand`-style definitions and handle just those. If none remain, the `macros` object stays empty.

Otherwise (working from raw `source/`), search **all** `.tex` files in `source/` for macro definitions:

```bash
grep -hE '^\s*\\(newcommand\*?|providecommand|DeclareMathOperator|def)\b' workspace/<id>/source/*.tex
```

Skip lines starting with `%` (commented out). For each definition, translate to a KaTeX `macros` entry — the key is the full command name (with backslash, doubled for the JS string); KaTeX infers the argument count from `#1`…`#n` in the body:

| LaTeX form | KaTeX `macros` entry |
|---|---|
| `\newcommand{\mc}[1]{\mathcal{#1}}` | `"\\mc": "\\mathcal{#1}"` |
| `\newcommand{\dmodel}{d_{\text{model}}}` | `"\\dmodel": "d_{\\text{model}}"` |
| `\newcommand\kq{q}` | `"\\kq": "q"` |
| `\DeclareMathOperator{\softmax}{softmax}` | `"\\softmax": "\\operatorname{softmax}"` |

Notes:
- Backslashes in keys and bodies must be doubled (JS string).
- Skip macros that are purely text-mode (e.g. `\todo`, `\blfootnote`) — they won't appear inside math and would clutter the config.

Load KaTeX (CSS + core + the `auto-render` contrib extension); `defer` so they execute, in order, before `DOMContentLoaded`:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16/dist/contrib/auto-render.min.js"></script>
```

Then one inline `<script>` renders the math on `DOMContentLoaded` (which fires *after* the deferred scripts above, so `katex` and `renderMathInElement` are defined):

```html
<script>
  var katexMacros = {
    // "\\dmodel": "d_{\\text{model}}",   // entries from the table above
  };
  document.addEventListener('DOMContentLoaded', function () {
    if (typeof katex === 'undefined') return;
    // Display equations: each <div class="equation"> holds one verbatim equation
    // environment (step 3a); render it as-is in display mode.
    document.querySelectorAll('.equation').forEach(function (d) {
      try {
        katex.render(d.textContent, d,
          { displayMode: true, throwOnError: false, macros: katexMacros });
      } catch (e) {}
    });
    // Inline math everywhere else.
    if (typeof renderMathInElement === 'function') {
      renderMathInElement(document.body, {
        delimiters: [
          { left: '$', right: '$', display: false },
          { left: '\\(', right: '\\)', display: false }
        ],
        ignoredClasses: ['equation'],
        macros: katexMacros,
        throwOnError: false
      });
    }
    fitWideEquations();                    // defined in step 3a
    setTimeout(fitWideEquations, 200);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitWideEquations);
  });
</script>
```

`throwOnError: false` keeps one bad expression from blanking the page — KaTeX renders it as a red `.katex-error` span instead, which `verify-conversion` then catches. KaTeX keeps no persisted browser state, so — unlike MathJax — no `localStorage` reset is needed.

### 3a. Display equations: wrap, number, and fit the column

**Wrap.** Put each display equation — `\begin{equation}…\end{equation}`, `align`, a `cases` block, `\[…\]`, etc. — in its own `<div class="equation">`, the math copied verbatim. The render script (step 3) renders each such div in display mode.

**Number.** LaTeX numbers `equation` / `align` / … with a running counter; KaTeX has none. `verify-conversion` already has you confirm equation numbers against `paper.pdf` — so write each number in explicitly: add `\tag{N}` inside every *numbered* display equation (just before `\end{equation}`), N being its number from the PDF. Leave unnumbered display math (`equation*`, `\[…\]`, or `equation` with no `\tag`) untagged — KaTeX shows no number for those. If an equation is `\ref`'d, the `\tag` value is the link text the `\ref` must resolve to.

**Fit.** A long display equation overflows a single-column web page (in print the author hid this with `\footnotesize` and a two-column layout — neither survives conversion). KaTeX also centres the body in the full column width and overlays the `\tag` at the right edge, so a wide body collides with its number. One measured JS pass handles both — re-run it on first render, font load, and resize:

```js
function fitWideEquations() {
  var eqs = document.querySelectorAll('.katex');
  eqs.forEach(function (c) { c.style.fontSize = ''; });           // reset, then measure
  eqs.forEach(function (c) {
    if (!c.parentElement) return;
    var cs = getComputedStyle(c.parentElement);
    var avail = c.parentElement.clientWidth
              - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0);
    var base = parseFloat(getComputedStyle(c).fontSize) || 0;
    if (avail <= 0 || base <= 0) return;
    var ratio = 1;
    var tag = c.querySelector('.tag');
    var bases = c.querySelectorAll('.katex-html > .base');
    if (tag && bases.length) {                       // numbered: keep body clear of tag
      var bodyW = bases[bases.length - 1].getBoundingClientRect().right
                - bases[0].getBoundingClientRect().left;
      var tagW = tag.getBoundingClientRect().width;
      var fitTag = (avail - 1.5 * base) / (bodyW + 2 * tagW);
      if (fitTag < ratio) ratio = fitTag;
    }
    var w = c.scrollWidth;                           // plain overflow (inline or display)
    if (w > avail + 1) {
      var fitW = (avail - 4) / w;
      if (fitW < ratio) ratio = fitW;
    }
    if (ratio < 1) c.style.fontSize = (base * Math.max(ratio, 0.15)).toFixed(2) + 'px';
  });
}
// plus  addEventListener('load', fitWideEquations)  and a debounced 'resize' listener.
```

Why it is shaped this way:
- A KaTeX **display** `.katex` is `display:block`, so its bounding rect is only the container width — the true math width is read from `scrollWidth`.
- The scale is applied in **absolute px** off the element's own computed size, so it stays correct whatever base font-size `.katex` carries.
- Target **every** `.katex` (inline included) — a long inline `$…$` overflows a narrow viewport too.
- The `0.15` floor stops an equation vanishing on a phone. A width-dependent fix verified at one width is not verified — check it across the range (step 9).

### 4. Walk the document via `\input` / `\include` / `\subfile`

**Skip this step entirely if you are working from `expanded.tex`** — it is already a single flattened file.

Otherwise, read the main `.tex` from `\begin{document}` to `\end{document}` in order. Whenever you hit `\input{name}`, `\include{name}`, or `\subfile{name}`, read `source/name.tex` and splice its content in place. Recurse if those files contain further `\input`s.

### 5. Translate structure

Apply these mappings:

| LaTeX | HTML |
|---|---|
| `\section{X}` | `<h2 id="sec-X-slug">X</h2>` |
| `\subsection{X}` | `<h3 id="...">X</h3>` |
| `\subsubsection{X}` | `<h4>X</h4>` |
| `\paragraph{X}` | run-in: `<p><strong class="run-in">X.</strong> ...</p>` (do **not** use `<h5>` — placing block-level tags inside `<p>` is invalid HTML and browsers will split the paragraph) |
| `\label{key}` | add `id="key"` to the preceding heading or container |
| `\ref{key}` / `\autoref{key}` / `\cref{key}` | `<a href="#key">key</a>` (or the section number if obvious) |
| `\cite{a,b}` | `<a href="#ref-a">[a]</a><a href="#ref-b">[b]</a>` |
| `\emph{X}` / `\textit{X}` | `<em>X</em>` |
| `\textbf{X}` | `<strong>X</strong>` |
| `\texttt{X}` / `\verb|X|` | `<code>X</code>` |
| `\footnote{X}` | `<sup class="fn">X</sup>` (or move to end as a numbered note) |
| `\url{X}` / `\href{X}{Y}` | `<a href="X">X</a>` / `<a href="X">Y</a>` |
| Paragraph break (blank line) | `</p><p>` |
| `\begin{itemize}` | `<ul>` with `<li>` per `\item` |
| `\begin{enumerate}` | `<ol>` with `<li>` per `\item` |
| `\begin{quote}` | `<blockquote>` |

For tables (`\begin{tabular}`): translate to `<table>` with `<thead>`/`<tbody>`/`<tr>`/`<td>`. If a table is complex enough that this is hand-work, do it — don't skip it. If a table is wrapped in `\begin{table}` with `\caption{...}`, emit `<figure class="table">` with a `<figcaption>`. **Wrap the `<table>` in a `<div class="table-wrap">`** (styled `overflow-x: auto`, step 8) — a wide multi-column table then scrolls inside the column instead of pushing the whole page wider on a narrow window.

For figures (`\begin{figure}`): wrap the resulting `<img>` in `<figure>` with `<figcaption>` from `\caption{...}`. If the figure holds two or more side-by-side images (`minipage`s or `subfigure`s), wrap those `<img>`s in a `<div class="figpair">` (a flex row, step 8) so they sit side by side on wide screens and stack on narrow ones — captions that say "left"/"right" then still read correctly.

### 5a. Carry over LaTeX comments

arXiv e-print source is public, so the author's `%` comments are not private — carry them into the HTML as `<!-- -->` comments (they also let the `verify-conversion` skill check coverage). Apply these placement rules exactly:

- **Whole-line comment** (first non-space character on the line is `%`): emit it as its own `<!-- ... -->` line at the matching position in the HTML.
- **Trailing comment** (`%` after content on a line, `text % comment`): append `<!-- comment -->` at the end of the HTML element that the content became.
- **Comment inside a math environment** (`equation`, `align`, `$$…$$`, etc.): drop it — rendered math has nowhere to place it.
- **Sanitize**: HTML comments may not contain `--`. Replace every `--` in comment text with `- -` before emitting. Strip the leading `%` and one following space; keep the rest verbatim.

### 6. Resolve `\includegraphics`

For each `\includegraphics[opts]{path}`:

1. Find the actual file. LaTeX usually omits the extension; try `path.png`, `path.jpg`, `path.pdf`, `path.eps` in that order, possibly under `source/Figures/` if `\graphicspath{{Figures/}}` is set.
2. If the file is `.png`/`.jpg`/`.svg`, copy it to `workspace/<id>/Figures/` preserving the basename.
3. If the file is `.pdf` or `.eps`, convert to PNG with:
   ```bash
   pdftoppm -png -r 150 source/path/to/figure.pdf workspace/<id>/Figures/figure
   ```
   Then reference `Figures/figure-1.png` (pdftoppm appends `-1`).
4. Emit `<img src="Figures/<name>" alt="<caption text or filename>">`.

Make `workspace/<id>/Figures/` once at the start, then copy/convert as you go.

### 7. Render the bibliography

In priority order:

1. If `source/*.bbl` exists, parse it. Entries are typically `\bibitem{key} ...` blocks; translate each to `<li id="ref-key">...</li>` inside `<ol class="references">`.
2. Else if the main `.tex` contains `\begin{thebibliography}...\end{thebibliography}` (common in arXiv submissions), use that block — same `\bibitem{key}` translation.
3. Else if only `\bibliography{X}` + `X.bib` exists, parse the `.bib` keys referenced by `\cite` calls and emit a simple author-year list. Note this in an HTML comment near the references.
4. If none of the above, emit `<section id="references"><!-- no bibliography found in source --></section>` and continue.

### 8. Assemble `paper.html`

Single file. Skeleton:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{title}}</title>
  <style>
    body { max-width: 760px; margin: 2em auto; padding: 0 1em; font-family: Georgia, serif; line-height: 1.6; color: #222; overflow-wrap: break-word; }
    h1 { font-size: 1.8em; }
    h2 { margin-top: 2em; }
    figure { margin: 1.5em 0; text-align: center; }
    figure img { max-width: 100%; height: auto; }
    figcaption { font-size: 0.9em; color: #555; margin-top: 0.5em; }
    table { border-collapse: collapse; margin: 1em auto; }
    th, td { border: 1px solid #ccc; padding: 0.4em 0.8em; }
    code { background: #f4f4f4; padding: 0 0.2em; }
    a, code { overflow-wrap: anywhere; word-break: break-word; } /* long URLs wrap, never overflow */
    .table-wrap { overflow-x: auto; }   /* a wide table scrolls inside the column */
    .equation { overflow-x: auto; }     /* fallback if a display equation is still too wide */
    .figpair { display: flex; flex-wrap: wrap; gap: 1em; justify-content: center; }
    .figpair img { flex: 1 1 280px; min-width: 0; max-width: 100%; } /* min-width:0 — without it two-image figures never sit side by side */
    .katex { font-size: 1.1em; }        /* KaTeX defaults to 1.21em, usually a touch large next to body text */
    .authors { font-style: italic; color: #444; }
    .abstract { background: #f8f8f8; padding: 1em 1.2em; border-left: 3px solid #999; margin: 2em 0; }
    .references { font-size: 0.92em; }
    strong.run-in { font-weight: bold; }
    sup.fn { color: #c33; }
  </style>
  <!-- KaTeX assets + render script from step 3; fitWideEquations + listeners from step 3a -->
</head>
<body>
  <article>
    <h1>{{title}}</h1>
    <p class="authors">{{authors}}</p>
    <section class="abstract"><h2>Abstract</h2><p>{{abstract}}</p></section>
    <!-- body sections from step 5 -->
    <section id="references"><h2>References</h2><ol class="references"><!-- from step 7 --></ol></section>
  </article>
</body>
</html>
```

Keep the CSS minimal — the next skill polishes the design.

### 9. Sanity check

Before declaring done:

- `grep -c '<h2' workspace/<id>/paper.html` — should match the number of `\section`s plus 1–2 (Abstract, References).
- `grep -c '<img' workspace/<id>/paper.html` — should match the number of `\includegraphics` calls.
- Files referenced by `<img>` actually exist under `workspace/<id>/Figures/`.
- The KaTeX `macros` object contains every non-text custom command found in step 3.
- **No horizontal overflow.** Check the page at a wide *and* a narrow width (~400 px) — the document must have no horizontal scrollbar and nothing may poke past the column. Long URLs, wide tables, and wide equations are the usual offenders; steps 3a, 5 and 8 handle them. If something still overflows, fix it here — it is a `tex-to-html` bug, not a polish item.
- Open the file in a browser (or headless) and confirm the math rendered — every formula becomes a `.katex` node and there are **zero `.katex-error`** spans. If math is missing or errored, recheck the step 3 render script and the `macros` object.

Report to the user: paper title, output path, number of sections / figures / references, and any unresolved items (missing figures, skipped tables, etc.).

## Scope discipline

This skill produces functional, readable HTML — not designed HTML. No dark mode, no custom fonts beyond a serif default, no design-level responsive layout. If the user asks for polish, that's a separate skill. Resist scope creep here. **But never ship horizontal overflow:** keeping wide equations, wide tables, and long URLs inside the column at any window width is *correctness*, not polish — do it (steps 3a, 5, 8), and the small amount of layout JS that requires is in scope.
