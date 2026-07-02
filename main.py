import os, json, random, asyncio, subprocess, signal, time, psutil
import redis, requests, yt_dlp
from fastapi import FastAPI, HTTPException, Request, Depends, status, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Configurações de Caminhos e Limites
HISTORY_FILE = '/data/history.json'
NET_STATS_FILE = '/data/network_stats.json'
MAX_HISTORY = 100
os.makedirs('/data', exist_ok=True)
os.makedirs('/downloads', exist_ok=True)

APP_PASSWORD = os.environ.get('APP_PASSWORD', 'admin')
API_KEY = os.environ.get('API_KEY', 'secret')
REDIS_URL = os.getenv('REDIS_URL', 'redis://default:***@localhost:6379/0')

try:
    r = redis.from_url(REDIS_URL)
except Exception:
    r = None

executor = ThreadPoolExecutor(max_workers=10)
async def run_ydlp(func):
    return await asyncio.get_event_loop().run_in_executor(executor, func)

# --- GESTÃO DE CONTAINER (Cgroups) ---
class ContainerMetrics:
    @staticmethod
    def get_cpu_usage():
        try:
            # Tenta Cgroup v2
            with open('/sys/fs/cgroup/cpu.stat', 'r') as f:
                for line in f:
                    if line.startswith('usage_usec'):
                        return int(line.split()[1]) / 1000000 # Convert para segundos
        except:
            try:
                # Fallback Cgroup v1
                with open('/sys/fs/cgroup/cpuacct/cpuacct.usage', 'r') as f:
                    return int(f.read().strip()) / 1000000000 # Nano para segundos
            except: pass
        return 0

    @staticmethod
    def get_mem_usage():
        try:
            with open('/sys/fs/cgroup/memory.current', 'r') as f:
                current = int(f.read().strip())
            with open('/sys/fs/cgroup/memory.max', 'r') as f:
                limit = int(f.read().strip())
            return {'used_gb': round(current/1e9, 2), 'total_gb': round(limit/1e9, 2), 'percent': round((current/limit)*100, 1)}
        except:
            # Fallback para psutil se cgroups falharem (menos preciso para container)
            mem = psutil.virtual_memory()
            return {'used_gb': round(mem.used/1e9, 2), 'total_gb': round(mem.total/1e9, 2), 'percent': mem.percent}

    @staticmethod
    def get_net_io():
        try:
            # Lê a interface eth0 do container
            with open('/proc/net/dev', 'r') as f:
                for line in f:
                    if 'eth0' in line:
                        parts = line.split(':')[1].split()
                        return {'recv_mb': round(int(parts[0])/1e6, 2), 'sent_mb': round(int(parts[8])/1e6, 2)}
        except: pass
        return {'recv_mb': 0, 'sent_mb': 0}

metrics = ContainerMetrics()

# --- PROXY MANAGER ---
class ProxyManager:
    def __init__(self):
        self.br_all, self.global_all = [], []
        self.blocked_count = 0
        self.last_update = 0
        self.update_lists()
    def update_lists(self):
        self.last_update = time.time()
        try:
            self.br_all = [p['proxy'] for p in requests.get('https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json', timeout=10).json() if 'proxy' in p]
            self.global_all = [p['proxy'] for p in requests.get('https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json', timeout=10).json() if 'proxy' in p]
        except Exception: pass
    def get_proxy(self, platform, force_global=False):
        pool = self.global_all if force_global else (self.br_all if self.br_all else self.global_all)
        if not pool: return None
        healthy = [p for p in pool if not (r and r.get(f'blocked:{platform}:{p}'))]
        return random.choice(healthy if healthy else pool)
    def mark_blocked(self, proxy, platform):
        if r: r.setex(f'blocked:{platform}:{proxy}', 1800, '1')
        self.blocked_count += 1

class StatsManager:
    def __init__(self):
        self.net_file = '/data/network_stats.json'
        self.sys_file = '/data/system_metrics.json'
        self.init_files()

    def init_files(self):
        for f in [self.net_file, self.sys_file]:
            if not os.path.exists(f):
                with open(f, 'w') as wf: json.dump({}, wf)

    def update_net(self, sent, recv):
        try:
            with open(self.net_file, 'r+') as f:
                data = json.load(f)
                data['total_sent'] = data.get('total_sent', 0) + sent
                data['total_recv'] = data.get('total_recv', 0) + recv
                f.seek(0); json.dump(data, f); f.truncate()
        except: pass

    def log_metrics(self, cpu, mem):
        try:
            with open(self.sys_file, 'r+') as f:
                data = json.load(f)
                history = data.get('history', [])
                history.append({'t': time.time(), 'c': cpu, 'm': mem})
                if len(history) > 1000: history = history[-1000:]
                data['history'] = history
                f.seek(0); json.dump(data, f); f.truncate()
        except: pass

stats_manager = StatsManager()

# --- AUTH ---
def verify_auth(request: Request):
    if request.headers.get('X-API-Key') == API_KEY: return True
    if request.cookies.get('session') == 'authenticated': return True
    raise HTTPException(status_code=401, detail='Unauthorized')

# --- DATA HELPERS ---
def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f: return json.load(f)
    except Exception: pass
    return []

def save_history(entry):
    history = load_history()
    history.insert(0, entry)
    if len(history) > MAX_HISTORY: history = history[:MAX_HISTORY]
    with open(HISTORY_FILE, 'w') as f: json.dump(history, f, indent=2)

