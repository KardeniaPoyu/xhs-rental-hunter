"""Build a deterministic Skill archive using an explicit public-file allowlist."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


def build() -> Path:
    root = Path(__file__).resolve().parents[1]
    allowed = [
        "SKILL.md",
        "README.md",
        "CHANGELOG.md",
        "docs/*.md",
        "docs/pricing-evidence.json",
        "LICENSE",
        "pyproject.toml",
        "rental_hunter/*.py",
        "references/*.md",
        "scripts/bridge_server.py",
        "scripts/xhs_rental_pipeline.py",
        "scripts/xhs/bridge.py",
        "scripts/xhs/bridge_auth.py",
        "scripts/xhs/request_state.py",
        "scripts/xhs/errors.py",
        "scripts/xhs/urls.py",
        "scripts/xhs/__init__.py",
        "extension/*.js",
        "extension/*.html",
        "extension/manifest.json",
    ]
    paths = sorted({p for pattern in allowed for p in root.glob(pattern) if p.is_file()})
    if (root / "SKILL.md") not in paths:
        raise RuntimeError("SKILL.md missing")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root, text=True
        ).strip()
    )
    files = {}
    output = root / "dist"
    output.mkdir(exist_ok=True)
    target = output / f"{root.name}-skill.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if path.is_symlink():
                raise ValueError(f"Symlink not allowed: {path}")
            content = path.read_bytes()
            if len(content) > 20_000_000:
                raise ValueError(f"File exceeds 20 MB: {path}")
            name = path.relative_to(root).as_posix()
            files[name] = hashlib.sha256(content).hexdigest()
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, content)
        manifest = {
            "schema_version": 1,
            "git_revision": revision,
            "dirty": dirty,
            "files": files,
            "note": "MIT source; no private media, tokens or API keys",
        }
        archive.writestr(
            zipfile.ZipInfo("PACKAGE.json", (2026, 1, 1, 0, 0, 0)),
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{digest}  {target.name}\n", "utf-8")
    print(
        json.dumps(
            {
                "archive": str(target),
                "bytes": target.stat().st_size,
                "files": len(files),
                "sha256": digest,
                "dirty": dirty,
            }
        )
    )
    return target


if __name__ == "__main__":
    build()
