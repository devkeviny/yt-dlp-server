#!/usr/bin/env python3
"""
SmartProxyPool — gerenciador de pool de proxies do yt-dlp-server.

Portado de devkeviny/our-free-model-docker (proxy/proxy_server.py, SmartProxyPool),
que por sua vez evoluiu do antigo ProxyManager/SmartProxyTester deste servidor.
Substitui o par ProxyManager + SmartProxyTester por um unico componente:

- Fontes: as 5 listas agregadoras historicas deste servidor + PROXY_SOURCE_URLS
  extras (txt/json) + PROXY_POOL_URLS fixos.
- Health check neutro periodico (alvo configuravel, padrao generate_204 do Google),
  em thread de fundo, com teto de verificacoes por ciclo.
- Quarentena com cooldown: proxy que falha sai por PROXY_COOLDOWN_SECONDS e volta
  sozinho (sem bloqueio semi-permanente).
- Rotacao round-robin entre os ativos (get_candidates), com failover a cargo do
  chamador (stream_download/info/transcript marcam falha via mark_failure).
- PROXY_URL (ou PROXY_SINGLE_URL) definido => atalho: usa so esse proxy.
- Fallback direto (None) por ultimo, salvo PROXY_ALLOW_DIRECT_FALLBACK=false.
- Estado persistido em proxies_active.json (formato OFM) em DATA_DIR.

Adaptacoes em relacao ao original assincrono do OFM: execucao sincrona com
ThreadPoolExecutor (o app e FastAPI + geradores sincronos) e protecao por lock.
Somente biblioteca padrao.
"""

import json
import logging
import os
import random
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("proxy-pool")

