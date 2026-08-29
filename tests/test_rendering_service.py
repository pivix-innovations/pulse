from __future__ import annotations

import pytest

from domain.enums import TemplateFormat
from renderers.jinja_renderer import TemplateRenderError
from services.rendering_service import RenderingService


def test_markdown_renders_text_and_html():
    out = RenderingService().render(
        body="# Hello {{ name }}",
        subject="Hi {{ name }}",
        format=TemplateFormat.MARKDOWN,
        context={"name": "Ada"},
    )
    assert out.subject == "Hi Ada"
    assert out.text == "# Hello Ada"
    assert out.html == "<h1>Hello Ada</h1>"


def test_text_format_has_no_html():
    out = RenderingService().render(
        body="plain {{ x }}", subject=None, format=TemplateFormat.TEXT, context={"x": 1}
    )
    assert out.subject is None
    assert out.html is None
    assert out.text == "plain 1"


def test_sandbox_blocks_dangerous_templates():
    with pytest.raises(TemplateRenderError):
        RenderingService().render(
            body="{{ ''.__class__.__mro__[1].__subclasses__() }}",
            subject=None,
            format=TemplateFormat.TEXT,
            context={},
        )


def test_invalid_template_raises_render_error():
    with pytest.raises(TemplateRenderError):
        RenderingService().render(
            body="{% if %}", subject=None, format=TemplateFormat.TEXT, context={}
        )
