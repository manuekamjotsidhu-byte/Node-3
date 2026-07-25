(() => {
  'use strict';

  const KEY = 'zerox-theme-settings-v1';
  const ROOT_CLASS = 'zerox-theme';
  const COOKIE = 'zerox_theme_settings';
  const defaults = {
    enabled: true, preset: 'amethyst', accent: '#8b5cf6', accentAlt: '#22d3ee',
    success: '#34d399', danger: '#fb7185', background: '#080b1c',
    surface: '#11162d', text: '#f8fafc', muted: '#aab2c8', backgroundImage: '',
    backgroundOpacity: 0.28, blur: 16, radius: 16, density: 'comfortable',
    fontScale: 1, reduceMotion: false, highContrast: false,
    brandName: 'ZeroX Host', footerText: 'ZeroX Host © 2025 - 2026', footerUrl: '', hideFooter: false, customCss: '',
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
    result.brandName = String(source.brandName ?? defaults.brandName).slice(0, 80);
    result.footerText = String(source.footerText ?? defaults.footerText).slice(0, 160);
    result.footerUrl = safeUrl(source.footerUrl);
    result.hideFooter = Boolean(source.hideFooter);
    result.customCss = String(source.customCss ?? '').slice(0, 20000);
    return result;
  };
  const read = () => {
    try {
      const stored = localStorage.getItem(KEY);
      if (stored) return sanitise(JSON.parse(stored));
    } catch (_) { /* Fall through to the panel-wide path cookie. */ }
    try {
      const value = document.cookie.split('; ').find(item => item.startsWith(`${COOKIE}=`));
      if (value) return sanitise(JSON.parse(decodeURIComponent(value.slice(COOKIE.length + 1))));
    } catch (_) { /* Use defaults if persisted data is invalid. */ }
    return { ...defaults };
  };
  const apply = raw => {
    const settings = sanitise(raw);
    const root = document.documentElement;
    root.classList.toggle(ROOT_CLASS, settings.enabled);
    root.classList.toggle('zerox-theme-disabled', !settings.enabled);
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
    let customStyle = document.querySelector('#zerox-theme-custom-css');
    if (!customStyle) {
      customStyle = document.createElement('style');
      customStyle.id = 'zerox-theme-custom-css';
      document.head.appendChild(customStyle);
    }
    customStyle.textContent = settings.enabled ? settings.customCss : '';
    const footer = document.querySelector('footer, [class*="Footer"], #footer');
    if (footer) {
      footer.hidden = settings.hideFooter;
      const target = footer.querySelector('a') || footer;
      if (settings.footerText) target.textContent = settings.footerText;
      if (target.tagName === 'A' && settings.footerUrl) target.href = settings.footerUrl;
    }
    document.querySelectorAll('.logo-lg, [data-zerox-brand-target]').forEach(target => {
      if (settings.brandName && target.textContent !== settings.brandName) target.textContent = settings.brandName;
    });
    document.documentElement.dataset.zeroxBrand = settings.brandName;
    window.dispatchEvent(new CustomEvent('zerox-theme:applied', { detail: settings }));
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
  document.addEventListener('click', () => window.setTimeout(() => apply(read()), 120), true);
})();
