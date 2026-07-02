"""Render interview report as Markdown -> HTML -> PDF."""
from pathlib import Path
import uuid
import markdown as md
from jinja2 import Template
from weasyprint import HTML

from ..config import settings


HTML_TEMPLATE = Template(
    """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{{ title }}</title>
<style>
body { font-family: "Noto Sans CJK SC", "Helvetica", sans-serif; padding: 32px; color: #222; }
h1 { border-bottom: 2px solid #333; padding-bottom: 8px; }
h2 { color: #1a56db; margin-top: 24px; }
h3 { color: #444; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: left; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.pass { background: #d1fae5; color: #065f46; }
.fail { background: #fee2e2; color: #991b1b; }
blockquote { border-left: 3px solid #ddd; margin: 8px 0; padding: 4px 12px; color: #555; }
</style></head><body>
{{ content_html | safe }}
</body></html>
"""
)


def render_pdf(title: str, markdown_text: str) -> str:
    content_html = md.markdown(markdown_text, extensions=["tables", "fenced_code"])
    html_str = HTML_TEMPLATE.render(title=title, content_html=content_html)
    out_dir = Path(settings.STORAGE_DIR) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.pdf"
    fpath = out_dir / fname
    HTML(string=html_str).write_pdf(str(fpath))
    return str(fpath.relative_to(settings.STORAGE_DIR))
