import { RateLimiter } from './rateLimiter.js';

const DEFAULT_BAD_PATHS = [/\.env$/i, /wp-login\.php/i, /xmlrpc\.php/i, /\.git\//i];
const DEFAULT_BAD_USER_AGENTS = [/sqlmap/i, /masscan/i, /nikto/i, /zgrab/i, /curl\/7\.29/i];

export function clientIp(req, trustedProxyHops = 0) {
  const remote = req.socket?.remoteAddress || 'unknown';
  if (!trustedProxyHops) return remote;
  const forwarded = String(req.headers['x-forwarded-for'] || '').split(',').map(v => v.trim()).filter(Boolean);
  return forwarded.at(-trustedProxyHops) || remote;
}

export function createLayer7Guard({
  limiter = new RateLimiter(),
  trustedProxyHops = 0,
  maxBodyBytes = 1_000_000,
  badPaths = DEFAULT_BAD_PATHS,
  badUserAgents = DEFAULT_BAD_USER_AGENTS,
  allowedMethods = ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
  subnetBlocker = null,
} = {}) {
  const methodSet = new Set(allowedMethods);
  return function layer7Guard(req, res, next) {
    const ip = clientIp(req, trustedProxyHops);
    const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
    const ua = String(req.headers['user-agent'] || '');
    const contentLength = Number(req.headers['content-length'] || 0);

    if (subnetBlocker?.isBlocked?.(ip)) return deny(res, 403, 'subnet_blocked');
    if (!methodSet.has(req.method)) return block(subnetBlocker, ip, res, 405, 'method_not_allowed');
    if (contentLength > maxBodyBytes) return block(subnetBlocker, ip, res, 413, 'payload_too_large');
    if (badPaths.some(rx => rx.test(url.pathname))) return block(subnetBlocker, ip, res, 403, 'blocked_path');
    if (badUserAgents.some(rx => rx.test(ua))) return block(subnetBlocker, ip, res, 403, 'blocked_user_agent');
    if (!limiter.allow(`${ip}:${req.method}:${url.pathname}`)) return block(subnetBlocker, ip, res, 429, 'rate_limited');

    res.setHeader('x-ddos-guard', 'pass');
    return next();
  };
}

function block(subnetBlocker, ip, res, statusCode, reason) {
  subnetBlocker?.record?.(ip, reason);
  return deny(res, statusCode, reason);
}

function deny(res, statusCode, reason) {
  res.statusCode = statusCode;
  res.setHeader('content-type', 'application/json');
  res.setHeader('x-ddos-guard', reason);
  res.end(JSON.stringify({ error: reason }));
}
