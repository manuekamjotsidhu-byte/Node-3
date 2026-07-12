const A2S_INFO_PREFIX = Buffer.from([0xff, 0xff, 0xff, 0xff, 0x54]);
const A2S_PLAYER_PREFIX = Buffer.from([0xff, 0xff, 0xff, 0xff, 0x55]);
const MINECRAFT_LEGACY_PING = Buffer.from([0xfe, 0x01]);

export const GAME_PROTOCOLS = {
  source: {
    minBytes: 5,
    maxBytes: 1400,
    validate(packet) {
      return startsWith(packet, A2S_INFO_PREFIX) || startsWith(packet, A2S_PLAYER_PREFIX);
    },
  },
  minecraft: {
    minBytes: 1,
    maxBytes: 1500,
    validate(packet) {
      return startsWith(packet, MINECRAFT_LEGACY_PING) || isLikelyMinecraftHandshake(packet);
    },
  },
  genericUdp: {
    minBytes: 1,
    maxBytes: 1200,
    validate() { return true; },
  },
};

export function validateGamePacket(packet, protocolName) {
  const protocol = GAME_PROTOCOLS[protocolName];
  if (!protocol) return { allowed: false, reason: 'unknown_protocol' };
  if (!Buffer.isBuffer(packet)) return { allowed: false, reason: 'not_buffer' };
  if (packet.length < protocol.minBytes) return { allowed: false, reason: 'packet_too_small' };
  if (packet.length > protocol.maxBytes) return { allowed: false, reason: 'packet_too_large' };
  if (!protocol.validate(packet)) return { allowed: false, reason: 'protocol_mismatch' };
  return { allowed: true, reason: 'ok' };
}

function startsWith(packet, prefix) {
  return packet.length >= prefix.length && prefix.equals(packet.subarray(0, prefix.length));
}

function isLikelyMinecraftHandshake(packet) {
  // Modern Minecraft handshake packets are VarInt-framed and contain packet id 0x00 early in the payload.
  if (packet.length < 3 || packet.length > 512) return false;
  const frameLength = packet[0];
  return frameLength > 0 && frameLength <= packet.length - 1 && packet[1] === 0x00;
}
