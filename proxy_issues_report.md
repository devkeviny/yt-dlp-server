# Proxy Implementation Issues Report

## Summary

The `main.py` proxy implementation has several correctness gaps, race conditions, and design issues that violate the Kanban acceptance criteria.

## Critical Issues

### 1. Race Conditions & Thread Safety
- **Location**: `ProxyManager` class (lines 43-100)
- **Problem**: Multiple concurrent calls to `get_proxy()` can corrupt `blocked_proxies` set and `active` list due to lack of synchronization.
- **Fix**: Add `threading.Lock` to protect all shared state mutations.

### 2. Improper Cache Refresh
- **Location**: `get_proxy()` method (line 85)
- **Problem**: Every call to `get_proxy()` triggers a fresh fetch from proxifly list, causing unnecessary network overhead and potential timing issues.
- **Fix**: Defer `update_lists()` to the background thread (`SmartProxyTester`) and only refresh when necessary (e.g., periodic interval).

### 3. Incorrect Proxy Loading
- **Location**: Line 85 in `/api/proxies` endpoint
- **Problem**: `get_proxy()` opens `/data/proxies_active.json` directly instead of using `smart_tester.load_active()` which properly handles the active list.
- **Fix**: Replace with `smart_tester.load_active()` to get consistent active proxy list.

### 4. Missing History-Based Prioritization
- **Location**: `test_all_br()` method (lines 162-216)
- **Problem**: Proxies are selected randomly from the pool regardless of historical success rates.
- **Fix**: Track success counts per proxy and sort by success rate before selecting top `max_active` proxies.

### 5. Over-Bloody Blocking
- **Location**: `test_all_br()` method (lines 188-191)
- **Problem**: All failed BR proxies are marked as blocked immediately, preventing reuse after temporary failures.
- **Fix**: Only mark as blocked after sustained failure threshold; allow retry after successful test.

### 6. Sequential Proxy Testing
- **Location**: `test_all_br()` method (lines 177-216)
- **Problem**: Proxies are tested sequentially, causing slow overall completion time.
- **Fix**: Use `ThreadPoolExecutor` to test proxies concurrently (e.g., 10 workers).

### 7. Hardcoded Interval
- **Location**: `__init__` of `SmartProxyTester` (line 117)
- **Problem**: `test_interval` is hardcoded to 1800 seconds; should be configurable via environment variable.
- **Fix**: Read from `PROXY_TEST_INTERVAL` env var with default.

## Test Cases Needed

1. **Concurrent access test** – Verify no corruption when multiple threads call `get_proxy()` simultaneously.
2. **Blocking persistence test** – Confirm blocked proxies are removed after successful retry.
3. **History-based ranking test** – Ensure proxies with higher success rates are prioritized.
4. **Interval override test** – Verify `PROXY_TEST_INTERVAL` environment variable affects test frequency.
5. **Performance test** – Measure time to test all BR proxies with concurrency enabled.

## Recommended Fixes

- Add `threading.Lock` to `ProxyManager` class.
- Refactor `get_proxy()` to use lazy loading from `smart_tester`.
- Implement weighted selection based on historical success rates.
- Make `test_interval` configurable via environment variable.
- Adjust blocking logic to allow recovery after transient failures.
- Use `ThreadPoolExecutor` for parallel proxy testing.

## Artifacts

- `proxy_issues_report.md` – This report
- `main.py` – Original implementation (no changes yet, pending fixes)
