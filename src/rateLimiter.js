import { TokenBucket } from './tokenBucket.js';

export class RateLimiter {
  constructor({ capacity = 120, refillRatePerSec = 60, ttlMs = 300000, now = () => Date.now() } = {}) {
    this.capacity = capacity;
    this.refillRatePerSec = refillRatePerSec;
    this.ttlMs = ttlMs;
    this.now = now;
    this.buckets = new Map();
  }

  allow(key, cost = 1) {
    const bucket = this.getBucket(key);
    const allowed = bucket.take(cost);
    bucket.lastSeen = this.now();
    return allowed;
  }

  cleanup() {
    const cutoff = this.now() - this.ttlMs;
    for (const [key, bucket] of this.buckets) {
      if (bucket.lastSeen < cutoff) this.buckets.delete(key);
    }
  }

  getBucket(key) {
    let bucket = this.buckets.get(key);
    if (!bucket) {
      bucket = new TokenBucket({ capacity: this.capacity, refillRatePerSec: this.refillRatePerSec, now: this.now });
      bucket.lastSeen = this.now();
      this.buckets.set(key, bucket);
    }
    return bucket;
  }
}