# Fontes agregadoras historicas deste servidor (as mesmas do antigo ProxyManager)
BUILTIN_SOURCES = [
    ("proxifly_br", "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json", "json"),
    ("zaeem_socks5", "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/refs/heads/master/socks5.txt", "socks5"),
    ("zaeem_https", "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/refs/heads/master/https.txt", "http"),
    ("hproxy_br", "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/refs/heads/main/by-country/BR.txt", "http"),
    ("databay_br", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/refs/heads/master/by-country/br/http.txt", "http"),
    ("proxifly_global", "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json", "json"),
]

CHECK_INTERVAL = int(os.getenv("PROXY_CHECK_INTERVAL", "300"))
CHECK_TIMEOUT = int(os.getenv("PROXY_CHECK_TIMEOUT", "8"))
COOLDOWN_SECONDS = int(os.getenv("PROXY_COOLDOWN_SECONDS", "300"))
HEALTH_TARGET = os.getenv("PROXY_HEALTH_TARGET", "http://www.gstatic.com/generate_204")
HEALTH_MAX_CHECKS = int(os.getenv("PROXY_HEALTH_MAX_CHECKS", "150"))
ALLOW_DIRECT_FALLBACK = os.getenv("PROXY_ALLOW_DIRECT_FALLBACK", "true").lower() in ("true", "1", "yes")


class SmartProxyPool:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.active_file = os.path.join(data_dir, "proxies_active.json")
        self.single_proxy = (os.getenv("PROXY_URL") or os.getenv("PROXY_SINGLE_URL") or "").strip() or None
        # Fallback dedicado (ex.: tunel FRP p/ o celular): tentado depois dos
        # candidatos do pool e antes da conexao direta. URL completa na env,
        # ex.: socks5h://user:pass@host:11001
        self.frp_proxy = (os.getenv("FRP_PROXY_URL") or "").strip() or None

        self.all_proxies = []
        self.active_proxies = []
        self.quarantine_until = {}  # proxy -> timestamp (OFM)
        self.stats = {
            "requests_total": 0,
            "requests_proxied": 0,
            "requests_fallback": 0,
            "requests_frp": 0,
            "failovers": 0,
            "quarantines": 0,
            "health_cycles": 0,
        }
        self.current_index = 0
        self.latency_ms = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread = None
        self._load_state()
        self.refresh_sources()

    # ------------------------------------------------------------- fontes
    def refresh_sources(self):
        """(Re)carrega as listas agregadoras + fontes extras do ambiente."""
        proxies = set()

        raw_fixed = os.getenv("PROXY_POOL_URLS", "")
        for p in re.split(r"[,\s]+", raw_fixed):
            p = p.strip()
            if p:
                proxies.add(p)

        for name, url, ptype in BUILTIN_SOURCES:
            try:
                resp = urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "yt-dlp-server-proxy-pool/1.0"}),
                    timeout=15)
                raw = resp.read().decode("utf-8", errors="ignore")
                if ptype == "json":
                    for item in json.loads(raw):
                        if isinstance(item, dict) and item.get("proxy"):
                            proxies.add(item["proxy"])
                else:
                    scheme = "socks5" if ptype == "socks5" else "http"
                    for line in raw.splitlines():
                        line = line.strip()
                        if line and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$", line):
                            proxies.add(f"{scheme}://{line}")
                logger.info(f"[ProxyPool] fonte {name}: ok")
            except Exception as e:
                logger.warning(f"[ProxyPool] fonte {name} falhou: {e}")

        extra = os.getenv("PROXY_SOURCE_URLS", "")
        for url in [u.strip() for u in extra.split(",") if u.strip()]:
            proxies.update(self._fetch_remote_source(url))

        with self._lock:
            self.all_proxies = sorted(proxies)
            # Ativo = somente quem ja passou no health check (semantica do antigo
            # SmartProxyTester). Nao-testados ficam no pool bruto ate serem validados.
            self.active_proxies = [p for p in self.active_proxies if p in proxies]
        logger.info(f"[ProxyPool] {len(self.all_proxies)} proxies carregados ({len(self.active_proxies)} ativos).")
        self.save_state()

    def _fetch_remote_source(self, url: str):
        """Busca uma lista remota (txt ou json) — mesmo parser do OFM."""
        discovered = set()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "egress-proxy-fetcher/1.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", errors="ignore").strip()
                if raw.startswith("[") or raw.startswith("{"):
                    data = json.loads(raw)
                    items = data if isinstance(data, list) else data.get("proxies", [])
                    for item in items:
                        if isinstance(item, dict) and item.get("proxy"):
                            p = item["proxy"].strip()
                            if not p.startswith("http://") and not p.startswith("socks5://"):
                                p = f"http://{p}"
                            discovered.add(p)
                        elif isinstance(item, str) and item.strip():
                            p = item.strip()
                            if not p.startswith("http://") and not p.startswith("socks5://"):
                                p = f"http://{p}"
                            discovered.add(p)
                else:
                    for line in raw.splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        m = re.search(r"((?:https?|socks5)://)?([0-9]{1,3}(?:\.[0-9]{1,3}){3}:[0-9]{2,5})", line)
                        if m:
                            scheme = m.group(1) or "http://"
                            discovered.add(f"{scheme}{m.group(2)}")
        except Exception as e:
            logger.warning(f"[ProxyPool] fonte extra {url} falhou: {e}")
        return discovered

    # ------------------------------------------------------------- estado
    def _load_state(self):
        """Restaura ativos/quarentena do proxies_active.json (formato OFM ou legado)."""
        try:
            if os.path.exists(self.active_file):
                with open(self.active_file) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.active_proxies = list(data)
                elif isinstance(data, dict):
                    self.active_proxies = list(data.get("active_proxies") or data.get("working_proxies") or [])
                    q = data.get("quarantine_until") or {}
                    if isinstance(q, dict):
                        self.quarantine_until = {k: float(v) for k, v in q.items()}
        except Exception:
            pass

    def save_state(self):
        try:
            with self._lock:
                payload = {
                    "updated_at": time.time(),
                    "total": len(self.all_proxies),
                    "active_count": len(self.active_proxies),
                    "active_proxies": list(self.active_proxies),
                    "quarantined_count": len(self.quarantine_until),
                    "quarantine_until": dict(self.quarantine_until),
                }
            with open(self.active_file, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"[ProxyPool] falha ao salvar estado: {e}")

    def active_snapshot(self):
        with self._lock:
            return list(self.active_proxies)

    def quarantined_snapshot(self):
        with self._lock:
            return sorted(self.quarantine_until.keys())

    # ------------------------------------------------------------- quarentena (OFM)
    def quarantine(self, proxy: str, reason: str = "error"):
        if not proxy:
            return
        with self._lock:
            self.quarantine_until[proxy] = time.time() + COOLDOWN_SECONDS
            self.stats["quarantines"] += 1
            if proxy in self.active_proxies:
                self.active_proxies.remove(proxy)
        logger.warning(f"[ProxyPool] Quarentena: '{proxy}' por {COOLDOWN_SECONDS}s ({reason}).")
        self.save_state()

    # Compat: o restante do app chamava evict_proxy()
    def mark_failure(self, proxy: str, reason: str = "request_failed"):
        self.quarantine(proxy, reason=reason)

    def _revive_expired(self):
        now = time.time()
        with self._lock:
            expired = [p for p, t in self.quarantine_until.items() if now >= t]
            for p in expired:
                del self.quarantine_until[p]
                if p in self.all_proxies and p not in self.active_proxies:
                    self.active_proxies.append(p)
                    logger.info(f"[ProxyPool] '{p}' saiu da quarentena e voltou ao pool.")

    # ------------------------------------------------------------- selecao (OFM: round-robin)
    def get_candidates(self, limit: int = 5):
        """Candidatos em ordem: FRP (celular, PRIMARIO) -> pool publico em
        round-robin -> direto (None) por ultimo.

        Compativel com o antigo get_candidate_proxies(limit): PROXY_URL tem
        prioridade absoluta; sem ativos, cai para uma amostra das listas.
        """
        if self.single_proxy:
            return [self.single_proxy]
        self._revive_expired()
        with self._lock:
            self.stats["requests_total"] += 1
            actives = [p for p in self.active_proxies if p not in self.quarantine_until]
            candidates = []
            if actives:
                start = self.current_index % len(actives)
                ordered = actives[start:] + actives[:start]
                candidates = ordered[:limit]
                self.current_index = (start + max(1, len(candidates))) % len(actives)
            if len(candidates) < limit:
                rest = [p for p in self.all_proxies
                        if p not in self.quarantine_until and p not in candidates]
                if rest:
                    random.shuffle(rest)
                    candidates.extend(rest[: limit - len(candidates)])
            if candidates:
                self.stats["requests_proxied"] += 1
            else:
                self.stats["requests_fallback"] += 1
        # FRP PRIMARIO (decisao do usuario, 22/out): o celular entrega em
        # segundos; com o FRP por ultimo, o download queimava o timeout do
        # cliente (600s) tentando proxies publicos mortos do pool.
        ordered = []
        if self.frp_proxy:
            ordered.append(self.frp_proxy)
            self.stats["requests_frp"] += 1
        ordered.extend(c for c in candidates if c != self.frp_proxy)
        if ALLOW_DIRECT_FALLBACK and None not in ordered:
            ordered.append(None)
        return ordered

    def get_candidate(self):
        """Um unico candidato (round-robin), como no OFM."""
        c = self.get_candidates(limit=1)
        return c[0] if c else None

    # ------------------------------------------------------------- health check (OFM)
    def test_proxy_sync(self, proxy_url: str) -> bool:
        """Testa o proxy contra o alvo neutro de saude."""
        try:
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(HEALTH_TARGET, headers={"User-Agent": "egress-proxy-tester/1.0"})
            with opener.open(req, timeout=CHECK_TIMEOUT) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False

    def run_health_cycle(self):
        """Um ciclo de auditoria (modelo OFM + meta de ativos do antigo tester):
        revalida os ativos atuais, revive quarentenas vencidas e testa o pool
        bruto em lote ate atingir PROXY_TARGET_ACTIVE ativos validados ou o
        teto de verificacoes do ciclo. Falha = quarentena com cooldown."""
        self._revive_expired()
        target = int(os.getenv("PROXY_TARGET_ACTIVE", "15"))
        with self._lock:
            actives = list(self.active_proxies)
            tested = set(actives)
            others = [p for p in self.all_proxies
                      if p not in tested and p not in self.quarantine_until]
        random.shuffle(others)
        if len(actives) >= target:
            to_test = actives  # meta atingida: so revalida os ativos
        else:
            budget = max(HEALTH_MAX_CHECKS, len(actives))
            to_test = list(dict.fromkeys(actives + others))[:budget]
        if not to_test:
            return
        logger.info(f"[ProxyPool] Health cycle: testando {len(to_test)} proxies (meta: {target} ativos)...")
        def _timed(p):
            t0 = time.monotonic()
            ok = self.test_proxy_sync(p)
            return ok, int((time.monotonic() - t0) * 1000)

        results = {}
        with ThreadPoolExecutor(max_workers=15) as ex:
            fut = {ex.submit(_timed, p): p for p in to_test}
            for f, p in fut.items():
                try:
                    ok, ms = f.result()
                    results[p] = ok
                    if ok:
                        self.latency_ms[p] = ms
                    else:
                        self.latency_ms.pop(p, None)
                except Exception:
                    results[p] = False
        with self._lock:
            for p, ok in results.items():
                if ok:
                    self.quarantine_until.pop(p, None)
                    if p in self.all_proxies and p not in self.active_proxies:
                        self.active_proxies.append(p)
                else:
                    if p in self.active_proxies:
                        self.active_proxies.remove(p)
                    self.quarantine_until[p] = time.time() + COOLDOWN_SECONDS
                    self.stats["quarantines"] += 1
            # Se a meta foi atingida cedo, ativos excedentes alem da meta sao
            # mantidos (sao validados); o ciclo seguinte apenas revalida.
            self.stats["health_cycles"] += 1
            active_count = len(self.active_proxies)
        self.save_state()
        logger.info(f"[ProxyPool] Health cycle ok: {active_count} ativos validados de {len(self.all_proxies)}.")

    # ------------------------------------------------------------- fundo
    def start_background(self):
        if self._running:
            return
        self._running = True

        def loop():
            time.sleep(5)
            while self._running:
                try:
                    self.refresh_sources()
                    self.run_health_cycle()
                except Exception as e:
                    logger.warning(f"[ProxyPool] erro no ciclo de fundo: {e}")
                time.sleep(CHECK_INTERVAL)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        logger.info(f"[ProxyPool] Thread de fundo iniciada (intervalo {CHECK_INTERVAL}s).")

    def stop_background(self):
        self._running = False
