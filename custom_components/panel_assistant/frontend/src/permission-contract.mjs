// Fixed, explicitly confirmed post-install grants for the authenticated APK.
// Nothing here configures MQTT, networking, device ownership or app data.
import { OBSOLETE_ACCESSIBILITY_COMPONENTS,
  accessibilityComponentFor, accessibilityComponentsFor, homeComponentsFor, isLaunchComponent } from './app-identity.mjs';
import { CHOOSER_OUTCOMES, HOME_QUERY, buildHomeClaim } from './home-chooser.mjs';
import { receiverAbsenceCheck } from './previous-home.mjs';

// What HOME resolves to now, as the network installer reads it.
export { HOME_QUERY };
// Each runtime permission is asked for only where Android lists its hardware
// feature, the same pairs the network installer grants (adb_protocol.py
// RUNTIME_GRANTS); a refusal is reported, never fatal.
export const RUNTIME_GRANTS = Object.freeze([['camera.any', 'CAMERA'], ['microphone', 'RECORD_AUDIO']]);

// The installers keep every decision already made about a permission and
// grant only what nobody has decided, by one rule written out the same way as
// the network installer's (adb_protocol.py; tests/test_repository.py compares
// the text). A permission line whose flags record the owner's answer
// (USER_SET, USER_FIXED) or a device policy's (POLICY_FIXED) is a decision;
// Android's own USER_SENSITIVE_WHEN_* defaults are not.
export const OWNER_DECISION = String.raw`flags=\[([ A-Z0-9_|]*[ |])?(USER_SET|USER_FIXED|POLICY_FIXED)[]| ]`;
// The same pattern for this side's parsers (JavaScript reads `[]` as empty).
const OWNER_DECIDED = new RegExp(OWNER_DECISION.replace('[]| ]', String.raw`[\]| ]`));
// Grant `permission` only where Android shows nobody decided it: its dump line
// is a denial without a decision, or, as Android 9 and older print a permission
// nobody decided, a dump that succeeded and shows this package lists no line
// for it (a decision always sets a flag, which keeps the line). A denial the
// owner or a policy decided is kept and sets runtime_denied=1; a failed dump,
// one without this package, or a malformed line is unknown, never a reason to
// grant. Leaves the line it last read in $runtime and prints nothing.
export function runtimePermissionRepair(packageId, permission) {
  const denied = String.raw`[[:space:]]*${permission.replaceAll('.', '\\.')}: granted=false(, flags=\[[ A-Z0-9_|]*\])?[[:space:]]*`;
  const read = `runtime_dump=$(dumpsys package ${packageId}); runtime_status=$?; runtime=; ` +
    `if [ "$runtime_status" -eq 0 ]; then runtime=$(printf '%s\\n' "$runtime_dump" | grep -F '${permission}: granted='); fi`;
  const grant = `pm grant ${packageId} ${permission} >/dev/null 2>&1; ${read}`;
  return `${read}; runtime_denied=0; if [ -n "$runtime" ] && ! printf '%s\\n' "$runtime" | grep -Evq '^${denied}$'; then ` +
    `if printf '%s\\n' "$runtime" | grep -Eq '${OWNER_DECISION}'; then runtime_denied=1; else ${grant}; fi; ` +
    `elif [ -z "$runtime" ] && [ "$runtime_status" -eq 0 ] && printf '%s\\n' "$runtime_dump" | ` +
    `grep -Fq 'Package [${packageId}]'; then ${grant}; fi`;
}
// The app operations the app needs, set to allow only while nobody has set
// them. Android 14 answers an unset operation with two lines, including its
// default; nobody chose that default for this app, so it reads as `default`
// unless it already allows. An explicit mode, such as the deny the owner's
// Settings switch writes, is kept.
const APP_OPS = 'WRITE_SETTINGS SYSTEM_ALERT_WINDOW REQUEST_INSTALL_PACKAGES';
const readAppOp = packageId => `mode=$(appops get ${packageId} "$operation"); mode_status=$?; case "$mode" in ` +
  `'No operations.\nDefault mode: allow') mode="$operation: allow" ;; ` +
  ['default', 'deny', 'ignore', 'foreground', 'errored'].map(mode => `'No operations.\nDefault mode: ${mode}'`).join('|') +
  `) mode="$operation: default" ;; esac; `;
