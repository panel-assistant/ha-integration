import test from 'node:test';
import assert from 'node:assert/strict';
import * as vendor from '../src/vendor-provisioning.mjs';

test('USB connection guide tells Shelly owners exactly which labels to tap before enabling debugging', () => {
  const nodes = new Map();
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); } };
  vendor.renderShellyDeveloperGuide(document);
  const [summary, ...steps] = Array.from(nodes.values());
  assert.match(summary.textContent, /Shelly Wall Display/);
  assert.match(steps[0].textContent, /confirmed on the X2i on earlier firmware.*unavailable on 2\.8\.4.*ask Shelly.*your model and firmware.*Settings > General > About Device/);
  assert.match(steps[1].textContent, /row labels.*FW Version.*Hardware Revision.*values.*card headings/);
  assert.equal(steps[2].textContent, 'Tap: FW Version → Hardware Revision → FW Version → FW Version → Hardware Revision → FW Version → Hardware Revision → Hardware Revision.');
  assert.match(steps[3].textContent, /Android Settings.*USB debugging/);
});
