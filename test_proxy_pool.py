#!/usr/bin/env python3
"""Testes da Parte 1 — fork yt-dlp-server.

Prova (sem rede; urlopen mockado):
  1. pin do yt-dlp em requirements.txt == 2026.10.26 (ultima estavel).
  2. residencial (FRP_PROXY_URL) sempre primeiro, inclusive com PROXY_URL setado.
  3. listas buscadas DIRETO do link a cada uso (TTL curto): sem fetch dentro
     do TTL, com fetch apos o TTL expirar.
  4. timeout curto por lista (LIST_FETCH_TIMEOUT, padrao 5s).
  5. .txt (um proxy por linha) e .json tratados.
  6. refresh com todas as fontes mortas NAO esvazia o pool.

Rodar: python3 -m unittest test_proxy_pool -v
"""
import json
import os
import re
import tempfile
import unittest
import urllib.request
from unittest import mock

import proxy_pool
from proxy_pool import SmartProxyPool, BUILTIN_SOURCES

FRP = "socks5h://tailscale:1080"

FIXTURES = {
    "proxifly/free-proxy-list": json.dumps([
        {"proxy": "http://1.2.3.4:8080"},
        {"proxy": "socks5://2.3.4.5:1080"},
    ]).encode(),
    "Zaeem20/FREE_PROXIES_LIST/refs/heads/master/socks5.txt":
        b"1.2.3.4:1080\n5.6.7.8:1080\n# comentario\n\nnao-e-proxy\n",
    "Zaeem20/FREE_PROXIES_LIST/refs/heads/master/https.txt":
        b"9.9.9.9:3128\n",
    "hproxy-com/free-proxy-list": b"10.0.0.1:80\n",
    "databay-labs/free-proxy-list": b"10.0.0.2:80\n",
    "proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json":
        json.dumps([{"proxy": "http://11.22.33.44:8080"}]).encode(),
}


def fixture_for(url):
    # a chave global e mais especifica: testar antes da chave BR (substring)
    for key in ("proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json",
                "proxifly/free-proxy-list",
                "Zaeem20/FREE_PROXIES_LIST/refs/heads/master/socks5.txt",
                "Zaeem20/FREE_PROXIES_LIST/refs/heads/master/https.txt",
                "hproxy-com/free-proxy-list",
                "databay-labs/free-proxy-list"):
        if key in url:
            return FIXTURES[key]
    raise AssertionError(f"sem fixture para {url}")


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeUrlopen:
    """Registra (url, timeout) de cada chamada; falha nas URLs de `dead`."""

    def __init__(self, dead=()):
        self.calls = []
        self.dead = set(dead)

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.calls.append((url, timeout))
        if any(d in url for d in self.dead):
            raise urllib.error.URLError("lista morta (fixture)")
        return FakeResponse(fixture_for(url))


BASE_ENV = {
    "FRP_PROXY_URL": FRP,
    "PROXY_URL": "",
    "PROXY_SINGLE_URL": "",
    "PROXY_POOL_URLS": "",
    "PROXY_SOURCE_URLS": "",
}


