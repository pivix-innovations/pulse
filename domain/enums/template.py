from __future__ import annotations

from enum import Enum


class TemplateFormat(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"
