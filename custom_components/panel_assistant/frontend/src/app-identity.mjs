/**
 * The panel application ids this installer accepts while the identity moves.
 *
 * A new application id is a new app to Android, so a panel crosses over by
 * running both packages for one handover. Both ids are therefore installable
 * and observable at the same time, and every verifier accepts the pair.
 *
 * Components are written out, never built from an id. The manifest classes live
 * in the Kotlin package, which is not the application id: builds up to app
 * 0.9.9 keep them in `io.github.maxlyth.hapaneld`, and from 0.9.10 the new app's
 * live in `io.panelassistant.android`. A signed descriptor's `launchComponent`
 * says which set an exact build carries, and every other component is looked up
 * from it. `<id>/.Class` appears only where the class lives in the id's package.
 * The schema identifier strings and the database pattern are frozen on the
 * legacy spelling and are never derived from these values.
 */
export const LEGACY_PACKAGE_ID = 'io.github.maxlyth.hapaneld';
export const SUCCESSOR_PACKAGE_ID = 'io.panelassistant.android';

/** Both installable panel application ids, legacy first. */
export const ACCEPTED_PACKAGE_IDS = Object.freeze([LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID]);

const OLD_CLASSES_SUCCESSOR_LAUNCH = 'io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity';
const NEW_CLASSES_SUCCESSOR_LAUNCH = 'io.panelassistant.android/io.panelassistant.android.MainActivity';

/** Every launcher a signed build of each id may name, in the order they shipped. */
export const LAUNCH_COMPONENTS = Object.freeze({
  [LEGACY_PACKAGE_ID]: Object.freeze(['io.github.maxlyth.hapaneld/.MainActivity']),
  [SUCCESSOR_PACKAGE_ID]: Object.freeze([OLD_CLASSES_SUCCESSOR_LAUNCH, NEW_CLASSES_SUCCESSOR_LAUNCH]),
});

/** The old app's one launcher. */
export const LEGACY_LAUNCH_COMPONENT = LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0];

/** The accessibility service written into the device-wide list, by the build's launcher. */
export const ACCESSIBILITY_COMPONENTS = Object.freeze({
  [LEGACY_LAUNCH_COMPONENT]: 'io.github.maxlyth.hapaneld/.input.PanelAccessibilityService',
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
  [LEGACY_LAUNCH_COMPONENT]: Object.freeze([
    'io.github.maxlyth.hapaneld/.input.PanelAccessibilityService',
    'io.github.maxlyth.hapaneld/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
  ]),
  [OLD_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
  ]),
  [NEW_CLASSES_SUCCESSOR_LAUNCH]: Object.freeze([
    'io.panelassistant.android/.input.PanelAccessibilityService',
    'io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService',
  ]),
});

export function isAcceptedPackageId(packageId) {
  return ACCEPTED_PACKAGE_IDS.includes(packageId);
}

/** Whether a build of this accepted id may name this launcher. */
export function isLaunchComponent(packageId, component) {
  return isAcceptedPackageId(packageId) && LAUNCH_COMPONENTS[packageId].includes(component);
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

export function counterpartOf(packageId) {
  if (packageId === LEGACY_PACKAGE_ID) return SUCCESSOR_PACKAGE_ID;
  if (packageId === SUCCESSOR_PACKAGE_ID) return LEGACY_PACKAGE_ID;
  throw new RangeError('unknown application id');
}
