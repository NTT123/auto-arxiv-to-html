#!/usr/bin/env python3
"""Download an arXiv paper's LaTeX source, PDF, and metadata into workspace/<id>/."""

from __future__ import annotations

import argparse
import gzip
import html
import io
import json
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

USER_AGENT = "auto-arxiv-to-html/0.1 (mailto:xcodevn@gmail.com)"
API_BASE = "https://export.arxiv.org/api/query"
ABS_BASE = "https://arxiv.org/abs"
PDF_BASE = "https://arxiv.org/pdf"
EPRINT_BASE = "https://arxiv.org/e-print"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# New-style: 2401.12345 (4 digits + dot + 4-5 digits), optional vN
NEW_ID = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
# Old-style: archive/9901001 — archive can include dashes/dots, paper is 7 digits, optional vN
OLD_ID = re.compile(r"^([a-z\-]+(?:\.[A-Z]{2})?)/(\d{7})(v\d+)?$")


def normalize_id(raw: str) -> tuple[str, str]:
    """Return (canonical_id, safe_dirname). Accepts ID or arxiv URL."""
    s = raw.strip()
    # Strip URL prefixes
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                   "https://arxiv.org/pdf/", "http://arxiv.org/pdf/",
                   "https://www.arxiv.org/abs/", "http://www.arxiv.org/abs/",
                   "arxiv.org/abs/", "arxiv:", "arXiv:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):]
            break
    # Strip trailing .pdf
    if s.lower().endswith(".pdf"):
        s = s[:-4]
    # Strip trailing slash or query
    s = s.split("?")[0].rstrip("/")

    if NEW_ID.match(s):
        return s, s
    if OLD_ID.match(s):
        return s, s.replace("/", "_")
    raise ValueError(f"Unrecognized arxiv id format: {raw!r}")


def http_get(url: str, *, accept: str | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_metadata(arxiv_id: str) -> dict:
    """Hit the arxiv API and parse the Atom entry into a dict."""
    query = urllib.parse.urlencode({"id_list": arxiv_id})
    raw = http_get(f"{API_BASE}?{query}")
    root = ET.fromstring(raw)
    entry = root.find("atom:entry", NS)
    if entry is None:
        raise RuntimeError(f"No entry returned for {arxiv_id}")

    # The API returns an entry even for unknown IDs, but with an error title.
    title_el = entry.find("atom:title", NS)
    title = (title_el.text or "").strip() if title_el is not None else ""
    if title.lower() == "error":
        summary = entry.find("atom:summary", NS)
        msg = (summary.text or "").strip() if summary is not None else "unknown"
        raise RuntimeError(f"arxiv API error for {arxiv_id}: {msg}")

    authors = [
        (a.findtext("atom:name", default="", namespaces=NS) or "").strip()
        for a in entry.findall("atom:author", NS)
    ]
    summary_el = entry.find("atom:summary", NS)
    abstract = (summary_el.text or "").strip() if summary_el is not None else ""
    published = entry.findtext("atom:published", default="", namespaces=NS)
    updated = entry.findtext("atom:updated", default="", namespaces=NS)
    primary = entry.find("arxiv:primary_category", NS)
    primary_cat = primary.get("term") if primary is not None else ""
    categories = [c.get("term", "") for c in entry.findall("atom:category", NS)]
    doi = entry.findtext("arxiv:doi", default="", namespaces=NS)
    comment = entry.findtext("arxiv:comment", default="", namespaces=NS)

    return {
        "arxiv_id": arxiv_id,
        "title": " ".join(title.split()),
        "authors": authors,
        "abstract": abstract,
        "published": published,
        "updated": updated,
        "primary_category": primary_cat,
        "categories": categories,
        "doi": doi,
        "comment": comment,
    }


def fetch_metadata_from_abs(arxiv_id: str) -> dict:
    """Fallback metadata source: scrape the arxiv.org/abs HTML page.

    Used when the export.arxiv.org API is unavailable (e.g. HTTP 429 rate
    limiting). The /abs page is on a separate, far more lenient host and
    carries the same title / authors / abstract in <meta> tags. Parsing is
    best-effort: a field it cannot read is left blank rather than failing,
    since downstream skills can also recover title/authors from the .tex.
    """
    page = http_get(f"{ABS_BASE}/{arxiv_id}").decode("utf-8", "replace")

    def meta_all(field: str) -> list[str]:
        """All <meta name="field" content="..."> values, any attribute order."""
        out = []
        for tag in re.finditer(r"<meta\b[^>]*>", page, re.I):
            t = tag.group(0)
            nm = re.search(r'name="([^"]*)"', t, re.I)
            ct = re.search(r'content="([^"]*)"', t, re.I)
            if nm and ct and nm.group(1).lower() == field.lower():
                out.append(html.unescape(ct.group(1)).strip())
        return out

    def meta_one(field: str) -> str:
        vals = meta_all(field)
        return vals[0] if vals else ""

    title = " ".join(meta_one("citation_title").split())

    # citation_author content is "Last, First" -- flip to natural order.
    authors = []
    for a in meta_all("citation_author"):
        if "," in a:
            last, first = a.split(",", 1)
            a = f"{first.strip()} {last.strip()}".strip()
        if a:
            authors.append(a)

    # Abstract sits in <blockquote class="abstract ...">.
    abstract = ""
    m = re.search(r'<blockquote class="abstract[^"]*">(.*?)</blockquote>',
                  page, re.S | re.I)
    if m:
        body = re.sub(r'<span class="descriptor">.*?</span>', "",
                      m.group(1), flags=re.S | re.I)
        abstract = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", body)).split())

    # Subjects, e.g. "Computation and Language (cs.CL)" -> primary cs.CL.
    primary_cat, categories = "", []
    m = re.search(r'<span class="primary-subject">([^<]*)</span>', page, re.I)
    if m:
        c = re.search(r"\(([\w\-]+\.[\w\-]+)\)", m.group(1))
        primary_cat = c.group(1) if c else ""
    m = re.search(r'<td class="tablecell subjects">(.*?)</td>', page, re.S | re.I)
    if m:
        categories = re.findall(r"\(([a-zA-Z\-]+\.[a-zA-Z\-]+)\)", m.group(1))
    if primary_cat and primary_cat not in categories:
        categories.insert(0, primary_cat)

    # Author comment, e.g. "15 pages, 5 figures".
    comment = ""
    m = re.search(r'<td class="tablecell comments[^"]*">(.*?)</td>',
                  page, re.S | re.I)
    if m:
        comment = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "published": meta_one("citation_date"),
        "updated": meta_one("citation_online_date"),
        "primary_category": primary_cat,
        "categories": categories,
        "doi": meta_one("citation_doi"),
        "comment": comment,
    }


