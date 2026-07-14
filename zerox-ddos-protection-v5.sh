#!/usr/bin/env bash
# Zerox DDoS Protection V5 — Host and Minecraft Protocol Defense
# Production-safe host/XDP/nftables installer for Pterodactyl/Wings Minecraft VPSes.
set -Eeuo pipefail

APP="/etc/zerox-ddos-v5"
BIN="/usr/local/sbin"
STATE="$APP/state"
BUILD="$APP/build"
CONF="$APP/config.env"
NFT_RULES="$APP/zerox-ddos-v5.nft"
NFT_TABLE="zerox_ddos_v5"
NFT_LOADER="$BIN/zerox-ddos-v5-load"
CLI="$BIN/zerox-ddos-v5"
XDP_SRC="$APP/raknet_xdp_v5.c"
XDP_OBJ="$APP/raknet_xdp_v5.o"
XDP_CTRL="$BIN/zerox-ddos-v5-xdp"
JAVA_VALIDATOR="$BIN/zerox-java-validator-v5.py"
SERVICE="/etc/systemd/system/zerox-ddos-v5.service"
JAVA_SERVICE="/etc/systemd/system/zerox-java-validator-v5.service"
SYSCTL_FILE="/etc/sysctl.d/99-zerox-ddos-v5.conf"
SYSCTL_SAVED="$APP/sysctl.previous"
TRUST4="$APP/trusted4.list"
TRUST6="$APP/trusted6.list"
JAVA_PORTS="$APP/java_ports.list"
RAKNET_PORTS="$APP/raknet_ports.list"
PROFILE_FILE="$APP/profile"
BAN_SECONDS_FILE="$APP/ban_seconds"
XDP_MODE_FILE="$STATE/xdp.mode"
XDP_PROG_FILE="$STATE/xdp.prog_id"
XDP_OWNER_FILE="$STATE/xdp.owner"
SELF_PATH="${BASH_SOURCE[0]}"

log(){ printf '[%s] %s\n' "$(date -Is)" "$*"; }
ok(){ printf '[OK] %s\n' "$*"; }
warn(){ printf '[WARN] %s\n' "$*" >&2; }
die(){ printf '[ERROR] %s\n' "$*" >&2; exit 1; }
need_root(){ [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root."; }
have(){ command -v "$1" >/dev/null 2>&1; }
read_default(){ local p="$1" d="$2" v; read -r -p "$p [$d]: " v; printf '%s' "${v:-$d}"; }
yesno(){ local p="$1" d="${2:-n}" a; read -r -p "$p [$d]: " a; a="${a:-$d}"; [[ "$a" =~ ^[Yy] ]]; }
valid_port(){ [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1>=1 && 10#$1<=65535)); }
valid_int(){ [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1>=0)); }
ensure_dirs(){ mkdir -p "$APP" "$STATE" "$BUILD"; touch "$TRUST4" "$TRUST6" "$JAVA_PORTS" "$RAKNET_PORTS"; chmod 700 "$APP"; }
unique_ports(){ local f="$1"; sort -n -u "$f" -o "$f"; }
unique_cidrs(){ local f="$1"; sort -u "$f" -o "$f"; }
join_ports(){ local f="$1"; if [[ -s "$f" ]]; then paste -sd, "$f" | sed 's/,/, /g'; else true; fi; }
set_literal(){ local f="$1"; if [[ -s "$f" ]]; then paste -sd, "$f" | sed 's/,/, /g'; fi; }
load_conf(){ [[ -r "$CONF" ]] && # shellcheck disable=SC1090
  source "$CONF" || true; PROFILE="${PROFILE:-$(cat "$PROFILE_FILE" 2>/dev/null || echo conservative)}"; BAN_SECONDS="${BAN_SECONDS:-$(cat "$BAN_SECONDS_FILE" 2>/dev/null || echo 120)}"; }

write_default_conf(){
  local iface; iface="$(ip route get 1.1.1.1 2>/dev/null | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"; iface="${iface:-eth0}"
  cat >"$CONF" <<EOF
IFACE="$iface"
IFACE_FILTER=1
PROFILE="conservative"
BAN_SECONDS=120
JAVA_VALIDATOR_ENABLED=0 # transport-only by default; no live Java stream enforcement
JAVA_CONN_LIMIT=40
JAVA_SYN_RATE=50
JAVA_SYN_BURST=150
RAKNET_SOFT_PPS=1000
RAKNET_SOFT_BURST=3000
HARD_SRC_PPS=5000
TCP_NEW_RATE=120
TCP_NEW_BURST=240
UDP_BASE_PPS=6000
UDP_BASE_BURST=12000
ICMP_RATE=100
ICMP_BURST=300
EST_TCP_PPS=20000
XDP_BYTE_RATE=8000000
XDP_MAP_SIZE=131072
RAKNET_MIN_MTU=576
RAKNET_MAX_MTU=1492
RAKNET_MAX_LEN=1492
RAKNET_PROTOCOLS="11,10"
JAVA_MAX_INITIAL=4096
EOF
  echo conservative >"$PROFILE_FILE"; echo 120 >"$BAN_SECONDS_FILE"
}

apply_profile_defaults(){
  load_conf
  case "$PROFILE" in
    conservative) JAVA_CONN_LIMIT=40; JAVA_SYN_RATE=50; JAVA_SYN_BURST=150; RAKNET_SOFT_PPS=1000; RAKNET_SOFT_BURST=3000; HARD_SRC_PPS=5000; UDP_BASE_PPS=6000;;
    balanced) JAVA_CONN_LIMIT=60; JAVA_SYN_RATE=80; JAVA_SYN_BURST=240; RAKNET_SOFT_PPS=1600; RAKNET_SOFT_BURST=4800; HARD_SRC_PPS=8000; UDP_BASE_PPS=9000;;
    aggressive) JAVA_CONN_LIMIT=40; JAVA_SYN_RATE=40; JAVA_SYN_BURST=120; RAKNET_SOFT_PPS=800; RAKNET_SOFT_BURST=2400; HARD_SRC_PPS=4000; UDP_BASE_PPS=5000; warn "Aggressive profile can affect legitimate users behind shared IPs.";;
  esac
  perl -0777 -i -pe "s/^PROFILE=.*/PROFILE=\"$PROFILE\"/m; s/^JAVA_CONN_LIMIT=.*/JAVA_CONN_LIMIT=$JAVA_CONN_LIMIT/m; s/^JAVA_SYN_RATE=.*/JAVA_SYN_RATE=$JAVA_SYN_RATE/m; s/^JAVA_SYN_BURST=.*/JAVA_SYN_BURST=$JAVA_SYN_BURST/m; s/^RAKNET_SOFT_PPS=.*/RAKNET_SOFT_PPS=$RAKNET_SOFT_PPS/m; s/^RAKNET_SOFT_BURST=.*/RAKNET_SOFT_BURST=$RAKNET_SOFT_BURST/m; s/^HARD_SRC_PPS=.*/HARD_SRC_PPS=$HARD_SRC_PPS/m; s/^UDP_BASE_PPS=.*/UDP_BASE_PPS=$UDP_BASE_PPS/m; s/^BAN_SECONDS=.*/BAN_SECONDS=$BAN_SECONDS/m" "$CONF"
}

