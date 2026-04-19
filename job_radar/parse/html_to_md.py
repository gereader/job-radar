"""HTML → Markdown. Readability extracts main content, markdownify emits md."""

from __future__ import annotations

import re

from markdownify import markdownify as _md
from readability import Document


def html_to_markdown(html: str) -> str:
    """Strip chrome/nav, keep the JD body, render as sane markdown.

    Readability occasionally discards bullet lists when total content is thin.
    If the extracted summary is conspicuously shorter than the input, we fall
    back to the raw body — losing a little nav noise is cheaper than losing a
    requirements list.
    """
    if not html or not html.strip():
        return ""
    summary = html
    try:
        doc = Document(html)
        candidate = doc.summary(html_partial=True)
        # Heuristic: keep readability's output only if it's at least 60% of the
        # text length of the input. Otherwise the JD likely had a bullet list
        # that got trimmed.
        if _text_len(candidate) >= 0.6 * _text_len(html):
            summary = candidate
    except Exception:
        pass

    md = _md(
        summary,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "img", "svg", "button", "input", "nav", "header", "footer"],
    )
    return _clean_md(md)


def _text_len(html_or_md: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html_or_md or ""))


def _clean_md(md: str) -> str:
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    # Collapse markdown links that wrap themselves: [text](text) → text
    md = re.sub(r"\[([^\]]+)\]\(\1\)", r"\1", md)
    return md.strip()
