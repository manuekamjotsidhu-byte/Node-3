#!/usr/bin/env python3
"""Build deterministic Blueprint and one-zip ZeroX Theme release archives."""
from __future__ import annotations

import hashlib
import stat
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "blueprint" / "zerox-theme"
DIST = ROOT / "dist"
INSTALLER = ROOT / "packaging" / "install.sh"
BLUEPRINT_NAME = "zerox-theme.blueprint"
RELEASE_NAME = "zerox-theme.zip"
EPOCH = (2026, 1, 1, 0, 0, 0)


def add_file(archive: zipfile.ZipFile, source: Path, name: str, executable: bool = False) -> None:
    add_bytes(archive, source.read_bytes(), name, executable)


def add_bytes(archive: zipfile.ZipFile, content: bytes, name: str, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name, EPOCH)
    mode = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
    info.external_attr = mode
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content)


def build() -> Path:
    DIST.mkdir(exist_ok=True)
    release_path = DIST / RELEASE_NAME
    with tempfile.TemporaryDirectory() as temporary:
        blueprint_path = Path(temporary) / BLUEPRINT_NAME
        with zipfile.ZipFile(blueprint_path, "w", compresslevel=9) as archive:
            for source in sorted(path for path in SOURCE.rglob("*") if path.is_file()):
                relative = source.relative_to(SOURCE).as_posix()
                if relative == "admin.blade.php":
                    view = source.read_text(encoding="utf-8")
                    script = (SOURCE / "admin.js").read_text(encoding="utf-8")
                    marker = "<!-- ZEROX_ADMIN_SCRIPT: replaced with the isolated admin bundle at build time. -->"
                    assert marker in view, "Admin script build marker is missing"
                    add_bytes(archive, view.replace(marker, f"<script>\n{script}\n</script>").encode(), relative)
                else:
                    add_file(archive, source, relative)
        digest = hashlib.sha256(blueprint_path.read_bytes()).hexdigest()
        checksum = Path(temporary) / "SHA256SUMS"
        checksum.write_text(f"{digest}  {BLUEPRINT_NAME}\n", encoding="utf-8")
        with zipfile.ZipFile(release_path, "w", compresslevel=9) as archive:
            add_file(archive, blueprint_path, BLUEPRINT_NAME)
            add_file(archive, INSTALLER, "install.sh", executable=True)
            add_file(archive, checksum, "SHA256SUMS")
            # Keep the editable extension source in the GitHub download as well
            # as the installable Blueprint package. This makes the single ZIP
            # self-contained and lets panel owners audit every shipped file.
            for source in sorted(path for path in SOURCE.rglob("*") if path.is_file()):
                name = Path("source") / "zerox-theme" / source.relative_to(SOURCE)
                add_file(archive, source, name.as_posix())
    return release_path


if __name__ == "__main__":
    release = build()
    print(f"Built {release.relative_to(ROOT)}")