install_deps(){
  if have apt-get; then DEBIAN_FRONTEND=noninteractive apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y nftables iproute2 clang llvm gcc make libbpf-dev bpftool python3 conntrack; else warn "Install dependencies manually: nftables iproute2 clang llvm gcc libbpf-dev bpftool python3 conntrack"; fi
}

validate_cidr(){ python3 - "$1" <<'PY'
import ipaddress,sys
try: ipaddress.ip_network(sys.argv[1], strict=False); sys.exit(0)
except Exception as e: print(e); sys.exit(1)
PY
}

write_xdp_source(){
cat >"$XDP_SRC" <<'EOF_C'
// SPDX-License-Identifier: GPL-2.0
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/ipv6.h>
#include <linux/udp.h>
#include <linux/in.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>
#ifndef IP_MF
#define IP_MF 0x2000
#endif
#ifndef IP_OFFSET
#define IP_OFFSET 0x1fff
#endif
#define MAX_PORTS 64
#define MAX_PROTOS 16
#ifndef MAP_SIZE
#define MAP_SIZE 131072
#endif
#ifndef PPS_LIMIT
#define PPS_LIMIT 5000
#endif
#ifndef BYTE_LIMIT
#define BYTE_LIMIT 8000000ULL
#endif
#ifndef MIN_MTU
#define MIN_MTU 576
#endif
#ifndef MAX_MTU
#define MAX_MTU 1492
#endif
#ifndef MAX_PKT
#define MAX_PKT 1492
#endif
#define WINDOW_NS 1000000000ULL
#define PENDING_NS 5000000000ULL
#define VERIFIED_IDLE_NS 30000000000ULL
#define VERIFIED_ABS_NS 300000000000ULL
struct vlan_min { __be16 tci; __be16 encap; };
struct rate_val { struct bpf_spin_lock lock; __u64 start; __u64 packets; __u64 bytes; };
struct ep4 { __u32 src; __u16 sport; __u16 dport; };
struct ep6 { struct in6_addr src; __u16 sport; __u16 dport; };
struct st { __u64 created; __u64 last; __u8 stage; };
struct cidr4 { __u32 addr; __u32 mask; };
struct cidr6 { struct in6_addr addr; __u8 prefix; __u8 pad[3]; };
struct counters { __u64 pass, drop, invalid_magic, req1_bad, req2_bad, conn_bad, rate_drop, pending4, verified4, pending6, verified6; };
struct { __uint(type,BPF_MAP_TYPE_ARRAY); __uint(max_entries,1); __type(key,__u32); __type(value,struct counters); } stats SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_ARRAY); __uint(max_entries,MAX_PORTS); __type(key,__u32); __type(value,__u16); } ports SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_ARRAY); __uint(max_entries,MAX_PROTOS); __type(key,__u32); __type(value,__u8); } protos SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_ARRAY); __uint(max_entries,128); __type(key,__u32); __type(value,struct cidr4); } trust4 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_ARRAY); __uint(max_entries,128); __type(key,__u32); __type(value,struct cidr6); } trust6 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_LRU_HASH); __uint(max_entries,MAP_SIZE); __type(key,__u32); __type(value,struct rate_val); } rates4 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_LRU_HASH); __uint(max_entries,MAP_SIZE); __type(key,struct in6_addr); __type(value,struct rate_val); } rates6 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_LRU_HASH); __uint(max_entries,MAP_SIZE); __type(key,struct ep4); __type(value,struct st); } pending4 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_LRU_HASH); __uint(max_entries,MAP_SIZE); __type(key,struct ep6); __type(value,struct st); } pending6 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_LRU_HASH); __uint(max_entries,MAP_SIZE); __type(key,struct ep4); __type(value,struct st); } verified4 SEC(".maps");
struct { __uint(type,BPF_MAP_TYPE_LRU_HASH); __uint(max_entries,MAP_SIZE); __type(key,struct ep6); __type(value,struct st); } verified6 SEC(".maps");
static const __u8 magic[16]={0,0xff,0xff,0,0xfe,0xfe,0xfe,0xfe,0xfd,0xfd,0xfd,0xfd,0x12,0x34,0x56,0x78};
static __always_inline void cnt(__u64 off){ __u32 k=0; struct counters*c=bpf_map_lookup_elem(&stats,&k); if(c) __sync_fetch_and_add(((__u64*)c)+off,1); }
static __always_inline int port_ok(__u16 d){ for(__u32 i=0;i<MAX_PORTS;i++){__u16*p=bpf_map_lookup_elem(&ports,&i); if(!p||!*p) return 0; if(*p==d) return 1;} return 0; }
static __always_inline int proto_ok(__u8 v){ for(__u32 i=0;i<MAX_PROTOS;i++){__u8*p=bpf_map_lookup_elem(&protos,&i); if(!p||!*p) return 0; if(*p==v) return 1;} return 0; }
static __always_inline int trust4_ok(__u32 s){ for(__u32 i=0;i<128;i++){struct cidr4*c=bpf_map_lookup_elem(&trust4,&i); if(!c||!c->mask) return 0; if((s&c->mask)==(c->addr&c->mask)) return 1;} return 0; }
static __always_inline int trust6_ok(const struct in6_addr*s){ for(__u32 i=0;i<128;i++){struct cidr6*c=bpf_map_lookup_elem(&trust6,&i); if(!c||!c->prefix) return 0; __u8 full=c->prefix/8, rem=c->prefix%8; int ok=1; for(int j=0;j<16;j++){ if(j<full && s->s6_addr[j]!=c->addr.s6_addr[j]) ok=0; if(j==full && rem){__u8 m=0xff<<(8-rem); if((s->s6_addr[j]&m)!=(c->addr.s6_addr[j]&m)) ok=0;}} if(ok) return 1;} return 0; }
static __always_inline int magic_ok(const __u8*p,const void*e){ for(int i=0;i<16;i++){ if((void*)(p+i+1)>e||p[i]!=magic[i]) return 0;} return 1; }
static __always_inline int rate4(__u32 s,__u32 bytes,__u64 now){ if(trust4_ok(s)) return 1; struct rate_val*r=bpf_map_lookup_elem(&rates4,&s); if(!r){struct rate_val n={.start=now,.packets=1,.bytes=bytes}; bpf_map_update_elem(&rates4,&s,&n,BPF_ANY); return 1;} int ok=1; bpf_spin_lock(&r->lock); if(now-r->start>=WINDOW_NS){r->start=now;r->packets=1;r->bytes=bytes;} else {r->packets++;r->bytes+=bytes; if(r->packets>PPS_LIMIT||r->bytes>BYTE_LIMIT) ok=0;} bpf_spin_unlock(&r->lock); return ok; }
static __always_inline int rate6(const struct in6_addr*s,__u32 bytes,__u64 now){ if(trust6_ok(s)) return 1; struct rate_val*r=bpf_map_lookup_elem(&rates6,s); if(!r){struct rate_val n={.start=now,.packets=1,.bytes=bytes}; bpf_map_update_elem(&rates6,s,&n,BPF_ANY); return 1;} int ok=1; bpf_spin_lock(&r->lock); if(now-r->start>=WINDOW_NS){r->start=now;r->packets=1;r->bytes=bytes;} else {r->packets++;r->bytes+=bytes; if(r->packets>PPS_LIMIT||r->bytes>BYTE_LIMIT) ok=0;} bpf_spin_unlock(&r->lock); return ok; }
static __always_inline int ping_ok(const __u8*p,__u32 l,const void*e){ return l>=33 && l<=MAX_PKT && magic_ok(p+9,e); }
static __always_inline int req1_ok(const __u8*p,__u32 l,const void*e){ return l>=MIN_MTU && l<=MAX_MTU && magic_ok(p+1,e) && (void*)(p+18)<=e && proto_ok(p[17]); }
static __always_inline int req2_at(const __u8*p,__u32 l,const void*e,__u32 off){ if((void*)(p+off+1)>e) return 0; __u8 fam=p[off++]; __u32 alen=(fam==4)?6:((fam==6)?28:0); if(!alen||off+alen+10!=l||(void*)(p+off+alen+10)>e) return 0; off+=alen; __u16 mtu=((__u16)p[off]<<8)|p[off+1]; return mtu>=MIN_MTU&&mtu<=MAX_MTU; }
static __always_inline int parse_req2(const __u8*p,__u32 l,const void*e){ if(l<34||l>80||!magic_ok(p+1,e)||(void*)(p+18)>e) return 0; if(req2_at(p,l,e,17)) return 1; if(l>=39 && (void*)(p+22)<=e && (p[21]==0||p[21]==1) && req2_at(p,l,e,22)) return 1; return 0; }
static __always_inline int connected_ok(const __u8*p,__u32 l,const void*e){ if(l<1||l>MAX_PKT) return 0; __u8 id=p[0]; if(id>=0x80 && id<=0x8d) return l>=4; if(id==0xc0||id==0xa0){ if(l<4||(void*)(p+4)>e) return 0; __u16 ranges=((__u16)p[2]<<8)|p[3]; return ranges>0 && ranges<=256 && l<=4+ranges*10; } return 0; }
static __always_inline int h4(__u32 s,__u16 sp,__u16 dp,const __u8*p,__u32 l,const void*e,__u64 now){ struct ep4 ep={s,sp,dp}; struct st*v=bpf_map_lookup_elem(&verified4,&ep); if(v){ if(now-v->last<VERIFIED_IDLE_NS && now-v->created<VERIFIED_ABS_NS){ if(connected_ok(p,l,e)){v->last=now; cnt(0); return XDP_PASS;} cnt(5); cnt(1); return XDP_DROP;} bpf_map_delete_elem(&verified4,&ep); cnt(4); cnt(1); return XDP_DROP;} if(l<1){cnt(4);return XDP_DROP;} __u8 id=p[0]; if(id==1||id==2){ if(ping_ok(p,l,e)){cnt(0);return XDP_PASS;} cnt(2);return XDP_DROP;} if(id==5){ if(!req1_ok(p,l,e)){cnt(3);return XDP_DROP;} struct st st={now,now,1}; bpf_map_update_elem(&pending4,&ep,&st,BPF_ANY); cnt(7); cnt(0); return XDP_PASS;} if(id==7){ if(!parse_req2(p,l,e)){cnt(4);return XDP_DROP;} struct st*q=bpf_map_lookup_elem(&pending4,&ep); if(!q||q->stage!=1||now-q->last>PENDING_NS){cnt(4);return XDP_DROP;} struct st st={now,now,2}; bpf_map_update_elem(&verified4,&ep,&st,BPF_ANY); bpf_map_delete_elem(&pending4,&ep); cnt(8); cnt(0); return XDP_PASS;} cnt(4); return XDP_DROP; }
static __always_inline int h6(const struct in6_addr*s,__u16 sp,__u16 dp,const __u8*p,__u32 l,const void*e,__u64 now){ struct ep6 ep={.sport=sp,.dport=dp}; __builtin_memcpy(&ep.src,s,sizeof(*s)); struct st*v=bpf_map_lookup_elem(&verified6,&ep); if(v){ if(now-v->last<VERIFIED_IDLE_NS&&now-v->created<VERIFIED_ABS_NS){ if(connected_ok(p,l,e)){v->last=now;cnt(0);return XDP_PASS;} cnt(5);cnt(1);return XDP_DROP;} bpf_map_delete_elem(&verified6,&ep);cnt(4);cnt(1);return XDP_DROP;} if(l<1){cnt(4);return XDP_DROP;} __u8 id=p[0]; if(id==1||id==2){ if(ping_ok(p,l,e)){cnt(0);return XDP_PASS;} cnt(2);return XDP_DROP;} if(id==5){ if(!req1_ok(p,l,e)){cnt(3);return XDP_DROP;} struct st st={now,now,1}; bpf_map_update_elem(&pending6,&ep,&st,BPF_ANY);cnt(9);cnt(0);return XDP_PASS;} if(id==7){ if(!parse_req2(p,l,e)){cnt(4);return XDP_DROP;} struct st*q=bpf_map_lookup_elem(&pending6,&ep); if(!q||q->stage!=1||now-q->last>PENDING_NS){cnt(4);return XDP_DROP;} struct st st={now,now,2}; bpf_map_update_elem(&verified6,&ep,&st,BPF_ANY); bpf_map_delete_elem(&pending6,&ep);cnt(10);cnt(0);return XDP_PASS;} cnt(4);return XDP_DROP; }
SEC("xdp") int raknet_xdp_v5(struct xdp_md*ctx){ void*data=(void*)(long)ctx->data,*end=(void*)(long)ctx->data_end; struct ethhdr*eth=data; if((void*)(eth+1)>end) return XDP_PASS; __u16 proto=bpf_ntohs(eth->h_proto); __u64 off=sizeof(*eth); for(int i=0;i<2;i++){ if(proto==ETH_P_8021Q||proto==ETH_P_8021AD){ struct vlan_min*vh=data+off; if((void*)(vh+1)>end) return XDP_PASS; proto=bpf_ntohs(vh->encap); off+=sizeof(*vh); }} __u64 now=bpf_ktime_get_ns(); if(proto==ETH_P_IP){ struct iphdr*ip=data+off; if((void*)(ip+1)>end||ip->version!=4||ip->ihl<5) return XDP_PASS; __u32 ihl=ip->ihl*4; if((void*)ip+ihl>end||ip->protocol!=IPPROTO_UDP) return XDP_PASS; struct udphdr*u=(void*)ip+ihl; if((void*)(u+1)>end) return XDP_PASS; __u16 dp=bpf_ntohs(u->dest); if(!port_ok(dp)) return XDP_PASS; if(bpf_ntohs(ip->frag_off)&(IP_MF|IP_OFFSET)){cnt(1);return XDP_DROP;} __u16 ul=bpf_ntohs(u->len), tl=bpf_ntohs(ip->tot_len); if(ul<8||tl<ihl+ul||(void*)u+ul>end){cnt(1);return XDP_DROP;} if(!rate4(ip->saddr,ul,now)){cnt(6);cnt(1);return XDP_DROP;} return h4(ip->saddr,bpf_ntohs(u->source),dp,(void*)(u+1),ul-8,end,now); } if(proto==ETH_P_IPV6){ struct ipv6hdr*ip6=data+off; if((void*)(ip6+1)>end) return XDP_PASS; __u8 nh=ip6->nexthdr; __u64 o=off+sizeof(*ip6); for(int i=0;i<6;i++){ if(nh==IPPROTO_HOPOPTS||nh==IPPROTO_ROUTING||nh==IPPROTO_DSTOPTS){ if(data+o+2>end) return XDP_PASS; __u8*n=data+o; nh=n[0]; o+=(n[1]+1)*8; if(data+o>end) return XDP_PASS; } else if(nh==IPPROTO_FRAGMENT){ if(data+o+8>end) return XDP_PASS; struct frag_hdr{__u8 n;__u8 r;__be16 off;__be32 id;}*f=data+o; nh=f->n; if(f->off & bpf_htons(0xfff9)){cnt(1);return XDP_DROP;} o+=8; } else break; } if(nh!=IPPROTO_UDP) return XDP_PASS; struct udphdr*u=data+o; if((void*)(u+1)>end) return XDP_PASS; __u16 dp=bpf_ntohs(u->dest); if(!port_ok(dp)) return XDP_PASS; __u16 ul=bpf_ntohs(u->len); if(ul<8||bpf_ntohs(ip6->payload_len)<ul||(void*)u+ul>end){cnt(1);return XDP_DROP;} if(!rate6(&ip6->saddr,ul,now)){cnt(6);cnt(1);return XDP_DROP;} return h6(&ip6->saddr,bpf_ntohs(u->source),dp,(void*)(u+1),ul-8,end,now); } return XDP_PASS; }
char LICENSE[] SEC("license")="GPL";
EOF_C
}

