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
