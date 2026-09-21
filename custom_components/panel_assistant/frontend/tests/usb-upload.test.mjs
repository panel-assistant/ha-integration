import test from 'node:test';
import assert from 'node:assert/strict';
import { uploadApk } from '../src/usb-upload.mjs';
import { stagingPath } from '../src/staging-contract.mjs';

const JOB_ID = 'a'.repeat(32);
const decoder = new TextDecoder();

function fixture({ bytes = 4096 } = {}) {
  const apk = new Blob([new Uint8Array(bytes).fill(7)]);
  const release = { kind: 'authenticated-apk-bytes', apk,
    descriptor: { apkSize: bytes, apkSha256: 'b'.repeat(64) } };
  const frames = [];
  const socket = {
    writable: new WritableStream({ write(chunk) { frames.push(chunk); } }),
    readable: new ReadableStream({ start(controller) {
      const okay = new Uint8Array(8);
      okay.set(new TextEncoder().encode('OKAY'));
      controller.enqueue(okay);
      controller.close();
    } }),
    close: async () => {},
  };
  return { release, frames, adb: { createSocket: async () => socket } };
}

const identifiers = frames => frames.map(frame => decoder.decode(frame.subarray(0, 4)));
const payloadWord = frame => new DataView(frame.buffer, frame.byteOffset, frame.byteLength).getUint32(4, true);

test('the copy sends the staged path, the bytes and one DONE frame', async () => {
  const f = fixture();
  await uploadApk(f.adb, JOB_ID, f.release, { quarantine: () => {} });
  assert.deepEqual(identifiers(f.frames), ['SEND', 'DATA', 'DONE']);
  assert.equal(decoder.decode(f.frames[0].subarray(8)), `${stagingPath(JOB_ID)},33188`);
  assert.equal(f.frames[1].byteLength, 8 + 4096);
});

test('the DONE frame dates the staged file to now, never to the epoch', async () => {
  const before = Math.floor(Date.now() / 1000);
  const f = fixture();
  await uploadApk(f.adb, JOB_ID, f.release, { quarantine: () => {} });
  const done = f.frames.at(-1);
  const mtime = payloadWord(done);
  assert.equal(decoder.decode(done.subarray(0, 4)), 'DONE');
  assert.equal(done.byteLength, 8);
  // A file stamped 1970 is what a zero payload produces, and what the panel
  // showed after the first browser upload.
  assert.notEqual(mtime, 0);
  assert.ok(mtime >= before && mtime <= Math.floor(Date.now() / 1000) + 1,
    `DONE carried ${mtime}, which is not the time this copy finished`);
  assert.ok(new Date(mtime * 1000).getUTCFullYear() >= 2026);
});
