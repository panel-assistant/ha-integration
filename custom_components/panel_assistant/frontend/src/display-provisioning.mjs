// The display and text size a panel's profile recommends, applied at setup
// exactly as display_provisioning.py applies it: the panel's own values are
// recorded on the panel once, then `wm density` and `font_scale` are set only
// where Android does not already read them. Panel Assistant's removal reads
// that record to put them back. A test compares this program with the Python
// one by value.
export const DISPLAY_RESTORE_RECORD = '/data/local/tmp/panel-assistant-display-restore';
const QUIET = '>/dev/null 2>&1';
const fail = () => { throw new Error('display_sizing_unreadable'); };

const DENSITY_FUNCTION = 'hp_density() { hp_w=$(wm density 2>/dev/null) || { echo unknown; return; }; ' +
  'case "$hp_w" in *"Override density: "*) ' +
  'hp_w=${hp_w##*Override density: }; hp_w=${hp_w%%[!0-9]*}; ' +
  'echo "${hp_w:-unknown}" ;; *"Physical density: "*) echo none ;; ' +
  '*) echo unknown ;; esac; }';

// `1` and `0.9`, never `1.0`, to six significant digits: Python's `f"{value:g}"`,
// the spelling the Python program writes, within the 0.5 to 1.5 range.
export const fontScaleText = value => String(Number(Number(value).toPrecision(6)));

// What Home Assistant's setup handover says to apply, or null.
export function displaySizing(value) {
  if (value === null || typeof value !== 'object') return null;
  const { density, font_scale: fontScale } = value;
  const densityOk = density === null || (Number.isInteger(density) && density >= 80 && density <= 640);
  const fontOk = fontScale === null || (typeof fontScale === 'number' && fontScale >= 0.5 && fontScale <= 1.5);
  if (!densityOk || !fontOk || (density === null && fontScale === null)) return null;
  return Object.freeze({ density, fontScale });
}

export function buildDisplaySizing(nonce, sizing) {
  if (!/^[a-f0-9]{32}$/.test(nonce) || !sizing) fail();
  const path = DISPLAY_RESTORE_RECORD;
  const record = `if [ ! -f ${path} ]; then hp_d=$(hp_density); ` +
    'if [ "$hp_d" != unknown ] && hp_f=$(settings get system font_scale); then ' +
    "printf 'density=%s\\nfont_scale=%s\\n' \"$hp_d\" \"$hp_f\" " +
    `> ${path}.part && mv ${path}.part ${path}; fi; rm -f ${path}.part; fi`;
  const writes = [];
  if (sizing.density !== null) {
    writes.push(`[ "$(hp_density)" = ${sizing.density} ] || wm density ${sizing.density} ${QUIET}`);
  }
  if (sizing.fontScale !== null) {
    const scale = fontScaleText(sizing.fontScale);
    // Android reads an unset text size as 1.
    const same = scale === '1' ? `${scale}|1.0|null` : scale;
    writes.push(`case "$(settings get system font_scale)" in ${same}) ;; ` +
      `*) settings put system font_scale ${scale} ${QUIET} ;; esac`);
  }
  return [
    `echo HAPANELD_DISPLAY_BEGIN:${nonce}`,
    DENSITY_FUNCTION,
    record,
    `if [ -f ${path} ]; then ` +
      'while IFS= read -r hp_line; do echo "record:$hp_line"; ' +
      `done < ${path}; ` + writes.join('; ') + '; fi',
    'echo "density=$(hp_density)"',
    'echo "font_scale=$(settings get system font_scale)"',
    `echo HAPANELD_DISPLAY_END:${nonce}:0`,
  ].join('; ');
}

const DENSITY_VALUE = /^(none|unknown|[0-9]{2,3})$/;
const FONT_VALUE = /^(null|[0-9]{1,2}(\.[0-9]{1,6})?)$/;

// `applied` when the recommendation reads back with the panel's own values on
// record, `not_applied` otherwise.
export function parseDisplaySizing(body, nonce, sizing) {
  if (typeof body !== 'string' || body.length > 4096) fail();
  const lines = body.replaceAll('\r\n', '\n').split('\n');
  if (lines.shift() !== `HAPANELD_DISPLAY_BEGIN:${nonce}` || lines.pop() !== '' ||
      lines.pop() !== `HAPANELD_DISPLAY_END:${nonce}:0` || lines.some(line => line.includes('HAPANELD_'))) fail();
  const records = lines.filter(line => line.startsWith('record:')).map(line => line.slice(7));
  const rest = lines.filter(line => !line.startsWith('record:'));
  if (rest.length !== 2 || ![0, 2].includes(records.length) ||
      !rest[0].startsWith('density=') || !rest[1].startsWith('font_scale=')) fail();
  const density = rest[0].slice(8);
  const fontScale = rest[1].slice(11);
  if (!DENSITY_VALUE.test(density) || !FONT_VALUE.test(fontScale)) fail();
  if (records.length && (!records[0].startsWith('density=') || !records[1].startsWith('font_scale=') ||
      !DENSITY_VALUE.test(records[0].slice(8)) || records[0] === 'density=unknown' ||
      !FONT_VALUE.test(records[1].slice(11)))) fail();
  const scale = sizing.fontScale === null ? null : fontScaleText(sizing.fontScale);
  const converged = records.length === 2 &&
    (sizing.density === null || density === String(sizing.density)) &&
    (scale === null || fontScale === scale || (scale === '1' && ['1.0', 'null'].includes(fontScale)));
  return converged ? 'applied' : 'not_applied';
}
