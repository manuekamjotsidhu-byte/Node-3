(() => {
  'use strict';

  const KEY = 'zerox-theme-settings-v1';
  const ROOT_CLASS = 'zerox-theme';
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
    try { return sanitise(JSON.parse(localStorage.getItem(KEY))); }
    catch (_) { return { ...defaults }; }
  };
  const apply = raw => {
    const settings = sanitise(raw);
    const root = document.documentElement;
    root.classList.toggle(ROOT_CLASS, settings.enabled);
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
    window.dispatchEvent(new CustomEvent('zerox-theme:applied', { detail: settings }));
    return settings;
  };
  const save = value => {
    const settings = sanitise(value);
    localStorage.setItem(KEY, JSON.stringify(settings));
    apply(settings);
    return settings;
  };
  window.ZeroXTheme = { key: KEY, defaults: { ...defaults }, sanitise, read, apply, save, reset: () => save(defaults) };
  apply(read());
})();
