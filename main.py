import os, json, time, psutil, asyncio, subprocess, logging, random, requests, traceback
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
        try:
            self.br_all = [p['proxy'] for p in requests.get('https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json', timeout=10).json() if 'proxy' in p]
            self.global_all = [p['proxy'] for p in requests.get('https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json', timeout=10).json() if 'proxy' in p]
        except Exception: pass

    def get_proxy(self, force_global=False):
        if PROXY_URL: return PROXY_URL
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
    return request.cookies.get('auth_session') == 'authenticated'

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
def stream_download(url, fmt):
    proxy = proxy_manager.get_proxy()
    start = time.time()
    bytes_sent = 0
    try:
        cmd = ['yt-dlp', url, '--quiet', '--no-warnings', '--no-playlist', '-o', '-']
        if proxy:
            cmd += ['--proxy', proxy]
        if fmt == 'mp4':
            cmd += ['-f', 'bestvideo+bestaudio/best']
        else:
            cmd += ['-f', 'bestaudio', '-x', '--audio-format', 'mp3', '--audio-quality', '192']
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            bytes_sent += len(chunk)
            yield chunk
        proc.wait()
        dur = round(time.time() - start, 1)
        data_manager.add_download({
            'url': url, 'format': fmt, 'size': bytes_sent, 'duration_s': dur,
            'timestamp': time.time(), 'status': 'completed',
            'platform': _detect_platform(url),
        })
    except Exception as e:
        with open(os.path.join(DATA_DIR, 'error.log'), 'a') as lf:
            lf.write(f"{time.time()} - stream error: {traceback.format_exc()}\n")
        data_manager.add_download({
            'url': url, 'format': fmt, 'status': 'failed', 'error': str(e),
            'timestamp': time.time(), 'platform': _detect_platform(url),
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
async def api_download(request: Request, url: str, fmt: str = 'mp4', api_key: str = None):
    if not api_token_enabled():
        raise HTTPException(status_code=403, detail="API token desativada nas configuracoes")
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="API Key invalida")
    ext = 'mp3' if fmt == 'mp3' else 'mp4'
    return StreamingResponse(stream_download(url, fmt),
        media_type=("audio/mpeg" if fmt == 'mp3' else "video/mp4"),
        headers={"Content-Disposition": f'attachment; filename="media.{ext}"',
                 "Cache-Control": "no-store", "X-Accel-Buffering": "no"})

@app.get('/stream')
async def stream(request: Request, url: str, fmt: str = 'mp4'):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")
    ext = 'mp3' if fmt == 'mp3' else 'mp4'
    fn = f"media_{int(time.time())}.{ext}"
    return StreamingResponse(stream_download(url, fmt),
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
    return JSONResponse(content={"blocked": list(proxy_manager.blocked_proxies),
        "br_count": len(proxy_manager.br_all), "global_count": len(proxy_manager.global_all)})

@app.get('/api/info')
async def get_info(url: str):
    proxy = proxy_manager.get_proxy()
    attempts = [proxy, None] if proxy else [None]
    for px in attempts:
        try:
            with yt_dlp.YoutubeDL({'proxy': px, 'quiet': True, 'no_warnings': True,
                                   'noplaylist': True, 'format': 'best'}) as ydl:
                info = ydl.extract_info(url, download=False)
                if info.get('entries'): info = info['entries'][0]
                safe = {k: info.get(k) for k in ('title', 'duration', 'thumbnail',
                         'uploader', 'view_count', 'webpage_url') if k in info}
                return safe
        except Exception:
            if px: proxy_manager.mark_blocked(px)
    raise HTTPException(status_code=500, detail="Falha ao obter informacoes")

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
