# SPEC-001: Rotação Inteligente de Proxies com Validação Real de Vídeo do YouTube

## Contexto & Problema
O servidor `yt-dlp.estudiopleiades.qzz.io` roda em VPS na nuvem (Oracle Cloud / Coolify), cujos IPs de datacenter sofrem bloqueio agressivo do YouTube (*"Sign in to confirm you’re not a bot"*).
Anteriormente, o `SmartProxyTester` testava apenas um `GET https://www.youtube.com/`, que retornava HTTP 200 na maioria dos proxies porque o YouTube não bloqueia o HTML da página inicial. Porém, ao tentar extrair metadados ou baixar vídeos reais (`/api/info`, `/api/download`, `/api/transcript`), o YouTube bloqueava 95% dos proxies salvos. Além disso, as rotas desistiam no primeiro proxy que falhasse, gerando HTTP 500 imediato.

## Objetivos
1. **Validação Real de Vídeo:** Testar proxies diretamente contra extração de vídeo via `yt-dlp` (vídeo rápido e estável, ex: `jNQXAC9IVRw`), garantindo que apenas proxies que realmente burlam o bot-check do YouTube sejam considerados ativos.
2. **Atualização Periódica Automática:** Puxar de hora em hora as 5 fontes públicas de proxy no GitHub (+ lista global do proxifly), testar em paralelo (threads) e salvar em `/data/proxies_active.json`.
3. **Fallback Progressivo Resiliente:** Em `/api/info`, `/api/transcript` e `/api/download`, se o primeiro proxy da lista falhar, ele é removido imediatamente da lista ativa e marcado como bloqueado, tentando o próximo proxy progressivamente (até 5 tentativas) antes de falhar.
4. **Cache Persistente & Remoção Dinâmica:** Se um proxy falhar durante uso real em produção, é removido do cache em memória e do arquivo `/data/proxies_active.json` em tempo de execução.

## Fontes Oficiais de Proxies
1. `proxifly_br`: `https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json`
2. `zaeem_socks5`: `https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/refs/heads/master/socks5.txt`
3. `zaeem_https`: `https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/refs/heads/master/https.txt`
4. `hproxy_br`: `https://raw.githubusercontent.com/hproxy-com/free-proxy-list/refs/heads/main/by-country/BR.txt`
5. `databay_br`: `https://raw.githubusercontent.com/databay-labs/free-proxy-list/refs/heads/master/by-country/br/http.txt`
6. `proxifly_global`: `https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json`