// A refused write stops the grant program, so it cannot report success.
export function appOpGrant(packageId) {
  return `for operation in ${APP_OPS}; do ${readAppOp(packageId)}` +
    `if [ "$mode_status" -eq 0 ] && [ -n "$mode" ] && ! printf '%s\\n' "$mode" | grep -Evq "^($operation: ` +
    `default(; [ -~]+)?|No operations\\.)$"; then appops set ${packageId} "$operation" allow || exit 1; fi; done`;
}
// One line per operation, in order: its mode, folded as the grant reads it.
export function appOpReadback(packageId) {
  return `for operation in ${APP_OPS}; do ${readAppOp(packageId)}` +
    `if [ "$mode_status" -eq 0 ]; then printf '%s\\n' "$mode"; else echo unknown; fi; done`;
}
// granted, denied (decided by the owner or a policy) or unknown, by the same
// reading as the network installer's readback.
function runtimeState(packageId, permission) {
  const escaped = permission.replaceAll('.', '\\.');
  const granted = String.raw`[[:space:]]*${escaped}: granted=true(, flags=\[[ A-Z0-9_|]*\])?[[:space:]]*`;
  const denied = String.raw`[[:space:]]*${escaped}: granted=false(, flags=\[[ A-Z0-9_|]*\])?[[:space:]]*`;
  return `runtime=$(dumpsys package ${packageId} 2>/dev/null | grep -F '${permission}: granted='); ` +
    `if [ -n "$runtime" ] && ! printf '%s\\n' "$runtime" | grep -Evq '^${granted}$'; then echo granted; ` +
    `elif [ -n "$runtime" ] && ! printf '%s\\n' "$runtime" | grep -Evq '^${denied}$' && ` +
    `printf '%s\\n' "$runtime" | grep -Eq '${OWNER_DECISION}'; then echo denied; else echo unknown; fi`;
}
const listed = feature => `hp_has=; for hp_f in $hp_features; do case "$hp_f" in ` +
  `feature:android.hardware.${feature}|feature:android.hardware.${feature}=*) hp_has=1 ;; esac; done`;

