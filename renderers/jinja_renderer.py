from __future__ import annotations

from typing import Any, Mapping

from jinja2.sandbox import SandboxedEnvironment


class TemplateRenderError(Exception):
    pass


class JinjaRenderer:
    """
    Renders template strings with a sandboxed Jinja environment. Templates
    are tenant-authored content, so the sandbox is not optional.
    """

    def __init__(self) -> None:
        self._env = SandboxedEnvironment()

    def render(self, template: str, context: Mapping[str, Any]) -> str:
        try:
            return self._env.from_string(template).render(**context)
        except Exception as exc:
            raise TemplateRenderError(str(exc)) from exc
