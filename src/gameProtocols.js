const A2S_INFO_PREFIX = Buffer.from([0xff, 0xff, 0xff, 0xff, 0x54]);
const A2S_PLAYER_PREFIX = Buffer.from([0xff, 0xff, 0xff, 0xff, 0x55]);
const MINECRAFT_LEGACY_PING = Buffer.from([0xfe, 0x01]);
const RAKNET_MAGIC = Buffer.from('00ffff00fefefefefdfdfdfd12345678', 'hex');
const SAMP_PREFIX = Buffer.from('SAMP');
const FIVEM_INFO = Buffer.from('getinfo xxx');
const TEAMSPEAK3_PREFIX = Buffer.from('TS3INIT1');

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
  raknet: {
    minBytes: 17,
    maxBytes: 1500,
    validate(packet) { return [0x05, 0x06, 0x07, 0x1c].includes(packet[0]) && packet.subarray(0, 40).includes(RAKNET_MAGIC); },
  },
  bedrock: {
    minBytes: 17,
    maxBytes: 1500,
    validate(packet) { return GAME_PROTOCOLS.raknet.validate(packet); },
  },
  samp: {
    minBytes: 11,
    maxBytes: 512,
    validate(packet) { return startsWith(packet, SAMP_PREFIX); },
  },
  fivem: {
    minBytes: 8,
    maxBytes: 1400,
    validate(packet) { return startsWith(Buffer.from(packet.toString('utf8').toLowerCase()), FIVEM_INFO) || startsWith(packet, Buffer.from([0xff, 0xff, 0xff, 0xff, 0x67])); },
  },
  teamspeak3: {
    minBytes: 4,
    maxBytes: 512,
    validate(packet) { return startsWith(packet, TEAMSPEAK3_PREFIX) || startsWith(packet, Buffer.from([0x05, 0xca, 0x7f, 0x16])); },
  },
  auto: {
    minBytes: 1,
    maxBytes: 1500,
    validate(packet) { return ['source', 'minecraft', 'raknet', 'samp', 'fivem', 'teamspeak3'].some(name => GAME_PROTOCOLS[name].validate(packet)); },
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
