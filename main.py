import os
import random
import asyncio
import redis
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

# --- CONFIGURAÇÕES ---
PROXY_BR_URL = "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json"
PROXY_GLOBAL_URL = "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.json"
DOWNLOAD_DIR = "/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

APP_PASSWORD = os.getenv("APP_PASSWORD", "SageCore_2026_Ultra")
API_KEY = os.getenv("API_KEY", "MediaCore_API_SAGE_99")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- GESTÃO DE PROXIES ---
class ProxyManager:
    def __init__(self):
        self.r = None
        try:
            self.r = redis.from_url(REDIS_URL, decode_responses=True)
        except:
            print("Redis não conectado.")
        self.br_all = []
        self.global_all = []
        self.update_lists()

    def update_lists(self):
        try:
            br_data = requests.get(PROXY_BR_URL, timeout=10).json()
            self.br_all = [p['proxy'] for p in br_data if 'proxy' in p]
            gl_data = requests.get(PROXY_GLOBAL_URL, timeout=10).json()
            self.global_all = [p['proxy'] for p in gl_data if 'proxy' in p]
        except:
            print("Erro ao atualizar listas de proxies.")

    def get_proxy(self, platform, force_global=False):
        pool = self.global_all if force_global else (self.br_all if self.br_all else self.global_all)
        if not pool: 
            return None
        
        # Tenta pegar um que não esteja bloqueado para a plataforma no Redis
        attempts = 0
        while attempts < 20:
            proxy = random.choice(pool)
            if self.r and self.r.get(f"blocked:{platform}:{proxy}"):
                attempts += 1
                continue
            return proxy
        return random.choice(pool)  # Fallback

    def mark_blocked(self, proxy, platform):
        if self.r:
            self.r.setex(f"blocked:{platform}:{proxy}", 1800, "1")  # Bloqueia por 30min

    def mark_healthy(self, proxy, platform):
        if self.r:
            self.r.delete(f"blocked:{platform}:{proxy}")

def get_ydlp_options(proxy):
    return {
        'quiet': True, 
        'no_warnings': True, 
        'format': 'best', 
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s', 
        'proxy': proxy
    }

proxy_manager = ProxyManager()
executor = ThreadPoolExecutor(max_workers=10)

async def run_ydlp(func):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, func)

# --- APP FASTAPI ---
app = FastAPI()

# Servir arquivos estáticos (HTMLs)
app.mount("/static", StaticFiles(directory="static"), name="static")

async def verify_auth(request: Request):
    # 1. Verifica API Key no Header
    api_key = request.headers.get("X-API-Key")
    if api_key == API_KEY:
        return True
    
    # 2. Verifica Cookie de Sessão
    session = request.cookies.get("session")
    if session == "authenticated":
        return True
    
    # 3. Se não for API nem Sessão, bloqueia (exceto rotas públicas)
    raise HTTPException(status_code=401, detail="Não autorizado. Por favor, faça login.")

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/login.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(auth=Depends(verify_auth)):
    with open("static/dashboard.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/auth")
async def auth(request: Request):
    data = await request.json()
    if data.get("password") == APP_PASSWORD:
        response = Response(content='{"status":"ok"}', media_type="application/json")
        response.set_cookie(key="session", value="authenticated", httponly=True)
        return response
    raise HTTPException(status_code=401, detail="Senha incorreta")

@app.get("/info")
async def get_info(url: str, net: str = "auto", auth=Depends(verify_auth)):
    platform = "general"
    force_global = (net == "global")
    for i in range(15):
        proxy = proxy_manager.get_proxy(platform, force_global)
        if not proxy: 
            continue
        try:
            def extract():
                with yt_dlp.YoutubeDL(get_ydlp_options(proxy)) as ydl:
                    return ydl.extract_info(url, download=False)
            info = await run_ydlp(extract)
            proxy_manager.mark_healthy(proxy, platform)
            return {
                "title": info.get("title"), 
                "duration": info.get("duration"), 
                "thumbnail": info.get("thumbnail"), 
                "proxy": proxy, 
                "status": "success"
            }
        except:
            proxy_manager.mark_blocked(proxy, platform)
            continue
    raise HTTPException(status_code=500, detail="Falha após 15 tentativas de proxy.")

@app.get("/download")
async def download_video(url: str, format: str = "mp4", net: str = "auto", auth=Depends(verify_auth)):
    platform = "general"
    force_global = (net == "global")
    for i in range(20):
        proxy = proxy_manager.get_proxy(platform, force_global)
        if not proxy: 
            continue
        try:
            opts = get_ydlp_options(proxy)
            if format == "mp3":
                opts['format'] = 'bestaudio/best'
                opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio', 
                    'preferredcodec': 'mp3', 
                    'preferredquality': '192'
                }]
            def dl():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)
            filename = await run_ydlp(dl)
            if format == "mp3": 
                filename = os.path.splitext(filename)[0] + ".mp3"
            def iterfile():
                with open(filename, mode="rb") as f:
                    while chunk := f.read(1024 * 1024): 
                        yield chunk
                os.remove(filename)
            return StreamingResponse(
                iterfile(), 
                media_type="application/octet-stream", 
                headers={"Content-Disposition": f"attachment; filename={os.path.basename(filename)}"}
            )
        except:
            proxy_manager.mark_blocked(proxy, platform)
            continue
    raise HTTPException(status_code=500, detail="Falha no download após 20 tentativas.")

@app.get("/health")
def health():
    return {
        "status": "healthy", 
        "br_pool": len(proxy_manager.br_all), 
        "gl_pool": len(proxy_manager.global_all)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