write_java_validator(){
cat >"$JAVA_VALIDATOR" <<'PY'
#!/usr/bin/env python3
"""Optional stream-aware Minecraft Java initial-flow validator.
It is intentionally advisory by default: it observes accepted sockets via journald startup test only unless integrated with TPROXY by an administrator. The nftables transport layer remains active without changing public ports.
"""
import argparse, socket, selectors, time, sys
MAX_INITIAL=4096

def read_varint(buf, off=0):
    num=0
    for i in range(5):
        if off+i>=len(buf): return None, off, False
        b=buf[off+i]; num |= (b & 0x7f) << (7*i)
        if not (b & 0x80): return num, off+i+1, True
    raise ValueError('oversized VarInt')

def validate_initial(data):
    ln,o,ok=read_varint(data,0)
    if not ok: return 'incomplete'
    if ln<1 or ln>MAX_INITIAL: raise ValueError('bad length')
    if len(data)-o < ln: return 'incomplete'
    end=o+ln
    pid,o,ok=read_varint(data,o)
    if not ok or pid!=0: raise ValueError('bad handshake packet id')
    proto,o,ok=read_varint(data,o)
    if not ok or proto<0: raise ValueError('bad protocol')
    slen,o,ok=read_varint(data,o)
    if not ok or slen<0 or slen>255 or o+slen+3>end: raise ValueError('bad server address')
    addr=data[o:o+slen]
    try: addr.decode('utf-8')
    except UnicodeDecodeError as e: raise ValueError('bad address utf8') from e
    o += slen
    port=(data[o]<<8)|data[o+1]; o += 2
    state,o,ok=read_varint(data,o)
    if not ok or state not in (1,2): raise ValueError('bad next state')
    if o!=end: raise ValueError('trailing handshake bytes')
    if state==1: return 'status'
    return 'login'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--startup-test', action='store_true')
    ap.add_argument('--max-initial', type=int, default=4096)
    ns=ap.parse_args(); global MAX_INITIAL; MAX_INITIAL=ns.max_initial
    if ns.startup_test:
        pkt=bytes([0x10,0x00,0xf7,0x05,0x09])+b'localhost'+bytes([0x63,0xdd,0x02])
        print(validate_initial(pkt)); return 0
    print('Java validator parser is installed. Enable TPROXY integration manually only after testing.', file=sys.stderr)
    return 0
if __name__=='__main__': sys.exit(main())
PY
chmod 0755 "$JAVA_VALIDATOR"
}

