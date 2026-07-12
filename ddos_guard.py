#!/usr/bin/env python3
"""Standalone Node-3 DDoS Guard.

One file that can run, install, uninstall, and inspect a VPS-level Layer 4/Layer 7
protection service with game protocol filtering. It uses only Python stdlib.
"""
from __future__ import annotations

import argparse
import http.server
import ipaddress
import json
import os
import re
import selectors
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

APP_NAME = os.environ.get("APP_NAME", "node-3-ddos-guard-py")
INSTALL_PATH = Path(os.environ.get("INSTALL_PATH", f"/opt/{APP_NAME}/ddos_guard.py"))
SERVICE_NAME = os.environ.get("SERVICE_NAME", APP_NAME)
ENV_PATH = Path(os.environ.get("ENV_PATH", f"/etc/{APP_NAME}.env"))

SOURCE_PREFIXES = (b"\xff\xff\xff\xffT", b"\xff\xff\xff\xffU", b"\xff\xff\xff\xffV", b"\xff\xff\xff\xffW")
RAKNET_UNCONNECTED = (b"\x05", b"\x06", b"\x07", b"\x1c")
RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
SAMP_QUERY = b"SAMP"
FIVEM_QUERY = b"getinfo xxx"
TEAMSPEAK3_PREFIX = b"TS3INIT1"


def now_ms() -> int:
    return int(time.time() * 1000)


class TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = float(capacity)
        self.updated = time.monotonic()

    def take(self, cost: int = 1) -> bool:
        current = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (current - self.updated) * self.refill_per_sec)
        self.updated = current
        if self.tokens < cost:
            return False
        self.tokens -= cost
        return True