// Lets full screen-off lift a firmware's keep-screen-on-while-plugged-in
// setting (the Shelly X2i); granted after every install like the network path.
const SECURE_SETTINGS = 'android.permission.WRITE_SECURE_SETTINGS';
// The app decides setup eligibility from its canonical store. Old supported
// apps keep their grant behavior only when a readable package dump proves
// this receiver absent; a present receiver with an unknown reply defers HOME.
export function buildSetupHomeClaim(packageId, launchComponent) {
  const receiver = `${packageId}/io.panelassistant.android.platform.PreservePreviousHomeReceiver`;
  const builtin = 'Broadcast completed: result=1, data="panel-assistant-home-builtin"';
  const retained = 'Broadcast completed: result=2, data="panel-assistant-home-retained"';
  const locked = 'Broadcast completed: result=1, data="panel-assistant-home-restore-shell-locked"';
  const restoration = `hp_chooser=$({ hp_chooser=deferred; ` +
    `if flock -xn 0 <&9 && [ -d /proc/self/fd/9 ]; then ` +
    `hp_lock_identity=$(stat -Lc '%d:%i' /proc/self/fd/0 <&9) && ` +
    `hp_restore=$(am broadcast --include-stopped-packages -n ${receiver} ` +
    `--es operation restore_previous_home_shell_lock_eligibility ` +
    `--es home_lock_identity "$hp_lock_identity" --es previous_home_component "$hp_previous" 2>/dev/null) && ` +
    `if printf '%s\\n' "$hp_restore" | grep -Fxq '${locked}'; then ` +
    `cmd package set-home-activity "$hp_previous" <&9 >/dev/null 2>&1; ` +
    `hp_home="$(${HOME_QUERY.replace('cmd ', 'cmd <&9 ')})"; ` +
    `case "\${hp_home#*/}" in .*) hp_home="\${hp_home%/*}/\${hp_home%/*}\${hp_home#*/}" ;; esac; ` +
    `if [ "$hp_home" = "$hp_previous" ]; then ` +
    `am broadcast --include-stopped-packages -n ${receiver} --es operation acknowledge_retained_home ` +
    `--es previous_home_component "$hp_previous" >/dev/null 2>&1; ` +
    `hp_chooser=retained; fi; fi; fi; printf '%s\\n' "$hp_chooser"; } 9</system 2>/dev/null) || hp_chooser=deferred`;
  return `hp_policy=$(am broadcast --include-stopped-packages -n ${receiver} ` +
    `--es operation dashboard_home_policy 2>/dev/null); hp_policy_status=$?; hp_chooser=deferred; hp_builtin=0; hp_previous=; ` +
    `if [ "$hp_policy_status" -eq 0 ] && printf '%s\\n' "$hp_policy" | grep -Fxq '${builtin}'; then hp_builtin=1; ` +
    `elif [ "$hp_policy_status" -eq 0 ] && printf '%s\\n' "$hp_policy" | grep -Fxq '${retained}'; then hp_chooser=retained; ` +
    `elif [ "$hp_policy_status" -eq 0 ]; then hp_previous=$(printf '%s\\n' "$hp_policy" | ` +
    `sed -n 's/^Broadcast completed: result=3, data="panel-assistant-home-restore:\\([A-Za-z][A-Za-z0-9_.]*\\/[A-Za-z.][A-Za-z0-9_.$]*\\)"$/\\1/p'); ` +
    `if [ -n "$hp_previous" ] && ! printf '%s\\n' "$hp_previous" | grep -q '[[:space:]]'; then ${restoration}; fi; fi; ` +
    `if [ "$hp_chooser" = deferred ] && [ "$hp_builtin" -eq 0 ] && [ -z "$hp_previous" ] && ${receiverAbsenceCheck(packageId)}; then hp_builtin=1; fi; ` +
    `if [ "$hp_builtin" -eq 1 ]; then ${buildBuiltinHomeClaim(packageId, launchComponent)}; fi`;
}

function buildBuiltinHomeClaim(packageId, launchComponent) {
  const receiver = `${packageId}/io.panelassistant.android.platform.PreservePreviousHomeReceiver`;
  const eligible = 'Broadcast completed: result=1, data="panel-assistant-setup-eligible"';
  const allowed = `hp_policy=$(am broadcast --include-stopped-packages -n ${receiver} ` +
    `--es operation dashboard_home_policy 2>/dev/null) && { printf '%s\\n' "$hp_policy" | ` +
    `grep -Fxq 'Broadcast completed: result=1, data="panel-assistant-home-builtin"' ` +
    `|| { ${receiverAbsenceCheck(packageId)}; }; }`;
  return `hp_setup=$(am broadcast --include-stopped-packages -n ${receiver} --es operation setup_eligibility 2>/dev/null); hp_setup_status=$?; ` +
    `hp_eligible=0; if [ "$hp_setup_status" -eq 0 ] && printf '%s\\n' "$hp_setup" | grep -Fxq '${eligible}'; then hp_eligible=1; else ` +
    `if ${receiverAbsenceCheck(packageId)}; then hp_eligible=1; fi; fi; ` +
    `if [ "$hp_eligible" -eq 1 ]; then ${buildHomeClaim(launchComponent, allowed)}; ` +
    // Native acknowledges only a fresh verified HOME claim; failure leaves
    // the pending intent in health for a later completion pass.
    `am broadcast --include-stopped-packages -n ${receiver} --es operation acknowledge_home >/dev/null 2>&1; ` +
    `else hp_chooser=deferred; fi`;
}
// Every refusal carries the same code; `detail` names the check that refused,
// for "Details for support" only, so the next failure names its cause.
const fail = detail => { throw Object.assign(new Error('permissions_unverified'), { detail }); };
// A panel line as support sees it: bounded, already printable ASCII.
const shown = line => line === undefined ? 'missing' : JSON.stringify(line.length > 80 ? `${line.slice(0, 80)}…` : line);
const checkBuild = (packageId, launchComponent) => {
  if (!isLaunchComponent(packageId, launchComponent)) fail('unknown app build');
};
const checkNonce = nonce => { if (!/^[a-f0-9]{32}$/.test(nonce)) fail('invalid nonce'); };
const checkSdk = sdk => { if (!Number.isInteger(sdk) || sdk < 1 || sdk > 100) fail('invalid Android version'); };

