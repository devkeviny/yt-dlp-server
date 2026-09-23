# TASKS-001: Roteiro de Implementação e Validação

- [ ] **Tarefa 1: Refatorar `SmartProxyTester` em `main.py`**
  - Implementar validação via `yt_dlp` em vídeo de teste (`jNQXAC9IVRw`).
  - Adicionar concorrência com `ThreadPoolExecutor` para acelerar varredura.
  - Testar loop de atualização a cada 1 hora (3600s).

- [ ] **Tarefa 2: Refatorar `ProxyManager` em `main.py`**
  - Adicionar métodos `get_candidate_proxies(limit=5)` e `evict_proxy(proxy_url)`.
  - Sincronizar cache em memória e em disco (`/data/proxies_active.json`).

- [ ] **Tarefa 3: Atualizar rotas consumidoras em `main.py`**
  - Refatorar `/api/info` com loop progressivo de retry (até 5 proxies).
  - Refatorar `/api/transcript` com loop progressivo de retry.
  - Refatorar `stream_download` com fallback de proxies.

- [ ] **Tarefa 4: Verificação Local**
  - Rodar script de teste local exercitando a extração do vídeo `MdvH2_D2Iqw` com os novos métodos.

- [ ] **Tarefa 5: Git Commit & Push**
  - Commit no repositório `https://github.com/devkeviny/yt-dlp-server.git`.
  - Autor: `devkeviny <kevinnyfonseca@gmail.com>`.

- [ ] **Tarefa 6: Redeploy no Coolify**
  - Disparar `POST /api/v1/deploy?uuid=ac6qo4e93udy2k5d5swwb6cj&force=true&cleanup=true`.
  - Monitorar status até `running:healthy` e `status=finished`.

- [ ] **Tarefa 7: Teste End-to-End em Produção**
  - Executar curl em `https://yt-dlp.estudiopleiades.qzz.io/api/info?url=https://youtu.be/MdvH2_D2Iqw`.
  - Validar resposta com título real.

- [ ] **Tarefa 8: Atualizar Obsidian Vault**
  - Documentar a nova arquitetura e histórico de testes em `/opt/data/obsidian-vault/02_Sistemas/yt-dlp-server.md`.
