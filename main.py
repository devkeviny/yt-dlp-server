import os, json, time, psutil, asyncio, subprocess, logging, random, requests, traceback
from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yt_dlp

APP_PASSWORD = os.getenv('APP_PASSWORD', 'admin123')
API_KEY = os.getenv('API_KEY', 'default_key_123')
PROXY_URL = os.getenv('PROXY_URL')
STITCH_API_KEY = os.getenv('STITCH_API_KEY', '')
DOWNLOAD_DIR = '/downloads'
DATA_DIR = '/data'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI()

def _serve_page(filename):
    """Serve static HTML injecting STITCH_API_KEY from env (keeps secret out of git)."""
    path = os.path.join("static", filename)
    with open(path, "r") as f:
        html = f.read()
    html = html.replace("STITCH_API_KEY_PLACEHOLDER", STITCH_API_KEY)
    # also expose as window.STITCH_API_KEY for stitch-client.js
    html = html.replace(
        '<meta name="stitch-api-key" content="STITCH_API_KEY_PLACEHOLDER"/>',
        '<script>window.STITCH_API_KEY="' + STITCH_API_KEY + '";</script>\n<meta name="stitch-api-key" content="' + STITCH_API_KEY + '"/>'
    )
    return HTMLResponse(content=html)

executor = ThreadPoolExecutor(max_workers=10)

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

    def get_proxy(self, platform, force_global=False):
        if PROXY_URL: return PROXY_URL
        pool = self.global_all if force_global else (self.br_all if self.br_all else self.global_all)
        if not pool: return None
        healthy = [p for p in pool if p not in self.blocked_proxies]
        return random.choice(healthy if healthy else pool)

    def mark_blocked(self, proxy, platform):
        self.blocked_proxies.add(proxy)
        self._save_blocked()

proxy_manager = ProxyManager()

class DataManager:
    def __init__(self):
        self.history_file = os.path.join(DATA_DIR, 'history.json')
        self.network_file = os.path.join(DATA_DIR, 'network.json')
        self.init_files()

    def init_files(self):
        try:
            for f in [self.history_file, self.network_file]:
                if not os.path.exists(f):
                    with open(f, 'w') as wf: json.dump([], wf)
        except Exception: pass

    def add_download(self, entry):
        try:
            with open(self.history_file, 'r+') as f:
                data = json.load(f)
                if not isinstance(data, list): data = []
                data.insert(0, entry)
                if len(data) > 100: data = data[:100]
                f.seek(0); json.dump(data, f); f.truncate()
        except Exception: pass

    def update_network(self, bytes_sent, bytes_recv):
        try:
            with open(self.network_file, 'w') as f:
                json.dump({'sent': bytes_sent, 'recv': bytes_recv, 'timestamp': time.time()}, f)
        except Exception: pass

    def get_history(self):
        try:
            with open(self.history_file, 'r') as f: return json.load(f)
        except: return []

    def get_network(self):
        try:
            with open(self.network_file, 'r') as f: return json.load(f)
        except: return {'sent': 0, 'recv': 0}

data_manager = DataManager()

def download_worker(url, fmt, api_key):
    proxy = proxy_manager.get_proxy('general')
    net_start = psutil.net_io_counters()
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'format': 'bestvideo+bestaudio/best' if fmt == 'mp4' else 'bestaudio/best',
        'proxy': proxy,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}] if fmt == 'mp3' else [],
        'quiet': True, 'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if fmt == 'mp3': filename = filename.rsplit('.', 1)[0] + '.mp3'
            net_end = psutil.net_io_counters()
            data_manager.update_network(net_end.bytes_sent - net_start.bytes_sent, net_end.bytes_recv - net_start.bytes_recv)
            data_manager.add_download({'title': info.get('title', 'Unknown'), 'url': url, 'format': fmt, 'size': info.get('filesize', 0), 'timestamp': time.time(), 'status': 'completed'})
            return {"status": "success", "file": filename, "title": info.get('title')}
    except Exception as e_proxy:
        # Fallback: tenta de novo sem proxy se um proxy foi usado
        if proxy:
            try:
                ydl_opts['proxy'] = None
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    if fmt == 'mp3': filename = filename.rsplit('.', 1)[0] + '.mp3'
                    net_end = psutil.net_io_counters()
                    data_manager.update_network(net_end.bytes_sent - net_start.bytes_sent, net_end.bytes_recv - net_start.bytes_recv)
                    data_manager.add_download({'title': info.get('title', 'Unknown'), 'url': url, 'format': fmt, 'size': info.get('filesize', 0), 'timestamp': time.time(), 'status': 'completed'})
                    return {"status": "success", "file": filename, "title": info.get('title')}
            except Exception:
                pass
        with open(os.path.join(DATA_DIR, 'error.log'), 'a') as lf: lf.write(f"{time.time()} - {traceback.format_exc()}\n")
        data_manager.add_download({'url': url, 'status': 'failed', 'error': str(e_proxy), 'timestamp': time.time()})
        return {"status": "error", "message": str(e_proxy)}
    except Exception as e:
        with open(os.path.join(DATA_DIR, 'error.log'), 'a') as lf: lf.write(f"{time.time()} - {traceback.format_exc()}\n")
        data_manager.add_download({'url': url, 'status': 'failed', 'error': str(e), 'timestamp': time.time()})
        return {"status": "error", "message": str(e)}

@app.get('/test-health')
async def health(): return {"status": "healthy", "ffmpeg": subprocess.run(['ffmpeg', '-version'], capture_output=True).returncode == 0}

@app.get('/')
async def root():
    return _serve_page('index.html')

@app.get('/login')
async def login_page():
    return _serve_page('login.html')

@app.post('/login')
async def login(password: str = Form(...)):
    if password == APP_PASSWORD:
        response = JSONResponse(content={"status": "success", "message": "Authenticated"})
        response.set_cookie(key="auth_session", value="authenticated", httponly=True)
        return response
    raise HTTPException(status_code=401, detail="Invalid Password")

@app.get('/info')
async def get_info(url: str):
    try:
        with yt_dlp.YoutubeDL({}) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception: pass
    for i in range(15):
        proxy = proxy_manager.get_proxy('general')
        if not proxy: continue
        try:
            with yt_dlp.YoutubeDL({'proxy': proxy}) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception:
            proxy_manager.mark_blocked(proxy, 'general')
            continue
    with open(os.path.join(DATA_DIR, 'error.log'), 'a') as lf: lf.write(f"{time.time()} - Info failed all attempts\n")
    raise HTTPException(status_code=500, detail="Proxy and No-Proxy failure")

@app.api_route('/download', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'])
async def download(url: str, fmt: str = 'mp4', api_key: str = None):
    if api_key != API_KEY: raise HTTPException(status_code=403, detail="Invalid API Key")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, download_worker, url, fmt, api_key)

@app.get('/api/stats/history')
async def get_history(): return data_manager.get_history()

@app.get('/api/stats/network')
async def get_network(): return data_manager.get_network()

@app.get('/api/stats/proxies')
async def get_proxies(): return list(proxy_manager.blocked_proxies)

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
