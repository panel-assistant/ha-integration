import { FRONTEND_TRANSLATIONS } from './translations/index.mjs';

export const ENGLISH_MESSAGES = FRONTEND_TRANSLATIONS.en;
const SUPPORTED = ['en', 'de', 'es', 'fr', 'it', 'nl', 'pl', 'uk', 'zh-Hans'];

export function frontendLocale(signal) {
  if (typeof signal !== 'string') return 'en';
  const tag = signal.trim().replaceAll('_', '-').toLowerCase();
  if (tag === 'zh' || tag === 'zh-cn' || tag.startsWith('zh-cn-') || tag === 'zh-sg' || tag.startsWith('zh-sg-') || tag === 'zh-hans' || tag.startsWith('zh-hans-')) return 'zh-Hans';
  const base = tag.split('-')[0];
  return SUPPORTED.includes(base) ? base : 'en';
}

export function frontendMessages(group, language, catalogues = FRONTEND_TRANSLATIONS) {
  const english = ENGLISH_MESSAGES[group];
  const translated = catalogues[frontendLocale(language)]?.[group];
  const placeholders = text => [...text.matchAll(/\{([A-Za-z_]+)\}/g)].map(match => match[1]).sort().join(',');
  return Object.fromEntries(Object.entries(english).map(([key, value]) => {
    const candidate = translated?.[key];
    return [key, typeof candidate === 'string' && (candidate || !value) && placeholders(candidate) === placeholders(value) ? candidate : value];
  }));
}

export function formatFrontendMessage(template, values) {
  return template.replace(/\{([A-Za-z_]+)\}/g, (match, key) => Object.hasOwn(values, key) ? String(values[key]) : match);
}

export function installerLocale(location = globalThis.location, navigator = globalThis.navigator) {
  const explicit = new URLSearchParams(location?.search ?? '').get('lang');
  if (explicit !== null) return frontendLocale(explicit);
  return frontendLocale(navigator?.languages?.[0] ?? navigator?.language);
}