def serve_html(name):
    try:
        with open(f'static/{name}.html', encoding='utf-8') as f: 
            html = f.read()
        now = __import__('datetime').datetime.now()
        html = html.replace('<!--YEAR-->', str(now.year))
        return HTMLResponse(html)
    except Exception:
        return HTMLResponse('not found', status_code=404)

# --- ROUTES ---
@app.get('/', response_class=HTMLResponse)
async def login_page(): return serve_html('login')

@app.get('/dashboard', response_class=HTMLResponse)
async def dash_page(auth=Depends(verify_auth)): return serve_html('dashboard')

@app.get('/transfers', response_class=HTMLResponse)
async def xfer_page(auth=Depends(verify_auth)): return serve_html('transfers')

@app.get('/storage', response_class=HTMLResponse)
async def stor_page(auth=Depends(verify_auth)): return serve_html('storage')

@app.get('/system', response_class=HTMLResponse)
async def sys_page(auth=Depends(verify_auth)): return serve_html('system')

@app.post('/auth')
async def auth_login(request: Request):
    data = await request.json()
    if data.get('password') == APP_PASSWORD:
        res = JSONResponse({'status':'ok'})
        res.set_cookie(key='session', value='authenticated', httponly=True, max_age=2592000)
        return res
    raise HTTPException(status_code=401, detail='Wrong password')

# --- API SYSTEM (CONTAINER-SENSITIVE) ---
@app.get('/api/system/stats')
async def api_system_stats(auth=Depends(verify_auth)):
    # Cálculo de CPU do container (Delta de tempo)
    t1 = metrics.get_cpu_usage()
    await asyncio.sleep(0.1)
    t2 = metrics.get_cpu_usage()
    cpu_perc = round(((t2 - t1) / 0.1) * 100, 1)
    if cpu_perc > 100: cpu_perc = 100.0 # Cap for single core logic

    mem = metrics.get_mem_usage()
    net = metrics.get_net_io()
    boot = time.time() - psutil.boot_time()
    
    return {
        'cpu': {'percent': cpu_perc, 'cores': os.cpu_count()},
        'memory': {'total_gb': mem['total_gb'], 'used_gb': mem['used_gb'], 'percent': mem['percent']},
        'network': {'sent_mb': net['sent_mb'], 'recv_mb': net['recv_mb']},
        'uptime_hours': round(boot/3600, 1)
    }

@app.get('/api/history')
async def api_history(auth=Depends(verify_auth)): return load_history()

@app.delete('/api/history')
async def api_history_clear(auth=Depends(verify_auth)):
    with open(HISTORY_FILE, 'w') as f: json.dump([], f)
    return {'status': 'cleared'}

@app.get('/info')
async def get_info(url: str = Query(...), net: str = 'auto', auth=Depends(verify_auth)):
    for i in range(15):
        proxy = proxy_manager.get_proxy('general', net == 'global')
        if not proxy: continue
        try:
            def extract():
                opts = {'quiet': True, 'no_warnings': True, 'format': 'best', 'outtmpl': f'/downloads/%(id)s.%(ext)s', 'proxy': proxy}
                with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=False)
            info = await run_ydlp(extract)
            return {'title': info.get('title'), 'duration': info.get('duration_string'), 'thumbnail': info.get('thumbnail'), 'proxy': proxy, 'status': 'success'}
        except:
            proxy_manager.mark_blocked(proxy, 'general')
            continue
    raise HTTPException(status_code=500, detail='Proxy failure')

@app.get('/download')
async def download_video(url: str = Query(...), fmt: str = 'mp4', net: str = 'auto', auth=Depends(verify_auth)):
    for i in range(20):
        proxy = proxy_manager.get_proxy('general', net == 'global')
        if not proxy: continue
        try:
            import uuid
            vid = str(uuid.uuid4())[:8]
            opts = {'quiet': True, 'no_warnings': True, 'format': 'best', 'outtmpl': f'/downloads/{vid}.%(ext)s', 'proxy': proxy}
            if fmt == 'mp3':
                opts['format'] = 'bestaudio/best'
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
            def dl():
                with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=True)
            info = await run_ydlp(dl)
            fp = f'/downloads/{vid}.{info.get("ext", "mp4")}' if fmt != 'mp3' else f'/downloads/{vid}.mp3'
            fsize = os.path.getsize(fp) if os.path.exists(fp) else 0
            save_history({'url':url,'title':info.get('title',''),'format':fmt,'size':fsize,'time':__import__('datetime').datetime.now().isoformat(),'status':'completed'})
            def iterfile():
                with open(fp, 'rb') as fh:
                    while chunk := fh.read(1048576): yield chunk
                try: os.remove(fp)
                except: pass
            return StreamingResponse(iterfile(), media_type='application/octet-stream', headers={'Content-Disposition': f'attachment; filename="{info.get("title", "video")}.{fmt}"'})
        except:
            proxy_manager.mark_blocked(proxy, 'general')
            continue
    raise HTTPException(status_code=500, detail='Download failure')

@app.get('/test-health')
def test_health():
    try:
        import yt_dlp
        ffmpeg_check = subprocess.run(['ffmpeg', '-version'], capture_output=True)
        return {
            'status': 'operational',
            'yt_dlp_version': yt_dlp.__version__,
            'ffmpeg': 'installed' if ffmpeg_check.returncode == 0 else 'missing',
            'proxy_count': len(proxy_manager.br_all) + len(proxy_manager.global_all)
        }
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}

@app.get('/health')
def health():
    return {'status':'healthy','br':len(proxy_manager.br_all),'gl':len(proxy_manager.global_all)}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
