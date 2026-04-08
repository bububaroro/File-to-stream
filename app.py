import os
import asyncio
import secrets
import traceback
import uvicorn
import re
import logging
from contextlib import asynccontextmanager

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from pyrogram.errors import FloodWait, UserNotParticipant
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pyrogram.file_id import FileId
from pyrogram import raw
from pyrogram.session import Session, Auth
import math

from config import Config
from database import db

# ==================== FIXED SESSION ====================
bot = Client(
    "sessions/SimpleStreamBot",   # ✅ IMPORTANT FIX
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN
)

multi_clients = {}
work_loads = {}
class_cache = {}

# ==================== FASTAPI ====================
app = FastAPI()
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== STARTUP ====================
@app.on_event("startup")
async def startup():
    print("Starting bot...")
    await db.connect()
    await bot.start()
    multi_clients[0] = bot
    work_loads[0] = 0
    print("Bot started")

@app.on_event("shutdown")
async def shutdown():
    await bot.stop()

# ==================== BOT ====================
@bot.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def file_handler(_, message: Message):
    try:
        sent = await message.copy(Config.STORAGE_CHANNEL)
        unique_id = secrets.token_urlsafe(8)

        await db.save_link(unique_id, sent.id)

        link = f"{Config.BASE_URL}/show/{unique_id}"

        btn = InlineKeyboardMarkup([[InlineKeyboardButton("Open Link", url=link)]])
        await message.reply_text("✅ File uploaded!", reply_markup=btn)

    except Exception:
        print(traceback.format_exc())
        await message.reply_text("Error occurred")

# ==================== ROUTES ====================
@app.get("/")
async def home():
    return {"status": "ok"}

@app.get("/show/{unique_id}", response_class=HTMLResponse)
async def show_page(request: Request, unique_id: str):
    return templates.TemplateResponse(
        "show.html",
        {"request": request, "unique_id": unique_id}   # ✅ FIX
    )

@app.get("/api/file/{unique_id}")
async def api_file(unique_id: str):
    message_id = await db.get_link(unique_id)
    if not message_id:
        raise HTTPException(404)

    msg = await bot.get_messages(Config.STORAGE_CHANNEL, message_id)
    media = msg.document or msg.video or msg.audio

    file_name = media.file_name or "file"

    safe_name = "".join(c for c in file_name if c.isalnum() or c in "._- ")

    base = Config.BASE_URL

    return {
        "file_name": file_name,
        "file_size": media.file_size,
        "is_media": True,
        "direct_dl_link": f"{base}/dl/{message_id}/{safe_name}",

        # ✅ FIXED MX PLAYER
        "mx_player_link": f"intent://{base.replace('https://','').replace('http://','')}/dl/{message_id}/{safe_name}#Intent;type=video/*;package=com.mxtech.videoplayer.ad;end",

        "vlc_player_link": f"vlc://{base}/dl/{message_id}/{safe_name}"
    }

# ==================== STREAM ====================
class ByteStreamer:
    def __init__(self, client):
        self.client = client

    async def yield_file(self, file_id, offset, chunk_size):
        client = self.client
        location = raw.types.InputDocumentFileLocation(
            id=file_id.media_id,
            access_hash=file_id.access_hash,
            file_reference=file_id.file_reference,
            thumb_size=""
        )

        current = offset
        while True:
            r = await client.invoke(
                raw.functions.upload.GetFile(
                    location=location,
                    offset=current,
                    limit=chunk_size
                )
            )
            if not r.bytes:
                break
            yield r.bytes
            current += chunk_size

@app.get("/dl/{mid}/{fname}")
async def stream(mid: int, fname: str):
    msg = await bot.get_messages(Config.STORAGE_CHANNEL, mid)
    media = msg.document or msg.video or msg.audio

    file_id = FileId.decode(media.file_id)
    file_size = media.file_size

    streamer = ByteStreamer(bot)

    return StreamingResponse(
        streamer.yield_file(file_id, 0, 1024 * 1024),
        media_type=media.mime_type,
        headers={"Content-Length": str(file_size)}
    )

# ==================== MAIN ====================
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=10000)
