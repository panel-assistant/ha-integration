// Panel Assistant's custom support on the USB install path, sent exactly as
// vendor_provisioning.py sends it; that module lists the panels that use it and
// why. The program selects itself: a Meta Portal by its codename, a Shelly Wall
// Display by its launcher, and any other panel is left alone.
//
// The Meta Portal's vendor setup: Meta's values are recorded on the panel once, then both dream
// components are blanked, Meta's swipe-up bar is denied its overlay, and Meta's
// package verifier, which refuses the app's own updates, is switched off under
// a record of its own. Panel Assistant's removal reads those records to put
// them back. A test compares this program with the Python one by value.

/** Meta's codenames for the Portals with a screen (2nd gen, 1st gen). */
export const META_PORTAL_DEVICES = Object.freeze(['omni', 'aloha']);
export const META_SCREENSAVER_COMPONENTS =
  'com.facebook.alohaapps.launcher/com.facebook.aloha.app.home.touch.HomeDreamService,' +
  'com.facebook.alohaapps.superframe/com.facebook.alohaapps.superframe.SuperframeDream';
export const META_SCREENSAVER_DEFAULT_COMPONENT =
  'com.facebook.alohaapps.launcher/com.facebook.aloha.app.home.touch.HomeDreamService';
export const META_CONTROL_CENTER = 'com.facebook.alohaapps.controlcenter';
export const META_RESTORE_RECORD = '/data/local/tmp/panel-assistant-meta-portal-restore';
export const SHELLY_LAUNCHER = 'cloud.shelly.stargate';
export const SHELLY_RESTORE_RECORD = '/data/local/tmp/panel-assistant-shelly-restore';
export const META_VERIFIER_RECORD = '/data/local/tmp/panel-assistant-meta-portal-verifier-restore';
const VERIFIER = 'package_verifier_enable';

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

const SHELLY_OVERLAY_FUNCTION = OVERLAY_FUNCTION.replace('hp_overlay()', 'hp_shelly_overlay()')
  .replaceAll(META_CONTROL_CENTER, SHELLY_LAUNCHER);

// A panel with Shelly's launcher: its overlay mode is recorded once, only when
// Android reports it, and then denied, as vendor_provisioning.py does.
function shellySetup() {
  const path = SHELLY_RESTORE_RECORD;
  return [
    SHELLY_OVERLAY_FUNCTION,
    'echo vendor:shelly',
    `if [ ! -f ${path} ]; then ` +
      'hp_o=$(hp_shelly_overlay); case "$hp_o" in ' +
      'allow|ignore|deny|default|foreground) ' +
      `printf 'stargate_overlay=%s\\n' "$hp_o" > ${path}.part && ` +
      `mv ${path}.part ${path}; rm -f ${path}.part ;; esac; fi`,
    `if [ -f ${path} ]; then ` +
      'while IFS= read -r hp_line; do echo "record:$hp_line"; ' +
      `done < ${path}; ` +
      'case "$(hp_shelly_overlay)" in absent|deny) ;; ' +
      `*) appops set ${SHELLY_LAUNCHER} SYSTEM_ALERT_WINDOW deny ${QUIET} ;; esac; fi`,
    'echo "stargate_overlay=$(hp_shelly_overlay)"',
  ].join('; ');
}

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
  const verifier = META_VERIFIER_RECORD;
  const verifierRecord = `if [ ! -f ${verifier} ]; then ` +
    `if hp_v=$(settings get global ${VERIFIER}); then ` +
    '[ "$hp_v" != 0 ] || hp_v=1; ' +
    `printf '${VERIFIER}=%s\\n' "$hp_v" ` +
    `> ${verifier}.part && mv ${verifier}.part ${verifier}; ` +
    `rm -f ${verifier}.part; fi; fi`;
  const showVerifier = `if [ -f ${verifier} ]; then ` +
    'while IFS= read -r hp_line; do echo "verifier_record:$hp_line"; ' +
    `done < ${verifier}; fi`;
  const applyVerifier = `if [ -f ${verifier} ]; then ` +
    `hp_value=$(settings get global ${VERIFIER}) && [ "$hp_value" = 0 ] || ` +
    `settings put global ${VERIFIER} 0 ${QUIET}; fi`;
  const readBack = [
    ...SETTINGS.map(name => `hp_value=$(settings get secure ${name}) ` +
      `&& echo "${name}=$hp_value" || echo unreadable:${name}`),
    'echo "controlcenter_overlay=$(hp_overlay)"',
    `hp_value=$(settings get global ${VERIFIER}) ` +
      `&& echo "${VERIFIER}=$hp_value" || echo unreadable:${VERIFIER}`,
  ];
  return [
    `echo HAPANELD_VENDOR_BEGIN:${nonce}`,
    `case "$(getprop ro.product.device)" in ${META_PORTAL_DEVICES.join('|')}) ` +
      [OVERLAY_FUNCTION, record, showRecord, apply, verifierRecord, showVerifier, applyVerifier, ...readBack].join('; ') +
      ` ;; *) if [ -n "$(pm path ${SHELLY_LAUNCHER} 2>/dev/null)" ]; then ` +
      shellySetup() + '; else echo device:other; fi ;; esac',
    `echo HAPANELD_VENDOR_END:${nonce}:0`,
  ].join('; ');
}