class PoolTestCase(unittest.TestCase):
    def make_pool(self, fake, env_extra=None):
        env = dict(BASE_ENV)
        env.update(env_extra or {})
        patcher_env = mock.patch.dict(os.environ, env, clear=False)
        patcher_env.start()
        self.addCleanup(patcher_env.stop)
        patcher_url = mock.patch.object(urllib.request, "urlopen", fake)
        patcher_url.start()
        self.addCleanup(patcher_url.stop)
        tmp = tempfile.mkdtemp()
        return SmartProxyPool(tmp)

    def test_pin_yt_dlp(self):
        req = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
        with open(req) as f:
            lines = [l.strip() for l in f]
        self.assertIn("yt-dlp==2026.10.26", lines,
                      "pin do yt-dlp deve ser a ultima estavel (2026.10.26)")

    def test_residencial_primeiro(self):
        fake = FakeUrlopen()
        pool = self.make_pool(fake)
        pool.active_proxies = ["http://1.2.3.4:8080", "http://9.9.9.9:3128"]
        cands = pool.get_candidates(limit=5)
        self.assertEqual(cands[0], FRP)
        self.assertEqual(cands[-1], None)  # direto por ultimo
        self.assertIn("http://1.2.3.4:8080", cands)

    def test_residencial_primeiro_com_proxy_unico(self):
        fake = FakeUrlopen()
        pool = self.make_pool(fake, {"PROXY_URL": "http://9.9.9.9:9999"})
        cands = pool.get_candidates(limit=5)
        self.assertEqual(cands[0], FRP)
        self.assertEqual(cands[1], "http://9.9.9.9:9999")

    def test_listas_buscadas_ao_vivo_com_ttl_curto(self):
        fake = FakeUrlopen()
        pool = self.make_pool(fake)
        n_builtin = len(BUILTIN_SOURCES)
        self.assertEqual(len(fake.calls), n_builtin)  # refresh do __init__
        pool.get_candidates(limit=5)
        self.assertEqual(len(fake.calls), n_builtin,
                         "dentro do TTL nao pode re-buscar as listas")
        # TTL expirado -> proximo uso re-busca DIRETO do link
        import time as _time
        pool._last_refresh_ts = _time.monotonic() - proxy_pool.LIST_TTL_SECONDS - 1
        pool.get_candidates(limit=5)
        self.assertEqual(len(fake.calls), 2 * n_builtin)
        urls = [u for u, _ in fake.calls]
        for _, url, _ in BUILTIN_SOURCES:
            self.assertIn(url, urls)

    def test_timeout_curto_por_lista(self):
        fake = FakeUrlopen()
        self.make_pool(fake)
        self.assertTrue(fake.calls)
        for url, timeout in fake.calls:
            self.assertEqual(timeout, proxy_pool.LIST_FETCH_TIMEOUT,
                             f"fetch de {url} sem timeout curto")
        self.assertLessEqual(proxy_pool.LIST_FETCH_TIMEOUT, 5)

    def test_txt_e_json_tratados(self):
        fake = FakeUrlopen()
        pool = self.make_pool(fake)
        allp = set(pool.all_proxies)
        # .json (proxifly): campo "proxy" preservado
        self.assertIn("http://1.2.3.4:8080", allp)
        self.assertIn("socks5://2.3.4.5:1080", allp)
        self.assertIn("http://11.22.33.44:8080", allp)
        # .txt (zaeem/hproxy/databay): ip:port vira scheme://ip:port
        self.assertIn("socks5://1.2.3.4:1080", allp)
        self.assertIn("socks5://5.6.7.8:1080", allp)
        self.assertIn("http://9.9.9.9:3128", allp)
        self.assertIn("http://10.0.0.1:80", allp)
        self.assertIn("http://10.0.0.2:80", allp)

    def test_fetch_remote_txt_e_json(self):
        fake = FakeUrlopen()
        pool = self.make_pool(fake)
        # .txt remoto via _fetch_remote_source (linhas com e sem scheme)
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=FakeResponse(b"http://7.7.7.7:8080\n8.8.8.8:3128\n# c\n")):
            got = pool._fetch_remote_source("https://exemplo.test/lista.txt")
        self.assertEqual(got, {"http://7.7.7.7:8080", "http://8.8.8.8:3128"})
        # .json remoto: {"proxies": [...]} e lista de strings
        body = json.dumps({"proxies": [{"proxy": "socks5://3.3.3.3:1080"}, "4.4.4.4:8080"]}).encode()
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=FakeResponse(body)):
            got = pool._fetch_remote_source("https://exemplo.test/lista.json")
        self.assertEqual(got, {"socks5://3.3.3.3:1080", "http://4.4.4.4:8080"})

    def test_refresh_totalmente_morto_nao_esvazia_pool(self):
        fake = FakeUrlopen()
        pool = self.make_pool(fake)
        antes = set(pool.all_proxies)
        self.assertTrue(antes)
        # todas as listas morrem no proximo refresh
        dead = FakeUrlopen(dead=("githubusercontent.com",))
        with mock.patch.object(urllib.request, "urlopen", dead):
            import time as _time
            pool._last_refresh_ts = _time.monotonic() - proxy_pool.LIST_TTL_SECONDS - 1
            pool.get_candidates(limit=5)
        self.assertEqual(set(pool.all_proxies), antes,
                         "refresh falho nao pode zerar o pool")


if __name__ == "__main__":
    unittest.main()
