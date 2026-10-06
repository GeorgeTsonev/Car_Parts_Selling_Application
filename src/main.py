from workers import WorkerEntrypoint
from fastapi import FastAPI, Request
import asgi
import base64, hashlib, hmac, secrets
from js import crypto, TextEncoder, Uint8Array, Object
from pyodide.ffi import to_js
from fastapi import HTTPException, Response
from pydantic import BaseModel
app = FastAPI()


async def q(env, sql, *args):
    stmt = env.DB.prepare(sql)
    if args:
        stmt = stmt.bind(*args)
    res = await stmt.all()
    rows = res.results
    return [r.to_py() if hasattr(r, "to_py") else r for r in rows]


@app.get("/api/health")
async def health():
    return {"ok": True}

import traceback
from fastapi.responses import PlainTextResponse

@app.get("/api/catalog")
async def catalog(request: Request):
    try:
        env = request.scope["env"]
        return {
            "brands": await q(env, "SELECT brand_id, name, logo_url FROM Brands ORDER BY name"),
            "models": await q(env, "SELECT model_id, brand_id, name, description, picture_url FROM Models"),
            "generations": await q(env, "SELECT generation_id, model_id, generation_name, start_year, end_year, overview_image_url FROM Vehicle_Generations"),
            "parts": await q(env, """
                SELECT p.part_id, p.name, p.description, p.oem_part_number, p.category,
                       p.disassembly_instructions, p.image_url, p.license, p.is_assembly,
                       p.is_available_physical, p.physical_print_price, p.status,
                       p.author_user_id, u.username AS author
                FROM Parts p JOIN Users u ON u.user_id = p.author_user_id"""),
            "compat": await q(env, "SELECT part_id, generation_id, hotspot_x, hotspot_y FROM Part_Compatibilities"),
            "files": await q(env, "SELECT file_id, part_id, file_type, file_name, recommended_material, recommended_infill_pct, supports_required FROM Part_Files"),
        }
    except Exception:
        return PlainTextResponse(traceback.format_exc(), status_code=500)

ITER = 100_000
COOKIE = "rf_session"


class SignupIn(BaseModel):
    username: str
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


async def run(env, sql, *args):
    return await env.DB.prepare(sql).bind(*args).run()


async def pbkdf2(password, salt, iterations):
    key = await crypto.subtle.importKey(
        "raw", TextEncoder.new().encode(password), "PBKDF2", False, to_js(["deriveBits"])
    )
    params = to_js(
        {"name": "PBKDF2", "hash": "SHA-256",
         "salt": Uint8Array.new(to_js(list(salt))), "iterations": iterations},
        dict_converter=Object.fromEntries,
    )
    bits = await crypto.subtle.deriveBits(params, key, 256)
    return bytes(Uint8Array.new(bits).to_py())


async def hash_password(pw):
    salt = secrets.token_bytes(16)
    h = await pbkdf2(pw, salt, ITER)
    return f"pbkdf2${ITER}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


async def verify_password(pw, stored):
    try:
        _, it, s, h = stored.split("$")
        calc = await pbkdf2(pw, base64.b64decode(s), int(it))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def public_user(u):
    return {
        "id": u["user_id"], "username": u["username"], "name": u["name"],
        "email": u["email"], "role": u["role"], "bio": u["bio"] or "",
        "phone": u["contact_info"] or "", "pic": u["avatar_url"] or "",
    }


async def start_session(env, response, user_id):
    token = secrets.token_urlsafe(32)
    th = hashlib.sha256(token.encode()).hexdigest()
    await run(env, "INSERT INTO Sessions(token_hash, user_id, expires_at) "
                   "VALUES(?, ?, datetime('now','+30 days'))", th, user_id)
    response.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True,
                        secure=True, samesite="lax", path="/")


async def current_user(request, env):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    rows = await q(env, "SELECT u.* FROM Sessions s JOIN Users u ON u.user_id = s.user_id "
                        "WHERE s.token_hash = ? AND s.expires_at > datetime('now')", th)
    return rows[0] if rows else None


@app.post("/api/signup")
async def signup(data: SignupIn, request: Request, response: Response):
    env = request.scope["env"]
    username, name, email = data.username.strip(), data.name.strip(), data.email.strip().lower()
    if len(username) < 3 or len(name) < 2 or "@" not in email or len(data.password) < 8:
        raise HTTPException(400, "Check your details. Password needs at least 8 characters.")
    if await q(env, "SELECT user_id FROM Users WHERE email = ? OR username = ?", email, username):
        raise HTTPException(409, "That email or username is already registered.")
    await run(env, "INSERT INTO Users(username, name, email, password_hash) VALUES(?, ?, ?, ?)",
              username, name, email, await hash_password(data.password))
    user = (await q(env, "SELECT * FROM Users WHERE email = ?", email))[0]
    await start_session(env, response, user["user_id"])
    return public_user(user)


