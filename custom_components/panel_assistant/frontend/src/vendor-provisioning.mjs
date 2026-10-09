// The Meta Portal's vendor setup, sent exactly as vendor_provisioning.py
// sends it: Meta's values are recorded on the panel once, then both dream
// components are blanked and Meta's swipe-up bar is denied its overlay. Panel
// Assistant's removal reads that record to put them back. A test compares this
// program with the Python one by value.

/** Meta's codenames for the Portals with a screen (2nd gen, 1st gen). */
export const META_PORTAL_DEVICES = Object.freeze(['omni', 'aloha']);
export const META_SCREENSAVER_COMPONENTS =
  'com.facebook.alohaapps.launcher/com.facebook.aloha.app.home.touch.HomeDreamService,' +
  'com.facebook.alohaapps.superframe/com.facebook.alohaapps.superframe.SuperframeDream';
export const META_SCREENSAVER_DEFAULT_COMPONENT =
  'com.facebook.alohaapps.launcher/com.facebook.aloha.app.home.touch.HomeDreamService';
export const META_CONTROL_CENTER = 'com.facebook.alohaapps.controlcenter';
export const META_RESTORE_RECORD = '/data/local/tmp/panel-assistant-meta-portal-restore';

const SETTINGS = ['screensaver_components', 'screensaver_default_component'];
const OVERLAY_MODES = ['allow', 'ignore', 'deny', 'default', 'foreground'];
const QUIET = '>/dev/null 2>&1';
const fail = () => { throw new Error('vendor_setup_unreadable'); };

const OVERLAY_FUNCTION = 'hp_overlay() { ' +
  `if [ -z "$(pm path ${META_CONTROL_CENTER} 2>/dev/null)" ]; then echo absent; return; fi; ` +
  `hp_mode=$(appops get ${META_CONTROL_CENTER} SYSTEM_ALERT_WINDOW 2>/dev/null) || { echo unknown; return; }; ` +
  'case "$hp_mode" in "SYSTEM_ALERT_WINDOW: "*) ' +
  'hp_mode=${hp_mode#SYSTEM_ALERT_WINDOW: }; hp_mode=${hp_mode%%;*} ;; ' +
  '*"Default mode: "*) hp_mode=${hp_mode##*Default mode: } ;; ' +
  '*) hp_mode=unknown ;; esac; ' +
  `case "$hp_mode" in ${OVERLAY_MODES.join('|')}) echo "$hp_mode" ;; *) echo unknown ;; esac; }`;

export function buildVendorSetup(nonce) {
  if (!/^[a-f0-9]{32}$/.test(nonce)) fail();
  const path = META_RESTORE_RECORD;
  const record = `if [ ! -f ${path} ]; then ` +
    'if hp_c=$(settings get secure screensaver_components) && ' +
    'hp_d=$(settings get secure screensaver_default_component); then ' +
    `[ -n "$hp_c" ] || hp_c='${META_SCREENSAVER_COMPONENTS}'; ` +
    `[ -n "$hp_d" ] || hp_d='${META_SCREENSAVER_DEFAULT_COMPONENT}'; ` +
    'hp_o=$(hp_overlay); case "$hp_o" in deny|unknown) hp_o=allow ;; esac; ' +
    "printf 'screensaver_components=%s\\nscreensaver_default_component=%s\\n" +
    `controlcenter_overlay=%s\\n' "$hp_c" "$hp_d" "$hp_o" ` +
    `> ${path}.part && mv ${path}.part ${path}; ` +
    `rm -f ${path}.part; fi; fi`;
  const showRecord = `if [ -f ${path} ]; then ` +
    'while IFS= read -r hp_line; do echo "record:$hp_line"; ' +
    `done < ${path}; fi`;
  const apply = `if [ -f ${path} ]; then ` +
    SETTINGS.map(name => `hp_value=$(settings get secure ${name}) && [ -z "$hp_value" ] || ` +
      `settings put secure ${name} '' ${QUIET}; `).join('') +
    'case "$(hp_overlay)" in absent|deny) ;; ' +
    `*) appops set ${META_CONTROL_CENTER} SYSTEM_ALERT_WINDOW deny ${QUIET} ;; esac; fi`;
  const readBack = [
    ...SETTINGS.map(name => `hp_value=$(settings get secure ${name}) ` +
      `&& echo "${name}=$hp_value" || echo unreadable:${name}`),
    'echo "controlcenter_overlay=$(hp_overlay)"',
  ];
  return [
    `echo HAPANELD_VENDOR_BEGIN:${nonce}`,
    `case "$(getprop ro.product.device)" in ${META_PORTAL_DEVICES.join('|')}) ` +
      [OVERLAY_FUNCTION, record, showRecord, apply, ...readBack].join('; ') +
      ' ;; *) echo device:other ;; esac',
    `echo HAPANELD_VENDOR_END:${nonce}:0`,
  ].join('; ');
}

const VALUE = /^[\x20-\x7e]{0,4096}$/;

// Null for a panel that is not a Portal; otherwise what it read back, with
// `converged` true only when the dreams are blank, the bar is denied and the
// values to undo it are on record.
export function parseVendorSetup(body, nonce) {
  if (typeof body !== 'string' || body.length > 16384) fail();
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.shift() !== `HAPANELD_VENDOR_BEGIN:${nonce}` || lines.pop() !== '' ||
      lines.pop() !== `HAPANELD_VENDOR_END:${nonce}:0` || lines.some(line => line.includes('HAPANELD_'))) fail();
  if (lines.length === 1 && lines[0] === 'device:other') return null;
  const records = lines.filter(line => line.startsWith('record:')).map(line => line.slice(7));
  const rest = lines.filter(line => !line.startsWith('record:'));
  if (rest.length !== 3 || ![0, 3].includes(records.length)) fail();
  const values = SETTINGS.map((name, index) => {
    if (rest[index] === `unreadable:${name}`) return null;
    if (!rest[index].startsWith(`${name}=`) || !VALUE.test(rest[index])) fail();
    return rest[index].slice(name.length + 1);
  });
  const overlay = rest[2].startsWith('controlcenter_overlay=') ? rest[2].slice(22) : fail();
  if (![...OVERLAY_MODES, 'absent', 'unknown'].includes(overlay)) fail();
  const keys = [...SETTINGS, 'controlcenter_overlay'];
  if (records.some((line, index) => !line.startsWith(`${keys[index]}=`) || !VALUE.test(line))) fail();
  const record = records.length ? records.map(line => line.slice(line.indexOf('=') + 1)) : null;
  return Object.freeze({
    record, screensaverComponents: values[0], screensaverDefaultComponent: values[1],
    controlCenterOverlay: overlay,
    converged: record !== null && values[0] === '' && values[1] === '' &&
      ['deny', 'absent'].includes(overlay),
  });
}
