export class TokenBucket {
  constructor({ capacity, refillRatePerSec, now = () => Date.now() }) {
    if (capacity <= 0 || refillRatePerSec <= 0) throw new Error('capacity and refillRatePerSec must be positive');
    this.capacity = capacity;
    this.refillRatePerSec = refillRatePerSec;
    this.tokens = capacity;
    this.updatedAt = now();
    this.now = now;
  }

  take(cost = 1) {
    this.refill();
    if (this.tokens < cost) return false;
    this.tokens -= cost;
    return true;
  }

  refill() {
    const current = this.now();
    const elapsed = Math.max(0, current - this.updatedAt) / 1000;
    this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.refillRatePerSec);
    this.updatedAt = current;
  }
}
