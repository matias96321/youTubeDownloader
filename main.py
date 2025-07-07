import asyncio
from queue import Queue
from apscheduler.schedulers.background import BackgroundScheduler
import os, time
import re
import threading
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi import Limiter
from yt_dlp import YoutubeDL
import os

app = FastAPI()

# Configurar rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
scheduler = BackgroundScheduler()

COOKIES_PATH = os.getenv("COOKIES_PATH")

clients = {}

download_phase = {
    "current": "video",
    "video_done": False
}

@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc):
    return JSONResponse(
        status_code=429,
        content={"detail": "Limite de requisições excedido. Tente novamente mais tarde."}
    )

@app.websocket("/ws/progress")
async def progress_ws(websocket: WebSocket):
    await websocket.accept()
    client_ip = websocket.client.host
    clients[client_ip] = websocket
    
    try:
        while True:
            await asyncio.sleep(1)  # Mantém conexão aberta
    except:
        clients.pop(client_ip, None)

@app.get("/api/download")
@limiter.limit("5/hour")  # Limite de 5 downloads por hora por IP
async def download_video(request: Request, url: str = Query(...), quality: str = Query("high")):

    loop = asyncio.get_running_loop()  

    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)

    client_ip = request.client.host
    progress_ws = clients.get(client_ip)
    
    # Primeiro só extrai informações
    ydl_opts_info = {
        'quiet': True,
        'skip_download': True,
    }

    ydl_opts_info["cookiefile"] = COOKIES_PATH

    with YoutubeDL(ydl_opts_info) as ydl:

        # Nome final do arquivo
        info = ydl.extract_info(url, download=False)
        video_id = info['id']
        filesize = info.get('filesize') or info.get('filesize_approx') or 0
        ext = info.get('ext') or 'mp4'

        # Verifica tamanho do vídeo (limite de 100MB)
        if filesize > 200 * 1024 * 1024:
            print(filesize)
            raise HTTPException(status_code=413, detail="Vídeo muito grande (limite de 100MB)")
        
        # Verifica se o link pertence a uma playlist
        if info.get('_type') == 'playlist':
            raise HTTPException(status_code=400, detail="Playlists não são permitidas.")

    # Configuração para download real
    hook = create_hook(progress_ws,loop)
    outtmpl = os.path.join(output_dir, f"{video_id}.%(ext)s")
    queie = Queue()


    if quality == "high":
        ydl_opts_download = {
            'format': '((bestvideo[height<=1080][height>=720])/best)[ext=mp4]+bestaudio/best[ext=mp4]',
            'outtmpl': outtmpl,
            'quiet': True,
            'progress_hooks': [hook]
        }
    else:
        ydl_opts_download = {
            'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
            'outtmpl':  outtmpl,
            'quiet': True,
            'progress_hooks': [hook]
    }

    ydl_opts_download["cookiefile"] = COOKIES_PATH

    def youtube_download(url,ydl_opts_download,queie):
        with YoutubeDL(ydl_opts_download) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            queie.put(filename)

    download_thread = threading.Thread(target=youtube_download, args=(url,ydl_opts_download,queie))  
    download_thread.start()  
    await asyncio.to_thread(download_thread.join)  

    return FileResponse(queie.get(), media_type='video/mp4')

@app.get("/api/info")
def get_video_info(url: str = Query(...)):

    with YoutubeDL({'quiet': True, 'skip_download': True}) as ydl:
        info = ydl.extract_info(url, download=False)

        if info.get('_type') == 'playlist':
            raise HTTPException(status_code=400, detail="Links de playlists não são permitidos.")
        return {
            "title": info["title"],
            "thumbnail": info.get("thumbnail"),
            "duration": f"{info.get('duration', 0) // 60}min"
        }

def clean_old_files(file_folder="downloads", max_time=3600):
    now = time.time()
    for file_name in os.listdir(file_folder):
        path = os.path.join(file_folder, file_name)
        if os.path.isfile(path):
            age = now - os.path.getmtime(path)
            if age > max_time:
                try:
                    os.remove(path)
                    print(f"🧹 Arquivo removido: {file_name}")
                except Exception as e:
                    print(f"Erro ao remover {file_name}: {e}")
    
def create_hook(progress_ws,loop):
    def hook(d):
        if d['status'] == 'downloading' and progress_ws:
            ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
            percent_str_raw = d.get('_percent_str', '').strip()
            percent_clean = ansi_escape.sub('', percent_str_raw).replace('%', '')

            try:
                percent = float(percent_clean)
            except ValueError:
                print(f"[Aviso] Não foi possível converter: '{percent_clean}'")
                return

            if download_phase["current"] == "video":
                total_progress = percent * 0.5
            else:
                total_progress = 50.0 + percent * 0.5

            asyncio.run_coroutine_threadsafe(progress_ws.send_text("{:.1f}".format(total_progress)),loop)

        elif d['status'] == 'finished':
            if download_phase["current"] == "video":
                download_phase["video_done"] = True
                download_phase["current"] = "audio"
    return hook
         

@app.on_event("startup")
def iniciar_agendador():
    scheduler.add_job(
        clean_old_files,
        'interval',
        minutes=30,  # Executa a cada 30 minutos
        kwargs={"file_folder": "downloads", "max_time": 3600}
    )
    scheduler.start()

@app.on_event("shutdown")
def parar_agendador():
    scheduler.shutdown()

app.mount("/", StaticFiles(directory="static", html=True), name="static")