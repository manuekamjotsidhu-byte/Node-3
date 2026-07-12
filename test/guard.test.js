import test from 'node:test';
import assert from 'node:assert/strict';
import { RateLimiter, validateGamePacket, UdpDdosGuard } from '../src/index.js';

test('rate limiter blocks after capacity and refills over time', () => {
  let now = 0;
  const limiter = new RateLimiter({ capacity: 2, refillRatePerSec: 1, now: () => now });
  assert.equal(limiter.allow('ip'), true);
  assert.equal(limiter.allow('ip'), true);
  assert.equal(limiter.allow('ip'), false);
  now = 1000;
  assert.equal(limiter.allow('ip'), true);
});

test('source protocol accepts A2S_INFO and rejects junk', () => {
  assert.deepEqual(validateGamePacket(Buffer.from([0xff, 0xff, 0xff, 0xff, 0x54, 0x00]), 'source'), { allowed: true, reason: 'ok' });
  assert.equal(validateGamePacket(Buffer.from('junk'), 'source').allowed, false);
});

test('udp guard reports denied protocol mismatches', () => {
  const denied = [];
  const guard = new UdpDdosGuard({ protocol: 'source', onDeny: (_packet, _rinfo, reason) => denied.push(reason) });
  const result = guard.inspect(Buffer.from('bad'), { address: '127.0.0.1', port: 12345 });
  assert.equal(result.allowed, false);
  assert.deepEqual(denied, ['packet_too_small']);
});

import { parseProcNet, subnetKey, TempSubnetBlocker } from '../src/index.js';

test('proc net parser discovers listening ports from kernel tables', () => {
  const sample = `  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000   100        0 12345 1 0000000000000000 100 0 0 10 0\n`;
  assert.deepEqual(parseProcNet(sample, { protocol: 'tcp', ipVersion: 4 })[0], {
    protocol: 'tcp',
    ipVersion: 4,
    address: '127.0.0.1',
    port: 8080,
    state: 'LISTEN',
    inode: '12345',
  });
});

test('temporary subnet blocker blocks and expires attacking subnets', () => {
  let now = 0;
  const actions = [];
  const firewall = {
    blockSubnet: (subnet) => actions.push(['block', subnet]),
    unblockSubnet: (subnet) => actions.push(['unblock', subnet]),
  };
  const blocker = new TempSubnetBlocker({ threshold: 2, windowMs: 1000, blockMs: 5000, firewall, now: () => now });
  assert.equal(subnetKey('203.0.113.55'), '203.0.113.0/24');
  assert.equal(blocker.record('203.0.113.10').blocked, false);
  assert.equal(blocker.record('203.0.113.20').blocked, true);
  assert.equal(blocker.isBlocked('203.0.113.99'), true);
  now = 5001;
  assert.equal(blocker.isBlocked('203.0.113.99'), false);
  assert.deepEqual(actions, [['block', '203.0.113.0/24'], ['unblock', '203.0.113.0/24']]);
});


test('expanded game protocol filters accept common game query families', () => {
  assert.equal(validateGamePacket(Buffer.concat([Buffer.from([0x05]), Buffer.alloc(15), Buffer.from('00ffff00fefefefefdfdfdfd12345678', 'hex')]), 'raknet').allowed, true);
  assert.equal(validateGamePacket(Buffer.concat([Buffer.from('SAMP'), Buffer.alloc(7)]), 'samp').allowed, true);
  assert.equal(validateGamePacket(Buffer.from('getinfo xxx'), 'fivem').allowed, true);
  assert.equal(validateGamePacket(Buffer.from('TS3INIT1'), 'teamspeak3').allowed, true);
});
