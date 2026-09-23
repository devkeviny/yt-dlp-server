# PLAN-001: Arquitetura de Validação e Rotação de Proxies

## Arquitetura de Componentes

### 1. `SmartProxyTester`
- **Intervalo:** 3600 segundos (1 hora).
- **Fontes:** As 5 fontes fixas + proxifly global.
- **Método de Teste:**
  - `test_proxy_video(proxy_url, timeout=6)`:
    - Roda `yt_dlp.YoutubeDL` com `proxy=proxy_url`, `skip_download=True`, `extract_flat='in_playlist'`, `socket_timeout=5`.
    - Alvo: `https://www.youtube.com/watch?v=jNQXAC9IVRw` (vídeo canônico rápido de 19s).
    - Se extrair o título com sucesso: `True`.
    - Se lançar `DownloadError`, bot check ou timeout: `False`.
  - Execução concorrente usando `ThreadPoolExecutor(max_workers=10)` para testar centenas de proxies em poucos minutos sem travar o event loop do FastAPI.
  - Salva a lista de proxies aprovados em `/data/proxies_active.json`.

### 2. `ProxyManager`
- `get_candidate_proxies(count=5)`:
  - Retorna uma lista ordenada/aleatória de proxies aprovados do cache ativo.
- `evict_proxy(proxy_url)`:
  - Remove imediatamente do cache `/data/proxies_active.json`.
  - Adiciona ao `blocked_proxies` e persiste `/data/proxies.json`.

### 3. Rotas de API (`/api/info`, `/api/transcript`, `stream_download`)
- Em vez de tentar `[None, proxy]` e falhar no primeiro, itera por uma lista de até 5 candidatos:
  ```python
  candidates = proxy_manager.get_candidate_proxies(limit=5)
  for px in candidates:
      try:
          # Executa yt_dlp
          return resultado
      except Exception as e:
          if px:
              proxy_manager.evict_proxy(px)
  ```
- Garante resiliência e auto-cura contínua: proxies ruins são descartados em tempo real.
