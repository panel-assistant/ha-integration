/**
 * The one panel application id this installer installs, observes and updates.
 *
 * Components are written out, never built from the id. The manifest classes
 * live in the Kotlin package, which is not the application id: builds of this
 * id up to app 0.9.9 keep them in `io.github.maxlyth.hapaneld`, and from 0.9.10
 * they live in `io.panelassistant.android`. Those old-class names are frozen. A
 * signed descriptor's `launchComponent` says which set an exact build carries,
 * and every other component is looked up from it. `<id>/.Class` appears only
 * where the class lives in the id's package. The schema identifier strings are
 * listed below, never derived from these values; the database pattern keeps its
 * spelling.
 */
export const PACKAGE_ID = 'io.panelassistant.android';
// The old app's id, kept only so preflight still probes for it and refuses a
// panel that holds it, and for the release-name grammar of frozen old asset names.
export const LEGACY_PACKAGE_ID = 'io.github.maxlyth.hapaneld';
// The app's device admin, which only locks the screen (app_identity.py).
export const DEVICE_ADMIN_RECEIVER = 'io.panelassistant.android.platform.PanelDeviceAdminReceiver';

/**
 * Signed release and feed schemas, as app_identity.py lists them: app releases
 * from 0.9.11 use the new spelling, earlier releases and the signed build feed
 * the old one. Both spellings are kept: old releases, the signed build feed and
 * stored records carry the old one, which is frozen. Readers accept every entry;
 * writers use the first.
 */
export const INSTALL_DESCRIPTOR_SCHEMAS = Object.freeze([
  'io.panelassistant.android.install.v1',
  'io.github.maxlyth.hapaneld.install.v1',
]);
export const BUILD_FEED_SCHEMAS = Object.freeze([
  'io.panelassistant.android.buildfeed.v1',
  'io.github.maxlyth.hapaneld.buildfeed.v1',
]);

const OLD_CLASSES_SUCCESSOR_LAUNCH = 'io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity';
const NEW_CLASSES_SUCCESSOR_LAUNCH = 'io.panelassistant.android/io.panelassistant.android.MainActivity';

/** Every launcher a signed build may name, in the order they shipped. */
export const LAUNCH_COMPONENTS = Object.freeze({
  [PACKAGE_ID]: Object.freeze([OLD_CLASSES_SUCCESSOR_LAUNCH, NEW_CLASSES_SUCCESSOR_LAUNCH]),
});

/** The dashboard activity that answers HOME, by the build's launcher. */
export const HOME_COMPONENTS = Object.freeze({
  [OLD_CLASSES_SUCCESSOR_LAUNCH]: 'io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity',
  [NEW_CLASSES_SUCCESSOR_LAUNCH]: 'io.panelassistant.android/io.panelassistant.android.DashboardActivity',
});

/** Every spelling HOME resolution reads back for that activity. */
export const EQUIVALENT_HOME_COMPONENTS = Object.freeze({
  [OLD_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([
    'io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity',
  ]),
  [NEW_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([
    'io.panelassistant.android/.DashboardActivity',
    'io.panelassistant.android/io.panelassistant.android.DashboardActivity',
  ]),
});

/** The accessibility service written into the device-wide list, by the build's launcher. */
export const ACCESSIBILITY_COMPONENTS = Object.freeze({
  [OLD_CLASSES_SUCCESSOR_LAUNCH]:
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
  [NEW_CLASSES_SUCCESSOR_LAUNCH]:
    'io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService',
});

/**
 * Android accepts either spelling of a component whose class lives in the
 * application id's own package, and the setting reads back whichever was
 * written, so both name the same enabled service there.
 */
export const EQUIVALENT_ACCESSIBILITY_COMPONENTS = Object.freeze({
  [OLD_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
  ]),
  [NEW_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([
    'io.panelassistant.android/.input.PanelAccessibilityService',
    'io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService',
  ]),
});

/**
 * The service class the app's 0.9.10 in-place update removed, by the launcher
 * that replaced it, as app_identity.py lists it. Both installers drop it from
 * the device-wide list. Frozen: panels updated in place may still list it.
 */
export const OBSOLETE_ACCESSIBILITY_COMPONENTS = Object.freeze({
  [NEW_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([ACCESSIBILITY_COMPONENTS[OLD_CLASSES_SUCCESSOR_LAUNCH]]),
});

/** Whether a build of this id may name this launcher. */
export function isLaunchComponent(packageId, component) {
  return packageId === PACKAGE_ID && LAUNCH_COMPONENTS[PACKAGE_ID].includes(component);
}

export function accessibilityComponentFor(launchComponent) {
  if (!Object.hasOwn(ACCESSIBILITY_COMPONENTS, launchComponent)) throw new RangeError('unknown launcher');
  return ACCESSIBILITY_COMPONENTS[launchComponent];
}

export function accessibilityComponentsFor(launchComponent) {
  if (!Object.hasOwn(EQUIVALENT_ACCESSIBILITY_COMPONENTS, launchComponent)) {
    throw new RangeError('unknown launcher');
  }
  return EQUIVALENT_ACCESSIBILITY_COMPONENTS[launchComponent];
}

export function homeComponentFor(launchComponent) {
  if (!Object.hasOwn(HOME_COMPONENTS, launchComponent)) throw new RangeError('unknown launcher');
  return HOME_COMPONENTS[launchComponent];
}

export function homeComponentsFor(launchComponent) {
  if (!Object.hasOwn(EQUIVALENT_HOME_COMPONENTS, launchComponent)) throw new RangeError('unknown launcher');
  return EQUIVALENT_HOME_COMPONENTS[launchComponent];
}
