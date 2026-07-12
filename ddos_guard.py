#!/usr/bin/env python3
"""Single-file VPS DDoS guard.

One .py file only. It creates its own runtime folders/config in the current
working directory, can run/install/uninstall itself, protects Layer 4 UDP game
traffic and Layer 7 HTTP traffic, keeps SSH (22) and uploads on HTTP (8080)
whitelisted, and uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

APP_NAME = os.environ.get("APP_NAME", "node3-ddos-guard")
SERVICE_NAME = os.environ.get("SERVICE_NAME", APP_NAME)
DEFAULT_WHITELIST_PORTS = {22, 8080}
SOURCE_PREFIXES = (b"\xff\xff\xff\xffT", b"\xff\xff\xff\xffU", b"\xff\xff\xff\xffV", b"\xff\xff\xff\xffW")
RAKNET_UNCONNECTED = (0x05, 0x06, 0x07, 0x1C)
RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def app_paths(base_dir: Path) -> dict[str, Path]:
    state = base_dir / ".node3_ddos_guard"
    return {
        "state": state,
        "uploads": state / "uploads",
        "logs": state / "logs",
        "config": state / "config.json",
        "installed_script": state / "ddos_guard.py",
    }


def ensure_current_dir_files(base_dir: Path, args: argparse.Namespace) -> dict[str, Path]:
    paths = app_paths(base_dir)
    for key in ("state", "uploads", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    config = {
        "http_port": args.http_port,
        "udp_port": args.udp_port,
        "game_protocol": args.game_protocol,
        "whitelist_ports": sorted(set(args.whitelist_port) | DEFAULT_WHITELIST_PORTS),
        "max_upload_bytes": args.max_upload_bytes,
        "created_by": "ddos_guard.py",
    }
    if not paths["config"].exists():
        paths["config"].write_text(json.dumps(config, indent=2) + "\n")
    return paths


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


@dataclass
class PacketVerdict:
    allowed: bool
    reason: str
    protocol: str


class GameProtocolFilter:
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
        ok = check(packet)
        return PacketVerdict(ok, "ok" if ok else "protocol_mismatch", self.protocol)


def is_source_query(packet: bytes) -> bool:
    return any(packet.startswith(prefix) for prefix in SOURCE_PREFIXES) and len(packet) <= 1400


def is_minecraft_query(packet: bytes) -> bool:
    return packet.startswith(b"\xfe") or (3 <= len(packet) <= 512 and packet[1] == 0x00 and 0 < packet[0] <= len(packet) - 1)


def is_raknet_query(packet: bytes) -> bool:
    return len(packet) >= 17 and packet[0] in RAKNET_UNCONNECTED and RAKNET_MAGIC in packet[:40]


def is_samp_query(packet: bytes) -> bool:
    return len(packet) >= 11 and packet.startswith(b"SAMP")


def is_fivem_query(packet: bytes) -> bool:
    return packet.lower().startswith(b"getinfo xxx") or packet.startswith(b"\xff\xff\xff\xffgetinfo")


def is_teamspeak3_query(packet: bytes) -> bool:
    return packet.startswith(b"TS3INIT1") or packet.startswith(b"\x05\xca\x7f\x16")


class TemporarySubnetBlocker:
    def __init__(self, threshold: int = 25, window_sec: int = 10, block_sec: int = 600, prefix: int = 24, firewall: bool = False, whitelist_ports: set[int] | None = None) -> None:
        self.threshold = threshold
        self.window_sec = window_sec
        self.block_sec = block_sec
        self.prefix = prefix
        self.firewall = firewall
        self.whitelist_ports = set(whitelist_ports or DEFAULT_WHITELIST_PORTS) | DEFAULT_WHITELIST_PORTS
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.blocks: dict[str, tuple[float, str]] = {}
        self.lock = threading.Lock()

    def subnet(self, ip: str) -> str:
        address = ipaddress.ip_address(ip)
        prefix = self.prefix if address.version == 4 else 64
        return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))

    def is_blocked(self, ip: str) -> bool:
        self.cleanup()
        try:
            return self.subnet(ip) in self.blocks
        except ValueError:
            return False

    def record(self, ip: str, reason: str) -> bool:
        try:
            subnet = self.subnet(ip)
        except ValueError:
            return False
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

    def ensure_whitelist(self) -> None:
        if not self.firewall:
            return
        for port in sorted(self.whitelist_ports):
            ensure_iptables_rule(["INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"])

    def _firewall_add(self, subnet: str) -> None:
        if not self.firewall:
            return
        self.ensure_whitelist()
        run(["ipset", "create", "node3_ddos_temp", "hash:net", "timeout", "0", "-exist"], check=False)
        ensure_iptables_rule(["INPUT", "-m", "set", "--match-set", "node3_ddos_temp", "src", "-j", "DROP"])
        run(["ipset", "add", "node3_ddos_temp", subnet, "timeout", str(self.block_sec), "-exist"], check=False)

    def _firewall_del(self, subnet: str) -> None:
        if self.firewall:
            run(["ipset", "del", "node3_ddos_temp", subnet], check=False)


class GuardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    limiter: RateLimiter
    blocker: TemporarySubnetBlocker
    upload_dir: Path
    max_upload_bytes: int = 100 * 1024 * 1024
    max_body: int = 1_000_000
    bad_paths = [re.compile(p, re.I) for p in (r"\.env$", r"wp-login\.php", r"xmlrpc\.php", r"/\.git/")]
    bad_agents = [re.compile(p, re.I) for p in (r"sqlmap", r"masscan", r"nikto", r"zgrab", r"masscan")]

    def do_GET(self) -> None:
        if self.path == "/health":
            return self._json(200, {"ok": True})
        self._handle()

    def do_HEAD(self) -> None: self._handle(body=False)
    def do_PUT(self) -> None: self._upload()
    def do_POST(self) -> None:
        if self.path.startswith("/upload"):
            return self._upload()
        self._handle()
    def do_PATCH(self) -> None: self._handle()
    def do_DELETE(self) -> None: self._handle()
    def do_OPTIONS(self) -> None: self._handle()

    def _handle(self, body: bool = True) -> None:
        ip = self.client_address[0]
        reason = self._blocked_reason(ip)
        if reason:
            return self._deny(403, reason)
        length = int(self.headers.get("content-length", "0") or 0)
        ua = self.headers.get("user-agent", "")
        if length > self.max_body:
            return self._attack(ip, 413, "payload_too_large")
        if any(rx.search(self.path) for rx in self.bad_paths):
            return self._attack(ip, 403, "blocked_path")
        if any(rx.search(ua) for rx in self.bad_agents):
            return self._attack(ip, 403, "blocked_user_agent")
        if not self.limiter.allow(f"{ip}:{self.command}:{self.path}"):
            return self._attack(ip, 429, "rate_limited")
        self._json(200, {"ok": True, "guard": "pass"}, body=body)

    def _upload(self) -> None:
        ip = self.client_address[0]
        reason = self._blocked_reason(ip)
        if reason:
            return self._deny(403, reason)
        if self.server.server_port != 8080:
            return self._attack(ip, 403, "uploads_only_allowed_on_8080")
        if not self.limiter.allow(f"upload:{ip}", cost=5):
            return self._attack(ip, 429, "upload_rate_limited")
        length = int(self.headers.get("content-length", "0") or 0)
        if length <= 0 or length > self.max_upload_bytes:
            return self._attack(ip, 413, "upload_size_rejected")
        filename = safe_filename(self.headers.get("x-filename") or f"upload-{int(time.time())}.bin")
        digest = hashlib.sha256(f"{ip}:{time.time()}:{filename}".encode()).hexdigest()[:16]
        target = self.upload_dir / f"{digest}-{filename}"
        remaining = length
        with target.open("wb") as fh:
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                fh.write(chunk)
        if remaining:
            target.unlink(missing_ok=True)
            return self._attack(ip, 400, "upload_incomplete")
        self._json(201, {"ok": True, "stored": str(target), "bytes": length})

    def _blocked_reason(self, ip: str) -> Optional[str]:
        return "subnet_blocked" if self.blocker.is_blocked(ip) else None

    def _attack(self, ip: str, status: int, reason: str) -> None:
        self.blocker.record(ip, reason)
        self._deny(status, reason)

    def _deny(self, status: int, reason: str) -> None:
        self._json(status, {"error": reason}, headers={"x-ddos-guard": reason})

    def _json(self, status: int, payload: dict[str, object], body: bool = True, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(data)

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
        print(f"[udp] listening {self.host}:{self.port} game_protocol={self.filter.protocol}", flush=True)
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
        iface = input("Could not auto-detect public IPv4 interface. Enter interface name: ").strip()
        return iface, interface_ipv4(iface) or "0.0.0.0"
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
        fields = line.split()
        iface = fields[1] if len(fields) > 1 else "unknown"
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
        print("[ports]", json.dumps(discover_ports()), flush=True)
        stop.wait(interval)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:128]
    return cleaned or "upload.bin"


def ensure_iptables_rule(rule: list[str]) -> None:
    if run(["iptables", "-C", *rule], check=False).returncode != 0:
        run(["iptables", "-I", *rule], check=False)


def run_guard(args: argparse.Namespace) -> None:
    base_dir = Path.cwd()
    paths = ensure_current_dir_files(base_dir, args)
    iface, detected_ip = auto_interface()
    bind_host = args.bind or "0.0.0.0"
    whitelist_ports = set(args.whitelist_port) | DEFAULT_WHITELIST_PORTS
    blocker = TemporarySubnetBlocker(args.subnet_threshold, args.subnet_window, args.subnet_block_seconds, firewall=args.firewall, whitelist_ports=whitelist_ports)
    blocker.ensure_whitelist()
    print(f"[init] cwd={base_dir} state={paths['state']} interface={iface} ipv4={detected_ip} whitelist_ports={sorted(whitelist_ports)}", flush=True)
    stop = threading.Event()
    threading.Thread(target=monitor_ports, args=(stop, args.port_scan_interval), daemon=True).start()
    udp = UDPGuard(bind_host, args.udp_port, args.game_protocol, RateLimiter(args.udp_capacity, args.udp_refill), blocker)
    udp.start()
    GuardHTTPRequestHandler.limiter = RateLimiter(args.http_capacity, args.http_refill)
    GuardHTTPRequestHandler.blocker = blocker
    GuardHTTPRequestHandler.upload_dir = paths["uploads"]
    GuardHTTPRequestHandler.max_upload_bytes = args.max_upload_bytes
    httpd = ThreadedHTTPServer((bind_host, args.http_port), GuardHTTPRequestHandler)

    def shutdown(_signum: int, _frame: object) -> None:
        stop.set(); udp.stop(); httpd.shutdown()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"[http] listening {bind_host}:{args.http_port}; uploads accepted only on port 8080 at /upload", flush=True)
    httpd.serve_forever()


def install(args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise SystemExit("install must run as root")
    base_dir = Path.cwd()
    paths = ensure_current_dir_files(base_dir, args)
    shutil.copy2(Path(__file__), paths["installed_script"])
    paths["installed_script"].chmod(0o755)
    firewall = "--firewall" if args.firewall else ""
    whitelist = " ".join(f"--whitelist-port {p}" for p in sorted(set(args.whitelist_port) | DEFAULT_WHITELIST_PORTS))
    service = f"""[Unit]
