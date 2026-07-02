import os, json, random, asyncio, subprocess, signal, time, psutil
import redis, requests, yt_dlp
from fastapi import FastAPI, HTTPException, Request, Depends, status, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# --- CONFIG ---
PROXY_BR_URL = "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json"
PROXY_GLOBAL_URL = "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json"
HISTORY_FILE = "/data/history.json"
MAX_HISTORY = 100
os.makedirs("/data", exist_ok=True)
os.makedirs("/downloads", exist_ok=True)

APP_PASSWORD=os.get...D", "SageCore_2026_Ultra")
API_KEY=os.get...Y", "MediaCore_API_SAGE_99")
REDIS_URL = os.getenv("REDIS_URL", "redis://default:***@localhost:6379/0")
try:
    r = redis.from_url(REDIS_URL)
except: r = None

executor = ThreadPoolExecutor(max_workers=10)
async def run_ydlp(func):
    return await asyncio.get_event_loop().run_in_executor(executor, func)

# ===================== PROXY MANAGER =====================
class ProxyManager:
    def __init__(self):
        self.br_all, self.global_all = [], []
        self.blocked_count = 0
        self.last_update = 0
        self.update_lists()
    def update_lists(self):
        self.last_update = time.time()
        try:
            self.br_all = [p['proxy'] for p in requests.get(PROXY_BR_URL, timeout=10).json() if 'proxy' in p]
            self.global_all = [p['proxy'] for p in requests.get(PROXY_GLOBAL_URL, timeout=10).json() if 'proxy' in p]
        except: pass
    def get_proxy(self, platform, force_global=False):
        pool = self.global_all if force_global else (self.br_all if self.br_all else self.global_all)
        if not pool: return None
        healthy = [p for p in pool if not (r and r.get(f"blocked:{platform}:{p}"))]
        return random.choice(healthy if healthy else pool)
    def mark_blocked(self, proxy, platform):
        if r: r.setex(f"blocked:{platform}:{proxy}", 1800, "1")
        self.blocked_count += 1
    def mark_healthy(self, proxy, platform):
        if r: r.delete(f"blocked:{platform}:{proxy}")
    def get_ydlp_options(self, proxy):
        return {'quiet': True, 'no_warnings': True, 'format': 'best', 'outtmpl': f'/downloads/%(id)s.%(ext)s', 'proxy': proxy}

proxy_manager = ProxyManager()
app = FastAPI(docs_url="/api/docs")

# ===================== AUTH =====================
async def verify_auth(request: Request):
    if request.headers.get("X-API-Key") == API_KEY: return True
    if request.cookies.get("session") == "authenticated": return True
    raise HTTPException(status_code=401, detail="Unauthorized")

# ===================== HISTORY (JSON FILE) =====================
def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE) as f: return json.load(f)
    except: pass
    return []

def save_history(entry):
    history = load_history()
    history.insert(0, entry)
    if len(history) > MAX_HISTORY:
        history = history[:MAX_HISTORY]
    with open(HISTORY_FILE, "w") as f: json.dump(history, f, indent=2)

# ===================== PAGES =====================
def serve_html(name):
    path = f"static/{name}.html"
    try:
        with open(path, "r", encoding="utf-8") as f: return f.read()
    except: return HTMLResponse(f"{name}.html not found", status_code=404)

@app.get("/", response_class=HTMLResponse)
async def login(): return serve_html("login")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(auth=Depends(verify_auth)): return serve_html("dashboard")

@app.get("/transfers", response_class=HTMLResponse)
async def transfers(auth=Depends(verify_auth)): return serve_html("transfers")

@app.get("/storage", response_class=HTMLResponse)
async def storage(auth=Depends(verify_auth)): return serve_html("storage")

@app.get("/system", response_class=HTMLResponse)
async def system(auth=Depends(verify_auth)): return serve_html("system")

@app.post("/auth")
async def auth(request: Request):
    data = await request.json()
    if data.get("password") == APP_PASSWORD:
        res = JSONResponse({"status":"ok"})
        res.set_cookie(key="session", value="authenticated", httponly=True)
        return res
    raise HTTPException(status_code=401, detail="Wrong password")

