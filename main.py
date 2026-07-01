import random
import requests
import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import os

app = FastAPI(title="YT-DLP Ultra Server")
PROXY_LIST_URL = "https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/countries/BR/data.json"
DOWNLOAD_DIR = "/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class ProxyManager:
    def __init__(self):
        self.proxies = []
    def update_proxies(self):
        try:
            response = requests.get(PROXY_LIST_URL, timeout=10)
            data = response.json()
            self.proxies = [p['proxy'] for p in data if 'proxy' in p]
        except Exception as e:
            print(f"Erro: {e}")
    def get_random_proxy(self):
        if not self.proxies: self.update_proxies()
        return random.choice(self.proxies) if self.proxies else None

proxy_manager = ProxyManager()
proxy_manager.update_proxies()

def get_yt_dlp_options(proxy=None):
    options = {'quiet': True, 'no_warnings': True, 'format': 'best', 'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s'}
    if proxy: options['proxy'] = proxy
    return options

@app.get("/info")
def get_info(url: str):
    for i in range(5):
        proxy = proxy_manager.get_random_proxy()
        try:
            with yt_dlp.YoutubeDL(get_yt_dlp_options(proxy)) as ydl:
                info = ydl.extract_info(url, download=False)
                return {"title": info.get("title"), "duration": info.get("duration"), "proxy_used": proxy}
        except Exception: continue
    raise HTTPException(status_code=500, detail="Falha após 5 proxies.")

@app.get("/download")
def download_video(url: str, format: str = "mp4"):
    for i in range(10):
        proxy = proxy_manager.get_random_proxy()
        try:
            opts = get_yt_dlp_options(proxy)
            if format == "mp3":
                opts['format'] = 'bestaudio/best'
                opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if format == "mp3": filename = os.path.splitext(filename)[0] + ".mp3"
                return FileResponse(filename, media_type="application/octet-stream", filename=os.path.basename(filename))
        except Exception: continue
    raise HTTPException(status_code=500, detail="Falha no download.")

@app.get("/health")
def health():
    return {"status": "healthy", "proxies": len(proxy_manager.proxies)}

@app.post("/refresh-proxies")
def refresh():
    proxy_manager.update_proxies()
    return {"count": len(proxy_manager.proxies)}
