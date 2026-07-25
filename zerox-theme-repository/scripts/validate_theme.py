#!/usr/bin/env python3
"""Static safety checks for the distributable ZeroX Theme Blueprint theme."""
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "blueprint" / "zerox-theme"
required = ["conf.yml", "admin.blade.php", "theme.css", "theme.js", "admin.css", "admin.js", "public/defaults.json", "icon.svg"]
missing = [name for name in required if not (ROOT / name).is_file()]
assert not missing, f"Missing theme files: {', '.join(missing)}"
defaults = json.loads((ROOT / "public/defaults.json").read_text())
assert defaults["enabled"] is True and re.fullmatch(r"#[0-9a-fA-F]{6}", defaults["accent"])
css = (ROOT / "theme.css").read_text()
assert ":root.zerox-theme" in css
assert "javascript:" in (ROOT / "conf.yml").read_text()
for line in (line.strip() for line in css.splitlines()):
    if line.endswith("{") and not line.startswith(("@", ":root.zerox-theme", "/*")):
        raise AssertionError(f"Found unscoped theme selector: {line[:-1].strip()}")
if shutil.which("node"):
    for script in ("theme.js", "admin.js"):
        subprocess.run(["node", "--check", str(ROOT / script)], check=True)
release = ROOT.parents[1] / "dist" / "zerox-theme.zip"
assert release.exists(), "Build dist/zerox-theme.zip before validating the release"
with zipfile.ZipFile(release) as archive:
    release_files = set(archive.namelist())
    assert {"zerox-theme.blueprint", "install.sh", "SHA256SUMS"}.issubset(release_files)
    expected_source = {f"source/zerox-theme/{name}" for name in required}
    assert expected_source.issubset(release_files), "Editable theme source is incomplete"
    with zipfile.ZipFile(archive.open("zerox-theme.blueprint")) as package:
        assert set(required).issubset(package.namelist())
        packaged_admin = package.read("admin.blade.php")
        assert b"window.ZeroXTheme" in packaged_admin, "Admin runtime was not embedded"
        assert b"ZEROX_ADMIN_SCRIPT" not in packaged_admin, "Admin build marker was not replaced"
        packaged_text = b"\n".join(package.read(name) for name in package.namelist())
        assert b"nebula" not in packaged_text.lower(), "Legacy theme branding remains in the package"
        assert b"ZeroX Theme" in packaged_text, "ZeroX Theme branding is missing"
print("ZeroX Theme validation passed")
