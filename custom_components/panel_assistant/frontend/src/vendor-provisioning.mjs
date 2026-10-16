import { PACKAGE_ID } from './app-identity.mjs';

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


// Keep this program equal to vendor_provisioning.py. Client dumps execute on
// the vendor main Looper; process death completes its pending shutdown hook.
const DISPLAY_SETTLEMENT_PROGRAM = String.raw`
hp_component=cloud.shelly.stargate/.activities.MainActivity
hp_vendor_dump() {
  hp_dump=$(timeout 2 dumpsys activity "$hp_component" 2>/dev/null) || return 1
  hp_dump_pid=$(printf '%s\n' "$hp_dump" |
    grep -F "ACTIVITY $hp_component " | sed 's/.* pid=//')
  hp_dump_state=$(printf '%s\n' "$hp_dump" |
    sed -n 's/^[[:space:]]*\(mResumed=.* mFinished=.*\)$/\1/p')
  hp_dump_config=$(printf '%s\n' "$hp_dump" |
    sed -n 's/^[[:space:]]*mCurrentConfig={\(.*\)$/\1/p')
  case "$hp_dump_pid" in ''|*[!0-9]*) return 1 ;; esac
  [ "$hp_dump_pid" = "$(pidof cloud.shelly.stargate)" ] &&
    [ -n "$hp_dump_state" ]
}
hp_font() {
  printf '%s\n' "$1" | grep -Eq '^[0-9]+([.][0-9]+)?$' || return 1
  case "$1" in *.*) printf '%s\n' "$1" | sed 's/0*$//;s/\.$//' ;;
    *) printf '%s\n' "$1" ;; esac
}
hp_config_matches() {
  hp_wm=$(wm density) || return 1
  hp_density=$(printf '%s\n' "$hp_wm" | sed -n 's/^Override density: //p')
  if [ -z "$hp_density" ]; then
    hp_density=$(printf '%s\n' "$hp_wm" | sed -n 's/^Physical density: //p')
  fi
  case "$hp_density" in ''|*[!0-9]*) return 1 ;; esac
  hp_density_token="$hp_density"dpi
  case "$hp_dump_config" in *" $hp_density_token "*) ;; *) return 1 ;; esac
  hp_scale=$(settings get system font_scale) || return 1
  [ "$hp_scale" != null ] || hp_scale=1
  hp_scale=$(hp_font "$hp_scale") || return 1
  hp_client_scale=$(printf '%s\n' "$hp_dump_config" | cut -d ' ' -f 1)
  hp_client_scale=$(hp_font "$hp_client_scale") || return 1
  [ "$hp_scale" = "$hp_client_scale" ]
}
hp_vendor_start() {
  [ "$(date +%s)" -lt "$hp_deadline" ] || return 1
  hp_start=$(timeout 5 am start -W -n "$hp_component" 2>&1) || return 1
  case "$hp_start" in *'Status: ok'*) return 0 ;; *) return 1 ;; esac
}
hp_settle_vendor_display() {
  hp_packages=$(pm list packages cloud.shelly.stargate) || return 1
  hp_package=$(printf '%s\n' "$hp_packages" |
    grep -Fx 'package:cloud.shelly.stargate')
  if [ -z "$hp_package" ]; then echo skipped; return; fi
  hp_home=$(cmd package resolve-activity --brief \
    -a android.intent.action.MAIN -c android.intent.category.HOME) || return 1
  hp_home=$(printf '%s\n' "$hp_home" | tail -n 1)
  case "$hp_home" in __HOME_PACKAGE__/*) ;; *) return 1 ;; esac
  hp_deadline=$(( $(date +%s) + 20 ))
  hp_matched=
  for hp_attempt in 1 2; do
    hp_old=$(pidof cloud.shelly.stargate)
    case "$hp_old" in *[!0-9]*) return 1 ;; esac
    hp_vendor_start || return 1
    while [ "$(date +%s)" -lt "$hp_deadline" ]; do
      if hp_vendor_dump &&
        [ "$hp_dump_state" = 'mResumed=true mStopped=false mFinished=false' ] &&
        hp_config_matches; then hp_matched=$hp_dump_pid; break; fi
      hp_pid=$(pidof cloud.shelly.stargate)
      case "$hp_pid" in *[!0-9]*) return 1 ;; esac
      if [ -z "$hp_old" ] && [ -n "$hp_pid" ]; then hp_old=$hp_pid; fi
      if [ -n "$hp_old" ] && [ ! -d "/proc/$hp_old" ]; then break; fi
      sleep 0.2
    done
    [ -z "$hp_matched" ] || break
  done
  [ -n "$hp_matched" ] || return 1
  input keyevent 3 || return 1
  hp_paused=0
  while [ "$(date +%s)" -lt "$hp_deadline" ]; do
    if hp_vendor_dump && [ "$hp_dump_pid" = "$hp_matched" ] &&
      hp_config_matches; then
      case "$hp_dump_state" in
        'mResumed=false mStopped=true mFinished=false'|\
        'mResumed=false mStopped=false mFinished=false') hp_paused=1; break ;;
      esac
    fi
    [ -d "/proc/$hp_matched" ] || return 1
    sleep 0.2
  done
  [ "$hp_paused" = 1 ] || return 1
  hp_vendor_start || return 1
  while [ "$(date +%s)" -lt "$hp_deadline" ]; do
    if [ ! -d "/proc/$hp_matched" ]; then echo settled; return; fi
    if hp_vendor_dump && [ "$hp_dump_pid" = "$hp_matched" ] &&
      [ "$hp_dump_state" = 'mResumed=true mStopped=false mFinished=false' ] &&
      hp_config_matches; then echo settled; return; fi
    sleep 0.2
  done
  return 1
}
`.trim();

export function buildVendorDisplaySettlement(nonce, packageId) {
  if (!/^[a-f0-9]{32}$/.test(nonce) || packageId !== PACKAGE_ID) fail();
  return [
    `echo HAPANELD_VENDOR_DISPLAY_SETTLEMENT_BEGIN:${nonce}`,
    DISPLAY_SETTLEMENT_PROGRAM.replaceAll('__HOME_PACKAGE__', packageId),
    'hp_settle_vendor_display || echo error',
    `echo HAPANELD_VENDOR_DISPLAY_SETTLEMENT_END:${nonce}:0`,
  ].join('\n');
}

export function parseVendorDisplaySettlement(body, nonce) {
  if (typeof body !== 'string' || body.length > 1024 || !/^[a-f0-9]{32}$/.test(nonce)) fail();
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.length !== 4 || lines[0] !== `HAPANELD_VENDOR_DISPLAY_SETTLEMENT_BEGIN:${nonce}` ||
      lines[2] !== `HAPANELD_VENDOR_DISPLAY_SETTLEMENT_END:${nonce}:0` || lines[3] !== '' ||
      !['settled', 'skipped'].includes(lines[1])) fail();
  return lines[1];
}
