'use strict';

const assert = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');

const classes = new Set();
const storage = new Map();
const root = {
  classList: { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } },
  style: { setProperty() {} },
  dataset: {},
};
const head = { appendChild() {} };
const document = {
  cookie: '', documentElement: root, head,
  createElement() { return { id: '', textContent: '' }; },
  querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
};
const context = {
  window: { location: { origin: 'https://panel.example' }, dispatchEvent() {}, setTimeout() {} },
  document,
  localStorage: { getItem(key) { return storage.get(key) ?? null; }, setItem(key, value) { storage.set(key, value); } },
  CustomEvent: function CustomEvent() {}, URL,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('blueprint/zerox-theme/theme.js', 'utf8'), context);

assert(context.window.ZeroXTheme, 'theme runtime was not exported');
context.window.ZeroXTheme.save({ ...context.window.ZeroXTheme.defaults, enabled: false });
assert(classes.has('zerox-theme-disabled'), 'disabled state was not applied');
context.window.ZeroXTheme.save({ ...context.window.ZeroXTheme.defaults, enabled: true, footerText: 'Custom footer' });
assert(!classes.has('zerox-theme-disabled'), 'enabled state was not restored');
assert.equal(context.window.ZeroXTheme.read().enabled, true, 'enabled setting did not persist');
assert.equal(context.window.ZeroXTheme.read().footerText, 'Custom footer', 'branding did not persist');
assert(document.cookie.includes('zerox_theme_settings='), 'cookie fallback was not written');
console.log('ZeroX Theme runtime persistence passed');
