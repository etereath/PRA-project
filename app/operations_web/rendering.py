"""只从已打包本地资源渲染运营 Web 页面。"""

from __future__ import annotations

from html import escape
from importlib.resources import files
from string import Template


def render_template(name: str, **values: object) -> str:
    resource = files("app.operations_web").joinpath("templates", name)
    template = Template(resource.read_text(encoding="utf-8"))
    return template.substitute({key: str(value) for key, value in values.items()})


def html(value: object) -> str:
    return escape(str(value), quote=True)


def static_text(name: str) -> str:
    resource = files("app.operations_web").joinpath("static", name)
    return resource.read_text(encoding="utf-8")
