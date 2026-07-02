import os, json, time, psutil, asyncio, subprocess
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import yt_dlp

app = FastAPI()
executor = ThreadPoolExecutor(max_workers=10)

@app.get('/')
async def root(): return HTMLResponse("Server is UP and Running!")

@app.get('/test-health')
async def health(): return {"status": "healthy", "msg": "Minimal server is working"}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
