// Fixed, explicitly confirmed post-install grants for the authenticated APK.
// Nothing here configures MQTT, networking, device ownership or app data.
import { DEVICE_ADMIN_RECEIVER, LEGACY_LAUNCH_COMPONENT, OBSOLETE_ACCESSIBILITY_COMPONENTS, SUCCESSOR_PACKAGE_ID,
  accessibilityComponentFor, accessibilityComponentsFor, homeComponentsFor, isLaunchComponent } from './app-identity.mjs';
import { CHOOSER_OUTCOMES, HOME_QUERY, buildHomeClaim } from './home-chooser.mjs';

// What HOME resolves to now, as the network installer reads it.
export { HOME_QUERY };
// Each runtime permission is granted only where Android lists its hardware
// feature, the same pairs the network installer grants (adb_protocol.py
// RUNTIME_GRANTS); a refusal is reported, never fatal.
export const RUNTIME_GRANTS = Object.freeze([['camera.any', 'CAMERA'], ['microphone', 'RECORD_AUDIO']]);
const listed = feature => `hp_has=; for hp_f in $hp_features; do case "$hp_f" in ` +
  `feature:android.hardware.${feature}|feature:android.hardware.${feature}=*) hp_has=1 ;; esac; done`;

// The one component written into the device-wide accessibility list for the
// installed build, and every spelling that already names it. The class lives in
// whichever package the build's launcher names, so it follows the launcher.
export const ACCESSIBILITY_SERVICE = accessibilityComponentFor(LEGACY_LAUNCH_COMPONENT);
// Lets full screen-off lift a firmware's keep-screen-on-while-plugged-in
// setting (the Shelly X2i); granted after every install like the network path.
const SECURE_SETTINGS = 'android.permission.WRITE_SECURE_SETTINGS';
// Makes the new app a device admin, which only locks the screen, unless Android
// already lists it: the network installer's device_admin_claim_command, as text.
export function buildDeviceAdminClaim(packageId) {
  if (packageId !== SUCCESSOR_PACKAGE_ID) return ':';
  const listed = `${packageId}/${DEVICE_ADMIN_RECEIVER.slice(packageId.length)}:`;
  return `case "$(dumpsys device_policy 2>/dev/null)" in *"${listed}"*) ;; ` +
    `*) dpm set-active-admin ${packageId}/${DEVICE_ADMIN_RECEIVER} >/dev/null 2>&1 ;; esac`;
}
const fail = () => { throw new Error('permissions_unverified'); };
const checkBuild = (packageId, launchComponent) => {
  if (!isLaunchComponent(packageId, launchComponent)) fail();
};
const checkNonce = nonce => { if (!/^[a-f0-9]{32}$/.test(nonce)) fail(); };
const checkSdk = sdk => { if (!Number.isInteger(sdk) || sdk < 1 || sdk > 100) fail(); };

function validateServices(value) {
  if (typeof value !== 'string' || value.length > 4096) fail();
  if (value === '' || value === 'null') return [];
  const services = value.split(':');
  if (services.length > 64 || new Set(services).size !== services.length ||
      services.some(service => !/^[A-Za-z][A-Za-z0-9_.]*\/[A-Za-z.][A-Za-z0-9_.$]*$/.test(service))) fail();
  return services;
}

function frame(body, nonce, kind) {
  checkNonce(nonce);
  if (typeof body !== 'string' || body.length > 16384 || /[^\x20-\x7e\r\n\t]/.test(body)) fail();
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.shift() !== `HAPANELD_${kind}_BEGIN:${nonce}` || lines.pop() !== '' ||
      lines.pop() !== `HAPANELD_${kind}_END:${nonce}:0` || lines.some(line => line.includes('HAPANELD_'))) fail();
  return lines;
}

export function buildPermissionRead(nonce) {
  checkNonce(nonce);
  return `echo HAPANELD_PERMISSIONS_READ_BEGIN:${nonce}; settings get secure enabled_accessibility_services; echo HAPANELD_PERMISSIONS_READ_END:${nonce}:$?`;
}

export function parsePermissionRead(body, nonce) {
  const lines = frame(body, nonce, 'PERMISSIONS_READ');
  if (lines.length !== 1) fail();
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
  // Runtime permissions and HOME come last and never decide the status: `:` ends
  // the program successfully whatever they answered; the readback reports them.
  // The HOME claim prints the one line this program answers: what it did.
  // Check again immediately before the list write: unrelated services must not
  // disappear if another setup operation changed this device-wide setting.
  return `echo HAPANELD_PERMISSIONS_GRANT_BEGIN:${nonce}; (
current=$(settings get secure enabled_accessibility_services) || exit 1
[ "$current" = '${existing}' ] || exit 1
${sdk >= 33 ? `pm grant ${PACKAGE} android.permission.POST_NOTIFICATIONS || exit 1` : ':'}
appops set ${PACKAGE} WRITE_SETTINGS allow || exit 1
appops set ${PACKAGE} SYSTEM_ALERT_WINDOW allow || exit 1
appops set ${PACKAGE} REQUEST_INSTALL_PACKAGES allow || exit 1
current=$(settings get secure enabled_accessibility_services) || exit 1
[ "$current" = '${existing}' ] || exit 1
${expected === existing ? ':' : `settings put secure enabled_accessibility_services '${expected}' || exit 1`}
settings put secure accessibility_enabled 1 || exit 1
hp_features=$(pm list features 2>/dev/null) || hp_features=
${RUNTIME_GRANTS.map(([feature, permission]) => `${listed(feature)}
[ -z "$hp_has" ] || pm grant ${PACKAGE} android.permission.${permission} >/dev/null 2>&1`).join('\n')}
pm grant ${PACKAGE} ${SECURE_SETTINGS} >/dev/null 2>&1
${buildDeviceAdminClaim(PACKAGE)}
${buildHomeClaim(launchComponent)}
echo "home_chooser:$hp_chooser"
:
); echo HAPANELD_PERMISSIONS_GRANT_END:${nonce}:$?`;
}