write_nft_rules(){
  load_conf
  local jp rp t4 t6 iface_expr="" trust4_set trust6_set java_set raknet_set; jp="$(join_ports "$JAVA_PORTS")"; rp="$(join_ports "$RAKNET_PORTS")"; t4="$(set_literal "$TRUST4")"; t6="$(set_literal "$TRUST6")"
  trust4_set="set trusted4 { type ipv4_addr; flags interval; }"; [[ -n "$t4" ]] && trust4_set="set trusted4 { type ipv4_addr; flags interval; elements = { $t4 }; }"
  trust6_set="set trusted6 { type ipv6_addr; flags interval; }"; [[ -n "$t6" ]] && trust6_set="set trusted6 { type ipv6_addr; flags interval; elements = { $t6 }; }"
  java_set="set java_ports { type inet_service; }"; [[ -n "$jp" ]] && java_set="set java_ports { type inet_service; elements = { $jp }; }"
  raknet_set="set raknet_ports { type inet_service; }"; [[ -n "$rp" ]] && raknet_set="set raknet_ports { type inet_service; elements = { $rp }; }"
  [[ "${IFACE_FILTER:-1}" == 1 ]] && iface_expr="iifname \"$IFACE\""
  cat >"$NFT_RULES" <<EOF
#!/usr/sbin/nft -f
table inet $NFT_TABLE {
  ${trust4_set}
  ${trust6_set}
  ${java_set}
  ${raknet_set}
  set game_ban4 { type ipv4_addr; flags timeout; }
  set game_ban6 { type ipv6_addr; flags timeout; }
  set java_conn4 { type ipv4_addr; flags dynamic; }
  set java_conn6 { type ipv6_addr; flags dynamic; }
  chain baseline {
    ct state invalid counter drop
    meta l4proto tcp tcp flags & (fin|syn|rst|psh|ack|urg) == 0 counter drop
    meta l4proto tcp tcp flags & (fin|syn) == (fin|syn) counter drop
    meta l4proto tcp tcp flags & (syn|rst) == (syn|rst) counter drop
    ip saddr @trusted4 return
    ip6 saddr @trusted6 return
    meta l4proto tcp ct state new meter tcpnew4 { ip saddr timeout 10s limit rate over ${TCP_NEW_RATE}/second burst ${TCP_NEW_BURST} packets } counter drop
    meta l4proto tcp ct state new meter tcpnew6 { ip6 saddr timeout 10s limit rate over ${TCP_NEW_RATE}/second burst ${TCP_NEW_BURST} packets } counter drop
    meta l4proto udp meter udpbase4 { ip saddr timeout 10s limit rate over ${UDP_BASE_PPS}/second burst ${UDP_BASE_BURST} packets } counter drop
    meta l4proto udp meter udpbase6 { ip6 saddr timeout 10s limit rate over ${UDP_BASE_PPS}/second burst ${UDP_BASE_BURST} packets } counter drop
    meta l4proto icmp limit rate over ${ICMP_RATE}/second burst ${ICMP_BURST} packets counter drop
    meta l4proto ipv6-icmp limit rate over ${ICMP_RATE}/second burst ${ICMP_BURST} packets counter drop
    meta l4proto tcp ct state established meter esttcp4 { ip saddr timeout 10s limit rate over ${EST_TCP_PPS}/second burst $((EST_TCP_PPS/2)) packets } counter drop
    meta l4proto tcp ct state established meter esttcp6 { ip6 saddr timeout 10s limit rate over ${EST_TCP_PPS}/second burst $((EST_TCP_PPS/2)) packets } counter drop
    return
  }
  chain game {
    ip saddr @trusted4 return
    ip6 saddr @trusted6 return
    ip saddr @game_ban4 meta l4proto udp udp dport @raknet_ports counter drop
    ip6 saddr @game_ban6 meta l4proto udp udp dport @raknet_ports counter drop
    meta l4proto udp udp dport @raknet_ports ip frag-off & 0x3fff != 0 counter drop
    meta l4proto udp udp dport @raknet_ports ip6 nexthdr frag counter drop
    meta l4proto tcp tcp dport @java_ports ct state new tcp flags syn meter javasyn4 { ip saddr timeout 10s limit rate over ${JAVA_SYN_RATE}/second burst ${JAVA_SYN_BURST} packets } counter drop
    meta l4proto tcp tcp dport @java_ports ct state new tcp flags syn meter javasyn6 { ip6 saddr timeout 10s limit rate over ${JAVA_SYN_RATE}/second burst ${JAVA_SYN_BURST} packets } counter drop
    meta l4proto tcp tcp dport @java_ports ct state new add @java_conn4 { ip saddr ct count over ${JAVA_CONN_LIMIT} } counter drop
    meta l4proto tcp tcp dport @java_ports ct state new add @java_conn6 { ip6 saddr ct count over ${JAVA_CONN_LIMIT} } counter drop
    meta l4proto udp udp dport @raknet_ports meter hardpps4 { ip saddr timeout ${BAN_SECONDS}s limit rate over ${HARD_SRC_PPS}/second burst $((HARD_SRC_PPS*2)) packets } add @game_ban4 { ip saddr timeout ${BAN_SECONDS}s } counter drop
    meta l4proto udp udp dport @raknet_ports meter hardpps6 { ip6 saddr timeout ${BAN_SECONDS}s limit rate over ${HARD_SRC_PPS}/second burst $((HARD_SRC_PPS*2)) packets } add @game_ban6 { ip6 saddr timeout ${BAN_SECONDS}s } counter drop
    meta l4proto udp udp dport @raknet_ports meter raksoft4 { ip saddr timeout 10s limit rate over ${RAKNET_SOFT_PPS}/second burst ${RAKNET_SOFT_BURST} packets } counter drop
    meta l4proto udp udp dport @raknet_ports meter raksoft6 { ip6 saddr timeout 10s limit rate over ${RAKNET_SOFT_PPS}/second burst ${RAKNET_SOFT_BURST} packets } counter drop
    return
  }
  chain input { type filter hook input priority -10; policy accept; ${iface_expr} jump baseline; ${iface_expr} jump game; }
  chain forward { type filter hook forward priority -10; policy accept; ${iface_expr} jump baseline; ${iface_expr} jump game; }
}
EOF
}

