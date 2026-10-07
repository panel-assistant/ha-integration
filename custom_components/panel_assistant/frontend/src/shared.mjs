// Pure helpers the installer modules share, so each rule has one definition.
export const hex = bytes => Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
export const newNonce = (source = crypto) => hex(source.getRandomValues(new Uint8Array(16)));
export const fullMatch = (pattern, value) => typeof value === 'string' && pattern.exec(value)?.[0] === value;
export const exactKeys = (value, expected) => value !== null && typeof value === 'object' &&
  !Array.isArray(value) && Object.keys(value).length === expected.length &&
  expected.every(key => Object.hasOwn(value, key));
