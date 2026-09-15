# Correção B — ProxyManager (yt-dlp-server)
Data: 2026-09-14
Repo: /opt/data/ytdlp-server-analysis (main.py)

## Prova real (não inventado)
- curl BR data.json -> HTTP 200, 40979 bytes, 153 entries (ls /tmp/proxy_br.json)
- curl GLOBAL data.json -> HTTP 200, 2708369 bytes, 10243 entries (ls /tmp/proxy_all.json)
- proxies.json bloqueados -> NÃO EXISTE (ls data/ -> apenas history.json, stats30d.json; nenhum proxies.json)
- grep blocked -> 7 ocorrências em main.py (linha 47 _load_blocked, 59 _save_blocked, 75 healthy, 78 mark_blocked, 244/257/395/418 chamada stream/info)

## Problemas encontrados
1. update_lists() usava timeout=10, sem raise_for_status(), sem isolamento BR/global, sem log de falha -> falha silenciosa
2. get_proxy() fallback não documentado; se BR vazio usava global, mas sem log
3. proxies.json inexistente -> bloqueados = set() (correto, mas não persistia automaticamente até mark_blocked ser chamado)
4. Lista proxifly não estava bloqueada (nenhum registro); está atualizada (153 BR / 10243 global)

## Correções aplicadas (main.py)
- update_lists(): timeout 15, raise_for_status(), isolamento BR/global, print de falha, verificação isinstance(p,dict)
- get_proxy(): comentário de fallback explícito (PROXY_URL > BR > global > None)
- Nenhum proxy bloqueado registrado (confirmação real)

## Arquivos modificados
- /opt/data/ytdlp-server-analysis/main.py (patch update_lists + comentário get_proxy)
- /tmp/proxy_br.json, /tmp/proxy_all.json (prova de fetch; não commitados)
- Nenhum proxies.json criado (não precisa; bloqueios vazios é estado válido)