const VALUE = /^[\x20-\x7e]{0,4096}$/;

// Null for a panel that is not a Portal; otherwise what it read back, with
// `converged` true only when the dreams are blank, the bar is denied, Meta's
// package verifier is off and the values to undo all three are on record.
export function parseVendorSetup(body, nonce) {
  if (typeof body !== 'string' || body.length > 16384) fail();
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.shift() !== `HAPANELD_VENDOR_BEGIN:${nonce}` || lines.pop() !== '' ||
      lines.pop() !== `HAPANELD_VENDOR_END:${nonce}:0` || lines.some(line => line.includes('HAPANELD_'))) fail();
  if (lines.length === 1 && lines[0] === 'device:other') return null;
  if (lines[0] === 'vendor:shelly') return parseShellySetup(lines.slice(1));
  const records = lines.filter(line => line.startsWith('record:')).map(line => line.slice(7));
  const verifierRecords = lines.filter(line => line.startsWith('verifier_record:')).map(line => line.slice(16));
  const rest = lines.filter(line => !line.startsWith('record:') && !line.startsWith('verifier_record:'));
  if (rest.length !== 4 || ![0, 3].includes(records.length) || verifierRecords.length > 1) fail();
  const values = [[SETTINGS[0], rest[0]], [SETTINGS[1], rest[1]], [VERIFIER, rest[3]]].map(([name, line]) => {
    if (line === `unreadable:${name}`) return null;
    if (!line.startsWith(`${name}=`) || !VALUE.test(line)) fail();
    return line.slice(name.length + 1);
  });
  const overlay = rest[2].startsWith('controlcenter_overlay=') ? rest[2].slice(22) : fail();
  if (![...OVERLAY_MODES, 'absent', 'unknown'].includes(overlay)) fail();
  const keys = [...SETTINGS, 'controlcenter_overlay'];
  if (records.some((line, index) => !line.startsWith(`${keys[index]}=`) || !VALUE.test(line))) fail();
  const record = records.length ? records.map(line => line.slice(line.indexOf('=') + 1)) : null;
  if (verifierRecords.some(line => !line.startsWith(`${VERIFIER}=`) || !VALUE.test(line))) fail();
  const verifierRecord = verifierRecords.length ? verifierRecords[0].slice(VERIFIER.length + 1) : null;
  return Object.freeze({
    record, screensaverComponents: values[0], screensaverDefaultComponent: values[1],
    controlCenterOverlay: overlay, verifierRecord, packageVerifierEnable: values[2],
    converged: record !== null && values[0] === '' && values[1] === '' &&
      ['deny', 'absent'].includes(overlay) && verifierRecord !== null && values[2] === '0',
  });
}

// A panel with Shelly's launcher: `converged` only when its overlay is denied
// and the mode to undo that is on record.
function parseShellySetup(lines) {
  const records = lines.filter(line => line.startsWith('record:'));
  const rest = lines.filter(line => !line.startsWith('record:'));
  if (records.length > 1 || rest.length !== 1 || !rest[0].startsWith('stargate_overlay=')) fail();
  const overlay = rest[0].slice(17);
  if (![...OVERLAY_MODES, 'absent', 'unknown'].includes(overlay)) fail();
  let record = null;
  if (records.length) {
    if (!records[0].startsWith('record:stargate_overlay=')) fail();
    record = records[0].slice(24);
    if (!OVERLAY_MODES.includes(record)) fail();
  }
  return Object.freeze({vendor: 'shelly', record, launcherOverlay: overlay,
    converged: record !== null && ['deny', 'absent'].includes(overlay)});
}
