<div id="zerox-theme-admin" class="zerox-theme-admin" aria-labelledby="zerox-theme-title">
  <header class="zerox-theme-admin__hero">
    <div><span class="zerox-theme-admin__eyebrow">ADMINISTRATION / APPEARANCE</span><h1 id="zerox-theme-title">ZeroX Theme</h1><p>Manage the panel theme directly from its own admin tab.</p></div>
    <label class="zerox-theme-switch"><input id="zerox-theme-enabled" type="checkbox"><span></span> Theme enabled</label>
  </header>
  <div id="zerox-theme-status" class="zerox-theme-status" role="status" aria-live="polite"></div>
  <div class="zerox-theme-admin__layout">
    <form id="zerox-theme-form" class="zerox-theme-panel">
      <nav class="zerox-theme-tabs" aria-label="ZeroX Theme settings">
        <button type="button" class="is-active" data-zerox-tab="appearance" aria-selected="true">Appearance</button>
        <button type="button" data-zerox-tab="background" aria-selected="false">Background</button>
        <button type="button" data-zerox-tab="layout" aria-selected="false">Layout</button>
        <button type="button" data-zerox-tab="branding" aria-selected="false">Branding & Footer</button>
        <button type="button" data-zerox-tab="advanced" aria-selected="false">Advanced</button>
      </nav>
      <section class="zerox-theme-tab is-active" data-zerox-panel="appearance">
        <h2>Brand & colours</h2><div class="zerox-theme-presets" id="zerox-theme-presets"></div>
        <div class="zerox-theme-fields">
          <label>Accent <input name="accent" type="color"></label><label>Secondary <input name="accentAlt" type="color"></label>
          <label>Background <input name="background" type="color"></label><label>Surface <input name="surface" type="color"></label>
          <label>Text <input name="text" type="color"></label><label>Muted text <input name="muted" type="color"></label>
          <label>Success <input name="success" type="color"></label><label>Danger <input name="danger" type="color"></label>
        </div>
      </section>
      <section class="zerox-theme-tab" data-zerox-panel="background" hidden>
        <h2>Panel background</h2><label class="zerox-theme-wide">Background image URL <input name="backgroundImage" type="url" placeholder="https://…"></label>
        <div class="zerox-theme-fields"><label>Image strength <input name="backgroundOpacity" type="range" min="0" max="0.85" step="0.01"></label></div>
      </section>
      <section class="zerox-theme-tab" data-zerox-panel="layout" hidden>
        <h2>Layout</h2><div class="zerox-theme-fields">
          <label>Glass blur <input name="blur" type="range" min="0" max="32" step="1"></label><label>Corner radius <input name="radius" type="range" min="0" max="28" step="1"></label>
          <label>Font scale <input name="fontScale" type="range" min="0.85" max="1.2" step="0.05"></label><label>Density <select name="density"><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
        </div>
      </section>
      <section class="zerox-theme-tab" data-zerox-panel="branding" hidden>
        <h2>Branding & footer</h2>
        <div class="zerox-theme-fields">
          <label>Panel brand name <input name="brandName" type="text" maxlength="80" placeholder="ZeroX Host"></label>
          <label>Footer text <input name="footerText" type="text" maxlength="160" placeholder="ZeroX Host © 2025 - 2026"></label>
          <label>Footer link <input name="footerUrl" type="url" placeholder="https://example.com"></label>
        </div>
        <div class="zerox-theme-checks"><label><input name="hideFooter" type="checkbox"> Hide panel footer completely</label></div>
      </section>
      <section class="zerox-theme-tab" data-zerox-panel="advanced" hidden>
        <h2>Accessibility & portability</h2><div class="zerox-theme-checks"><label><input name="reduceMotion" type="checkbox"> Reduce motion</label><label><input name="highContrast" type="checkbox"> Higher contrast</label></div>
        <label class="zerox-theme-wide">Custom CSS <textarea name="customCss" rows="10" maxlength="20000" placeholder="/* Advanced panel-wide overrides */"></textarea></label>
        <div class="zerox-theme-actions"><button type="button" id="zerox-theme-reset">Reset defaults</button><button type="button" id="zerox-theme-export">Download configuration</button><label class="zerox-theme-import">Import JSON<input id="zerox-theme-import" type="file" accept="application/json"></label></div>
      </section>
      <footer class="zerox-theme-savebar"><span id="zerox-theme-dirty">All changes saved</span><button type="submit" id="zerox-theme-save" class="zerox-theme-primary">Save Theme Settings</button></footer>
    </form>
    <aside class="zerox-theme-preview" aria-label="Theme preview"><div class="zerox-theme-preview__nav"><i></i><b>Your panel</b><span>● Online</span></div><div class="zerox-theme-preview__content"><p class="zerox-theme-preview__eyebrow">SERVER OVERVIEW</p><h2>Production server</h2><div class="zerox-theme-preview__stats"><div><small>CPU</small><b>42%</b></div><div><small>MEMORY</small><b>2.4 GB</b></div></div><div class="zerox-theme-preview__bar"><i></i></div><button>Manage server</button></div></aside>
  </div>
</div>
<!-- ZEROX_ADMIN_SCRIPT: replaced with the isolated admin bundle at build time. -->
