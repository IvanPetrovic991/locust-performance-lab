"""
ShopAPI — demo e-commerce API used as the system under test.

The API intentionally contains realistic performance characteristics so that
load test results are meaningful:

  * /search          — simulated cache misses (~15% of requests are slow)
  * /checkout        — limited "payment gateway" connection pool, so latency
                       degrades under concurrency (queueing)
  * /checkout        — ~1% simulated payment gateway timeouts (HTTP 502)

Set SIMULATE_MEMORY_LEAK=true to make the app leak ~50 KB per request —
used to demonstrate that the memory-leak gate (scripts/check_memory_leak.py)
actually catches a leaking service.

State is kept in memory; run with a single uvicorn worker.
"""

import asyncio
import hashlib
import os
import random
import time
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

app = FastAPI(title="ShopAPI", version="1.0.0")

AUTH_SECRET = "perf-lab-demo-secret"
CATALOG_SIZE = 200

CATALOG = {
    pid: {
        "id": pid,
        "name": f"Product {pid}",
        "category": random.choice(["books", "electronics", "home", "sports"]),
        "price": round(random.uniform(5, 500), 2),
        "in_stock": random.random() > 0.1,
    }
    for pid in range(1, CATALOG_SIZE + 1)
}

# Simulates a payment gateway with a small connection pool: under high
# concurrency, checkout requests queue here and p95 latency degrades.
PAYMENT_POOL = asyncio.Semaphore(4)

START_TIME = time.time()

SIMULATE_MEMORY_LEAK = os.getenv("SIMULATE_MEMORY_LEAK", "false").lower() == "true"
_LEAKED: list = []  # grows forever when the simulated leak is enabled


@app.middleware("http")
async def leaky_middleware(request, call_next):
    if SIMULATE_MEMORY_LEAK:
        _LEAKED.append(os.urandom(50 * 1024))
    return await call_next(request)


class LoginRequest(BaseModel):
    username: str
    password: str


class CartItem(BaseModel):
    product_id: int
    quantity: int = 1


class CheckoutRequest(BaseModel):
    items: List[CartItem]
    payment_method: str = "card"


def make_token(username: str) -> str:
    return hashlib.sha256(f"{username}:{AUTH_SECRET}".encode()).hexdigest()


def require_auth(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    if len(token) != 64:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


@app.get("/health")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - START_TIME, 1)}


@app.get("/metrics")
async def metrics():
    """Prometheus-format process metrics, so memory can be watched live in
    Grafana during a soak test (white-box complement to `docker stats`)."""
    try:
        with open("/proc/self/statm") as f:
            rss_pages = int(f.read().split()[1])
        rss_bytes = rss_pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        rss_bytes = 0  # /proc is Linux-only; report 0 when run outside Docker
    body = (
        "# TYPE process_resident_memory_bytes gauge\n"
        f"process_resident_memory_bytes {rss_bytes}\n"
        "# TYPE process_uptime_seconds gauge\n"
        f"process_uptime_seconds {time.time() - START_TIME:.1f}\n"
    )
    return PlainTextResponse(body)


@app.post("/auth/login")
async def login(body: LoginRequest):
    await asyncio.sleep(random.uniform(0.05, 0.15))  # password hashing cost
    if not body.username or not body.password:
        raise HTTPException(status_code=400, detail="Missing credentials")
    return {"token": make_token(body.username), "token_type": "bearer"}


@app.get("/products")
async def list_products(page: int = 1, page_size: int = 20):
    await asyncio.sleep(random.uniform(0.01, 0.04))
    items = list(CATALOG.values())
    start = (page - 1) * page_size
    return {"page": page, "total": len(items), "items": items[start : start + page_size]}


@app.get("/products/{product_id}")
async def get_product(product_id: int):
    await asyncio.sleep(random.uniform(0.02, 0.06))
    product = CATALOG.get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.get("/search")
async def search(q: str = ""):
    # ~15% cache misses: full catalog scan + ranking, an order of magnitude slower
    if random.random() < 0.15:
        await asyncio.sleep(random.uniform(0.4, 0.9))
    else:
        await asyncio.sleep(random.uniform(0.03, 0.09))
    ql = q.lower()
    hits = [p for p in CATALOG.values() if ql in p["name"].lower() or ql in p["category"]]
    return {"query": q, "count": len(hits), "items": hits[:20]}


@app.post("/cart/items")
async def add_to_cart(item: CartItem, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    if item.product_id not in CATALOG:
        raise HTTPException(status_code=404, detail="Product not found")
    await asyncio.sleep(random.uniform(0.02, 0.05))
    price = CATALOG[item.product_id]["price"]
    return {"added": item.product_id, "quantity": item.quantity, "subtotal": round(price * item.quantity, 2)}


@app.post("/checkout")
async def checkout(body: CheckoutRequest, authorization: Optional[str] = Header(None)):
    require_auth(authorization)
    if not body.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    async with PAYMENT_POOL:
        await asyncio.sleep(random.uniform(0.15, 0.35))  # payment gateway call

    if random.random() < 0.01:
        raise HTTPException(status_code=502, detail="Payment gateway timeout")

    total = sum(CATALOG[i.product_id]["price"] * i.quantity for i in body.items if i.product_id in CATALOG)
    return {"order_id": f"ORD-{random.randint(100000, 999999)}", "total": round(total, 2), "status": "confirmed"}