Description=Single-file Node3 DDoS Guard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={base_dir}
ExecStart={sys.executable} {paths['installed_script']} run --http-port {args.http_port} --udp-port {args.udp_port} --game-protocol {args.game_protocol} --max-upload-bytes {args.max_upload_bytes} {whitelist} {firewall}
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
    print(f"installed {SERVICE_NAME}.service; files created under {paths['state']}")


def uninstall(_args: argparse.Namespace) -> None:
    if os.geteuid() != 0:
        raise SystemExit("uninstall must run as root")
    run(["systemctl", "stop", f"{SERVICE_NAME}.service"], check=False)
    run(["systemctl", "disable", f"{SERVICE_NAME}.service"], check=False)
    Path(f"/etc/systemd/system/{SERVICE_NAME}.service").unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"], check=False)
    print(f"uninstalled service; current-directory state is intentionally preserved")


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)
    except FileNotFoundError:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 127, "" if capture else None, "command not found" if capture else None)


class SelfTests(unittest.TestCase):
    def test_protocols(self) -> None:
        self.assertTrue(GameProtocolFilter("source").validate(b"\xff\xff\xff\xffTSource Engine Query\x00").allowed)
        self.assertTrue(GameProtocolFilter("raknet").validate(bytes([0x05]) + b"\x00" * 15 + RAKNET_MAGIC).allowed)
        self.assertTrue(GameProtocolFilter("samp").validate(b"SAMP" + b"\x00" * 7).allowed)
        self.assertTrue(GameProtocolFilter("fivem").validate(b"getinfo xxx").allowed)
        self.assertTrue(GameProtocolFilter("teamspeak3").validate(b"TS3INIT1").allowed)

    def test_whitelist_defaults_and_subnet_block(self) -> None:
        blocker = TemporarySubnetBlocker(threshold=2, window_sec=10, block_sec=60, whitelist_ports={1234})
        self.assertIn(22, blocker.whitelist_ports)
        self.assertIn(8080, blocker.whitelist_ports)
        self.assertFalse(blocker.record("203.0.113.10", "bad"))
        self.assertTrue(blocker.record("203.0.113.20", "bad"))
        self.assertTrue(blocker.is_blocked("203.0.113.30"))

    def test_current_dir_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(http_port=8080, udp_port=27015, game_protocol="auto", whitelist_port=[], max_upload_bytes=1024)
            paths = ensure_current_dir_files(Path(tmp), ns)
            self.assertTrue(paths["uploads"].is_dir())
            self.assertTrue(paths["config"].is_file())


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Single-file Layer 4/7 DDoS guard with upload whitelist on 8080")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "install"):
        sp = sub.add_parser(name)
        sp.add_argument("--bind", default=os.environ.get("BIND_HOST"))
        sp.add_argument("--http-port", type=int, default=int(os.environ.get("HTTP_PORT", "8080")))
        sp.add_argument("--udp-port", type=int, default=int(os.environ.get("UDP_PORT", "27015")))
        sp.add_argument("--game-protocol", default=os.environ.get("GAME_PROTOCOL", "auto"), choices=["auto", "source", "valve", "minecraft", "raknet", "bedrock", "samp", "fivem", "teamspeak3", "genericudp"])
        sp.add_argument("--firewall", action="store_true", default=os.environ.get("FIREWALL_ENABLED", "false").lower() == "true")
        sp.add_argument("--whitelist-port", type=int, action="append", default=[], help="TCP port to ACCEPT before dynamic subnet drops; 22 and 8080 are always included")
        sp.add_argument("--http-capacity", type=int, default=120)
        sp.add_argument("--http-refill", type=float, default=60.0)
        sp.add_argument("--udp-capacity", type=int, default=80)
        sp.add_argument("--udp-refill", type=float, default=40.0)
        sp.add_argument("--max-upload-bytes", type=int, default=100 * 1024 * 1024)
        sp.add_argument("--subnet-threshold", type=int, default=25)
        sp.add_argument("--subnet-window", type=int, default=10)
        sp.add_argument("--subnet-block-seconds", type=int, default=600)
        sp.add_argument("--port-scan-interval", type=int, default=30)
    sub.add_parser("uninstall")
    sub.add_parser("ports")
    sub.add_parser("selftest")
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
    elif args.cmd == "selftest":
        unittest.main(argv=[sys.argv[0]], exit=False)


if __name__ == "__main__":
    main()
