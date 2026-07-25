# ZeroX Theme — standalone repository

This directory is a complete, independent ZeroX Theme project. It is isolated
from the existing Discord bot: it does not import, modify, install, or depend
on `bot.py`, `config.example.json`, the bot database, or any bot dependency.

To publish it as its own GitHub repository, copy the contents of this directory
to an empty repository. Do not copy the parent project.

## Build

```bash
python3 scripts/build_theme_release.py
python3 scripts/validate_theme.py
```

The generated `dist/zerox-theme.zip` is intentionally ignored by Git. The
included GitHub Actions workflow builds and uploads that ZIP as an artifact.

## Project layout

- `blueprint/zerox-theme/` — theme extension source.
- `packaging/install.sh` — guarded Blueprint installer.
- `scripts/` — deterministic builder and release validator.
- `.github/workflows/` — isolated release workflow for this repository.
