"""Cover letter template rendering. Pure string substitution."""

from __future__ import annotations

from datetime import date

from jinja2 import Environment, StrictUndefined


def render_cover_template(template: str, profile: dict, *, company: str, role: str) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    ident = profile.get("identity", {})
    ctx = {
        "date": date.today().isoformat(),
        "company": company,
        "role": role,
        "hiring_manager_or_team": "Hiring Team",
        "greeting_target": "Hiring Team",
        "full_name": ident.get("name", ""),
        "email": ident.get("email", ""),
        "phone": ident.get("phone", ""),
        "hook_paragraph": f"I'm writing about the {role} role at {company}.",
        "fit_paragraph": "[One paragraph on why this matches your background.]",
        "close_paragraph": "Happy to share more in a call. Thanks for reading.",
    }
    # Convert Jinja-style {{var}} while being forgiving about missing keys.
    try:
        return env.from_string(template).render(**ctx)
    except Exception:
        out = template
        for k, v in ctx.items():
            out = out.replace("{{" + k + "}}", str(v))
        return out
