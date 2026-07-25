# ZeroX Theme for Blueprint

ZeroX Theme is an additive, mobile-first theme for a Blueprint-enabled Pterodactyl
panel. It deliberately does not replace React components, routes, or files
owned by other extensions. The extension adds one namespaced stylesheet and a
small runtime that reads administrator-managed CSS variables.

## One-command installation

Open the repository's **Actions** tab, select **Build ZeroX Theme ZIP**, and
download the `zerox-theme` artifact from the latest successful run. Extract
the GitHub artifact once to get `zerox-theme.zip`, then run this command from
the directory containing it:

```bash
rm -rf /tmp/zerox-theme-install && unzip -q zerox-theme.zip -d /tmp/zerox-theme-install && cd /tmp/zerox-theme-install && sha256sum -c SHA256SUMS && sudo ./install.sh
```

The installer verifies its contents and delegates to `blueprint -install`.
It never copies over panel or addon files itself. After installation, open
**Admin > Extensions > ZeroX Theme** and configure the theme.

The generated ZIP is fully self-contained. It includes the ready to
install `zerox-theme.blueprint`, `install.sh`, `SHA256SUMS`, and every editable
theme file under `source/zerox-theme/`. You do not need to download the source
files separately.

The ZIP is intentionally not committed to Git because it is a binary release
artifact. GitHub Actions builds and validates it from the repository's text
source files on every relevant push, so repository creation and pull-request
diffs remain binary-free.

To rebuild the release archive from source, run:

```bash
python3 scripts/build_theme_release.py
```

The settings page includes live preview, presets, background controls,
accessibility options, import/export, and a reset button. Settings are stored
in the browser and applied immediately; **Download configuration** creates a
portable JSON backup. No credentials or panel data are collected.

The extension appears under **Admin > Extensions > ZeroX Theme** and opens a dedicated settings page with Appearance, Background, Layout, and Advanced tabs. The sticky **Save Theme Settings** button remains available on every tab.

The **Theme enabled** switch saves immediately; it does not require the Save
button. Blueprint loads admin and dashboard JavaScript as separate bundles, so
both bundles initialise the ZeroX Theme runtime independently.

## Compatibility guarantees

- Every custom class and browser key starts with `zerox-theme-`.
- Rules target Pterodactyl's stable semantic elements and use low-specificity
  `:where()` selectors. There are no DOM rewrites.
- Blueprint extension cards, routes, buttons, and forms remain interactive.
- The compact layout is opt-in and the theme respects reduced-motion and
  high-contrast preferences.
- Removing the extension (or disabling it in settings) restores the original
  panel without a database migration.

## Development check

```bash
python3 scripts/validate_theme.py
```

This validates the manifest, default configuration, namespace, required
assets, and JavaScript syntax when Node.js is available.