write_loader(){
cat >"$NFT_LOADER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
TMP=\$(mktemp); trap 'rm -f "\$TMP"' EXIT
if nft list table inet $NFT_TABLE >/dev/null 2>&1; then echo 'delete table inet $NFT_TABLE' >"\$TMP"; fi
cat "$NFT_RULES" >>"\$TMP"
nft -c -f "\$TMP"
nft -f "\$TMP"
EOF
chmod 0755 "$NFT_LOADER"
}

write_xdp_control(){
cat >"$XDP_CTRL" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
APP="$APP"; CONF="$CONF"; OBJ="$XDP_OBJ"; MODE_FILE="$XDP_MODE_FILE"; PROG_FILE="$XDP_PROG_FILE"; OWNER_FILE="$XDP_OWNER_FILE"
source "\$CONF"
current_id(){ ip -details link show dev "\$IFACE" | sed -n 's/.*prog\/xdp id \([0-9][0-9]*\).*/\1/p' | head -1; }
case "\${1:-}" in
  attach)
    mkdir -p "$(dirname "$XDP_MODE_FILE")"
    cur=\$(current_id || true)
    if [[ -n "\$cur" ]]; then
      old=\$(cat "\$PROG_FILE" 2>/dev/null || true)
      if [[ "\$cur" != "\$old" ]]; then
        if [[ "\${ZEROX_REPLACE_XDP:-0}" != 1 ]]; then echo "Unrelated XDP program id \$cur is already attached; refusing." >&2; exit 20; fi
      fi
    fi
    if ip link set dev "\$IFACE" xdpdrv obj "\$OBJ" sec xdp 2>/tmp/zerox-xdp.err; then echo native >"\$MODE_FILE"; else cat /tmp/zerox-xdp.err >&2; ip link set dev "\$IFACE" xdpgeneric obj "\$OBJ" sec xdp; echo generic >"\$MODE_FILE"; fi
    current_id >"\$PROG_FILE"; echo zerox-ddos-v5 >"\$OWNER_FILE";;
  detach)
    cur=\$(current_id || true); old=\$(cat "\$PROG_FILE" 2>/dev/null || true)
    if [[ -n "\$cur" && -n "\$old" && "\$cur" == "\$old" ]]; then ip link set dev "\$IFACE" xdp off; else echo "No Zerox-owned XDP program to detach."; fi
    rm -f "\$MODE_FILE" "\$PROG_FILE" "\$OWNER_FILE";;
  status) echo "Interface: \$IFACE"; echo "Mode: \$(cat "\$MODE_FILE" 2>/dev/null || echo detached)"; echo "Program ID: \$(current_id || echo none)"; ip -details link show dev "\$IFACE";;
  *) echo "Usage: \$0 {attach|detach|status}" >&2; exit 2;;