def download_pdf(arxiv_id: str, dest: Path) -> None:
    data = http_get(f"{PDF_BASE}/{arxiv_id}")
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"PDF endpoint did not return a PDF for {arxiv_id}")
    dest.write_bytes(data)


def download_source(arxiv_id: str, source_dir: Path) -> str:
    """Download e-print payload and unpack. Returns a status string."""
    try:
        data = http_get(f"{EPRINT_BASE}/{arxiv_id}")
    except urllib.error.HTTPError as e:
        return f"unavailable (HTTP {e.code})"

    source_dir.mkdir(parents=True, exist_ok=True)

    # gzip magic
    if data[:2] == b"\x1f\x8b":
        # Try tar.gz first
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                _safe_extract(tf, source_dir)
            return "tarball"
        except tarfile.ReadError:
            pass
        # Fall back to single gzipped tex
        try:
            decompressed = gzip.decompress(data)
            (source_dir / "main.tex").write_bytes(decompressed)
            return "single-tex"
        except OSError as e:
            return f"gzip-failed: {e}"

    # PDF magic — author uploaded PDF only
    if data[:4] == b"%PDF":
        (source_dir / "paper.pdf").write_bytes(data)
        return "pdf-only"

    # HTML — likely withdrawn or unavailable
    head = data[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return "withdrawn-or-unavailable"

    # Unknown blob — save raw for debugging
    (source_dir / "raw.bin").write_bytes(data)
    return "unknown-format"


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball, refusing entries that escape dest."""
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest_resolved)):
            raise RuntimeError(f"Tar entry escapes destination: {member.name}")
    tf.extractall(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download an arXiv paper.")
    parser.add_argument("paper", help="arXiv ID (e.g. 2401.12345 or hep-th/9901001) or URL")
    parser.add_argument("--workspace", default="workspace", help="Workspace root (default: ./workspace)")
    args = parser.parse_args()

    try:
        arxiv_id, safe_name = normalize_id(args.paper)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    out_dir = Path(args.workspace) / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Fetching metadata for {arxiv_id} ...", flush=True)
    try:
        meta = fetch_metadata(arxiv_id)
        meta["metadata_source"] = "api"
    except (urllib.error.URLError, RuntimeError, ET.ParseError) as e:
        print(f"      arxiv API unavailable ({e})", flush=True)
        print("      -- falling back to the arxiv.org/abs page", flush=True)
        meta = fetch_metadata_from_abs(arxiv_id)
        meta["metadata_source"] = "abs-page-fallback"

    # arxiv etiquette: ~3s between requests
    time.sleep(3)

    print(f"[2/3] Downloading source ...", flush=True)
    source_status = download_source(arxiv_id, out_dir / "source")
    print(f"      source: {source_status}")
    time.sleep(3)

    print(f"[3/3] Downloading PDF ...", flush=True)
    download_pdf(arxiv_id, out_dir / "paper.pdf")

    meta["source_status"] = source_status
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"\nSaved to {out_dir}/")
    print(f"  title: {meta['title']}")
    print(f"  authors: {', '.join(meta['authors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