class RateLimiter:
    def __init__(self, capacity: int = 120, refill_per_sec: float = 60.0, ttl_sec: int = 300) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.ttl_sec = ttl_sec
        self.buckets: dict[str, tuple[TokenBucket, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str, cost: int = 1) -> bool:
        with self.lock:
            bucket, _ = self.buckets.get(key, (TokenBucket(self.capacity, self.refill_per_sec), 0.0))
            self.buckets[key] = (bucket, time.monotonic())
            return bucket.take(cost)

    def cleanup(self) -> None:
        cutoff = time.monotonic() - self.ttl_sec
        with self.lock:
            for key, (_, seen) in list(self.buckets.items()):
                if seen < cutoff:
                    self.buckets.pop(key, None)


@dataclass
class PacketVerdict:
    allowed: bool
    reason: str
    protocol: str


class GameProtocolFilter:
    """Protocol-aware UDP validation for common game protocols."""

    def __init__(self, protocol: str = "auto", min_len: int = 1, max_len: int = 1500) -> None:
        self.protocol = protocol.lower()
        self.min_len = min_len
        self.max_len = max_len

    def validate(self, packet: bytes) -> PacketVerdict:
        if len(packet) < self.min_len:
            return PacketVerdict(False, "packet_too_small", self.protocol)
        if len(packet) > self.max_len:
            return PacketVerdict(False, "packet_too_large", self.protocol)
        checks: dict[str, Callable[[bytes], bool]] = {
            "source": is_source_query,
            "valve": is_source_query,
            "minecraft": is_minecraft_query,
            "raknet": is_raknet_query,
            "bedrock": is_raknet_query,
            "samp": is_samp_query,
            "fivem": is_fivem_query,
            "teamspeak3": is_teamspeak3_query,
            "genericudp": lambda _: True,
        }
        if self.protocol == "auto":
            for name, check in checks.items():
                if name != "genericudp" and check(packet):
                    return PacketVerdict(True, "ok", name)
            return PacketVerdict(False, "unknown_game_protocol", "auto")
        check = checks.get(self.protocol)
        if not check:
            return PacketVerdict(False, "unsupported_protocol", self.protocol)
        return PacketVerdict(bool(check(packet)), "ok" if check(packet) else "protocol_mismatch", self.protocol)


def is_source_query(packet: bytes) -> bool:
    return any(packet.startswith(prefix) for prefix in SOURCE_PREFIXES) and len(packet) <= 1400


def is_minecraft_query(packet: bytes) -> bool:
    if packet.startswith(b"\xfe\x01") or packet.startswith(b"\xfe"):
        return True
    if 3 <= len(packet) <= 512 and packet[1] == 0x00 and 0 < packet[0] <= len(packet) - 1:
        return True
    return False


def is_raknet_query(packet: bytes) -> bool:
    return len(packet) >= 17 and packet[:1] in RAKNET_UNCONNECTED and RAKNET_MAGIC in packet[:40]


def is_samp_query(packet: bytes) -> bool:
    return len(packet) >= 11 and packet.startswith(SAMP_QUERY)


def is_fivem_query(packet: bytes) -> bool:
    return packet.lower().startswith(FIVEM_QUERY) or packet.startswith(b"\xff\xff\xff\xffgetinfo")


def is_teamspeak3_query(packet: bytes) -> bool:
    return packet.startswith(TEAMSPEAK3_PREFIX) or packet.startswith(b"\x05\xca\x7f\x16")


class TemporarySubnetBlocker:
    def __init__(self, threshold: int = 25, window_sec: int = 10, block_sec: int = 600, prefix: int = 24, firewall: bool = False) -> None:
        self.threshold = threshold
        self.window_sec = window_sec
        self.block_sec = block_sec
        self.prefix = prefix
        self.firewall = firewall
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.blocks: dict[str, tuple[float, str]] = {}
        self.lock = threading.Lock()

    def subnet(self, ip: str) -> str:
        try:
            address = ipaddress.ip_address(ip)
            network = ipaddress.ip_network(f"{address}/{self.prefix if address.version == 4 else 64}", strict=False)
            return str(network)
        except ValueError:
            return ip

    def is_blocked(self, ip: str) -> bool:
        self.cleanup()
        return self.subnet(ip) in self.blocks

    def record(self, ip: str, reason: str) -> bool:
        subnet = self.subnet(ip)
        current = time.monotonic()
        with self.lock:
            if subnet in self.blocks:
                return True
            q = self.events[subnet]
            while q and current - q[0] > self.window_sec:
                q.popleft()
            q.append(current)
            if len(q) >= self.threshold:
                self.blocks[subnet] = (current + self.block_sec, reason)
                q.clear()
                self._firewall_add(subnet)
                print(f"[block] temporary subnet block {subnet} reason={reason} seconds={self.block_sec}", flush=True)
                return True
        return False

    def cleanup(self) -> None:
        current = time.monotonic()
        with self.lock:
            for subnet, (expires, _) in list(self.blocks.items()):
                if expires <= current:
                    self.blocks.pop(subnet, None)
                    self._firewall_del(subnet)
                    print(f"[unblock] expired subnet block {subnet}", flush=True)

    def _firewall_add(self, subnet: str) -> None:
        if not self.firewall:
            return
        run(["ipset", "create", "node3_ddos_temp", "hash:net", "timeout", "0", "-exist"], check=False)
        if run(["iptables", "-C", "INPUT", "-m", "set", "--match-set", "node3_ddos_temp", "src", "-j", "DROP"], check=False).returncode != 0:
            run(["iptables", "-I", "INPUT", "-m", "set", "--match-set", "node3_ddos_temp", "src", "-j", "DROP"], check=False)
        run(["ipset", "add", "node3_ddos_temp", subnet, "timeout", str(self.block_sec), "-exist"], check=False)

    def _firewall_del(self, subnet: str) -> None:
        if self.firewall:
            run(["ipset", "del", "node3_ddos_temp", subnet], check=False)


class GuardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    limiter: RateLimiter
    blocker: TemporarySubnetBlocker
    max_body: int = 1_000_000
    bad_paths = [re.compile(p, re.I) for p in (r"\.env$", r"wp-login\.php", r"xmlrpc\.php", r"/\.git/")]
    bad_agents = [re.compile(p, re.I) for p in (r"sqlmap", r"masscan", r"nikto", r"zgrab")]

    def do_GET(self) -> None: self._handle()
    def do_HEAD(self) -> None: self._handle(body=False)
    def do_POST(self) -> None: self._handle()
    def do_PUT(self) -> None: self._handle()
    def do_PATCH(self) -> None: self._handle()
    def do_DELETE(self) -> None: self._handle()
    def do_OPTIONS(self) -> None: self._handle()

    def _handle(self, body: bool = True) -> None:
        ip = self.client_address[0]
        length = int(self.headers.get("content-length", "0") or 0)
        ua = self.headers.get("user-agent", "")
        if self.blocker.is_blocked(ip):
            return self._deny(403, "subnet_blocked")
        if length > self.max_body:
            return self._attack(ip, 413, "payload_too_large")
        if any(rx.search(self.path) for rx in self.bad_paths):
            return self._attack(ip, 403, "blocked_path")
        if any(rx.search(ua) for rx in self.bad_agents):
            return self._attack(ip, 403, "blocked_user_agent")
        if not self.limiter.allow(f"{ip}:{self.command}:{self.path}"):
            return self._attack(ip, 429, "rate_limited")
        payload = json.dumps({"ok": True, "guard": "pass"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if body:
            self.wfile.write(payload)

    def _attack(self, ip: str, status: int, reason: str) -> None:
        self.blocker.record(ip, reason)
        self._deny(status, reason)

    def _deny(self, status: int, reason: str) -> None:
        payload = json.dumps({"error": reason}).encode()
        self.send_response(status)
        self.send_header("x-ddos-guard", reason)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[http] {self.client_address[0]} {fmt % args}", flush=True)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class UDPGuard(threading.Thread):
    def __init__(self, host: str, port: int, protocol: str, limiter: RateLimiter, blocker: TemporarySubnetBlocker) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.filter = GameProtocolFilter(protocol)
        self.limiter = limiter
        self.blocker = blocker
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.running = threading.Event()

    def run(self) -> None:
        self.sock.bind((self.host, self.port))
        self.running.set()
        print(f"[udp] Layer 4 game guard listening on {self.host}:{self.port} protocol={self.filter.protocol}", flush=True)
        while self.running.is_set():
            try:
                packet, addr = self.sock.recvfrom(2048)
            except OSError:
                break
            ip, src_port = addr
            if self.blocker.is_blocked(ip):
                continue
            verdict = self.filter.validate(packet)
            if not verdict.allowed or not self.limiter.allow(f"{ip}:{src_port}"):
                self.blocker.record(ip, verdict.reason if not verdict.allowed else "rate_limited")
                continue
            print(f"[udp] allowed {len(packet)}B from {ip}:{src_port} protocol={verdict.protocol}", flush=True)

    def stop(self) -> None:
        self.running.clear()
        self.sock.close()


def auto_interface() -> tuple[str, str]:
    route = run(["ip", "-o", "-4", "route", "show", "to", "default"], check=False, capture=True).stdout.strip()
    match = re.search(r"\bdev\s+(\S+)", route)
    if match:
        iface = match.group(1)
        ip = interface_ipv4(iface)
        if ip:
            return iface, ip
    candidates = public_ipv4_candidates()
    if candidates:
        return candidates[0]
    if sys.stdin.isatty():
        iface = input("Could not auto-detect the public IPv4 interface. Enter interface name: ").strip()
        ip = interface_ipv4(iface) or "0.0.0.0"
        return iface, ip
    print("[warn] could not auto-detect public interface; binding to 0.0.0.0", flush=True)
    return "0.0.0.0", "0.0.0.0"


def interface_ipv4(iface: str) -> Optional[str]:
    result = run(["ip", "-o", "-4", "addr", "show", "dev", iface], check=False, capture=True).stdout
    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", result)
    return match.group(1) if match else None


def public_ipv4_candidates() -> list[tuple[str, str]]:
    result = run(["ip", "-o", "-4", "addr", "show", "scope", "global"], check=False, capture=True).stdout
    found = []
    for line in result.splitlines():
        iface = line.split()[1]
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", line)
        if match and not ipaddress.ip_address(match.group(1)).is_private:
            found.append((iface, match.group(1)))
    return found


def discover_ports() -> list[dict[str, object]]:
    ports = []
    for proto, file in (("tcp", "/proc/net/tcp"), ("udp", "/proc/net/udp")):
        try:
            rows = Path(file).read_text().strip().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            local, state, inode = cols[1], cols[3], cols[9]
            if proto == "tcp" and state != "0A":
                continue
            raw_ip, raw_port = local.split(":")
            ip = socket.inet_ntoa(struct.pack("<L", int(raw_ip, 16)))
            ports.append({"protocol": proto, "address": ip, "port": int(raw_port, 16), "inode": inode})
    return sorted(ports, key=lambda p: (int(p["port"]), str(p["protocol"])))


def monitor_ports(stop: threading.Event, interval: int) -> None:
    while not stop.is_set():
        print("[ports] listening sockets:", json.dumps(discover_ports()), flush=True)
        stop.wait(interval)


def run_guard(args: argparse.Namespace) -> None:
    iface, detected_ip = auto_interface()
    bind_host = args.bind or "0.0.0.0"
    print(f"[iface] selected={iface} ipv4={detected_ip} bind={bind_host}", flush=True)
    blocker = TemporarySubnetBlocker(args.subnet_threshold, args.subnet_window, args.subnet_block_seconds, firewall=args.firewall)
    stop = threading.Event()
    threading.Thread(target=monitor_ports, args=(stop, args.port_scan_interval), daemon=True).start()
    udp = UDPGuard(bind_host, args.udp_port, args.game_protocol, RateLimiter(args.udp_capacity, args.udp_refill), blocker)
    udp.start()
    GuardHTTPRequestHandler.limiter = RateLimiter(args.http_capacity, args.http_refill)
    GuardHTTPRequestHandler.blocker = blocker
    GuardHTTPRequestHandler.max_body = args.max_body
    httpd = ThreadedHTTPServer((bind_host, args.http_port), GuardHTTPRequestHandler)

    def shutdown(_signum: int, _frame: object) -> None:
        stop.set(); udp.stop(); httpd.shutdown()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"[http] Layer 7 guard listening on {bind_host}:{args.http_port}", flush=True)
    httpd.serve_forever()


def install(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise SystemExit("install must run as root")
    INSTALL_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), INSTALL_PATH)
    INSTALL_PATH.chmod(0o755)
    ENV_PATH.write_text(
        f"HTTP_PORT={args.http_port}\nUDP_PORT={args.udp_port}\nGAME_PROTOCOL={args.game_protocol}\nFIREWALL_ENABLED={'true' if args.firewall else 'false'}\n"
    )
    service = f"""[Unit]
Description=Standalone Python Node-3 DDoS Guard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile={ENV_PATH}
ExecStart={sys.executable} {INSTALL_PATH} run --http-port ${{HTTP_PORT}} --udp-port ${{UDP_PORT}} --game-protocol ${{GAME_PROTOCOL}} {'--firewall' if args.firewall else ''}
Restart=always
RestartSec=2
StartLimitIntervalSec=0
NoNewPrivileges=true
PrivateTmp=true
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
"""
    Path(f"/etc/systemd/system/{SERVICE_NAME}.service").write_text(service)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", f"{SERVICE_NAME}.service"])
    run(["systemctl", "restart", f"{SERVICE_NAME}.service"])
    print(f"installed {SERVICE_NAME}.service from {INSTALL_PATH}")


def uninstall(_args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise SystemExit("uninstall must run as root")
    run(["systemctl", "stop", f"{SERVICE_NAME}.service"], check=False)
    run(["systemctl", "disable", f"{SERVICE_NAME}.service"], check=False)
    Path(f"/etc/systemd/system/{SERVICE_NAME}.service").unlink(missing_ok=True)
    INSTALL_PATH.unlink(missing_ok=True)
    ENV_PATH.unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"], check=False)
    print(f"uninstalled {SERVICE_NAME}.service")


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)
    except FileNotFoundError:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 127, "" if capture else None, "command not found" if capture else None)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Standalone Layer 4/7 DDoS guard with game protocol filtering")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "install"):
        sp = sub.add_parser(name)
        sp.add_argument("--bind", default=os.environ.get("BIND_HOST"))
        sp.add_argument("--http-port", type=int, default=int(os.environ.get("HTTP_PORT", "8080")))
        sp.add_argument("--udp-port", type=int, default=int(os.environ.get("UDP_PORT", "27015")))
        sp.add_argument("--game-protocol", default=os.environ.get("GAME_PROTOCOL", "auto"), choices=["auto", "source", "valve", "minecraft", "raknet", "bedrock", "samp", "fivem", "teamspeak3", "genericudp"])
        sp.add_argument("--firewall", action="store_true", default=os.environ.get("FIREWALL_ENABLED", "false").lower() == "true")
        sp.add_argument("--http-capacity", type=int, default=120)
        sp.add_argument("--http-refill", type=float, default=60.0)
        sp.add_argument("--udp-capacity", type=int, default=80)
        sp.add_argument("--udp-refill", type=float, default=40.0)
        sp.add_argument("--max-body", type=int, default=1_000_000)
        sp.add_argument("--subnet-threshold", type=int, default=25)
        sp.add_argument("--subnet-window", type=int, default=10)
        sp.add_argument("--subnet-block-seconds", type=int, default=600)
        sp.add_argument("--port-scan-interval", type=int, default=30)
    sub.add_parser("uninstall")
    sub.add_parser("ports")
    return p


def main() -> None:
    args = parser().parse_args()
    if args.cmd == "run":
        run_guard(args)
    elif args.cmd == "install":
        install(args)
    elif args.cmd == "uninstall":
        uninstall(args)
    elif args.cmd == "ports":
        print(json.dumps(discover_ports(), indent=2))


if __name__ == "__main__":
    main()
