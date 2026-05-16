# auto-arxiv-to-html

## Goal

Design Claude Code skills and agents that automatically convert arXiv papers into faithful, well-designed HTML pages.

## Pipeline

The skills in `.claude/skills/` form a five-stage pipeline, orchestrated by `arxiv-to-html`:

```
download-arxiv → expand-latex → tex-to-html → verify-conversion → frontend-design
```

Each stage reads the previous stage's output from `workspace/<id>/`. See each skill's `SKILL.md` for details.

## Workspace

Use `./workspace/` as a scratch directory for trying out skills end-to-end (downloaded PDFs, generated HTML, intermediate artifacts). It is gitignored — anything inside is local-only and will not be committed.
