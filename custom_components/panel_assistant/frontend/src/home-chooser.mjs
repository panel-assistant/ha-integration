// Makes the installed build's dashboard the panel's HOME, exactly as
// home_chooser.py does for the network installer: set-home-activity first, and
// where the firmware still resolves HOME to Android's chooser, press HOME and
// the chooser's Always button, but only when its title names this app. The
// program leaves what it did in hp_chooser. A test compares this text with the
// Python one by value.
import { homeComponentFor, homeComponentsFor } from './app-identity.mjs';

export const HOME_QUERY = 'cmd package resolve-activity --brief ' +
  '-a android.intent.action.MAIN -c android.intent.category.HOME 2>/dev/null | tail -n 1';
// The app's untranslated app_name by application id.
export const APP_LABELS = Object.freeze({
  'io.github.maxlyth.hapaneld': Object.freeze(['ha-paneld']),
  'io.panelassistant.android': Object.freeze(['Panel Assistant', 'ha-paneld']),
});
export const CHOOSER_DUMP = '/data/local/tmp/panel-assistant-home-chooser.xml';
export const CHOOSER_OUTCOMES = Object.freeze(['kept', 'set', 'tapped', 'other_app', 'no_button', 'other_home']);

const QUIET = '>/dev/null 2>&1';
const ALWAYS = 'resource-id="android:id/button_always"';

export function buildHomeClaim(launchComponent) {
  const homes = homeComponentsFor(launchComponent).map(name => `'${name}'`).join('|');
  const dashboard = homeComponentFor(launchComponent);
  const labels = APP_LABELS[launchComponent.split('/')[0]].map(label => `*'${label}'*`).join('|');
  const dump = CHOOSER_DUMP;
  const chooser = [
    'hp_chooser=no_button',
    `input keyevent 3 ${QUIET}`,
    'hp_ui=',
    'hp_try=0',
    'while [ "$hp_try" -lt 3 ]; do hp_try=$((hp_try+1)); sleep 1; ' +
      `rm -f ${dump}; uiautomator dump ${dump} ${QUIET}; ` +
      `hp_ui=$(tr '>' '\\n' < ${dump} 2>/dev/null); rm -f ${dump}; ` +
      `case "$hp_ui" in *'${ALWAYS}'*) break ;; esac; done`,
    'hp_always=$(printf \'%s\\n\' "$hp_ui" | ' +
      `grep -F '${ALWAYS}' | grep -F 'package="android"' | ` +
      'grep -F \'enabled="true"\')',
    'hp_title=$(printf \'%s\\n\' "$hp_ui" | ' +
      'grep -F \'resource-id="android:id/title"\' | ' +
      'grep -F \'package="android"\' | ' +
      'sed -n \'s/.* text="\\([^"]*\\)".*/\\1/p\')',
    'hp_box=$(printf \'%s\\n\' "$hp_always" | sed -n ' +
      '\'s/.* bounds="\\[\\([0-9]\\{1,5\\}\\),\\([0-9]\\{1,5\\}\\)\\]' +
      '\\[\\([0-9]\\{1,5\\}\\),\\([0-9]\\{1,5\\}\\)\\]".*/\\1 \\2 \\3 \\4/p\')',
    'case "$hp_always$hp_title$hp_box" in *\'\n\'*) hp_always= ;; esac',
    'if [ -n "$hp_always" ] && [ -n "$hp_box" ]; then ' +
      `case "$hp_title" in ${labels}) ` +
      'hp_l=${hp_box%% *}; hp_box=${hp_box#* }; hp_t=${hp_box%% *}; ' +
      'hp_box=${hp_box#* }; hp_r=${hp_box%% *}; hp_b=${hp_box#* }; ' +
      'if [ "$hp_r" -gt "$hp_l" ] && [ "$hp_b" -gt "$hp_t" ]; then ' +
      'input tap $(((hp_l+hp_r)/2)) $(((hp_t+hp_b)/2)) ' +
      `${QUIET}; hp_chooser=tapped; sleep 1; fi ;; ` +
      '*) hp_chooser=other_app ;; esac; fi',
    'if [ "$hp_chooser" != tapped ]; then ' +
      'case "$hp_ui" in *\'resource-id="android:id/button_\'*) ' +
      `input keyevent 4 ${QUIET} ;; esac; fi`,
  ].join('; ');
  return `case "$(${HOME_QUERY})" in ${homes}) hp_chooser=kept ;; ` +
    `*) cmd package set-home-activity ${dashboard} ${QUIET}; ` +
    `case "$(${HOME_QUERY})" in ${homes}) hp_chooser=set ;; ` +
    `android/*) ${chooser} ;; ` +
    '*) hp_chooser=other_home ;; esac ;; esac';
}
