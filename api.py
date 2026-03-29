# -*- coding: utf-8 -*-
"""
13 PRO MAX UC BOT — Admin API
FastAPI сервис для веб-апп админки
Деплоить на Railway рядом с ботом
"""

import os
import json
import asyncio
import psycopg2
import psycopg2.extras
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx

# ═══════════════════════════════════════════
#                  CONFIG
# ═══════════════════════════════════════════
DATABASE_URL  = os.getenv("DATABASE_URL", "")
BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "promax-secret-2025")  # задай в Railway Variables

app = FastAPI(title="13PROMAXUC Admin API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Netlify
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════
#                  DB
# ═══════════════════════════════════════════
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_extra_tables():
    """Создаём доп. таблицы если нет"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Цены на UC
            cur.execute("""
                CREATE TABLE IF NOT EXISTS uc_prices (
                    pack_key  TEXT PRIMARY KEY,
                    uc        INT,
                    price     INT,
                    label     TEXT
                )
            """)
            # Акции / события
            cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id        SERIAL PRIMARY KEY,
                    type      TEXT DEFAULT 'event',
                    title     TEXT,
                    sub       TEXT,
                    tag       TEXT,
                    img       TEXT,
                    created   TEXT
                )
            """)
            # Настройки бота
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Дефолтные цены если таблица пустая
            cur.execute("SELECT COUNT(*) FROM uc_prices")
            if cur.fetchone()[0] == 0:
                defaults = [
                    ("uc_60",  60,  80,  "60 UC — 80 руб"),
                    ("uc_120", 120, 155, "120 UC — 155 руб"),
                    ("uc_325", 325, 500, "325 UC — 500 руб"),
                    ("uc_660", 660, 890, "660 UC — 890 руб"),
                ]
                for d in defaults:
                    cur.execute(
                        "INSERT INTO uc_prices (pack_key,uc,price,label) VALUES (%s,%s,%s,%s)",
                        d
                    )
        conn.commit()

# ═══════════════════════════════════════════
#               AUTH
# ═══════════════════════════════════════════
def check_auth(x_api_key: str = Header(None)):
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# ═══════════════════════════════════════════
#               MODELS
# ═══════════════════════════════════════════
class PriceUpdate(BaseModel):
    pack_key: str
    price: int

class OrderStatusUpdate(BaseModel):
    order_id: int
    status: str

class EventCreate(BaseModel):
    type: str = "event"  # event | promo
    title: str
    sub: str = ""
    tag: str = "PUBG Mobile"
    img: str = ""

class BroadcastMessage(BaseModel):
    text: str

# ═══════════════════════════════════════════
#               ENDPOINTS
# ═══════════════════════════════════════════

@app.on_event("startup")
def startup():
    init_extra_tables()

@app.get("/")
def root():
    return {"status": "ok", "service": "13PROMAXUC Admin API"}

# ── ЦЕНЫ ──
@app.get("/prices")
def get_prices():
    """Публичный — читают все юзеры при загрузке веб-аппа"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM uc_prices ORDER BY uc")
            rows = cur.fetchall()
    return {"prices": [dict(r) for r in rows]}

@app.post("/prices")
def update_price(data: PriceUpdate, auth=Depends(check_auth)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE uc_prices SET price=%s, label=uc::text || ' UC — ' || %s::text || ' руб' WHERE pack_key=%s",
                (data.price, data.price, data.pack_key)
            )
        conn.commit()
    return {"ok": True}

# ── ЗАКАЗЫ ──
@app.get("/orders")
def get_orders(limit: int = 50, status: str = None, auth=Depends(check_auth)):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if status:
                cur.execute(
                    "SELECT * FROM orders WHERE status=%s ORDER BY order_id DESC LIMIT %s",
                    (status, limit)
                )
            else:
                cur.execute(
                    "SELECT * FROM orders ORDER BY order_id DESC LIMIT %s",
                    (limit,)
                )
            rows = cur.fetchall()
    return {"orders": [dict(r) for r in rows]}

@app.post("/orders/status")
def update_order_status(data: OrderStatusUpdate, auth=Depends(check_auth)):
    valid = ["pending", "paid", "done", "cancelled"]
    if data.status not in valid:
        raise HTTPException(400, "Invalid status")
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE orders SET status=%s WHERE order_id=%s RETURNING *",
                (data.status, data.order_id)
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "Order not found")
    return {"ok": True, "order": dict(row)}

# ── СТАТИСТИКА ──
@app.get("/stats")
def get_stats(auth=Depends(check_auth)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM orders")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
            pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='paid'")
            paid = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='done'")
            done = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM orders WHERE status='cancelled'")
            cancelled = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(SUM(price),0) FROM orders WHERE status IN ('paid','done')")
            revenue = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users")
            users = cur.fetchone()[0]
            # Заказы за сегодня
            cur.execute("SELECT COUNT(*) FROM orders WHERE time LIKE %s", (datetime.now().strftime("%d.%m.%Y") + "%",))
            today = cur.fetchone()[0]
    return {
        "total": total, "pending": pending, "paid": paid,
        "done": done, "cancelled": cancelled,
        "revenue": int(revenue), "users": users, "today": today
    }

# ── СОБЫТИЯ / АКЦИИ ──
@app.get("/events")
def get_events():
    """Публичный — читают все"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM events ORDER BY id DESC")
            rows = cur.fetchall()
    return {"events": [dict(r) for r in rows]}

@app.post("/events")
def create_event(data: EventCreate, auth=Depends(check_auth)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO events (type,title,sub,tag,img,created) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (data.type, data.title, data.sub, data.tag, data.img,
                 datetime.now().strftime("%d.%m.%Y %H:%M"))
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    return {"ok": True, "id": new_id}

@app.delete("/events/{event_id}")
def delete_event(event_id: int, auth=Depends(check_auth)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE id=%s", (event_id,))
        conn.commit()
    return {"ok": True}

# ── РАССЫЛКА ──
@app.post("/broadcast")
async def broadcast(data: BroadcastMessage, auth=Depends(check_auth)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            user_ids = [r[0] for r in cur.fetchall()]

    sent = failed = 0
    text = "📣 <b>Сообщение от администратора</b>\n\n" + data.text

    async with httpx.AsyncClient() as client:
        for uid in user_ids:
            try:
                r = await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": uid, "text": text, "parse_mode": "HTML"},
                    timeout=5
                )
                if r.json().get("ok"):
                    sent += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

    return {"ok": True, "sent": sent, "failed": failed}
