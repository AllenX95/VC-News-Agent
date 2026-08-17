"""Versioned, self-contained HTML renderer for daily report data."""

from __future__ import annotations

from pathlib import Path
import os
import tempfile

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .report_data import validate_report_data


_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def render_daily_html(report_data: dict, output_path: Path) -> Path:
    """Render validated report data to one standalone HTML file.

    Jinja autoescaping is intentionally enabled for both text and attributes;
    report data keeps raw database text and URLs so the JSON contract remains
    useful outside HTML.
    """

    errors = validate_report_data(report_data)
    if errors:
        raise ValueError("invalid report data: " + "; ".join(errors))

    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("daily_v1.html.j2")
    rendered = template.render(report=report_data)

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep the hand-off atomic for callers that expose a latest HTML pointer.
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            if not rendered.endswith("\n"):
                handle.write("\n")
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return target


__all__ = ["render_daily_html"]