# ===================== API: SYSTEM STATS =====================
@app.get("/api/system/stats")
async def system_stats(auth=Depends(verify_auth)):
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    boot = time.time() - psutil.boot_time()
    return {
        "cpu": {"percent": cpu, "cores": os.cpu_count()},
        "memory": {"total_gb": round(mem.total / 1e9, 1), "used_gb": round(mem.used / 1e9, 1), "percent": mem.percent},
        "disk": {"total_gb": round(disk.total / 1e9, 1), "used_gb": round(disk.used / 1e9, 1), "percent": disk.percent},
        "network": {"sent_mb": round(net.bytes_sent / 1e6, 1), "recv_mb": round(net.bytes_recv / 1e6, 1)},
        "uptime_hours": round(boot / 3600, 1), "temp": round(psutil.sensors_temperatures().get("coretemp", [{}])[0].get("current", 0), 1)
    }

# ===================== API: NETWORK =====================
@app.get("/api/network")
async def network_stats(auth=Depends(verify_auth)):
    net = psutil.net_io_counters()
    return {
        "total_sent_gb": round(net.bytes_sent / 1e9, 2),
        "total_recv_gb": round(net.bytes_recv / 1e9, 2),
        "packets_sent": net.packets_sent,
        "packets_recv": net.packets_recv,
        "speed_up": 0, "speed_down": 0
    }

# ===================== API: PROXIES =====================
@app.get("/api/proxies")
async def proxy_status(auth=Depends(verify_auth)):
    proxy_manager.update_lists()
    return {
        "br_count": len(proxy_manager.br_all),
        "global_count": len(proxy_manager.global_all),
        "blocked_count": proxy_manager.blocked_count,
        "last_update": proxy_manager.last_update,
        "br_pool": proxy_manager.br_all[:10],
        "global_pool": proxy_manager.global_all[:10]
    }

# ===================== API: HISTORY =====================
@app.get("/api/history")
async def get_history(auth=Depends(verify_auth)):
    return load_history()

@app.delete("/api/history")
async def clear_history(auth=Depends(verify_auth)):
    with open(HISTORY_FILE, "w") as f: json.dump([], f)
    return {"status": "cleared"}

@app.get("/api/queue")
async def get_queue(auth=Depends(verify_auth)):
    return {"active": [], "queued": 0}

# ===================== INFO / DOWNLOAD (yt-dlp streaming) =====================
active_downloads = {}

@app.get("/info")
async def get_info(url: str = Query(...), net: str = "auto", auth=Depends(verify_auth)):
    for i in range(15):
        proxy = proxy_manager.get_proxy("general", net == "global")
        if not proxy: continue
        try:
            def extract():
                with yt_dlp.YoutubeDL(proxy_manager.get_ydlp_options(proxy)) as ydl:
                    return ydl.extract_info(url, download=False)
            info = await run_ydlp(extract)
            proxy_manager.mark_healthy(proxy, "general")
            return {"title": info.get("title"), "duration": info.get("duration_string"), "thumbnail": info.get("thumbnail"),
                    "proxy": proxy, "status": "success", "webpage_url": info.get("webpage_url"), "ext": info.get("ext"),
                    "filesize": info.get("filesize") or info.get("filesize_approx")}
        except:
            proxy_manager.mark_blocked(proxy, "general")
            continue
    raise HTTPException(status_code=500, detail="Proxy failure")

@app.get("/download")
async def download_video(url: str = Query(...), fmt: str = "mp4", net: str = "auto", auth=Depends(verify_auth)):
    platform = "general"
    for i in range(20):
        proxy = proxy_manager.get_proxy(platform, net == "global")
        if not proxy: continue
        try:
            filename = f"/downloads/stream_{random.randint(10000,99999)}.%(ext)s"
            opts = {'quiet': True, 'no_warnings': True, 'format': 'best', 'outtmpl': filename, 'proxy': proxy}
            if fmt == "mp3":
                opts['format'] = 'bestaudio/best'
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
            def dl():
                with yt_dlp.YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=True)
            info = await run_ydlp(dl)
            actual = yt_dlp.YoutubeDL(opts).prepare_filename(info)
            if fmt == "mp3": actual = os.path.splitext(actual)[0] + ".mp3"
            fsize = os.path.getsize(actual)
            save_history({"url": url, "title": info.get("title",""), "format": fmt, "size": fsize, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "completed"})
            def iterfile():
                with open(actual, "rb") as f:
                    while chunk := f.read(1024*1024): yield chunk
                try: os.remove(actual)
                except: pass
            return StreamingResponse(iterfile(), media_type="application/octet-stream", filename=os.path.basename(actual))
        except:
            proxy_manager.mark_blocked(proxy, platform)
            continue
    raise HTTPException(status_code=500, detail="Download failure")

@app.get("/health")
def health():
    return {"status":"healthy","br":len(proxy_manager.br_all),"gl":len(proxy_manager.global_all),"redis":"connected" if r else "disconnected"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
