"""Comp-band filter. Honors profile.targets.comp.{min,target,max}."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompVerdict:
    ok: bool
    reason: str
    delta_vs_target: int | None = None


def check(
    comp_min: int | None,
    comp_max: int | None,
    target_min: int,
    target_max: int,
) -> CompVerdict:
    if comp_min is None and comp_max is None:
        return CompVerdict(ok=True, reason="comp-unspecified")

    top = comp_max or comp_min or 0
    bottom = comp_min or comp_max or 0

    if top < target_min:
        return CompVerdict(
            ok=False,
            reason=f"ceiling {top} below floor {target_min}",
            delta_vs_target=top - target_min,
        )
    if bottom > target_max:
        return CompVerdict(ok=True, reason=f"floor {bottom} above target max (bonus)")
    return CompVerdict(ok=True, reason=f"in band {bottom}-{top}")
