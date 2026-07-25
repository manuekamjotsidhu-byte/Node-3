(() => {
  'use strict';
  // The admin bundle is loaded independently from the dashboard bundle by
  // Blueprint. Bootstrap the same settings API here so the enable switch and
  // live preview always work on the extension settings page.
  if (!window.ZeroXTheme) {
    const KEY = 'zerox-theme-settings-v1';
    const COOKIE = 'zerox_theme_settings';
    const defaults = {
      enabled: true, preset: 'amethyst', accent: '#8b5cf6', accentAlt: '#22d3ee',
      success: '#34d399', danger: '#fb7185', background: '#080b1c',
      surface: '#11162d', text: '#f8fafc', muted: '#aab2c8', backgroundImage: '',
      backgroundOpacity: 0.28, blur: 16, radius: 16, density: 'comfortable',
      fontScale: 1, reduceMotion: false, highContrast: false,
    };
    const validHex = value => /^#[0-9a-f]{6}$/i.test(value);
    const clamp = (value, min, max, fallback) => {
      const number = Number(value);
      return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : fallback;
    };
    const safeUrl = value => {
      if (!value) return '';
      try {
        const url = new URL(value, window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
      } catch (_) { return ''; }
    };
    const sanitise = input => {
      const source = input && typeof input === 'object' ? input : {};
      const result = { ...defaults };
      ['accent', 'accentAlt', 'success', 'danger', 'background', 'surface', 'text', 'muted']
        .forEach(key => { if (validHex(source[key])) result[key] = source[key]; });
      result.enabled = source.enabled !== false;
      result.preset = String(source.preset || defaults.preset).slice(0, 32);
      result.backgroundImage = safeUrl(source.backgroundImage);
      result.backgroundOpacity = clamp(source.backgroundOpacity, 0, 0.85, defaults.backgroundOpacity);
      result.blur = clamp(source.blur, 0, 32, defaults.blur);
      result.radius = clamp(source.radius, 0, 28, defaults.radius);
      result.fontScale = clamp(source.fontScale, 0.85, 1.2, defaults.fontScale);
      result.density = ['comfortable', 'compact'].includes(source.density) ? source.density : defaults.density;
      result.reduceMotion = Boolean(source.reduceMotion);
      result.highContrast = Boolean(source.highContrast);
      return result;
    };
    const read = () => {
      try {
        const stored = localStorage.getItem(KEY);
        if (stored) return sanitise(JSON.parse(stored));
      } catch (_) { /* Fall through to cookie persistence. */ }
      try {
        const value = document.cookie.split('; ').find(item => item.startsWith(`${COOKIE}=`));
        if (value) return sanitise(JSON.parse(decodeURIComponent(value.slice(COOKIE.length + 1))));
      } catch (_) { /* Use defaults below. */ }
      return { ...defaults };
    };
    const apply = raw => {
      const settings = sanitise(raw);
      const root = document.documentElement;
      root.classList.toggle('zerox-theme', settings.enabled);
      root.classList.toggle('zerox-theme-compact', settings.enabled && settings.density === 'compact');
      root.classList.toggle('zerox-theme-reduce-motion', settings.enabled && settings.reduceMotion);
      root.classList.toggle('zerox-theme-high-contrast', settings.enabled && settings.highContrast);
      const variables = {
        '--zerox-theme-accent': settings.accent, '--zerox-theme-accent-alt': settings.accentAlt,
        '--zerox-theme-success': settings.success, '--zerox-theme-danger': settings.danger,
        '--zerox-theme-bg': settings.background, '--zerox-theme-surface': settings.surface,
        '--zerox-theme-text': settings.text, '--zerox-theme-muted': settings.muted,
        '--zerox-theme-bg-opacity': settings.backgroundOpacity,
        '--zerox-theme-blur': `${settings.blur}px`, '--zerox-theme-radius': `${settings.radius}px`,
        '--zerox-theme-font-scale': settings.fontScale,
        '--zerox-theme-bg-image': settings.backgroundImage ? `url("${settings.backgroundImage.replace(/["\\]/g, '')}")` : 'none',
      };
      Object.entries(variables).forEach(([key, value]) => root.style.setProperty(key, value));
      return settings;
    };
    const save = value => {
      const settings = sanitise(value);
      const serialised = JSON.stringify(settings);
      try { localStorage.setItem(KEY, serialised); } catch (_) { /* Cookie remains available. */ }
      document.cookie = `${COOKIE}=${encodeURIComponent(serialised)}; path=/; max-age=31536000; SameSite=Lax`;
      apply(settings);
      return settings;
    };
    window.ZeroXTheme = { key: KEY, defaults: { ...defaults }, sanitise, read, apply, save, reset: () => save(defaults) };
    apply(read());
  }

  const start = () => {
    const form = document.querySelector('#zerox-theme-form');
    if (!form || !window.ZeroXTheme) return;
    const status = document.querySelector('#zerox-theme-status');
    const enabled = document.querySelector('#zerox-theme-enabled');
    const dirty = document.querySelector('#zerox-theme-dirty');
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
    document.querySelectorAll('[data-zerox-tab]').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('[data-zerox-tab]').forEach(item => {
          const active = item === tab;
          item.classList.toggle('is-active', active);
          item.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll('[data-zerox-panel]').forEach(panel => {
          const active = panel.dataset.zeroxPanel === tab.dataset.zeroxTab;
          panel.classList.toggle('is-active', active);
          panel.hidden = !active;
        });
      });
    });
    form.addEventListener('input', () => {
      window.ZeroXTheme.apply(readForm());
      dirty.textContent = 'Unsaved changes';
      dirty.classList.add('is-dirty');
    });
    enabled.addEventListener('change', () => {
      writeForm(window.ZeroXTheme.save(readForm()));
      announce(enabled.checked ? 'ZeroX Theme enabled.' : 'ZeroX Theme disabled.');
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      writeForm(window.ZeroXTheme.save(readForm()));
      dirty.textContent = 'All changes saved';
      dirty.classList.remove('is-dirty');
      announce('ZeroX Theme settings saved successfully.');
    });
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
