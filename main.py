import os, json, time, psutil, asyncio, subprocess, logging
from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yt_dlp

# --- CONFIG ---
APP_PASSWORD = os.getenv('APP_PASSWORD', 'admin123')
API_KEY = os.getenv('API_KEY', 'default_key_123')
DOWNLOAD_DIR = '/downloads'
DATA_DIR = '/data'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI()
executor = ThreadPoolExecutor(max_workers=10)

# --- PERSISTENCE (Circular Buffer 100) ---
class StatsManager:
    def __init__(self):
        self.history_file = os.path.join(DATA_DIR, 'history.json')
        self.metrics_file = os.path.join(DATA_DIR, 'metrics.json')
        self.proxy_file = os.path.join(DATA_DIR, 'proxies.json')
        self.limit = 100
        self.init_files()

    def init_files(self):
        try:
            for f in [self.history_file, self.metrics_file, self.proxy_file]:
                if not os.path.exists(f):
                    with open(f, 'w') as wf: json.dump([], wf)
        except Exception as e: print(f"Stats Init Error: {e}")

    def add_download(self, entry):
        try:
            with open(self.history_file, 'r+') as f:
                data = json.load(f)
                if not isinstance(data, list): data = []
                data.insert(0, entry)
                if len(data) > self.limit: data = data[:self.limit]
                f.seek(0); json.dump(data, f); f.truncate()
        except Exception: pass

    def add_metric(self, metric):
        try:
            with open(self.metrics_file, 'r+') as f:
                data = json.load(f)
                if not isinstance(data, list): data = []
                data.insert(0, metric)
                if len(data) > self.limit: data = data[:self.limit]
                f.seek(0); json.dump(data, f); f.truncate()
        except Exception: pass

    def update_proxies(self, proxy_data):
        try:
            with open(self.proxy_file, 'w') as f: json.dump(proxy_data, f)
        except Exception: pass

    def get_history(self):
        try:
            with open(self.history_file, 'r') as f: return json.load(f)
        except: return []

    def get_metrics(self):
        try:
            with open(self.metrics_file, 'r') as f: return json.load(f)
        except: return []

    def get_proxies(self):
        try:
            with open(self.proxy_file, 'r') as f: return json.load(f)
        except: return {}

stats_manager = StatsManager()

# --- YT-DLP LOGIC ---
def download_worker(url, fmt, api_key):
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
        'format': 'bestvideo+bestaudio/best' if fmt == 'mp4' else 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }] if fmt == 'mp3' else [],
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if fmt == 'mp3': filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            stats_manager.add_download({
                'title': info.get('title', 'Unknown'),
                'url': url,
                'format': fmt,
                'size': info.get('filesize', 0),
                'timestamp': time.time(),
                'status': 'completed'
            })
            return {"status": "success", "file": filename, "title": info.get('title')}
    except Exception as e:
        stats_manager.add_download({'url': url, 'status': 'failed', 'error': str(e), 'timestamp': time.time()})
        return {"status": "error", "message": str(e)}

# --- ENDPOINTS ---
@app.get('/test-health')
async def health(): return {"status": "healthy", "ffmpeg": subprocess.run(['ffmpeg', '-version'], capture_output=True).returncode == 0}

@app.get('/info')
async def get_info(url: str):
    try:
        with yt_dlp.YoutubeDL({}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@app.api_route('/download', methods=['GET', 'POST'])
async def download(url: str, fmt: str = 'mp4', api_key: str = None):
    if api_key != API_KEY: raise HTTPException(status_code=403, detail="Invalid API Key")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, download_worker, url, fmt, api_key)

@app.get('/api/stats/history')
async def get_history(): return stats_manager.get_history()

@app.get('/api/stats/metrics')
async def get_metrics(): return stats_manager.get_metrics()

@app.get('/api/stats/proxies')
async def get_proxies(): return stats_manager.get_proxies()

# --- BACKGROUND METRICS ---
async def metrics_loop():
    while True:
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        net = psutil.net_io_counters()
        stats_manager.add_metric({
            't': time.time(), 'cpu': cpu, 'ram': ram, 
            'sent': net.bytes_sent, 'recv': net.bytes_recv
        })
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(metrics_loop())

# --- STATIC FILES ---
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
