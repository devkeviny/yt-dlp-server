/*
 * net-meter.js  —  Real-time network usage indicator (front-end)
 *
 * HONEST APPROACH: browsers do NOT expose OS-level NIC throughput to JS.
 * The platform only tells us about traffic THIS PAGE generates, so we:
 *   1. Use PerformanceObserver(ResourceTiming) to capture every response's
 *      transferSize (real bytes downloaded by the page).
 *   2. Wrap fetch() / XMLHttpRequest to measure request-body bytes (uploads).
 *   3. Derive a true 1-second rolling speed from cumulative counters and
 *      render smoothly with requestAnimationFrame (rAF) + EWMA smoothing.
 *
 * Dependency-free, framework-free, works in any modern browser. Also
 * exports under Node (CommonJS) so the math is unit-testable headlessly.
 *
 *   const meter = new NetworkIndicator({ onTick: s => console.log(formatRate(s.downSpeed)) });
 *   meter.start();
 */
(function (global, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else global.NetworkIndicator = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Pure: instantaneous bytes/sec over the trailing window from cumulative samples.
  function computeRate(samples, windowMs) {
    if (!samples || samples.length < 2) return { rxRate: 0, txRate: 0 };
    const last = samples[samples.length - 1];
    const cutoff = last.t - windowMs;
    let first = samples[0];
    for (let i = 0; i < samples.length; i++) {
      if (samples[i].t >= cutoff) { first = samples[i]; break; }
    }
    const span = (last.t - first.t) / 1000;
    if (span <= 0) return { rxRate: 0, txRate: 0 };
    return {
      rxRate: Math.max(0, (last.rx - first.rx) / span),
      txRate: Math.max(0, (last.tx - first.tx) / span)
    };
  }

  function formatRate(bps) {
    if (!isFinite(bps) || bps <= 0) return '0 B/s';
    const u = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s'];
    let v = bps, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    const dp = (v >= 100 || i === 0) ? 0 : (v >= 10 ? 1 : 2);
    return v.toFixed(dp) + ' ' + u[i];
  }

  function estimateBodyLength(body) {
    if (body == null) return 0;
    if (typeof body === 'string') return unescape(encodeURIComponent(body)).length;
    if (body instanceof ArrayBuffer) return body.byteLength;
    if (typeof Blob !== 'undefined' && body instanceof Blob) return body.size;
    if (typeof FormData !== 'undefined' && body instanceof FormData) return 0;
    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return body.toString().length;
    if (body && typeof body.size === 'number') return body.size;
    if (body && typeof body.byteLength === 'number') return body.byteLength;
    return 0;
  }

  function NetworkIndicator(opts) {
    opts = opts || {};
    this.windowMs = opts.windowMs || 1000;
    this.smoothing = opts.smoothing != null ? opts.smoothing : 0.25;
    this.onUpdate = opts.onUpdate || null;
    this.onTick = opts.onTick || null;
    this.onState = opts.onState || null;
    this.tagFilter = opts.tagFilter || null;
    this._rx = this._tx = 0;
    this._samples = [];
    this._rateRx = this._rateTx = 0;
    this._peakRx = this._peakTx = 0;
    this._running = false;
    this._rafId = this._pollId = this._tickTimer = this._lastFrame = this._observer = null;
    this._origFetch = this._origXhrOpen = this._origXhrSend = null;
    this._frameBound = null;
  }

  NetworkIndicator.computeRate = computeRate;
  NetworkIndicator.formatRate = formatRate;

  NetworkIndicator.prototype._passFilter = function (name) {
    if (!this.tagFilter) return true;
    try { return this.tagFilter(name); } catch (e) { return true; }
  };

  NetworkIndicator.prototype._hookResourceTiming = function () {
    if (typeof PerformanceObserver === 'undefined') return false;
    if (PerformanceObserver.supportedEntryTypes && PerformanceObserver.supportedEntryTypes.indexOf('resource') === -1) return false;
    try {
      this._observer = new PerformanceObserver((list) => {
        const entries = list.getEntries();
        for (let i = 0; i < entries.length; i++) {
          const e = entries[i];
          if (this.tagFilter && e.name && !this._passFilter(e.name)) continue;
          const size = e.transferSize || e.encodedBodySize || e.decodedBodySize || 0;
          if (size > 0) this._rx += size;
        }
      });
      try { this._observer.observe({ type: 'resource', buffered: false }); }
      catch (e1) { this._observer.observe({ entryTypes: ['resource'] }); }
      return true;
    } catch (e) { this._observer = null; return false; }
  };

  NetworkIndicator.prototype._hookFetch = function () {
    if (typeof window === "undefined" || typeof window.fetch !== "function") return;
    const self = this;
    this._origFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      init = init || {};
      const len = estimateBodyLength(init.body);
      if (len > 0) self._tx += len;            // upload bytes (request body)
      return self._origFetch(input, init).then(function (resp) {
        // Count DOWNLOAD bytes live, as they stream in. Use a clone so the
        // caller still receives the full, unread response body.
        try {
          if (resp && resp.body && typeof resp.body.getReader === "function" && resp.clone) {
            const cloned = resp.clone();
            const reader = cloned.body.getReader();
            const pump = function () {
              return reader.read().then(function (r) {
                if (r.value && r.value.byteLength) self._rx += r.value.byteLength;
                if (r.done) return;
                return pump();
              });
            };
            pump().catch(function () {});
          }
        } catch (e) {}
        return resp;
      });
    };
  };

  NetworkIndicator.prototype._hookXhr = function () {
    if (typeof window === 'undefined' || typeof XMLHttpRequest === 'undefined') return;
    const self = this;
    this._origXhrOpen = XMLHttpRequest.prototype.open;
    this._origXhrSend = XMLHttpRequest.prototype.send;
    const XHR = XMLHttpRequest.prototype;
    XHR.open = function (m, url) { this.__ni_url = url; return self._origXhrOpen.apply(this, arguments); };
    XHR.send = function (body) { const len = estimateBodyLength(body); if (len > 0) self._tx += len; return self._origXhrSend.apply(this, arguments); };
  };

  NetworkIndicator.prototype._restore = function () {
    if (typeof window !== 'undefined') {
      if (this._origFetch) { window.fetch = this._origFetch; this._origFetch = null; }
      if (this._origXhrOpen && XMLHttpRequest && XMLHttpRequest.prototype) {
        XMLHttpRequest.prototype.open = this._origXhrOpen;
        XMLHttpRequest.prototype.send = this._origXhrSend;
      }
    }
    if (this._observer) { try { this._observer.disconnect(); } catch (e) {} this._observer = null; }
  };

  NetworkIndicator.prototype._step = function (now) {
    this._samples.push({ t: now, rx: this._rx, tx: this._tx });
    const cutoff = now - this.windowMs;
    while (this._samples.length > 2 && this._samples[1].t < cutoff) this._samples.shift();
    const { rxRate, txRate } = computeRate(this._samples, this.windowMs);
    this._rateRx = this.smoothing * rxRate + (1 - this.smoothing) * this._rateRx;
    this._rateTx = this.smoothing * txRate + (1 - this.smoothing) * this._rateTx;
    if (this._rateRx > this._peakRx) this._peakRx = this._rateRx;
    if (this._rateTx > this._peakTx) this._peakTx = this._rateTx;
    if (this.onUpdate) this.onUpdate(this.getStats());
  };

  NetworkIndicator.prototype._frame = function () {
    if (!this._running) return;
    const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    this._step(now);
    this._rafId = requestAnimationFrame(this._frameBound);
  };
  NetworkIndicator.prototype._frameFallback = function () {
    const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    this._step(now);
  };
  NetworkIndicator.prototype._tick = function () { if (this.onTick) this.onTick(this.getStats()); };

  NetworkIndicator.prototype.getStats = function () {
    return {
      downSpeed: this._rateRx, upSpeed: this._rateTx,
      totalDown: this._rx, totalUp: this._tx,
      peakDown: this._peakRx, peakUp: this._peakTx,
      windowMs: this.windowMs, timestamp: Date.now()
    };
  };

  NetworkIndicator.prototype.start = function () {
    if (this._running) return this;
    this._running = true;
    this._rx = this._tx = this._rateRx = this._rateTx = 0;
    this._peakRx = this._peakTx = 0;
    this._samples = []; this._lastFrame = null;
    this._hookResourceTiming(); this._hookFetch(); this._hookXhr();
    const start = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    this._samples.push({ t: start, rx: 0, tx: 0 });
    if (typeof requestAnimationFrame === 'function') {
      this._frameBound = this._frame.bind(this);
      this._rafId = requestAnimationFrame(this._frameBound);
    } else if (typeof setInterval === 'function') {
      this._pollId = setInterval(this._frameFallback.bind(this), 200);
    }
    this._tickTimer = setInterval(this._tick.bind(this), 1000);
    if (this.onState) this.onState('start');
    return this;
  };

  NetworkIndicator.prototype.stop = function () {
    if (!this._running) return this;
    this._running = false;
    if (this._rafId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this._rafId);
    if (this._pollId != null) clearInterval(this._pollId);
    if (this._tickTimer != null) clearInterval(this._tickTimer);
    this._rafId = this._pollId = this._tickTimer = null;
    this._restore();
    if (this.onState) this.onState('stop');
    return this;
  };

  NetworkIndicator.prototype.addReceived = function (b) { if (b > 0) this._rx += b; };
  NetworkIndicator.prototype.addSent = function (b) { if (b > 0) this._tx += b; };

  return NetworkIndicator;
});
