# auto-arxiv-to-html

Claude Code skills that convert an arXiv paper into a faithful, verified, single-page HTML version.

## Usage

In Claude Code, give it an arXiv ID or URL:

```
/arxiv-to-html 2506.13131
```

That runs the full pipeline:

```
download-arxiv → expand-latex → tex-to-html → verify-conversion → frontend-design
```

| Stage | What it does |
|-------|--------------|
| `download-arxiv` | Fetch the LaTeX source, PDF, and metadata |
| `expand-latex` | Flatten multi-file source and expand custom macros |
| `tex-to-html` | Convert LaTeX to a single semantic HTML page with KaTeX |
| `verify-conversion` | Check the HTML against the source and PDF |
| `frontend-design` | Polish the page design |

Each stage is also a standalone skill — see `.claude/skills/<name>/SKILL.md`.

Output lands in `workspace/<id>/paper.html`.

## Requirements

- Claude Code
- Python 3.12+
- For verification: Playwright (`pip install playwright && playwright install chromium`) and `pdftoppm` (poppler-utils)
