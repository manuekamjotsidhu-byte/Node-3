import { discoverOpenPorts } from './portDiscovery.js';
import { FirewallBlocklist, TempSubnetBlocker } from './subnetBlocker.js';

export class HostProtectionEngine {
  constructor({ scanIntervalMs = 30000, procRoot = '/proc', firewallEnabled = false, blocker = null, onPorts = console.table, onBlock = console.warn } = {}) {
    this.scanIntervalMs = scanIntervalMs;
    this.procRoot = procRoot;
    this.firewall = new FirewallBlocklist({ dryRun: !firewallEnabled });
    this.blocker = blocker || new TempSubnetBlocker({ firewall: this.firewall });
    this.onPorts = onPorts;
    this.onBlock = onBlock;
    this.timer = null;
  }

  scanPorts() {
    const ports = discoverOpenPorts({ procRoot: this.procRoot });
    this.onPorts(ports.map(p => ({ protocol: p.protocol, port: p.port, address: p.address, pid: p.owner?.pid || null, command: p.owner?.command || null })));
    return ports;
  }

  recordAttack(ip, reason) {
    const result = this.blocker.record(ip, reason);
    if (result.blocked) this.onBlock(`Temporary subnet block applied: ${result.subnet} (${result.reason})`);
    return result;
  }

  start() {
    this.scanPorts();
    this.timer = setInterval(() => {
      this.blocker.cleanup();
      this.scanPorts();
    }, this.scanIntervalMs);
    this.timer.unref?.();
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
