import fs from 'node:fs';
import path from 'node:path';

const PROC_NET = ['/proc/net/tcp', '/proc/net/tcp6', '/proc/net/udp', '/proc/net/udp6'];

export function discoverOpenPorts({ procRoot = '/proc' } = {}) {
  const inodeOwners = buildInodeOwnerMap(procRoot);
  const results = [];
  for (const file of PROC_NET) {
    const fullPath = path.join(procRoot, file.replace('/proc/', ''));
    if (!fs.existsSync(fullPath)) continue;
    const protocol = file.includes('udp') ? 'udp' : 'tcp';
    const ipVersion = file.endsWith('6') ? 6 : 4;
    for (const row of parseProcNet(fs.readFileSync(fullPath, 'utf8'), { protocol, ipVersion })) {
      const owner = inodeOwners.get(row.inode) || null;
      results.push({ ...row, owner });
    }
  }
  return results.sort((a, b) => a.port - b.port || a.protocol.localeCompare(b.protocol));
}

export function parseProcNet(contents, { protocol, ipVersion }) {
  return contents.trim().split('\n').slice(1).map(line => line.trim().split(/\s+/)).filter(cols => cols.length > 9).map(cols => {
    const [rawAddress, rawPort] = cols[1].split(':');
    return {
      protocol,
      ipVersion,
      address: decodeAddress(rawAddress, ipVersion),
      port: Number.parseInt(rawPort, 16),
      state: protocol === 'tcp' ? tcpState(cols[3]) : 'LISTEN',
      inode: cols[9],
    };
  }).filter(row => row.port > 0 && (protocol === 'udp' || row.state === 'LISTEN'));
}

function buildInodeOwnerMap(procRoot) {
  const owners = new Map();
  let pids = [];
  try { pids = fs.readdirSync(procRoot).filter(name => /^\d+$/.test(name)); } catch { return owners; }
  for (const pid of pids) {
    const fdDir = path.join(procRoot, pid, 'fd');
    let fds = [];
    try { fds = fs.readdirSync(fdDir); } catch { continue; }
    const owner = readProcessOwner(procRoot, pid);
    for (const fd of fds) {
      try {
        const target = fs.readlinkSync(path.join(fdDir, fd));
        const match = target.match(/^socket:\[(\d+)\]$/);
        if (match && !owners.has(match[1])) owners.set(match[1], owner);
      } catch { /* process exited */ }
    }
  }
  return owners;
}

function readProcessOwner(procRoot, pid) {
  const owner = { pid: Number(pid), command: 'unknown' };
  try { owner.command = fs.readFileSync(path.join(procRoot, pid, 'comm'), 'utf8').trim(); } catch { /* ignore */ }
  return owner;
}

function decodeAddress(raw, ipVersion) {
  if (ipVersion === 4) {
    const bytes = raw.match(/../g).reverse().map(hex => Number.parseInt(hex, 16));
    return bytes.join('.');
  }
  const chunks = raw.match(/.{8}/g) || [];
  return chunks.map(chunk => chunk.match(/.{4}/g).reverse().join('')).join(':').replace(/(^|:)0(:0)+(:|$)/, '::');
}

function tcpState(hex) {
  return ({ '0A': 'LISTEN', '01': 'ESTABLISHED', '06': 'TIME_WAIT' })[hex] || hex;
}
