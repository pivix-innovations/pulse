from __future__ import annotations

import markdown


class MarkdownRenderer:
    """Converts Markdown to HTML."""

    def __init__(self) -> None:
        self._md = markdown.Markdown(extensions=["extra"])

    def to_html(self, text: str) -> str:
        self._md.reset()
        return self._md.convert(text)
