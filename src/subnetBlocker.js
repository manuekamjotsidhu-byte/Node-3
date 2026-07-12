import { spawnSync } from 'node:child_process';

export function subnetKey(ip, prefix = 24) {
  if (!ip.includes('.')) return ip;
  const octets = ip.split('.').map(Number);
  const keep = Math.max(0, Math.min(4, Math.floor(prefix / 8)));
  return `${octets.slice(0, keep).join('.')}.${Array(4 - keep).fill('0').join('.')}/${prefix}`;
}

export class TempSubnetBlocker {
  constructor({ threshold = 25, windowMs = 10000, blockMs = 600000, prefix = 24, firewall = null, now = () => Date.now() } = {}) {
    this.threshold = threshold;
    this.windowMs = windowMs;
    this.blockMs = blockMs;
    this.prefix = prefix;
    this.firewall = firewall;
    this.now = now;
    this.events = new Map();
    this.blocks = new Map();
  }

  record(ip, reason = 'attack') {
    const subnet = subnetKey(ip, this.prefix);
    const current = this.now();
    if (this.isBlocked(ip)) return { blocked: true, subnet, reason: 'already_blocked' };
    const events = (this.events.get(subnet) || []).filter(ts => current - ts <= this.windowMs);
    events.push(current);
    this.events.set(subnet, events);
    if (events.length >= this.threshold) return this.block(subnet, reason);
    return { blocked: false, subnet, count: events.length };
  }

  block(subnet, reason = 'threshold') {
    const expiresAt = this.now() + this.blockMs;
    this.blocks.set(subnet, { expiresAt, reason });
    this.events.delete(subnet);
    this.firewall?.blockSubnet?.(subnet, this.blockMs, reason);
    return { blocked: true, subnet, expiresAt, reason };
  }

  isBlocked(ip) {
    this.cleanup();
    return this.blocks.has(subnetKey(ip, this.prefix));
  }

  cleanup() {
    const current = this.now();
    for (const [subnet, block] of this.blocks) {
      if (block.expiresAt <= current) {
        this.blocks.delete(subnet);
        this.firewall?.unblockSubnet?.(subnet);
      }
    }
  }
}

export class FirewallBlocklist {
  constructor({ setName = 'node3_ddos_temp', dryRun = true, commandRunner = runCommand } = {}) {
    this.setName = setName;
    this.dryRun = dryRun;
    this.commandRunner = commandRunner;
  }

  ensure() {
    const created = this.exec('ipset', ['create', this.setName, 'hash:net', 'timeout', '0', '-exist']);
    this.ensureDropRule('INPUT');
    return created;
  }

  ensureDropRule(chain) {
    const rule = [chain, '-m', 'set', '--match-set', this.setName, 'src', '-j', 'DROP'];
    const check = this.exec('iptables', ['-C', ...rule]);
    if (check.status !== 0) this.exec('iptables', ['-I', ...rule]);
  }

  blockSubnet(subnet, blockMs) {
    this.ensure();
    const timeout = String(Math.ceil(blockMs / 1000));
    return this.exec('ipset', ['add', this.setName, subnet, 'timeout', timeout, '-exist']);
  }

  unblockSubnet(subnet) {
    return this.exec('ipset', ['del', this.setName, subnet]);
  }

  exec(cmd, args) {
    if (this.dryRun) return { status: 0, dryRun: true, cmd, args };
    return this.commandRunner(cmd, args);
  }
}

function runCommand(cmd, args) {
  const result = spawnSync(cmd, args, { encoding: 'utf8' });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}
