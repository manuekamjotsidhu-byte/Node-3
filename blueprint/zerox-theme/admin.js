(() => {
  'use strict';
  const start = () => {
    const form = document.querySelector('#zerox-theme-form');
    if (!form || !window.ZeroXTheme) return;
    const status = document.querySelector('#zerox-theme-status');
    const enabled = document.querySelector('#zerox-theme-enabled');
    const presets = {
      amethyst: { accent: '#8b5cf6', accentAlt: '#22d3ee', background: '#080b1c', surface: '#11162d' },
      emerald: { accent: '#10b981', accentAlt: '#60a5fa', background: '#061511', surface: '#0d2420' },
      ember: { accent: '#f97316', accentAlt: '#fb7185', background: '#160b09', surface: '#281310' },
      arctic: { accent: '#38bdf8', accentAlt: '#818cf8', background: '#07111f', surface: '#102037' },
    };
    const announce = message => { status.textContent = message; window.setTimeout(() => { status.textContent = ''; }, 3000); };
    const writeForm = settings => {
      Object.entries(settings).forEach(([key, value]) => {
        const field = form.elements.namedItem(key);
        if (!field) return;
        if (field.type === 'checkbox') field.checked = Boolean(value); else field.value = String(value);
      });
      enabled.checked = settings.enabled;
    };
    const readForm = () => {
      const value = window.ZeroXTheme.read();
      new FormData(form).forEach((item, key) => { value[key] = item; });
      ['reduceMotion', 'highContrast'].forEach(key => { value[key] = form.elements.namedItem(key).checked; });
      value.enabled = enabled.checked;
      return window.ZeroXTheme.sanitise(value);
    };
    Object.entries(presets).forEach(([name, colours]) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = name;
      button.addEventListener('click', () => { writeForm({ ...readForm(), ...colours, preset: name }); window.ZeroXTheme.apply(readForm()); });
      document.querySelector('#zerox-theme-presets').appendChild(button);
    });
    form.addEventListener('input', () => window.ZeroXTheme.apply(readForm()));
    enabled.addEventListener('change', () => window.ZeroXTheme.apply(readForm()));
    form.addEventListener('submit', event => { event.preventDefault(); writeForm(window.ZeroXTheme.save(readForm())); announce('Theme settings saved on this browser.'); });
    document.querySelector('#zerox-theme-reset').addEventListener('click', () => { writeForm(window.ZeroXTheme.reset()); announce('Defaults restored.'); });
    document.querySelector('#zerox-theme-export').addEventListener('click', () => {
      const blob = new Blob([JSON.stringify(readForm(), null, 2)], { type: 'application/json' });
      const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'zerox-theme.json'; link.click(); URL.revokeObjectURL(link.href);
    });
    document.querySelector('#zerox-theme-import').addEventListener('change', async event => {
      const file = event.target.files[0]; if (!file) return;
      try { writeForm(window.ZeroXTheme.save(JSON.parse(await file.text()))); announce('Configuration imported.'); }
      catch (_) { announce('That configuration file is not valid JSON.'); }
      event.target.value = '';
    });
    writeForm(window.ZeroXTheme.read());
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', start) : start();
})();
