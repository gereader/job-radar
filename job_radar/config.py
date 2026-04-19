"""Config: locate private/ dir, load profile.yml, expose paths.

The whole personalization model: everything user-specific is under `private/`
(gitignored). System code reads from it, never writes outside of it for
personal data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or CWD) looking for a job-radar pyproject.toml.

    Only matches a pyproject whose [project].name is job-radar, so running
    `jr` from inside an unrelated Python project doesn't hijack its tree.
    Falls back to CWD if nothing matches.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        pp = candidate / "pyproject.toml"
        if pp.exists():
            try:
                text = pp.read_text()
            except OSError:
                continue
            if 'name = "job-radar"' in text:
                return candidate
    return cur


@dataclass
class Config:
    root: Path
    private: Path
    profile: dict[str, Any] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.private / "data" / "career.db"

    @property
    def jds_active(self) -> Path:
        return self.private / "jds" / "active"

    @property
    def jds_archive(self) -> Path:
        return self.private / "jds" / "archive"

    @property
    def applications_dir(self) -> Path:
        return self.private / "applications"

    @property
    def exports_dir(self) -> Path:
        return self.private / "exports"

    @property
    def cv_path(self) -> Path:
        return self.private / "cv.md"

    @property
    def cover_template_path(self) -> Path:
        return self.private / "cover-template.md"

    @property
    def keywords_path(self) -> Path:
        return self.private / "keywords.yml"

    @property
    def portals_path(self) -> Path:
        return self.private / "portals.yml"

    @property
    def story_bank_path(self) -> Path:
        return self.private / "story-bank.md"

    @classmethod
    def load(cls, root: Path | None = None) -> Config:
        r = _find_root(root)
        private = Path(os.environ.get("JOB_RADAR_PRIVATE") or (r / "private"))
        profile_path = private / "profile.yml"
        profile: dict[str, Any] = {}
        if profile_path.exists():
            profile = yaml.safe_load(profile_path.read_text()) or {}
        return cls(root=r, private=private, profile=profile)

    def ensure_dirs(self) -> None:
        for p in (
            self.private,
            self.private / "data",
            self.jds_active,
            self.jds_archive,
            self.applications_dir,
            self.exports_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

    def relpath(self, p: Path) -> str:
        """Path relative to cfg.root, robust when private/ is outside the repo.

        ``Path.relative_to`` raises if the target isn't a subpath; ``os.path.
        relpath`` returns a ``..``-traversal version instead. Both work when
        joined back via ``cfg.root / stored``.
        """
        return os.path.relpath(str(p), str(self.root))
