# Node-3 DDoS Guard

Production-oriented Layer 4 and Layer 7 DDoS protection primitives for Node.js services.

> No application-layer library can stop all volumetric DDoS traffic by itself. Run this behind upstream capacity such as anycast scrubbing, cloud firewalls, or a CDN/WAF, and use these guards as the last-mile policy layer close to your game or web service.

## Features

- **Layer 4 UDP guard** with per-client token buckets, packet-size enforcement, and protocol validation.
- **Game protocol filtering** for Source Engine A2S queries, Minecraft server pings/handshakes, and a generic UDP baseline.
- **Layer 7 HTTP guard** with method allow-listing, path/user-agent deny rules, body-size limits, trusted proxy IP extraction, and per-IP/path rate limiting.
- **Dependency-free core** using Node.js built-ins for easier auditability and deployment.

## Quick start

```bash
npm test
HTTP_PORT=8080 UDP_PORT=27015 GAME_PROTOCOL=source npm start
```


## One-file install, run, and uninstall

Install and start the guard as a systemd service that automatically restarts if it crashes:

```bash
sudo HTTP_PORT=8080 UDP_PORT=27015 GAME_PROTOCOL=source ./install-or-run.sh install
```

Run in the foreground without installing a service:

```bash
HTTP_PORT=8080 UDP_PORT=27015 GAME_PROTOCOL=source ./install-or-run.sh run
```

Remove the service, installed files, environment file, and service user:

```bash
sudo ./uninstall.sh
```

The installer writes `/etc/node-3-ddos-guard.env`, installs the app under `/opt/node-3-ddos-guard`, and creates a `node-3-ddos-guard.service` unit with `Restart=always`.

## Layer 7 usage

```js
import http from 'node:http';
import { createLayer7Guard } from './src/index.js';

const guard = createLayer7Guard({ trustedProxyHops: 1, maxBodyBytes: 500_000 });

http.createServer((req, res) => guard(req, res, () => {
  res.end('origin response');
})).listen(8080);
```

## Layer 4 / game protocol usage

```js
import { createUdpGuardServer } from './src/index.js';

const udp = createUdpGuardServer({
  bindPort: 27015,
  protocol: 'source',
  onAllow(packet, rinfo) {
    // Forward to the protected game server or handle the query.
  },
  onDeny(packet, rinfo, reason) {
    // Emit metrics/logs without responding to suspicious traffic.
  },
});

udp.listen();
```

## Production hardening checklist

1. Put the service behind provider-level L3/L4 DDoS scrubbing for high-volume floods.
2. Restrict origin firewall rules so only trusted proxy/scrubbing ranges can reach your service.
3. Export `onAllow`/`onDeny` counters to Prometheus or your SIEM.
4. Tune token bucket capacities per endpoint and game protocol after measuring normal traffic.
5. Add protocol-specific validators for every game you host before exposing a new UDP port.