function validateServices(value) {
  // Other apps' services are named only by count.
  const unreadable = () => fail(`accessibility services unreadable (${String(value).split(':').length} entries)`);
  if (typeof value !== 'string' || value.length > 4096) unreadable();
  if (value === '' || value === 'null') return [];
  const services = value.split(':');
  if (services.length > 64 || new Set(services).size !== services.length ||
      services.some(service => !/^[A-Za-z][A-Za-z0-9_.]*\/[A-Za-z.][A-Za-z0-9_.$]*$/.test(service))) unreadable();
  return services;
}

function frame(body, nonce, kind) {
  checkNonce(nonce);
  const step = kind.slice('PERMISSIONS_'.length).toLowerCase();
  if (typeof body !== 'string' || body.length > 16384 || /[^\x20-\x7e\r\n\t]/.test(body)) {
    fail(`${step}: reply too long or not plain text`);
  }
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.shift() !== `HAPANELD_${kind}_BEGIN:${nonce}`) fail(`${step}: no start marker`);
  if (lines.pop() !== '') fail(`${step}: reply cut short`);
  const end = lines.pop(), ending = `HAPANELD_${kind}_END:${nonce}:`;
  if (end !== `${ending}0`) {
    fail(end?.startsWith(ending) ? `${step}: a command failed (status ${shown(end.slice(ending.length))})`
      : `${step}: no end marker`);
  }
  const marker = lines.findIndex(line => line.includes('HAPANELD_'));
  if (marker >= 0) fail(`${step} line ${marker + 1}: unexpected marker`);
  return lines;
}

export function buildPermissionRead(nonce) {
  checkNonce(nonce);
  return `echo HAPANELD_PERMISSIONS_READ_BEGIN:${nonce}; settings get secure enabled_accessibility_services; echo HAPANELD_PERMISSIONS_READ_END:${nonce}:$?`;
}

export function parsePermissionRead(body, nonce) {
  const lines = frame(body, nonce, 'PERMISSIONS_READ');
  if (lines.length !== 1) fail(`read: ${lines.length} lines, expected 1`);
  validateServices(lines[0]);
  return lines[0];
}

export function expectedServices(existing, packageId, launchComponent) {
  checkBuild(packageId, launchComponent);
  const services = validateServices(existing);
  const known = accessibilityComponentsFor(launchComponent);
  const obsolete = OBSOLETE_ACCESSIBILITY_COMPONENTS[launchComponent] ?? [];
  const kept = services.filter(service => !obsolete.includes(service));
  return (kept.some(service => known.includes(service))
    ? kept : [...kept, accessibilityComponentFor(launchComponent)]).join(':');
}

