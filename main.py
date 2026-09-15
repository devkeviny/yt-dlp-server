import os, json, time, psutil, asyncio, subprocess, logging, random, requests, traceback, sys, threading, re
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from concurrent.futures import ThreadPoolExecutor
import yt_dlp

# ============================================================
# Config (env)
# ============================================================
APP_PASSWORD = os.getenv('APP_PASSWORD', 'admin123')
API_KEY = os.getenv('API_KEY', 'default_key_123')
PROXY_URL = os.getenv('PROXY_URL')
STITCH_API_KEY = os.getenv('STITCH_API_KEY', '')
DATA_DIR = os.getenv('DATA_DIR', '/data')
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except PermissionError:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    os.makedirs(DATA_DIR, exist_ok=True)

API_ENABLED_FILE = os.path.join(DATA_DIR, 'api_enabled.json')

app = FastAPI()

def _serve_page(filename):
    path = os.path.join("static", filename)
    with open(path, "r") as f:
        html = f.read()
    html = html.replace("STITCH_API_KEY_PLACEHOLDER", STITCH_API_KEY)
    html = html.replace(
        '<meta name="stitch-api-key" content="STITCH_API_KEY_PLACEHOLDER"/>',
        '<script>window.STITCH_API_KEY="' + STITCH_API_KEY + '";</script>\n<meta name="stitch-api-key" content="' + STITCH_API_KEY + '"/>'
    )
    return HTMLResponse(content=html)

executor = ThreadPoolExecutor(max_workers=10)

# ============================================================
# Proxy manager (free proxifly list + rotation + fallback)
# ============================================================
class ProxyManager:
    def __init__(self):
        self.br_all, self.global_all = [], []
        self.proxy_file = os.path.join(DATA_DIR, 'proxies.json')
        self.blocked_proxies = self._load_blocked()
        self.update_lists()

    def _load_blocked(self):
        try:
            if os.path.exists(self.proxy_file):
                with open(self.proxy_file, 'r') as f:
                    data = json.load(f)
                    return set(data if isinstance(data, list) else [])
        except Exception: pass
        return set()

    def _save_blocked(self):
        try:
            with open(self.proxy_file, 'w') as f:
                json.dump(list(self.blocked_proxies), f)
        except Exception: pass

    def update_lists(self):
        # 5 fontes fixas (sempre atualiza)
        sources = [
            ("proxifly_br", "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json", "json"),
            ("zaeem_socks5", "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/refs/heads/master/socks5.txt", "socks5"),
            ("zaeem_https", "https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/refs/heads/master/https.txt", "http"),
            ("hproxy_br", "https://raw.githubusercontent.com/hproxy-com/free-proxy-list/refs/heads/main/by-country/BR.txt", "http"),
            ("databay_br", "https://raw.githubusercontent.com/databay-labs/free-proxy-list/refs/heads/master/by-country/br/http.txt", "http"),
        ]
        br_set = set()
        for name, url, ptype in sources:
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                if ptype == "json":
                    for item in resp.json():
                        if isinstance(item, dict) and item.get("proxy"):
                            br_set.add(item["proxy"])
                else:
                    for line in resp.text.splitlines():
                        line = line.strip()
                        if line and re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', line):
                            br_set.add(f"{ptype}://{line}")
                print(f"[ProxyManager] {name}: OK")
            except Exception as e:
                print(f"[ProxyManager] {name}: {e}")
        self.br_all = sorted(br_set)
        # Global mantém proxifly global
        try:
            gl_resp = requests.get('https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json', timeout=15)
            gl_resp.raise_for_status()
            self.global_all = [p['proxy'] for p in gl_resp.json() if isinstance(p, dict) and 'proxy' in p]
        except Exception as e:
            print(f"[ProxyManager] GLOBAL list fail: {e}")

    def get_proxy(self, force_global=False):
        # SEMPRE busca do github/proxifly atualizado antes de selecionar
        self.update_lists()
        if PROXY_URL: return PROXY_URL
        # Preferência: BR > global > direto; testa e mantém só ativos
        try:
            with open("/data/proxies_active.json") as f:
                active = json.load(f)
            if active:
                return random.choice(active)
        except:
            pass
        # Fallback: lista local (não bloqueados)
        pool = self.global_all if force_global else (self.br_all if self.br_all else self.global_all)
        if not pool: return None
        healthy = [p for p in pool if p not in self.blocked_proxies]
        return random.choice(healthy if healthy else pool)

    def mark_blocked(self, proxy):
        if proxy:
            self.blocked_proxies.add(proxy)
            self._save_blocked()

proxy_manager = ProxyManager()