// What the HOME claim did, or null when its line could not be read; never fatal.
export function parsePermissionGrant(body, nonce) {
  const lines = frame(body, nonce, 'PERMISSIONS_GRANT');
  if (lines.length > 1 || (lines.length === 1 && !lines[0].startsWith('home_chooser:'))) fail();
  const chooser = lines.length ? lines[0].slice(13) : null;
  return CHOOSER_OUTCOMES.includes(chooser) ? chooser : null;
}

export function buildPermissionVerification(nonce, sdk, packageId, launchComponent) {
  checkNonce(nonce); checkSdk(sdk); checkBuild(packageId, launchComponent);
  const PACKAGE = packageId;
  return `echo HAPANELD_PERMISSIONS_VERIFY_BEGIN:${nonce}; (
settings get secure enabled_accessibility_services || exit 1
settings get secure accessibility_enabled || exit 1
appops get ${PACKAGE} WRITE_SETTINGS || exit 1
appops get ${PACKAGE} SYSTEM_ALERT_WINDOW || exit 1
appops get ${PACKAGE} REQUEST_INSTALL_PACKAGES || exit 1
${sdk >= 33 ? `dumpsys package ${PACKAGE} | grep 'android.permission.POST_NOTIFICATIONS: granted=' || exit 1` : "echo not_required"}
hp_features=$(pm list features 2>/dev/null); hp_features_status=$?
${RUNTIME_GRANTS.map(([feature, permission]) => `if [ "$hp_features_status" -eq 0 ]; then ${listed(feature)}; if [ -z "$hp_has" ]; then echo unsupported; else hp_r=$(dumpsys package ${PACKAGE} 2>/dev/null | grep -F 'android.permission.${permission}: granted='); case "$hp_r" in '') echo unknown ;; *granted=false*) echo denied ;; *) echo granted ;; esac; fi; else echo unknown; fi`).join('\n')}
hp_s=$(dumpsys package ${PACKAGE} 2>/dev/null) && case "$hp_s" in *"${SECURE_SETTINGS}: granted=true"*) echo granted ;; *) echo missing ;; esac || echo unknown
echo "home:$(${HOME_QUERY})"
echo "device:$(getprop ro.product.device)"
); echo HAPANELD_PERMISSIONS_VERIFY_END:${nonce}:$?`;
}

export function parsePermissionVerification(body, nonce, sdk, existing, packageId, launchComponent) {
  checkSdk(sdk); checkBuild(packageId, launchComponent);
  const lines = frame(body, nonce, 'PERMISSIONS_VERIFY');
  if (lines.length !== 11 || lines[0] !== expectedServices(existing, packageId, launchComponent) || lines[1] !== '1' ||
      !/^WRITE_SETTINGS: allow(?:; [\x20-\x7e]{1,1024})?$/.test(lines[2]) ||
      !/^SYSTEM_ALERT_WINDOW: allow(?:; [\x20-\x7e]{1,1024})?$/.test(lines[3]) ||
      !/^REQUEST_INSTALL_PACKAGES: allow(?:; [\x20-\x7e]{1,1024})?$/.test(lines[4]) ||
      (sdk >= 33 ? !/^\s*android\.permission\.POST_NOTIFICATIONS: granted=true, flags=\[[ A-Z0-9_|]*\]\s*$/.test(lines[5])
        : lines[5] !== 'not_required') ||
      [lines[6], lines[7]].some(line => !['granted', 'denied', 'unknown', 'unsupported'].includes(line)) ||
      !['granted', 'missing', 'unknown'].includes(lines[8]) ||
      !lines[9].startsWith('home:') || !lines[10].startsWith('device:')) fail();
  const device = lines[10].slice(7);
  // HOME, the camera, the microphone and secure settings are reported, never a reason to
  // fail the install: the app runs without them (without secure settings the
  // screen dims instead of switching off) and the person can choose HOME on
  // the panel.
  return Object.freeze({permissionsVerified: true, notificationsRequired: sdk >= 33,
    camera: lines[6], microphone: lines[7], secureSettings: lines[8],
    home: homeComponentsFor(launchComponent).includes(lines[9].slice(5)),
    device: /^[A-Za-z0-9_.-]{1,64}$/.test(device) ? device : null});
}