export function buildPermissionGrant(nonce, sdk, existing, packageId, launchComponent) {
  checkNonce(nonce); checkSdk(sdk); checkBuild(packageId, launchComponent);
  const PACKAGE = packageId;
  const expected = expectedServices(existing, packageId, launchComponent);
  // Validated component characters cannot escape single-quoted shell literals.
  // Notifications, the app operations and the runtime permissions are granted
  // only where nobody has decided them; a decision is kept, never granted over,
  // and the readback reports it. Runtime permissions and HOME come last and
  // never decide the status: `:` ends the program successfully whatever they
  // answered. The HOME claim prints the one line this program answers: what it did.
  // Check again immediately before the list write: unrelated services must not
  // disappear if another setup operation changed this device-wide setting.
  return `echo HAPANELD_PERMISSIONS_GRANT_BEGIN:${nonce}; (
current=$(settings get secure enabled_accessibility_services) || exit 1
[ "$current" = '${existing}' ] || exit 1
${sdk >= 33 ? runtimePermissionRepair(PACKAGE, 'android.permission.POST_NOTIFICATIONS') : ':'}
${appOpGrant(PACKAGE)}
current=$(settings get secure enabled_accessibility_services) || exit 1
[ "$current" = '${existing}' ] || exit 1
${expected === existing ? ':' : `settings put secure enabled_accessibility_services '${expected}' || exit 1`}
settings put secure accessibility_enabled 1 || exit 1
hp_features=$(pm list features 2>/dev/null) || hp_features=
${RUNTIME_GRANTS.map(([feature, permission]) => `${listed(feature)}
[ -z "$hp_has" ] || { ${runtimePermissionRepair(PACKAGE, `android.permission.${permission}`)}; }`).join('\n')}
pm grant ${PACKAGE} ${SECURE_SETTINGS} >/dev/null 2>&1
${buildSetupHomeClaim(PACKAGE, launchComponent)}
echo "home_chooser:$hp_chooser"
:
); echo HAPANELD_PERMISSIONS_GRANT_END:${nonce}:$?`;
}

// What the HOME claim did, or null when its line could not be read; never fatal.
export function parsePermissionGrant(body, nonce) {
  const lines = frame(body, nonce, 'PERMISSIONS_GRANT');
  if (lines.length > 1 || (lines.length === 1 && !lines[0].startsWith('home_chooser:'))) {
    fail(`grant line 1: ${shown(lines[0])}${lines.length > 1 ? ` and ${lines.length - 1} more` : ''}`);
  }
  const chooser = lines.length ? lines[0].slice(13) : null;
  return CHOOSER_OUTCOMES.includes(chooser) ? chooser : null;
}

export function buildPermissionVerification(nonce, sdk, packageId, launchComponent) {
  checkNonce(nonce); checkSdk(sdk); checkBuild(packageId, launchComponent);
  const PACKAGE = packageId;
  return `echo HAPANELD_PERMISSIONS_VERIFY_BEGIN:${nonce}; (
settings get secure enabled_accessibility_services || exit 1
settings get secure accessibility_enabled || exit 1
${appOpReadback(PACKAGE)}
${sdk >= 33 ? `dumpsys package ${PACKAGE} | grep 'android.permission.POST_NOTIFICATIONS: granted=' || exit 1` : "echo not_required"}
hp_features=$(pm list features 2>/dev/null); hp_features_status=$?
${RUNTIME_GRANTS.map(([feature, permission]) => `if [ "$hp_features_status" -eq 0 ]; then ${listed(feature)}; if [ -z "$hp_has" ]; then echo unsupported; else ${runtimeState(PACKAGE, `android.permission.${permission}`)}; fi; else echo unknown; fi`).join('\n')}
hp_s=$(dumpsys package ${PACKAGE} 2>/dev/null) && case "$hp_s" in *"${SECURE_SETTINGS}: granted=true"*) echo granted ;; *) echo missing ;; esac || echo unknown
echo "home:$(${HOME_QUERY})"
echo "device:$(getprop ro.product.device)"
); echo HAPANELD_PERMISSIONS_VERIFY_END:${nonce}:$?`;
}

