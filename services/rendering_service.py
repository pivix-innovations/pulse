from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.enums import TemplateFormat
from renderers.jinja_renderer import JinjaRenderer
from renderers.markdown_renderer import MarkdownRenderer


@dataclass(frozen=True, slots=True)
class RenderedContent:
    subject: str | None
    text: str
    html: str | None


class RenderingService:
    """
    Renders a template (subject + body) against a context: Jinja first,
    then Markdown -> HTML when the template format asks for it.
    """

    def __init__(
        self,
        jinja: JinjaRenderer | None = None,
        markdown_renderer: MarkdownRenderer | None = None,
    ) -> None:
        self._jinja = jinja or JinjaRenderer()
        self._markdown = markdown_renderer or MarkdownRenderer()

    def render(
        self,
        *,
        body: str,
        subject: str | None,
        format: TemplateFormat,
        context: Mapping[str, Any],
    ) -> RenderedContent:
        rendered_subject = self._jinja.render(subject, context) if subject else None
        rendered_body = self._jinja.render(body, context)

        if format is TemplateFormat.MARKDOWN:
            return RenderedContent(
                subject=rendered_subject,
                text=rendered_body,
                html=self._markdown.to_html(rendered_body),
            )
        if format is TemplateFormat.HTML:
            return RenderedContent(subject=rendered_subject, text=rendered_body, html=rendered_body)
        return RenderedContent(subject=rendered_subject, text=rendered_body, html=None)
