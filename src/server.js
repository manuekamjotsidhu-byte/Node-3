import http from 'node:http';
import { createLayer7Guard, createUdpGuardServer, HostProtectionEngine } from './index.js';

const httpPort = Number(process.env.HTTP_PORT || 8080);
const udpPort = Number(process.env.UDP_PORT || 27015);
const gameProtocol = process.env.GAME_PROTOCOL || 'source';
const firewallEnabled = process.env.FIREWALL_ENABLED === 'true';
const hostProtection = new HostProtectionEngine({ firewallEnabled });

hostProtection.start();

const layer7 = createLayer7Guard({
  trustedProxyHops: Number(process.env.TRUSTED_PROXY_HOPS || 0),
  subnetBlocker: hostProtection.blocker,
});

const server = http.createServer((req, res) => layer7(req, res, () => {
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ ok: true }));
}));

server.listen(httpPort, () => console.log(`Layer 7 guard listening on :${httpPort}`));

const udp = createUdpGuardServer({
  bindPort: udpPort,
  protocol: gameProtocol,
  subnetBlocker: hostProtection.blocker,
});
udp.listen();
console.log(`Layer 4 UDP guard listening on :${udpPort} for ${gameProtocol}`);