export function parsePermissionVerification(body, nonce, sdk, existing, packageId, launchComponent) {
  checkSdk(sdk); checkBuild(packageId, launchComponent);
  const lines = frame(body, nonce, 'PERMISSIONS_VERIFY');
  const services = expectedServices(existing, packageId, launchComponent);
  const mode = (op, modes) => line => new RegExp(`^${op}: (?:${modes})(?:; [\\x20-\\x7e]{1,1024})?$`).test(line);
  // An operation set to anything but allow or default was set by someone: the
  // grant kept it, and it is reported, never a reason to fail.
  const kept = op => mode(op, 'deny|ignore|errored|foreground');
  const allowedOrKept = op => line => mode(op, 'allow')(line) || kept(op)(line);
  const notificationsGranted = line => /^\s*android\.permission\.POST_NOTIFICATIONS: granted=true, flags=\[[ A-Z0-9_|]*\]\s*$/.test(line);
  // A "Don't allow" the owner (or a policy) gave is kept; an undecided denial
  // after the grant is a refusal, and fails as before.
  const notificationsKept = line => /^\s*android\.permission\.POST_NOTIFICATIONS: granted=false, flags=\[[ A-Z0-9_|]*\]\s*$/.test(line)
    && OWNER_DECIDED.test(line);
  const reading = values => line => values.includes(line);
  // One check per line the verification program prints, in its order.
  const checks = [
    ['accessibility services', line => line === services],
    ['accessibility enabled', line => line === '1'],
    ['WRITE_SETTINGS', allowedOrKept('WRITE_SETTINGS')],
    ['SYSTEM_ALERT_WINDOW', allowedOrKept('SYSTEM_ALERT_WINDOW')],
    ['REQUEST_INSTALL_PACKAGES', allowedOrKept('REQUEST_INSTALL_PACKAGES')],
    ['notifications', sdk >= 33
      ? line => notificationsGranted(line) || notificationsKept(line)
      : line => line === 'not_required'],
    ['camera', reading(['granted', 'denied', 'unknown', 'unsupported'])],
    ['microphone', reading(['granted', 'denied', 'unknown', 'unsupported'])],
    ['secure settings', reading(['granted', 'missing', 'unknown'])],
    ['home', line => line?.startsWith('home:')],
    ['device', line => line?.startsWith('device:')],
  ];
  checks.forEach(([name, ok], index) => {
    if (ok(lines[index])) return;
    // Other apps' services are named only by count.
    const seen = index === 0 && lines[0] !== undefined
      ? `${lines[0] === '' || lines[0] === 'null' ? 0 : lines[0].split(':').length} entries${lines[0] === existing ? ', unchanged' : ''}`
      : shown(lines[index]);
    fail(`verify line ${index + 1} (${name}): ${seen}`);
  });
  if (lines.length !== checks.length) fail(`verify: ${lines.length} lines, expected ${checks.length}`);
  const device = lines[10].slice(7);
  // What the owner (or a policy) turned off and the grant kept, by the names
  // the network installer reports, in its order.
  const denied = Object.freeze([
    ...[['modify system settings', 'WRITE_SETTINGS', 2], ['display over other apps', 'SYSTEM_ALERT_WINDOW', 3],
      ['install packages', 'REQUEST_INSTALL_PACKAGES', 4]].filter(([, op, index]) => kept(op)(lines[index])).map(([name]) => name),
    ...[['camera', 6], ['microphone', 7]].filter(([, index]) => lines[index] === 'denied').map(([name]) => name),
    ...(sdk >= 33 && notificationsKept(lines[5]) ? ['notifications'] : []),
  ]);
  // HOME, the camera, the microphone, secure settings and every permission the
  // owner turned off are reported, never a reason to fail the install: the app
  // runs without them (without secure settings the screen dims instead of
  // switching off) and the person can choose HOME on the panel.
  return Object.freeze({permissionsVerified: true, notificationsRequired: sdk >= 33, denied,
    camera: lines[6], microphone: lines[7], secureSettings: lines[8],
    home: homeComponentsFor(launchComponent).includes(lines[9].slice(5)),
    device: /^[A-Za-z0-9_.-]{1,64}$/.test(device) ? device : null});
}
