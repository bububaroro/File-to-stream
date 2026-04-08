import os
import secrets
import traceback
import uvicorn

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pyrogram.file_id import FileId
from pyrogram import raw

from config import Config
from database import db

# ================= BOT (FIXED SESSION PATH) =================
bot = Client(
    "/tmp/bot",   # ✅ ALWAYS WORKS IN RENDER
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

# ================= FASTAPI =================
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ================= STARTUP =================
@app.on_event("startup")
async def startup():
    try:
        print("Connecting DB...")
        await db.connect()

        print("Starting bot...")
        await bot.start()

        print("✅ Bot started successfully")
    except Exception as e:
        print("❌ STARTUP ERROR:", e)

@app.on_event("shutdown")
async def shutdown():
    await bot.stop()

# ================= BOT HANDLER =================
@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def handle_file(_, message: Message):
    try:
        sent = await message.copy(Config.STORAGE_CHANNEL)
        uid = secrets.token_urlsafe(8)

        await db.save_link(uid, sent.id)

        link = f"{Config.BASE_URL}/show/{uid}"

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Open Link", url=link)]])
        await message.reply_text("✅ File Uploaded", reply_markup=btn)

    except Exception:
        print(traceback.format_exc())
        await message.reply_text("❌ Error uploading file")

# ================= ROUTES =================
@app.get("/")
async def home():
    return {"status": "ok", "message": "Server is running"}

@app.get("/show/{uid}", response_class=HTMLResponse)
async def show_page(request: Request, uid: str):
    return templates.TemplateResponse("show.html", {"request": request})

@app.get("/api/file/{uid}", response_class=JSONResponse)
async def api_file(uid: str):
    mid = await db.get_link(uid)
    if not mid:
        raise HTTPException(404, "Invalid link")

    msg = await bot.get_messages(Config.STORAGE_CHANNEL, mid)
    media = msg.document or msg.video or msg.audio

    file_name = media.file_name or "file"
    safe_name = "".join(c for c in file_name if c.isalnum() or c in "._- ")

    base = Config.BASE_URL

    return {
        "file_name": file_name,
        "file_size": str(media.file_size),
        "is_media": True,
        "direct_dl_link": f"{base}/dl/{mid}/{safe_name}",

        "mx_player_link": f"intent://{base.replace('https://','').replace('http://','')}/dl/{mid}/{safe_name}#Intent;type=video/*;package=com.mxtech.videoplayer.ad;end",

        "vlc_player_link": f"vlc://{base}/dl/{mid}/{safe_name}"
    }

# ================= STREAM =================
@app.get("/dl/{mid}/{fname}")
async def stream(mid: int, fname: str):
    try:
        msg = await bot.get_messages(Config.STORAGE_CHANNEL, mid)
        media = msg.document or msg.video or msg.audio

        file_id = FileId.decode(media.file_id)

        async def generator():
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=""
            )

            offset = 0
            while True:
                chunk = await bot.invoke(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=offset,
                        limit=1024 * 1024
                    )
                )
                if not chunk.bytes:
                    break
                yield chunk.bytes
                offset += 1024 * 1024

        return StreamingResponse(generator(), media_type=media.mime_type)

    except Exception:
        print(traceback.format_exc())
        raise HTTPException(500, "Streaming error")

# ================= MAIN =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