esac
EOF
chmod 0755 "$XDP_CTRL"
}

compile_xdp(){
  load_conf; write_xdp_source
  local arch inc; arch="$(uname -m)"; case "$arch" in x86_64) arch=x86;; aarch64) arch=arm64;; arm*) arch=arm;; esac
  inc="/usr/include/$(gcc -dumpmachine)"
  clang -O2 -g -Wall -Werror -target bpf -D__TARGET_ARCH_${arch} -I"$inc" -DMAP_SIZE="$XDP_MAP_SIZE" -DPPS_LIMIT="$HARD_SRC_PPS" -DBYTE_LIMIT="${XDP_BYTE_RATE}ULL" -DMIN_MTU="$RAKNET_MIN_MTU" -DMAX_MTU="$RAKNET_MAX_MTU" -DMAX_PKT="$RAKNET_MAX_LEN" -c "$XDP_SRC" -o "$XDP_OBJ"
}

write_services(){
cat >"$SERVICE" <<EOF
[Unit]
Description=Zerox DDoS Protection V5 — Host and Minecraft Protocol Defense
After=network-online.target nftables.service
Wants=network-online.target
Before=docker.service wings.service

[Service]
Type=oneshot
ExecStart=$NFT_LOADER
ExecStartPost=/bin/bash -c 'source $CONF; if [[ -s $RAKNET_PORTS ]]; then $CLI attach-xdp; fi'
ExecReload=$NFT_LOADER
ExecReload=/bin/bash -c 'source $CONF; if [[ -s $RAKNET_PORTS ]]; then $CLI attach-xdp; fi'
ExecStop=$XDP_CTRL detach
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
cat >"$JAVA_SERVICE" <<EOF
[Unit]
Description=Zerox optional Minecraft Java stream validator parser self-test service
After=network-online.target

[Service]
Type=oneshot
ExecStart=$JAVA_VALIDATOR --startup-test
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

save_sysctl(){ [[ -r "$SYSCTL_SAVED" ]] && return 0; : >"$SYSCTL_SAVED"; for k in net.ipv4.tcp_syncookies net.ipv4.tcp_max_syn_backlog net.core.somaxconn net.ipv4.tcp_synack_retries; do sysctl -n "$k" 2>/dev/null | sed "s#^#$k=#" >>"$SYSCTL_SAVED" || true; done; }
apply_sysctl(){ save_sysctl; cat >"$SYSCTL_FILE" <<'EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 8192
net.core.somaxconn = 4096
net.ipv4.tcp_synack_retries = 4
EOF
sysctl --system >/dev/null || true; }
restore_sysctl(){ [[ -r "$SYSCTL_SAVED" ]] && while IFS='=' read -r k v; do [[ -n "$k" ]] && sysctl -w "$k=$v" >/dev/null 2>&1 || true; done <"$SYSCTL_SAVED"; rm -f "$SYSCTL_FILE"; sysctl --system >/dev/null 2>&1 || true; }

map_ids_by_name(){
  local prog="$1" name="$2" ids mid
  ids="$(bpftool prog show id "$prog" 2>/dev/null | sed -n 's/.*map_ids \([^\n]*\)$/\1/p' | tr ',' ' ')"
  for mid in $ids; do bpftool map show id "$mid" 2>/dev/null | grep -q "name $name" && { echo "$mid"; return 0; }; done
  return 1
}
hex_key(){ python3 - "$1" <<'PYHEX'
import struct,sys
print(' '.join(f'{b:02x}' for b in struct.pack('<I', int(sys.argv[1]))))
PYHEX
}
hex_u16(){ python3 - "$1" <<'PYHEX'
import struct,sys
print(' '.join(f'{b:02x}' for b in struct.pack('<H', int(sys.argv[1]))))
PYHEX
}
hex_u8(){ printf '%02x' "$1"; }
hex_cidr4(){ python3 - "$1" <<'PYHEX'
import ipaddress,sys
n=ipaddress.ip_network(sys.argv[1], strict=False)
mask=((0xffffffff << (32-n.prefixlen)) & 0xffffffff) if n.prefixlen else 0
print(' '.join(f'{b:02x}' for b in (int(n.network_address).to_bytes(4,'big') + mask.to_bytes(4,'big'))))
PYHEX
}
hex_cidr6(){ python3 - "$1" <<'PYHEX'
import ipaddress,sys
n=ipaddress.ip_network(sys.argv[1], strict=False)
print(' '.join(f'{b:02x}' for b in (n.network_address.packed + bytes([n.prefixlen,0,0,0]))))
PYHEX
}
populate_xdp_maps(){
  have bpftool || die "bpftool is required to populate XDP maps"
  load_conf
  local id; id="$(cat "$XDP_PROG_FILE" 2>/dev/null || true)"; [[ -n "$id" ]] || die "No recorded XDP program id"
  local ports_map protos_map trust4_map trust6_map i v
  ports_map="$(map_ids_by_name "$id" ports)" || die "XDP ports map not found"
  protos_map="$(map_ids_by_name "$id" protos)" || die "XDP protos map not found"
  trust4_map="$(map_ids_by_name "$id" trust4)" || true
  trust6_map="$(map_ids_by_name "$id" trust6)" || true
  i=0; while read -r v; do [[ -n "$v" ]] || continue; bpftool map update id "$ports_map" key hex $(hex_key "$i") value hex $(hex_u16 "$v"); i=$((i+1)); done <"$RAKNET_PORTS"
  i=0; IFS=',' read -ra _proto_arr <<<"${RAKNET_PROTOCOLS:-11,10}"; for v in "${_proto_arr[@]}"; do v="${v// /}"; [[ -n "$v" ]] || continue; bpftool map update id "$protos_map" key hex $(hex_key "$i") value hex $(hex_u8 "$v"); i=$((i+1)); done
  if [[ -n "${trust4_map:-}" ]]; then i=0; while read -r v; do [[ -n "$v" ]] || continue; bpftool map update id "$trust4_map" key hex $(hex_key "$i") value hex $(hex_cidr4 "$v"); i=$((i+1)); done <"$TRUST4"; fi
  if [[ -n "${trust6_map:-}" ]]; then i=0; while read -r v; do [[ -n "$v" ]] || continue; bpftool map update id "$trust6_map" key hex $(hex_key "$i") value hex $(hex_cidr6 "$v"); i=$((i+1)); done <"$TRUST6"; fi
  ok "Populated XDP ports, RakNet protocol versions, and trusted CIDR maps."
}

validate_nft_transaction(){
  local tmp; tmp="$(mktemp)"; trap 'rm -f "$tmp"' RETURN
  if nft list table inet $NFT_TABLE >/dev/null 2>&1; then echo "delete table inet $NFT_TABLE" >"$tmp"; fi
  cat "$NFT_RULES" >>"$tmp"
  nft -c -f "$tmp"
}

test_load_xdp(){
  have bpftool || return 1
  local pin="/sys/fs/bpf/zerox-ddos-v5-test-$$"
  rm -f "$pin"
  bpftool prog load "$XDP_OBJ" "$pin" type xdp
  rm -f "$pin"
}

run_self_tests(){
  load_conf; local failed=0
  bash -n "$SELF_PATH" || failed=1
  [[ -d "/sys/class/net/${IFACE:-}" ]] || { echo "Interface ${IFACE:-unset} missing"; failed=1; }
  have nft || { echo "nft missing"; failed=1; }
  have clang || { echo "clang missing"; failed=1; }
  have ip || { echo "ip missing"; failed=1; }
  write_nft_rules; validate_nft_transaction || failed=1
  if [[ -s "$RAKNET_PORTS" ]]; then compile_xdp || failed=1; if have llvm-objdump; then llvm-objdump -h "$XDP_OBJ" | grep -w xdp || failed=1; elif have readelf; then readelf -S "$XDP_OBJ" | grep -w xdp || failed=1; fi; bpftool feature probe kernel unprivileged 2>/dev/null | head -20 || true; test_load_xdp || failed=1; "$XDP_CTRL" status >/dev/null 2>&1 || true; fi
  if [[ "${JAVA_VALIDATOR_ENABLED:-0}" == 1 ]]; then "$JAVA_VALIDATOR" --startup-test --max-initial "$JAVA_MAX_INITIAL" || failed=1; fi
  nft describe ct state >/dev/null || failed=1
  nft describe limit >/dev/null || failed=1
  [[ $failed -eq 0 ]] || die "Self-tests failed; previous installation preserved."
  ok "Self-tests passed."
}

apply_reload(){
  ensure_dirs; load_conf; [[ "$SELF_PATH" != "$CLI" ]] && install -m 0755 "$SELF_PATH" "$CLI" || true; write_nft_rules; write_loader; write_xdp_control; write_java_validator; [[ -s "$RAKNET_PORTS" ]] && compile_xdp
  run_self_tests
  systemctl daemon-reload || true
  local old_nft="$STATE/previous.nft" old_xdp="$STATE/previous_xdp_id"
  nft list table inet "$NFT_TABLE" >"$old_nft" 2>/dev/null || : >"$old_nft"
  [[ -x "$XDP_CTRL" ]] && "$XDP_CTRL" status | sed -n 's/^Program ID: //p' >"$old_xdp" 2>/dev/null || true
  if ! "$NFT_LOADER"; then warn "nftables load failed; previous managed table preserved by atomic validation."; return 1; fi
  if [[ -s "$RAKNET_PORTS" ]]; then
    if ! "$XDP_CTRL" attach || ! populate_xdp_maps; then
      warn "XDP attach/map population failed; detaching new XDP program and restoring previous managed nftables table."
      "$XDP_CTRL" detach || true
      if [[ -s "$old_nft" ]]; then { echo "delete table inet $NFT_TABLE"; cat "$old_nft"; } | nft -f - || true; else nft delete table inet "$NFT_TABLE" 2>/dev/null || true; fi
      return 1
    fi
  fi
  ok "Protection applied."
}

install_all(){
  need_root; ensure_dirs; install_deps; [[ -r "$CONF" ]] || write_default_conf; load_conf
  local v; v="$(read_default 'Public interface' "$IFACE")"; [[ -d "/sys/class/net/$v" ]] || die "Interface $v not found"; perl -0777 -i -pe "s/^IFACE=.*/IFACE=\"$v\"/m" "$CONF"
  if yesno "Add default Java port 25565?" y; then echo 25565 >>"$JAVA_PORTS"; fi
  if yesno "Add default RakNet/Geyser Bedrock port 19132?" y; then echo 19132 >>"$RAKNET_PORTS"; fi
  unique_ports "$JAVA_PORTS"; unique_ports "$RAKNET_PORTS"
  v="$(read_default 'Temporary ban seconds (60-600)' "$(cat "$BAN_SECONDS_FILE")")"; [[ "$v" =~ ^[0-9]+$ ]] && ((v>=60&&v<=600)) || die "Invalid ban seconds"; echo "$v" >"$BAN_SECONDS_FILE"; perl -0777 -i -pe "s/^BAN_SECONDS=.*/BAN_SECONDS=$v/m" "$CONF"
  install -m 0755 "$SELF_PATH" "$CLI"; write_java_validator; write_services; apply_sysctl; apply_reload; systemctl enable --now zerox-ddos-v5.service >/dev/null 2>&1 || true
}

add_port(){ local f="$1" p; p="$(read_default 'Port' '')"; valid_port "$p" || die "Invalid port"; echo "$p" >>"$f"; unique_ports "$f"; ok "Added $p"; }
remove_port(){ local p; echo "Java: $(join_ports "$JAVA_PORTS")"; echo "RakNet: $(join_ports "$RAKNET_PORTS")"; p="$(read_default 'Port to remove' '')"; valid_port "$p" || die "Invalid port"; sed -i "/^$p$/d" "$JAVA_PORTS" "$RAKNET_PORTS"; ok "Removed $p"; }
manage_trust(){ ensure_dirs; while true; do echo "1 Add IPv4/CIDR  2 Add IPv6/CIDR  3 Remove  4 List  5 Clear  0 Back"; read -r -p '> ' c; case "$c" in 1|2) local n f; n="$(read_default 'CIDR' '')"; validate_cidr "$n" || continue; python3 - "$n" "$TRUST4" "$TRUST6" <<'PY'
import ipaddress,sys
net=ipaddress.ip_network(sys.argv[1], strict=False)
open(sys.argv[2] if net.version==4 else sys.argv[3],'a').write(str(net)+'\n')
PY
unique_cidrs "$TRUST4"; unique_cidrs "$TRUST6";; 3) local n; n="$(read_default 'CIDR to remove' '')"; sed -i "#^$n\$#d" "$TRUST4" "$TRUST6";; 4) echo IPv4; cat "$TRUST4"; echo IPv6; cat "$TRUST6";; 5) :>"$TRUST4"; :>"$TRUST6";; 0) break;; esac; done; }
select_profile(){ echo "1 Conservative (recommended)  2 Balanced  3 Aggressive"; read -r -p '> ' c; case "$c" in 1) PROFILE=conservative;;2) PROFILE=balanced;;3) PROFILE=aggressive;;*) die invalid;; esac; echo "$PROFILE" >"$PROFILE_FILE"; perl -0777 -i -pe "s/^PROFILE=.*/PROFILE=\"$PROFILE\"/m" "$CONF"; apply_profile_defaults; }
show_bans(){ nft list set inet "$NFT_TABLE" game_ban4 2>/dev/null || true; nft list set inet "$NFT_TABLE" game_ban6 2>/dev/null || true; }
clear_ban(){ local ip; ip="$(read_default 'IP to clear' '')"; nft delete element inet "$NFT_TABLE" game_ban4 "{ $ip }" 2>/dev/null || nft delete element inet "$NFT_TABLE" game_ban6 "{ $ip }" 2>/dev/null || true; }
clear_all_bans(){ nft flush set inet "$NFT_TABLE" game_ban4 2>/dev/null || true; nft flush set inet "$NFT_TABLE" game_ban6 2>/dev/null || true; }
status(){
  load_conf
  echo "=== Zerox DDoS Protection V5 status ==="
  systemctl is-active zerox-ddos-v5.service 2>/dev/null || true
  echo "Interface: ${IFACE:-unset}"
  echo "Profile: ${PROFILE:-unset}"
  echo "Java protection: transport-only unless an administrator adds external TPROXY/sk_skb integration"
  echo "Java ports: $(join_ports "$JAVA_PORTS")"
  echo "RakNet ports: $(join_ports "$RAKNET_PORTS")"
  echo "RakNet protocol allowlist: ${RAKNET_PROTOCOLS:-11,10}"
  echo "Trusted IPv4:"; cat "$TRUST4" 2>/dev/null || true
  echo "Trusted IPv6:"; cat "$TRUST6" 2>/dev/null || true
  echo "--- nftables ---"; nft -a list table inet "$NFT_TABLE" 2>/dev/null || echo not-loaded
  echo "--- game bans ---"; show_bans
  echo "--- XDP ---"
  if [[ -x "$XDP_CTRL" ]]; then "$XDP_CTRL" status || true; fi
  if have bpftool; then
    local xid sm pm vm rm
    xid="$(cat "$XDP_PROG_FILE" 2>/dev/null || true)"
    if [[ -n "$xid" ]]; then
      for mname in stats ports protos trust4 trust6 pending4 pending6 verified4 verified6 rates4 rates6; do
        mid="$(map_ids_by_name "$xid" "$mname" 2>/dev/null || true)"
        [[ -n "$mid" ]] || continue
        echo "map $mname id=$mid (stats counters are cumulative; LRU map dumps show current sampled entries)"
        bpftool map dump id "$mid" 2>/dev/null | head -40 || true
      done
    fi
  fi
  echo "--- conntrack ---"
  local c m; c=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0); m=$(cat /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null || echo 0)
  echo "conntrack_count=$c max=$m usage=$([[ $m -gt 0 ]] && awk -v c=$c -v m=$m 'BEGIN{printf "%.2f%%",100*c/m}' || echo n/a)"
}
uninstall_all(){ need_root; systemctl disable --now zerox-ddos-v5.service zerox-java-validator-v5.service 2>/dev/null || true; [[ -x "$XDP_CTRL" ]] && "$XDP_CTRL" detach || true; nft delete table inet "$NFT_TABLE" 2>/dev/null || true; restore_sysctl; rm -f "$SERVICE" "$JAVA_SERVICE" "$NFT_LOADER" "$XDP_CTRL" "$JAVA_VALIDATOR"; rm -rf "$APP"; systemctl daemon-reload || true; ok "Uninstalled."; }

