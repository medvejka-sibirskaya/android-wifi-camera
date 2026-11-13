// Runs the real inline viewer script without third-party browser packages.
const assert = require('node:assert/strict');
const vm = require('node:vm');
let source = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', part => source += part);
process.stdin.on('end', async () => {
  try {
    const image = { removeAttribute(name) { delete this[name]; } };
    const message = { hidden: false };
    const scheduled = [], revoked = [];
    const replies = [200, 200, 204, 500, new Error('connection lost'), 200];
    let serial = 0;
    const context = {
      document: { getElementById: id => id === 'v' ? image : message },
      URL: {
        createObjectURL: () => `blob:${++serial}`,
        revokeObjectURL: url => revoked.push(url),
      },
      fetch: async () => {
        const status = replies.shift();
        if (status instanceof Error) throw status;
        return { status, ok: status >= 200 && status < 300, blob: async () => ({}) };
      },
      setTimeout: callback => scheduled.push(callback),
    };
    await vm.runInNewContext(source, context);
    assert.equal(image.src, 'blob:1');
    assert.equal(message.hidden, true);
    await scheduled.shift()();
    assert.equal(image.src, 'blob:2');
    assert.deepEqual(revoked, ['blob:1']);
    await scheduled.shift()(); // 204 is successful HTTP, but contains no picture.
    assert.equal(image.src, undefined);
    assert.equal(message.hidden, false);
    assert.deepEqual(revoked, ['blob:1', 'blob:2']);
    await scheduled.shift()(); // Server error.
    assert.equal(message.hidden, false);
    await scheduled.shift()(); // Network error.
    assert.equal(message.hidden, false);
    await scheduled.shift()(); // Receiver recovers.
    assert.equal(image.src, 'blob:3');
    assert.equal(message.hidden, true);
    assert.equal(scheduled.length, 1);
    console.log('Viewer: new frames, expiry, errors and reconnection passed');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
});
