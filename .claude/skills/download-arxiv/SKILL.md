---
name: download-arxiv
description: Download an arXiv paper (LaTeX source, PDF, and metadata) given an arXiv ID or URL. Use when the user asks to fetch, download, grab, or get an arxiv paper, or mentions an arxiv ID like 2401.12345, an old-style ID like hep-th/9901001, or an arxiv.org URL.
---

# download-arxiv

Fetches an arXiv paper's LaTeX source tarball, PDF, and metadata into `workspace/<id>/`. This is the first stage of the arxiv → HTML pipeline; later skills consume the LaTeX source from `workspace/<id>/source/`.

## When to use

Invoke when the user wants to download or retrieve an arxiv paper. Inputs are flexible:

- Bare ID: `2401.12345`, `2401.12345v2`, `hep-th/9901001`
- Any arxiv URL: `https://arxiv.org/abs/2401.12345`, `https://arxiv.org/pdf/2401.12345v2`

## How to invoke

Run the script from the repo root, passing the ID or URL as-is:

```bash
python .claude/skills/download-arxiv/scripts/download.py <id-or-url>
```

The script writes to `./workspace/<id>/` by default. Override with `--workspace <dir>` only if the user asks for a different location.

## Output layout

```
workspace/<id>/
  source/         # extracted LaTeX (or paper.pdf if author only uploaded PDF)
  paper.pdf       # the rendered PDF
  metadata.json   # title, authors, abstract, categories, DOI, source_status, metadata_source
```

`metadata.json.source_status` records how the source was retrieved:

- `tarball` — normal LaTeX source extracted from a tar.gz
- `single-tex` — a single gzipped .tex file (older papers)
- `pdf-only` — author uploaded only a PDF; no LaTeX available
- `withdrawn-or-unavailable` — arxiv returned an HTML page instead of source
- `unavailable (HTTP <code>)` — e-print endpoint errored

Downstream skills should check `source_status` before assuming LaTeX is present.

## Notes for the model

- Don't pre-validate the ID — pass the user's input through; the script normalizes IDs and URLs and reports a clear error if the format is unrecognized.
- The script sleeps ~3s between arxiv requests (API etiquette). A successful run takes ~10s end-to-end.
- If the `export.arxiv.org` metadata API is unavailable (e.g. HTTP 429 rate limiting), the script falls back to scraping the `arxiv.org/abs` page for title / authors / abstract — the fields downstream skills need. `source_status` is computed from the e-print download either way, so a blocked API never stops the pipeline. `metadata.json.metadata_source` records which path was used: `api` or `abs-page-fallback`.
- After a successful run, summarize the result for the user: paper title, where it was saved, and the `source_status`. If `source_status` is `pdf-only` or `withdrawn-or-unavailable`, flag that LaTeX-based HTML conversion won't be possible for this paper.