menu(){ need_root; ensure_dirs; [[ -r "$CONF" ]] || write_default_conf; while true; do cat <<EOF

Zerox DDoS Protection V5 — Host and Minecraft Protocol Defense
1 Install
2 Update configuration
3 Add protected Java port
4 Add protected RakNet port
5 Remove protected port
6 Manage trusted networks
7 Select protection profile
8 Apply/reload
9 Show status and counters
10 Show active bans
11 Clear a selected ban
12 Clear all bans
13 XDP status
14 Run self-tests
15 Uninstall
0 Exit
EOF
read -r -p 'Choice: ' c; case "$c" in 1) install_all;; 2) ${EDITOR:-nano} "$CONF";; 3) add_port "$JAVA_PORTS";; 4) add_port "$RAKNET_PORTS";; 5) remove_port;; 6) manage_trust;; 7) select_profile;; 8) apply_reload;; 9) status;; 10) show_bans;; 11) clear_ban;; 12) clear_all_bans;; 13) [[ -x "$XDP_CTRL" ]] && "$XDP_CTRL" status || echo no-xdp-control;; 14) run_self_tests;; 15) uninstall_all;; 0) exit 0;; *) warn invalid;; esac; done; }

case "${1:-menu}" in
  install) install_all;; reload|apply) apply_reload;; status) status;; uninstall) uninstall_all;; self-test) run_self_tests;; attach-xdp) "$XDP_CTRL" attach && populate_xdp_maps;; menu) menu;; *) echo "Usage: $0 {menu|install|reload|status|self-test|attach-xdp|uninstall}"; exit 2;;
esac
