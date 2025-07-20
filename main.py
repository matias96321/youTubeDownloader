from apscheduler.schedulers.background import BackgroundScheduler
import os
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse 
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi import Limiter
from yt_dlp import YoutubeDL
import os

app = FastAPI()

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

cookie_data = os.getenv("COOKIES_PATH")
cookie_path = 'cookies.txt'
if cookie_data:
    with open(cookie_path, "wb") as f:
        f.write(cookie_data)

def delete_file(path: str):
    if os.path.exists(path):
        os.remove(path)

@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc):
    return JSONResponse(
        status_code=429,
        content={"detail": "Limite de requisições excedido. Tente novamente mais tarde."}
    )

@app.get("/api/download")
@limiter.limit("5/hour")  # Limite de 5 downloads por hora por IP
async def download_video(request: Request, background_tasks: BackgroundTasks,url: str = Query(...), quality: str = Query("high") ):

    os.makedirs("tmp", exist_ok=True)
    output_template = "tmp/temp_video.%(ext)s"

    if quality == "high":
        ydl_opts_download = {
            'format': '((bestvideo[height<=1080][height>=720])/best)[ext=mp4]+bestaudio/best[ext=mp4]',
            'merge_output_format': 'mp4',
            'outtmpl': output_template,
            'quiet': True,
        }
    else:
        ydl_opts_download = {
            'format': 'bestvideo[height<=480]+bestaudio/best[height<=480]',
            'merge_output_format': 'mp4',
            'outtmpl':  output_template,
            'quiet': True,
    }

    ydl_opts_download["cookiefile"] = cookie_path

    try :
        with YoutubeDL(ydl_opts_download) as ydl:
            info = ydl.extract_info(url, download=True)
            final_path = ydl.prepare_filename(info).replace(".webm", ".mp4").replace(".mkv", ".mp4")

        response = FileResponse(
            final_path,
            media_type="video/mp4",
            background=background_tasks
        )

        if not os.path.exists(final_path):
            raise HTTPException(status_code=500, detail="Falha ao processar o vídeo. Tente novamente.")

        background_tasks.add_task(delete_file, final_path)

        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao baixar: {str(e)}")
    
@app.get("/api/info")
def get_video_info(url: str = Query(...)):

    with YoutubeDL({'quiet': True, 'skip_download': True,'cookiefile': cookie_path}) as ydl:

        info = ydl.extract_info(url, download=False)
        filesize = info.get('filesize') or info.get('filesize_approx') or 0

        if filesize > 200 * 1024 * 1024:
            print(filesize)
            raise HTTPException(status_code=413, detail="Vídeo muito grande (limite de 100MB)")
        
        if info.get('_type') == 'playlist':
            raise HTTPException(status_code=400, detail="Links de playlists não são permitidos.")
        
        return {
            "title": info["title"],
            "thumbnail": info.get("thumbnail"),
            "duration": f"{info.get('duration', 0) // 60}min"
        }

app.mount("/", StaticFiles(directory="static", html=True), name="static")