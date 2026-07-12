import dgram from 'node:dgram';
import { RateLimiter } from './rateLimiter.js';
import { validateGamePacket } from './gameProtocols.js';

export class UdpDdosGuard {
  constructor({ protocol = 'genericUdp', limiter = new RateLimiter({ capacity: 80, refillRatePerSec: 40 }), subnetBlocker = null, onAllow = () => {}, onDeny = () => {} } = {}) {
    this.protocol = protocol;
    this.limiter = limiter;
    this.subnetBlocker = subnetBlocker;
    this.onAllow = onAllow;
    this.onDeny = onDeny;
  }

  inspect(packet, rinfo) {
    if (this.subnetBlocker?.isBlocked?.(rinfo.address)) return this.deny(packet, rinfo, 'subnet_blocked');
    const key = `${rinfo.address}:${rinfo.port}`;
    const validation = validateGamePacket(packet, this.protocol);
    if (!validation.allowed) return this.deny(packet, rinfo, validation.reason);
    if (!this.limiter.allow(key)) return this.deny(packet, rinfo, 'rate_limited');
    this.onAllow(packet, rinfo);
    return { allowed: true, reason: 'ok' };
  }

  deny(packet, rinfo, reason) {
    this.subnetBlocker?.record?.(rinfo.address, reason);
    this.onDeny(packet, rinfo, reason);
    return { allowed: false, reason };
  }
}

export function createUdpGuardServer(options = {}) {
  const { bindHost = '0.0.0.0', bindPort, socket = dgram.createSocket('udp4') } = options;
  const guard = new UdpDdosGuard(options);
  socket.on('message', (packet, rinfo) => guard.inspect(packet, rinfo));
  return { guard, socket, listen: () => socket.bind(bindPort, bindHost), close: () => socket.close() };
}