# ============================================================
# Smart Proxy Tester - background loop to test proxies against YouTube
# ============================================================
class SmartProxyTester:
    """
    Testa proxies BR contra YouTube de forma inteligente:
    - Testa TODOS os proxies BR periodicamente
    - Mantém cache de proxies ativos (/data/proxies_active.json)
    - Re-testa proxies que estavam ativos mas falharam
    - Prioriza proxies com melhor histórico
    - Atualiza lista a cada 30 min (configurável)
    """
    def __init__(self, proxy_manager, test_interval=1800, max_active=20):
        self.pm = proxy_manager
        self.test_interval = test_interval  # 30 min
        self.max_active = max_active
        self.active_file = os.path.join(DATA_DIR, 'proxies_active.json')
        self.test_url = "https://www.youtube.com/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Connection": "keep-alive"
        }
        self._running = False
        self._thread = None

    def test_proxy(self, proxy_url, timeout=8):
        """Testa um único proxy contra YouTube. Retorna True se passa."""
        try:
            r = requests.get(
                self.test_url,
                proxies={"https": proxy_url, "http": proxy_url},
                timeout=timeout,
                headers=self.headers
            )
            text = r.text[:200].lower()
            return r.status_code == 200 and "sign in to confirm" not in text and "captcha" not in text and "unusual traffic" not in text
        except Exception:
            return False

    def load_active(self):
        """Carrega lista de proxies ativos do arquivo."""
        try:
            with open(self.active_file) as f:
                return json.load(f)
        except Exception:
            return []

    def save_active(self, active_list):
        """Salva lista de proxies ativos."""
        try:
            with open(self.active_file, 'w') as f:
                json.dump(active_list, f)
        except Exception:
            pass

    def test_all_br(self):
        """Testa TODOS os proxies BR e atualiza cache."""
        print("[SmartProxyTester] Iniciando teste de todos os proxies BR...")
        self.pm.update_lists()
        br_proxies = self.pm.br_all
        if not br_proxies:
            print("[SmartProxyTester] Nenhum proxy BR encontrado")
            return

        # Carregar ativos atuais para preservar histórico
        current_active = self.load_active()
        current_set = set(current_active)

        new_active = []
        tested = 0
        for proxy in br_proxies:
            # Pular se já está nos bloqueados
            if proxy in self.pm.blocked_proxies:
                continue
            
            # Testar
            if self.test_proxy(proxy):
                new_active.append(proxy)
                if proxy not in current_set:
                    print(f"[SmartProxyTester] NOVO ativo: {proxy}")
            else:
                # Marcar como bloqueado se falhou
                if proxy not in self.pm.blocked_proxies:
                    self.pm.blocked_proxies.add(proxy)
                    self.pm._save_blocked()
            
            tested += 1
            # Log de progresso a cada 50
            if tested % 50 == 0:
                print(f"[SmartProxyTester] Testados {tested}/{len(br_proxies)}, ativos: {len(new_active)}")

        # Combinar: novos ativos + atuais que ainda funcionam (re-testar atuais)
        # Re-testar atuais para garantir que ainda funcionam
        still_active = []
        for proxy in current_active:
            if proxy in new_active:
                still_active.append(proxy)
            elif self.test_proxy(proxy):
                still_active.append(proxy)
            else:
                # Parou de funcionar
                if proxy not in self.pm.blocked_proxies:
                    self.pm.blocked_proxies.add(proxy)
                    self.pm._save_blocked()
                print(f"[SmartProxyTester] EXPIROU: {proxy}")

        # Mesclar e limitar
        combined = list(dict.fromkeys(still_active + new_active))  # preserva ordem, remove duplicados
        final_active = combined[:self.max_active]
        self.save_active(final_active)
        print(f"[SmartProxyTester] Concluído: {len(final_active)} ativos salvos (testados {tested})")

    def start_background(self):
        """Inicia thread de background para testar periodicamente."""
        if self._running:
            return
        self._running = True
        
        def run_loop():
            while self._running:
                try:
                    self.test_all_br()
                except Exception as e:
                    print(f"[SmartProxyTester] Erro no loop: {e}")
                time.sleep(self.test_interval)
        
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        print("[SmartProxyTester] Thread de background iniciada")

    def stop_background(self):
        """Para a thread de background."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)


# Iniciar testador inteligente
smart_tester = SmartProxyTester(proxy_manager, test_interval=1800, max_active=20)
smart_tester.start_background()


# ============================================================
# Data manager: history (cap 100) + metrics + 30d stats
# ============================================================
HISTORY_CAP = 100
STATS_DAYS = 30
STATS_INTERVAL = 300  # 5 min between snapshots

class DataManager:
    def __init__(self):
        self.history_file = os.path.join(DATA_DIR, 'history.json')
        self.stats_file = os.path.join(DATA_DIR, 'stats30d.json')
        self.init_files()

    def init_files(self):
        for f, default in [(self.history_file, []), (self.stats_file, {"points": []})]:
            try:
                if not os.path.exists(f):
                    with open(f, 'w') as wf: json.dump(default, wf)
            except Exception: pass

    def add_download(self, entry):
        try:
            with open(self.history_file, 'r+') as f:
                data = json.load(f)
                if not isinstance(data, list): data = []
                data.insert(0, entry)
                if len(data) > HISTORY_CAP: data = data[:HISTORY_CAP]
                f.seek(0); json.dump(data, f); f.truncate()
        except Exception: pass

    def get_history(self):
        try:
            with open(self.history_file, 'r') as f: return json.load(f)
        except: return []

    def record_stats(self):
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            net = psutil.net_io_counters()
            point = {
                "ts": time.time(),
                "cpu": round(cpu, 1),
                "ram_pct": round(mem.percent, 1),
                "ram_used_mb": round(mem.used / (1024*1024), 0),
                "ram_total_mb": round(mem.total / (1024*1024), 0),
                "net_recv_mbps": round(net.bytes_recv / (1024*1024), 2),
                "net_sent_mbps": round(net.bytes_sent / (1024*1024), 2),
            }
            with open(self.stats_file, 'r+') as f:
                data = json.load(f)
                if not isinstance(data, dict): data = {"points": []}
                pts = data.get("points", [])
                pts.append(point)
                cutoff = time.time() - (STATS_DAYS * 86400)
                pts = [p for p in pts if p["ts"] >= cutoff]
                data["points"] = pts
                f.seek(0); json.dump(data, f, separators=(',', ':')); f.truncate()
        except Exception: pass

    def get_stats(self):
        try:
            with open(self.stats_file, 'r') as f:
                data = json.load(f)
            pts = data.get("points", [])
            daily = {}
            for p in pts:
                d = datetime.fromtimestamp(p["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
                daily.setdefault(d, []).append(p)
            daily_agg = []
            for d, arr in sorted(daily.items()):
                daily_agg.append({
                    "date": d,
                    "cpu_avg": round(sum(x["cpu"] for x in arr)/len(arr), 1),
                    "ram_avg_pct": round(sum(x["ram_pct"] for x in arr)/len(arr), 1),
                    "net_recv_gb": round(sum(x["net_recv_mbps"] for x in arr) * STATS_INTERVAL / (1024**2), 3),
                    "net_sent_gb": round(sum(x["net_sent_mbps"] for x in arr) * STATS_INTERVAL / (1024**2), 3),
                })
            return {"points": pts[-300:], "daily": daily_agg,
                    "current": pts[-1] if pts else None, "count": len(pts)}
        except Exception:
            return {"points": [], "daily": [], "current": None, "count": 0}

data_manager = DataManager()

def _stats_collector():
    while True:
        data_manager.record_stats()
        time.sleep(STATS_INTERVAL)

import threading
threading.Thread(target=_stats_collector, daemon=True).start()

# ============================================================
# Auth helpers
# ============================================================
def is_authenticated(request: Request):
    # Check cookie first (web UI)
    if request.cookies.get('auth_session') == 'authenticated':
        return True
    # Check Authorization header (API access)
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]  # Remove 'Bearer '
        if token == API_KEY:
            return True
    return False

def api_token_enabled():
    try:
        with open(API_ENABLED_FILE) as f:
            return json.load(f).get('enabled', True)
    except Exception:
        return True

def set_api_token(enabled: bool):
    with open(API_ENABLED_FILE, 'w') as f:
        json.dump({"enabled": enabled}, f)

def _detect_platform(url):
    u = (url or '').lower()
    for name in ['youtube', 'youtu.be', 'tiktok', 'instagram', 'twitter', 'x.com',
                 'facebook', 'vimeo', 'twitch', 'soundcloud']:
        if name in u: return name
    return 'other'

# ============================================================
# Streaming download (passa direto pro usuario, nao salva)
# ============================================================
def _build_cmd(url, fmt, proxy, q=0):
    cmd = ['yt-dlp', url, '--quiet', '--no-warnings', '--no-playlist', '-o', '-',
           '--retries', '3', '--fragment-retries', '3', '--socket-timeout', '30',
           '--extractor-args', 'youtube:player_client=android']
    if proxy:
        cmd += ['--proxy', proxy]
    if fmt == 'mp4':
        if q and q > 0:
            cmd += ['-f', f'bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]/best[height<={q}]']
        else:
            cmd += ['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best']
        cmd += ['--merge-output-format', 'mp4']
    else:
        cmd += ['-f', 'bestaudio', '-x', '--audio-format', 'mp3']
        if q and q > 0:
            cmd += ['--audio-quality', f'{q}k']
        else:
            cmd += ['--audio-quality', '192']
    return cmd

def stream_download(url, fmt, q=0, meta=None):
    attempts = [None, proxy_manager.get_proxy()] if proxy_manager.get_proxy() else [None]
    start = time.time()
    title = (meta or {}).get('title') if meta else None
    thumb = (meta or {}).get('thumbnail') if meta else None
    for attempt_proxy in attempts:
        bytes_sent = 0
        try:
            cmd = _build_cmd(url, fmt, attempt_proxy, q)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                bytes_sent += len(chunk)
                yield chunk
            proc.wait()
            dur = round(time.time() - start, 1)
            if bytes_sent == 0:
                if attempt_proxy:
                    proxy_manager.mark_blocked(attempt_proxy)
                continue
            data_manager.add_download({
                'url': url, 'format': fmt, 'size': bytes_sent, 'duration_s': dur,
                'timestamp': time.time(), 'status': 'completed', 'proxy': bool(attempt_proxy),
                'platform': _detect_platform(url), 'title': title, 'thumbnail': thumb,
                'quality': q,
            })
            return
        except Exception as e:
            with open(os.path.join(DATA_DIR, 'error.log'), 'a') as lf:
                lf.write(f"{time.time()} - stream error (proxy={attempt_proxy}): {traceback.format_exc()}\n")
            if attempt_proxy:
                proxy_manager.mark_blocked(attempt_proxy)
            continue
    dur = round(time.time() - start, 1)
    data_manager.add_download({
        'url': url, 'format': fmt, 'status': 'failed', 'error': 'sem bytes produzidos',
        'timestamp': time.time(), 'duration_s': dur, 'platform': _detect_platform(url),
        'title': title, 'thumbnail': thumb, 'quality': q,
    })

# ============================================================
# Routes
# ============================================================
@app.get('/test-health')
async def health():
    return {"status": "healthy",
            "ffmpeg": subprocess.run(['ffmpeg', '-version'], capture_output=True).returncode == 0}

@app.get('/')
async def root(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url='/login')
    return _serve_page('dashboard.html')

@app.get('/dashboard')
async def dashboard_page(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url='/login')
    return _serve_page('dashboard.html')

@app.get('/login')
async def login_page():
    return _serve_page('login.html')

@app.post('/login')
async def login(password: str = Form(...)):
    if password == APP_PASSWORD:
        response = JSONResponse(content={"status": "success", "message": "Authenticated"})
        response.set_cookie(key="auth_session", value="authenticated", httponly=True, samesite="lax")
        return response
    raise HTTPException(status_code=401, detail="Senha incorreta")

@app.get('/logout')
async def logout():
    response = RedirectResponse(url='/login')
    response.delete_cookie('auth_session')
    return response

@app.get('/api/me')
async def me(request: Request):
    if is_authenticated(request):
        return {"status": "ok", "authenticated": True}
    raise HTTPException(status_code=401, detail="Not authenticated")

@app.get('/api/download')
async def api_download(request: Request, url: str, fmt: str = 'mp4', q: int = 0, api_key: str = None):
    if not api_token_enabled():
        raise HTTPException(status_code=403, detail="API token desativada nas configuracoes")
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="API Key invalida")
    ext = 'mp3' if fmt == 'mp3' else 'mp4'
    return StreamingResponse(stream_download(url, fmt, q),
        media_type=("audio/mpeg" if fmt == 'mp3' else "video/mp4"),
        headers={"Content-Disposition": f'attachment; filename="media.{ext}"',
                 "Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _fetch_meta(url):
    """Busca titulo/thumbnail rapidamente (sem proxy) para o historico."""
    try:
        with yt_dlp.YoutubeDL({'proxy': None, 'quiet': True, 'no_warnings': True,
                               'noplaylist': True, 'format': 'best'}) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get('entries'): info = info['entries'][0]
            return {'title': info.get('title'), 'thumbnail': info.get('thumbnail'),
                    'view_count': info.get('view_count'), 'duration': info.get('duration')}
    except Exception:
        return None

@app.get('/stream')
async def stream(request: Request, url: str, fmt: str = 'mp4', q: int = 0):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    ext = 'mp3' if fmt == 'mp3' else 'mp4'
    fn = f"media_{int(time.time())}.{ext}"
    meta = _fetch_meta(url)
    return StreamingResponse(stream_download(url, fmt, q, meta),
        media_type=("audio/mpeg" if fmt == 'mp3' else "video/mp4"),
        headers={"Content-Disposition": f'attachment; filename="{fn}"',
                 "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
@app.get('/api/history')
async def get_history(request: Request):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return JSONResponse(content=data_manager.get_history())

@app.get('/api/stats')
async def get_stats(request: Request):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return JSONResponse(content=data_manager.get_stats())

@app.get('/api/proxies')
async def get_proxies(request: Request):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return JSONResponse(content={"active": smart_tester.load_active(),
        "active_count": len(smart_tester.load_active()),
        "blocked": list(proxy_manager.blocked_proxies),
        "br_count": len(proxy_manager.br_all), "global_count": len(proxy_manager.global_all)}
    )

@app.get('/api/info')
async def get_info(url: str):
    proxy = proxy_manager.get_proxy()
    attempts = [None, proxy] if proxy else [None]
    for px in attempts:
        try:
            with yt_dlp.YoutubeDL({'proxy': px, 'quiet': True, 'no_warnings': True,
                                   'noplaylist': True, 'format': 'best',
                                   'extractor_args': {'youtube': 'player_client=android'}}) as ydl:
                info = ydl.extract_info(url, download=False)
                if info.get('entries'): info = info['entries'][0]
                safe = {k: info.get(k) for k in ('title', 'duration', 'thumbnail',
                         'uploader', 'view_count', 'webpage_url') if k in info}
                # Lista de qualidades para o usuario escolher
                fmts = []
                seen = set()
                for f in info.get('formats', []):
                    if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                        h = f.get('height')
                        if h and h not in seen:
                            seen.add(h)
                            fmts.append({'height': h, 'note': f.get('format_note', f'{h}p'),
                                         'ext': f.get('ext', 'mp4')})
                    elif f.get('vcodec') != 'none' and f.get('acodec') == 'none':
                        h = f.get('height')
                        if h and h not in seen:
                            seen.add(h)
                            fmts.append({'height': h, 'note': f.get('format_note', f'{h}p'),
                                         'ext': f.get('ext', 'mp4'), 'video_only': True})
                fmts.sort(key=lambda x: x['height'], reverse=True)
                safe['formats'] = fmts[:12]
                safe['has_audio'] = any(f.get('acodec') != 'none' for f in info.get('formats', []))
                return safe
        except Exception:
            if px: proxy_manager.mark_blocked(px)
    raise HTTPException(status_code=500, detail="Falha ao obter informacoes")


@app.get('/api/transcript')
async def get_transcript(url: str):
    proxy = proxy_manager.get_proxy()
    attempts = [None, proxy] if proxy else [None]
    for px in attempts:
        try:
            with yt_dlp.YoutubeDL({'proxy': px, 'quiet': True, 'no_warnings': True,
                                   'noplaylist': True, 'writesubtitles': True,
                                   'writeautomaticsub': True, 'skip_download': True,
                                   'subtitlesformat': 'json3', 'outtmpl': '/tmp/tr_%(id)s'}) as ydl:
                info = ydl.extract_info(url, download=False)
                subs = info.get('subtitles', {}) or info.get('automatic_captions', {}) or {}
                if not subs:
                    return {'available': False, 'message': 'Sem transcricao disponivel'}
                lang = 'pt' if 'pt' in subs else (list(subs.keys())[0] if subs else None)
                if not lang:
                    return {'available': False, 'message': 'Sem transcricao disponivel'}
                return {'available': True, 'lang': lang, 'languages': list(subs.keys())}
        except Exception:
            if px: proxy_manager.mark_blocked(px)
    return {'available': False, 'message': 'Falha ao verificar transcricao'}

@app.get('/api/config/api')
async def get_api_config(request: Request):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    return {"enabled": api_token_enabled()}

@app.post('/api/config/api')
async def post_api_config(request: Request, enabled: bool = Form(...)):
    if not is_authenticated(request): raise HTTPException(status_code=401)
    set_api_token(enabled)
    return {"enabled": enabled}

app.mount("/static", StaticFiles(directory="static"), name="static")

# Page routes (shared layout)
@app.get('/transfers')
async def transfers_page(request: Request):
    if not is_authenticated(request): return RedirectResponse(url='/login')
    return _serve_page('transfers.html')

@app.get('/storage')
async def storage_page(request: Request):
    if not is_authenticated(request): return RedirectResponse(url='/login')
    return _serve_page('storage.html')

@app.get('/system')
async def system_page(request: Request):
    if not is_authenticated(request): return RedirectResponse(url='/login')
    return _serve_page('system.html')
