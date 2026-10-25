"""ReDoc 文档页。"""

from __future__ import annotations

REDOC_CDN = "https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"


def redoc_html(title: str, spec_url: str = "/openapi.json") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} (ReDoc)</title>
  <style>body {{ margin: 0; padding: 0; }}</style>
</head>
<body>
  <redoc spec-url="{spec_url}" hide-download-button="true"></redoc>
  <script src="{REDOC_CDN}"></script>
</body>
</html>"""