@app.post("/api/login")
async def login(data: LoginIn, request: Request, response: Response):
    env = request.scope["env"]
    rows = await q(env, "SELECT * FROM Users WHERE email = ?", data.email.strip().lower())
    if not rows or not await verify_password(data.password, rows[0]["password_hash"]):
        raise HTTPException(401, "Invalid email or password.")
    await start_session(env, response, rows[0]["user_id"])
    return public_user(rows[0])


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    env = request.scope["env"]
    token = request.cookies.get(COOKIE)
    if token:
        await run(env, "DELETE FROM Sessions WHERE token_hash = ?",
                  hashlib.sha256(token.encode()).hexdigest())
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
async def me_route(request: Request):
    u = await current_user(request, request.scope["env"])
    return public_user(u) if u else None

class CartIn(BaseModel):
    part_id: int


class CheckoutIn(BaseModel):
    shipping_address: str


class ProfileIn(BaseModel):
    bio: str = ""
    phone: str = ""


async def require_user(request):
    u = await current_user(request, request.scope["env"])
    if not u:
        raise HTTPException(401, "Please log in.")
    return u


CART_SQL = """
    SELECT c.part_id, c.quantity, c.material_printed, p.name,
           COALESCE(p.physical_print_price, 0) AS unit_price
    FROM Cart_Items c JOIN Parts p ON p.part_id = c.part_id
    WHERE c.user_id = ? ORDER BY c.cart_item_id"""


@app.get("/api/cart")
async def get_cart(request: Request):
    user = await require_user(request)
    return await q(request.scope["env"], CART_SQL, user["user_id"])


@app.post("/api/cart")
async def add_to_cart(data: CartIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    ok = await q(env, "SELECT part_id FROM Parts WHERE part_id = ? "
                      "AND is_available_physical = 1 AND physical_print_price IS NOT NULL",
                 data.part_id)
    if not ok:
        raise HTTPException(400, "This part is not available as a printed item.")
    await run(env, "INSERT OR IGNORE INTO Cart_Items(user_id, part_id) VALUES(?, ?)",
              user["user_id"], data.part_id)
    return {"ok": True}


@app.delete("/api/cart/{part_id}")
async def remove_from_cart(part_id: int, request: Request):
    user = await require_user(request)
    await run(request.scope["env"],
              "DELETE FROM Cart_Items WHERE user_id = ? AND part_id = ?",
              user["user_id"], part_id)
    return {"ok": True}


@app.post("/api/checkout")
async def checkout(data: CheckoutIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    address = data.shipping_address.strip()
    if not address or len(address) > 500:
        raise HTTPException(400, "Enter a shipping address.")
    items = await q(env, CART_SQL, user["user_id"])
    if not items:
        raise HTTPException(400, "Your cart is empty.")
    total = round(sum(i["unit_price"] * i["quantity"] for i in items), 2)
    order = await q(env, "INSERT INTO Physical_Orders(user_id, total_amount, shipping_address) "
                         "VALUES(?, ?, ?) RETURNING order_id",
                    user["user_id"], total, address)
    order_id = order[0]["order_id"]
    for i in items:
        await run(env, "INSERT INTO Physical_Order_Items(order_id, part_id, quantity, unit_price, material_printed) "
                       "VALUES(?, ?, ?, ?, ?)",
                  order_id, i["part_id"], i["quantity"], i["unit_price"], i["material_printed"])
    await run(env, "DELETE FROM Cart_Items WHERE user_id = ?", user["user_id"])
    return {"order_id": order_id, "total": total, "status": "pending"}


@app.put("/api/profile")
async def update_profile(data: ProfileIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    await run(env, "UPDATE Users SET bio = ?, contact_info = ? WHERE user_id = ?",
              data.bio[:1000], data.phone[:255], user["user_id"])
    rows = await q(env, "SELECT * FROM Users WHERE user_id = ?", user["user_id"])
    return public_user(rows[0])


@app.get("/api/download/{part_id}")
async def download(part_id: int, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    files = await q(env, "SELECT file_id, r2_object_key, file_name FROM Part_Files "
                         "WHERE part_id = ? ORDER BY file_id LIMIT 1", part_id)
    if not files:
        raise HTTPException(404, "No file is attached to this part yet.")
    f = files[0]
    obj = await env.BUCKET.get(f["r2_object_key"])
    if obj is None:
        raise HTTPException(404, "File not found in storage.")
    data = bytes((await obj.arrayBuffer()).to_py())
    await run(env, "INSERT INTO Part_Download_Logs(part_id, file_id, user_id, ip_country) "
                   "VALUES(?, ?, ?, ?)",
              part_id, f["file_id"], user["user_id"],
              (request.headers.get("cf-ipcountry") or "")[:2])
    safe = f["file_name"].replace('"', "").replace("\n", "")
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request.js_object, self.env)