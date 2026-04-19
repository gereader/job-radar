"""Cover letter template rendering. Pure string substitution.

If the application has cached ``app_answers`` rows, they win over the
generic placeholders. ``why_company`` lifts into ``hook_paragraph``;
``biggest_challenge`` or ``proudest_project`` lifts into ``fit_paragraph``.
"""

from __future__ import annotations

from datetime import date

from jinja2 import Environment, StrictUndefined


def render_cover_template(
    template: str,
    profile: dict,
    *,
    company: str,
    role: str,
    cached_answers: dict[str, str] | None = None,
    referral_name: str | None = None,
) -> str:
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    ident = profile.get("identity", {})
    cached_answers = cached_answers or {}
    hook = cached_answers.get("why_company") or (
        f"I'm writing about the {role} role at {company}."
    )
    fit = (
        cached_answers.get("biggest_challenge")
        or cached_answers.get("proudest_project")
        or "[One paragraph on why this matches your background.]"
    )
    if referral_name:
        hook = (
            f"{referral_name} suggested I reach out about the {role} role "
            f"at {company}. " + hook
        )
    ctx = {
        "date": date.today().isoformat(),
        "company": company,
        "role": role,
        "hiring_manager_or_team": "Hiring Team",
        "greeting_target": "Hiring Team",
        "full_name": ident.get("name", ""),
        "email": ident.get("email", ""),
        "phone": ident.get("phone", ""),
        "hook_paragraph": hook,
        "fit_paragraph": fit,
        "close_paragraph": "Happy to share more in a call. Thanks for reading.",
        "referral_name": referral_name or "",
    }
    try:
        return env.from_string(template).render(**ctx)
    except Exception:
        out = template
        for k, v in ctx.items():
            out = out.replace("{{" + k + "}}", str(v))
        return out
