from workers import WorkerEntrypoint
from fastapi import FastAPI, Request
import asgi
import base64, hashlib, hmac, secrets
from js import crypto, TextEncoder, Uint8Array, Object
from pyodide.ffi import to_js
from fastapi import HTTPException, Response
from pydantic import BaseModel
import json
app = FastAPI()


async def q(env, sql, *args):
    stmt = env.DB.prepare(sql)
    if args:
        stmt = stmt.bind(*args)
    res = await stmt.all()
    rows = res.results
    return [r.to_py() if hasattr(r, "to_py") else r for r in rows]

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
                SELECT p.part_id, p.created_at, p.name, p.description, p.oem_part_number, p.category,
                       p.disassembly_instructions, p.image_url, p.license, p.is_assembly,
                       p.is_available_physical, p.physical_print_price, p.status,
                       p.author_user_id, u.username AS author, p.manufacturing_technique,
                       p.material, p.manufacturing_specs
                FROM Parts p JOIN Users u ON u.user_id = p.author_user_id"""),
            "compat": await q(env, "SELECT part_id, generation_id, hotspot_x, hotspot_y FROM Part_Compatibilities"),
            "files": await public_files(env),
            "spec_schema": SPEC_SCHEMA,
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

from fastapi.responses import RedirectResponse
@app.get("/api/download/{part_id}")
async def download(part_id: int, request: Request):
    env = request.scope["env"]
    user = await current_user(request, env)
    uid = user["user_id"] if user else 0
    files = await q(env, "SELECT file_id, r2_object_key FROM Part_Files "
                         "WHERE part_id = ? ORDER BY file_id LIMIT 1", part_id)
    if not files:
        raise HTTPException(404, "No file is attached to this part yet.")
    f = files[0]
    await run(env, "INSERT INTO Part_Download_Logs(part_id, file_id, user_id, ip_country) "
                   "VALUES(?, ?, NULLIF(?, 0), ?)",
              part_id, f["file_id"], uid, (request.headers.get("cf-ipcountry") or "")[:2])
    return RedirectResponse(public_url(env, f["r2_object_key"]), status_code=302)


# ---------- Uploads ----------
CATEGORIES = {"interior", "exterior", "underhood", "trim_clip", "lighting_bracket"}
LICENSES = {"CC0", "CC-BY-4.0", "CC-BY-SA-4.0", "CERN-OHL-P-2.0", "CERN-OHL-S-2.0"}
MATERIALS = {"PLA", "PETG", "ABS", "ASA", "PA_CF", "TPU"}
MODEL_EXT = {"stl", "3mf", "step", "stp"}
IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
LIMITS = {"model": 25 * 1024 * 1024, "image": 5 * 1024 * 1024, "avatar": 2 * 1024 * 1024}
CTYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class PartIn(BaseModel):
    name: str
    description: str
    category: str
    license: str
    generation_id: int
    file_key: str
    oem_part_number: str = ""
    image_key: str = ""
    steps: str = ""
    technique: str
    specs: dict = {}


class AvatarIn(BaseModel):
    key: str


def safe_name(name):
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:80]
    return cleaned or "file"


def public_url(env, key):
    return str(env.PUBLIC_R2_URL).rstrip("/") + "/" + key


def looks_like_image(data):
    return (data[:3] == b"\xff\xd8\xff"
            or data[:8] == b"\x89PNG\r\n\x1a\n"
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


@app.post("/api/upload/{kind}")
async def upload(kind: str, filename: str, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    uid = user["user_id"]


    if kind not in LIMITS:
        raise HTTPException(404, "Unknown upload type.")

    name = safe_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in (MODEL_EXT if kind == "model" else IMAGE_EXT):
        raise HTTPException(400, "This file type is not allowed.")

    if int(request.headers.get("content-length") or 0) > LIMITS[kind]:
        raise HTTPException(413, "The file is too large.")
    data = await request.body()
    if not data or len(data) > LIMITS[kind]:
        raise HTTPException(413, "The file is empty or too large.")

    token = secrets.token_hex(8)

    if kind == "model":
        key = f"parts/{uid}/{token}-{name}"
        opts = to_js({"httpMetadata": {
            "contentType": "application/octet-stream",
            "contentDisposition": f'attachment; filename="{name}"'}},
            dict_converter=Object.fromEntries)
        await env.BUCKET.put(key, to_js(data), opts)
        return {"key": key, "size": len(data)}

    if not looks_like_image(data):
        raise HTTPException(400, "This does not look like a valid image.")
    key = f"{'avatars' if kind == 'avatar' else 'images'}/{uid}/{token}.{ext}"
    opts = to_js({"httpMetadata": {"contentType": CTYPES[ext]}},
                 dict_converter=Object.fromEntries)
    await env.BUCKET.put(key, to_js(data), opts)
    return {"key": key, "url": public_url(env, key)}


@app.post("/api/parts")
async def create_part(data: PartIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    uid = user["user_id"]

    name, desc = data.name.strip(), data.description.strip()
    if not (3 <= len(name) <= 150) or not desc:
        raise HTTPException(400, "Enter a name (3+ characters) and a description.")
    if data.category not in CATEGORIES or data.license not in LICENSES:
        raise HTTPException(400, "Invalid category or license.")
    specs = clean_specs(data.technique, data.specs)
    if not data.file_key.startswith(f"parts/{uid}/"):
        raise HTTPException(400, "Invalid file reference.")
    if data.image_key and not data.image_key.startswith(f"images/{uid}/"):
        raise HTTPException(400, "Invalid picture reference.")
    if not await q(env, "SELECT generation_id FROM Vehicle_Generations WHERE generation_id = ?",
                   data.generation_id):
        raise HTTPException(400, "Choose a valid car generation.")

    head = await env.BUCKET.head(data.file_key)
    if head is None:
        raise HTTPException(400, "The uploaded file was not found. Please upload it again.")

    ext = data.file_key.rsplit(".", 1)[-1].lower()
    ftype = {"3mf": "mesh_3mf", "step": "cad_step", "stp": "cad_step"}.get(ext, "mesh_stl")
    fname = data.file_key.rsplit("/", 1)[-1].split("-", 1)[-1]
    image_url = public_url(env, data.image_key) if data.image_key else ""
    steps = data.steps.strip()[:4000] or "The author has not added removal steps yet."

    material = specs.get("material", "")
    file_material = PART_FILE_MATERIALS.get(material, "")
    infill = int(specs.get("infill_pct", 0))
    supports = 1 if specs.get("supports") == "Yes" else 0

    rows = await q(env,
        "INSERT INTO Parts(sku, name, description, oem_part_number, category, disassembly_instructions, "
        "author_user_id, license, image_url, manufacturing_technique, material, "
        "manufacturing_specs, status) "
        "VALUES(?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, 'draft') RETURNING part_id",
        "U-" + secrets.token_hex(6), name, desc, data.oem_part_number.strip(), data.category, steps, uid,
        data.license, image_url, data.technique, material, json.dumps(specs))
    pid = rows[0]["part_id"]

    await run(env, "INSERT INTO Part_Compatibilities(part_id, generation_id, hotspot_x, hotspot_y) "
                   "VALUES(?, ?, NULL, NULL)", pid, data.generation_id)
    await run(env, "INSERT INTO Part_Files(part_id, file_type, r2_object_key, file_name, "
                   "file_size_bytes, recommended_material, recommended_infill_pct, supports_required) "
                   "VALUES(?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?)",
              pid, ftype, data.file_key, fname, int(head.size), file_material, infill, supports)
    return {"part_id": pid}

@app.put("/api/profile/avatar")
async def set_avatar(data: AvatarIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    if not data.key.startswith(f"avatars/{user['user_id']}/"):
        raise HTTPException(400, "Invalid picture reference.")
    await run(env, "UPDATE Users SET avatar_url = ? WHERE user_id = ?",
              public_url(env, data.key), user["user_id"])
    rows = await q(env, "SELECT * FROM Users WHERE user_id = ?", user["user_id"])
    return public_user(rows[0])
# ---------- End uploads ----------
async def public_files(env):
    rows = await q(env, "SELECT file_id, part_id, file_type, file_name, file_size_bytes, r2_object_key, "
                        "recommended_material, recommended_infill_pct, supports_required FROM Part_Files")
    for r in rows:
        r["url"] = public_url(env, r.pop("r2_object_key"))
    return rows

# ---------- Admin ----------
class BrandIn(BaseModel):
    name: str
    logo_url: str = ""


class ModelIn(BaseModel):
    brand_id: int
    name: str
    picture_url: str = ""


class GenIn(BaseModel):
    model_id: int
    generation_name: str = ""
    start_year: int
    end_year: int = 0
    overview_image_url: str = ""


class StatusIn(BaseModel):
    status: str


class PartEditIn(BaseModel):
    price: float = 0
    image_url: str = ""
    oem: str = ""
    physical: bool = False


async def require_admin(request):
    u = await require_user(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Admins only.")
    return u


async def admin_write(env, sql, *args):
    try:
        await run(env, sql, *args)
    except Exception:
        raise HTTPException(409, "Could not save. Check for duplicates or invalid references.")
    return {"ok": True}


@app.post("/api/admin/brands")
async def admin_add_brand(data: BrandIn, request: Request):
    await require_admin(request)
    if not data.name.strip():
        raise HTTPException(400, "Enter a brand name.")
    return await admin_write(request.scope["env"],
        "INSERT INTO Brands(name, logo_url) VALUES(?, NULLIF(?, ''))",
        data.name.strip(), data.logo_url.strip())


@app.post("/api/admin/models")
async def admin_add_model(data: ModelIn, request: Request):
    await require_admin(request)
    if not data.name.strip():
        raise HTTPException(400, "Enter a model name.")
    return await admin_write(request.scope["env"],
        "INSERT INTO Models(brand_id, name, picture_url) VALUES(?, ?, NULLIF(?, ''))",
        data.brand_id, data.name.strip(), data.picture_url.strip())


@app.post("/api/admin/generations")
async def admin_add_generation(data: GenIn, request: Request):
    await require_admin(request)
    if not (1900 <= data.start_year <= 2100):
        raise HTTPException(400, "Enter a valid start year.")
    return await admin_write(request.scope["env"],
        "INSERT INTO Vehicle_Generations(model_id, generation_name, start_year, end_year, overview_image_url) "
        "VALUES(?, ?, ?, NULLIF(?, 0), NULLIF(?, ''))",
        data.model_id, data.generation_name.strip(), data.start_year,
        data.end_year, data.overview_image_url.strip())
async def _count(env, sql, *args):
    return (await q(env, sql, *args))[0]["n"]

@app.put("/api/admin/brands/{brand_id}")
async def admin_edit_brand(brand_id: int, data: BrandIn, request: Request):
    await require_admin(request)
    if not data.name.strip():
        raise HTTPException(400, "Enter a brand name.")
    return await admin_write(request.scope["env"],
        "UPDATE Brands SET name = ?, logo_url = NULLIF(?, '') WHERE brand_id = ?",
        data.name.strip(), data.logo_url.strip(), brand_id)

@app.delete("/api/admin/brands/{brand_id}")
async def admin_delete_brand(brand_id: int, request: Request):
    env = request.scope["env"]
    await require_admin(request)
    n = await _count(env, "SELECT COUNT(*) AS n FROM Models WHERE brand_id = ?", brand_id)
    if n:
        raise HTTPException(409, f"This brand still has {n} model(s). Delete or move them first.")
    return await admin_write(env, "DELETE FROM Brands WHERE brand_id = ?", brand_id)

@app.put("/api/admin/models/{model_id}")
async def admin_edit_model(model_id: int, data: ModelIn, request: Request):
    await require_admin(request)
    if not data.name.strip():
        raise HTTPException(400, "Enter a model name.")
    return await admin_write(request.scope["env"],
        "UPDATE Models SET brand_id = ?, name = ?, picture_url = NULLIF(?, '') WHERE model_id = ?",
        data.brand_id, data.name.strip(), data.picture_url.strip(), model_id)

@app.delete("/api/admin/models/{model_id}")
async def admin_delete_model(model_id: int, request: Request):
    env = request.scope["env"]
    await require_admin(request)
    n = await _count(env, "SELECT COUNT(*) AS n FROM Vehicle_Generations WHERE model_id = ?", model_id)
    if n:
        raise HTTPException(409, f"This model still has {n} generation(s). Delete or move them first.")
    return await admin_write(env, "DELETE FROM Models WHERE model_id = ?", model_id)

@app.put("/api/admin/generations/{generation_id}")
async def admin_edit_generation(generation_id: int, data: GenIn, request: Request):
    await require_admin(request)
    if not (1900 <= data.start_year <= 2100):
        raise HTTPException(400, "Enter a valid start year.")
    if data.end_year and data.end_year < data.start_year:
        raise HTTPException(400, "The end year cannot be before the start year.")
    return await admin_write(request.scope["env"],
        "UPDATE Vehicle_Generations SET model_id = ?, generation_name = ?, start_year = ?, "
        "end_year = NULLIF(?, 0), overview_image_url = NULLIF(?, '') WHERE generation_id = ?",
        data.model_id, data.generation_name.strip(), data.start_year,
        data.end_year, data.overview_image_url.strip(), generation_id)

@app.delete("/api/admin/generations/{generation_id}")
async def admin_delete_generation(generation_id: int, request: Request):
    env = request.scope["env"]
    await require_admin(request)
    if not await q(env, "SELECT generation_id FROM Vehicle_Generations WHERE generation_id = ?", generation_id):
        raise HTTPException(404, "Generation not found.")
    try:
        # Only the fitment links go. Parts and Part_Files are never touched.
        await run(env, "DELETE FROM Part_Compatibilities WHERE generation_id = ?", generation_id)
        await run(env, "DELETE FROM Vehicle_Generations WHERE generation_id = ?", generation_id)
    except Exception:
        raise HTTPException(409, "Could not delete this generation. Something else still references it.")
    return {"ok": True}

@app.put("/api/admin/parts/{part_id}/status")
async def admin_part_status(part_id: int, data: StatusIn, request: Request):
    await require_admin(request)
    if data.status not in ("draft", "community_tested", "verified_fit", "flagged"):
        raise HTTPException(400, "Invalid status.")
    return await admin_write(request.scope["env"],
        "UPDATE Parts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
        data.status, part_id)


@app.put("/api/admin/parts/{part_id}")
async def admin_edit_part(part_id: int, data: PartEditIn, request: Request):
    await require_admin(request)
    price = data.price if data.physical and data.price > 0 else 0
    return await admin_write(request.scope["env"],
        "UPDATE Parts SET is_available_physical = ?, physical_print_price = NULLIF(?, 0), "
        "image_url = NULLIF(?, ''), oem_part_number = NULLIF(?, ''), updated_at = CURRENT_TIMESTAMP "
        "WHERE part_id = ?",
        1 if (data.physical and price > 0) else 0, price,
        data.image_url.strip(), data.oem.strip(), part_id)


@app.delete("/api/admin/parts/{part_id}")
async def admin_delete_part(part_id: int, request: Request):
    env = request.scope["env"]
    await require_admin(request)
    files = await q(env, "SELECT r2_object_key FROM Part_Files WHERE part_id = ?", part_id)
    try:
        await run(env, "DELETE FROM Parts WHERE part_id = ?", part_id)
    except Exception:
        raise HTTPException(409, "This part is in an order and can't be deleted. Set its status to Flagged instead.")
    for f in files:
        try:
            await env.BUCKET.delete(f["r2_object_key"])
        except Exception:
            pass
    return {"ok": True}
# ---------- Admin: replace a part's picture or 3D file by uploading ----------
class PartImageIn(BaseModel):
    image_key: str

class PartFileIn(BaseModel):
    file_key: str

def r2_key_from_url(env, url):
    base = str(env.PUBLIC_R2_URL).rstrip("/") + "/"
    return url[len(base):] if url and url.startswith(base) else None

async def drop_if_unused(env, key):
    """Delete an old R2 object only when nothing in the database points to it any more."""
    if not key:
        return
    url = public_url(env, key)
    rows = await q(env,
        "SELECT (SELECT COUNT(*) FROM Parts WHERE image_url = ?) "
        "+ (SELECT COUNT(*) FROM Models WHERE picture_url = ?) "
        "+ (SELECT COUNT(*) FROM Vehicle_Generations WHERE overview_image_url = ?) "
        "+ (SELECT COUNT(*) FROM Brands WHERE logo_url = ?) "
        "+ (SELECT COUNT(*) FROM Users WHERE avatar_url = ?) "
        "+ (SELECT COUNT(*) FROM Part_Files WHERE r2_object_key = ?) AS n",
        url, url, url, url, url, key)
    if rows[0]["n"] == 0:
        try:
            await env.BUCKET.delete(key)
        except Exception:
            pass

@app.put("/api/admin/parts/{part_id}/image")
async def admin_part_image(part_id: int, data: PartImageIn, request: Request):
    env = request.scope["env"]
    admin = await require_admin(request)
    rows = await q(env, "SELECT image_url FROM Parts WHERE part_id = ?", part_id)
    if not rows:
        raise HTTPException(404, "Part not found.")
    key = data.image_key
    if not key.startswith(f"images/{admin['user_id']}/"):
        raise HTTPException(400, "Invalid picture reference.")
    if await env.BUCKET.head(key) is None:
        raise HTTPException(400, "The uploaded picture was not found. Please upload it again.")
    await run(env, "UPDATE Parts SET image_url = ?, updated_at = CURRENT_TIMESTAMP WHERE part_id = ?",
              public_url(env, key), part_id)
    await drop_if_unused(env, r2_key_from_url(env, rows[0]["image_url"]))
    return {"ok": True, "url": public_url(env, key)}

@app.put("/api/admin/parts/{part_id}/file")
async def admin_part_file(part_id: int, data: PartFileIn, request: Request):
    env = request.scope["env"]
    admin = await require_admin(request)
    parts = await q(env, "SELECT manufacturing_specs FROM Parts WHERE part_id = ?", part_id)
    if not parts:
        raise HTTPException(404, "Part not found.")
    key = data.file_key
    if not key.startswith(f"parts/{admin['user_id']}/"):
        raise HTTPException(400, "Invalid file reference.")
    ext = key.rsplit(".", 1)[-1].lower()
    if ext not in MODEL_EXT:
        raise HTTPException(400, "The 3D file must be STL, 3MF or STEP.")
    head = await env.BUCKET.head(key)
    if head is None:
        raise HTTPException(400, "The uploaded file was not found. Please upload it again.")
    ftype = {"3mf": "mesh_3mf", "step": "cad_step", "stp": "cad_step"}.get(ext, "mesh_stl")
    fname = key.rsplit("/", 1)[-1].split("-", 1)[-1]
    old = await q(env, "SELECT file_id, r2_object_key FROM Part_Files WHERE part_id = ? "
                       "ORDER BY file_id LIMIT 1", part_id)
    if old:
        # Same row, so download history stays attached to it.
        await run(env, "UPDATE Part_Files SET file_type = ?, r2_object_key = ?, file_name = ?, "
                       "file_size_bytes = ?, sha256_checksum = NULL WHERE file_id = ?",
                  ftype, key, fname, int(head.size), old[0]["file_id"])
        await drop_if_unused(env, old[0]["r2_object_key"])
    else:
        try:
            specs = json.loads(parts[0]["manufacturing_specs"] or "{}")
        except ValueError:
            specs = {}
        await run(env, "INSERT INTO Part_Files(part_id, file_type, r2_object_key, file_name, file_size_bytes, "
                       "recommended_material, recommended_infill_pct, supports_required) "
                       "VALUES(?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?)",
                  part_id, ftype, key, fname, int(head.size),
                  PART_FILE_MATERIALS.get(specs.get("material", ""), ""),
                  int(specs.get("infill_pct", 0) or 0), 1 if specs.get("supports") == "Yes" else 0)
    return {"ok": True}
# ---------- End replace files ----------
class PinIn(BaseModel):
    part_id: int
    x: float | None = None
    y: float | None = None


class HotspotsIn(BaseModel):
    generation_id: int
    pins: list[PinIn]


@app.put("/api/admin/hotspots")
async def admin_hotspots(data: HotspotsIn, request: Request):
    env = request.scope["env"]
    await require_admin(request)
    if not data.pins or len(data.pins) > 200:
        raise HTTPException(400, "Send between 1 and 200 pins.")
    for p in data.pins:
        if (p.x is None) != (p.y is None):
            raise HTTPException(400, "Give both x and y, or neither.")
        if p.x is not None and not (0 <= p.x <= 100 and 0 <= p.y <= 100):
            raise HTTPException(400, "Positions must be between 0 and 100.")
    for p in data.pins:
        x = -1 if p.x is None else round(p.x, 1)
        y = -1 if p.y is None else round(p.y, 1)
        await run(env, "UPDATE Part_Compatibilities SET hotspot_x = NULLIF(?, -1), "
                       "hotspot_y = NULLIF(?, -1) WHERE part_id = ? AND generation_id = ?",
                  x, y, p.part_id, data.generation_id)
    return {"ok": True}
# ---------- JSON import (admin) ----------
import difflib

STATUSES = ("draft", "community_tested", "verified_fit", "flagged")
MAX_VEHICLE_RECORDS = 400
MAX_PARTS = 100

class ImportIn(BaseModel):
    type: str
    dry_run: bool = True
    brands: list[dict] = []
    parts: list[dict] = []
    add_cars: list[dict] = []

def _s(v, n=200):
    return "" if v is None else str(v).strip()[:n]

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def car_label(brand, model, gen, yr):
    return " ".join(x for x in (brand, model, gen, f"({yr})") if x)

async def run_batch(env, stmts):
    try:
        from js import Array
        batch = Array.new()
        for sql, args in stmts:
            batch.push(env.DB.prepare(sql).bind(*args))
        await env.DB.batch(batch)
        return
    except Exception as e:
        print("D1 batch failed, writing one statement at a time:", e)
    for sql, args in stmts:
        await run(env, sql, *args)

async def load_cat(env, brand_names=None):
    cat = {"b": {}, "m": {}, "g": {}, "label": {}}
    for r in await q(env, "SELECT name FROM Brands"):
        cat["b"][r["name"].lower()] = r["name"]
    if brand_names is None:
        groups = [None]
    else:
        wanted = sorted({cat["b"][n.lower()] for n in brand_names if n.lower() in cat["b"]})
        groups = [wanted[i:i + 80] for i in range(0, len(wanted), 80)]
    for grp in groups:
        where = "" if grp is None else " WHERE b.name IN (" + ",".join("?" * len(grp)) + ")"
        args = () if grp is None else tuple(grp)
        for r in await q(env, "SELECT b.name AS brand, m.name AS model FROM Models m "
                              "JOIN Brands b ON b.brand_id = m.brand_id" + where, *args):
            cat["m"][(r["brand"].lower(), r["model"].lower())] = r["model"]
        for r in await q(env, "SELECT b.name AS brand, m.name AS model, g.generation_name AS gen, "
                              "g.start_year AS yr FROM Vehicle_Generations g "
                              "JOIN Models m ON m.model_id = g.model_id "
                              "JOIN Brands b ON b.brand_id = m.brand_id" + where, *args):
            gen = r["gen"] or ""
            cat["g"][(r["brand"].lower(), r["model"].lower(), gen.lower(), int(r["yr"]))] = gen
            label = car_label(r["brand"], r["model"], gen, int(r["yr"]))
            cat["label"][label.lower()] = label
    return cat

def vehicle_records(brands):
    total = 0
    for b in brands:
        total += 1
        models = b.get("models") if isinstance(b.get("models"), list) else []
        for m in models:
            total += 1
            if isinstance(m, dict) and isinstance(m.get("generations"), list):
                total += len(m["generations"])
    return total

def plan_vehicles(cat, brands, errors, stmts, n):
    for i, b in enumerate(brands):
        bname = _s(b.get("name"), 100)
        if not bname:
            errors.append(f"brand #{i + 1}: the name is required.")
            continue
        bp = f"brand '{bname}'"
        bk = bname.lower()
        if bk not in cat["b"]:
            cat["b"][bk] = bname
            n["brands"] += 1
            stmts.append(("INSERT OR IGNORE INTO Brands(name, logo_url) VALUES(?, NULLIF(?, ''))",
              (bname, _s(b.get("logo_url"), 500))))
        else:
            n["skipped"] += 1
        bcanon = cat["b"][bk]
        models = b.get("models") or []
        if not isinstance(models, list):
            errors.append(f"{bp}: 'models' must be a list.")
            continue
        for j, m in enumerate(models):
            mname = _s(m.get("name"), 100) if isinstance(m, dict) else ""
            if not mname:
                errors.append(f"{bp}, model #{j + 1}: the name is required.")
                continue
            mp = f"{bp} > model '{mname}'"
            mk = mname.lower()
            if (bk, mk) not in cat["m"]:
                cat["m"][(bk, mk)] = mname
                n["models"] += 1
                stmts.append(("INSERT INTO Models(brand_id, name, picture_url) "
                    "SELECT b.brand_id, ?, NULLIF(?, '') FROM Brands b "
                    "WHERE b.name = ? AND NOT EXISTS (SELECT 1 FROM Models m "
                    "WHERE m.brand_id = b.brand_id AND m.name = ?) LIMIT 1",
                    (mname, _s(m.get("picture_url"), 500), bcanon, mname)))
            else:
                n["skipped"] += 1
            mcanon = cat["m"][(bk, mk)]
            gens = m.get("generations") or []
            if not isinstance(gens, list):
                errors.append(f"{mp}: 'generations' must be a list.")
                continue
            for k, g in enumerate(gens):
                gp = f"{mp} > generation #{k + 1}"
                if not isinstance(g, dict):
                    errors.append(f"{gp}: must be an object.")
                    continue
                gname = _s(g.get("name"), 100)
                start, end = _int(g.get("start_year")), _int(g.get("end_year")) or 0
                if start is None or not (1900 <= start <= 2100):
                    errors.append(f"{gp}: start_year must be a year between 1900 and 2100.")
                    continue
                if end and not (start <= end <= 2100):
                    errors.append(f"{gp}: end_year must be between the start year and 2100.")
                    continue
                key = (bk, mk, gname.lower(), start)
                if key not in cat["g"]:
                    cat["g"][key] = gname
                    n["generations"] += 1
                    stmts.append(("INSERT INTO Vehicle_Generations(model_id, generation_name, start_year, "
                        "end_year, overview_image_url) "
                        "SELECT m.model_id, ?, ?, NULLIF(?, 0), NULLIF(?, '') FROM Models m "
                        "JOIN Brands b ON b.brand_id = m.brand_id "
                        "WHERE b.name = ? AND m.name = ? AND NOT EXISTS (SELECT 1 FROM Vehicle_Generations g "
                        "WHERE g.model_id = m.model_id AND COALESCE(g.generation_name, '') = ? "
                        "AND g.start_year = ?) LIMIT 1",
                        (gname, start, end, _s(g.get("photo_url"), 500), bcanon, mcanon, gname, start)))
                else:
                    n["skipped"] += 1

PART_INSERT = ("INSERT INTO Parts(sku, name, description, oem_part_number, category, "
               "disassembly_instructions, image_url, author_user_id, license, status, "
               "is_available_physical, physical_print_price, manufacturing_technique, "
               "material, manufacturing_specs) "
               "SELECT ?, ?, ?, NULLIF(?, ''), ?, ?, NULLIF(?, ''), ?, ?, ?, ?, NULLIF(?, 0), ?, "
               "NULLIF(?, ''), ? WHERE NOT EXISTS (SELECT 1 FROM Parts WHERE sku = ?)")

COMPAT_INSERT = ("INSERT OR IGNORE INTO Part_Compatibilities(part_id, generation_id, hotspot_x, hotspot_y) "
                 "SELECT p.part_id, g.generation_id, NULLIF(?, -1), NULLIF(?, -1) "
                 "FROM Parts p JOIN Vehicle_Generations g ON 1 = 1 "
                 "JOIN Models m ON m.model_id = g.model_id JOIN Brands b ON b.brand_id = m.brand_id "
                 "WHERE p.sku = ? AND b.name = ? AND m.name = ? "
                 "AND COALESCE(g.generation_name, '') = ? AND g.start_year = ? LIMIT 1")

async def plan_parts(env, cat, parts, errors, stmts, n, missing, admin_id):
    skus_db = {r["sku"] for r in await q(env, "SELECT sku FROM Parts WHERE sku IS NOT NULL")}
    names = sorted({_s(p.get("author"), 50) for p in parts if _s(p.get("author"), 50)})
    authors = {}
    if names:
        marks = ",".join("?" * len(names))
        for r in await q(env, f"SELECT user_id, username FROM Users WHERE username IN ({marks})", *names):
            authors[r["username"]] = r["user_id"]
    seen = set()
    for i, p in enumerate(parts):
        sku = _s(p.get("sku"), 64)
        if not sku or not all(c.isalnum() or c in "-_." for c in sku):
            errors.append(f"parts[{i}]: sku is required (letters, digits, - _ . only).")
            continue
        pp = f"part '{sku}'"
        if sku in seen:
            errors.append(f"{pp}: the sku appears twice in this file.")
            continue
        seen.add(sku)
        if sku in skus_db:
            n["skipped"] += 1
            continue

        name, desc = _s(p.get("name"), 150), _s(p.get("description"), 4000)
        if len(name) < 3:
            errors.append(f"{pp}: name needs 3 to 150 characters.")
        if not desc:
            errors.append(f"{pp}: description is required.")
        category = _s(p.get("category"))
        if category not in CATEGORIES:
            errors.append(f"{pp}: category must be one of {', '.join(sorted(CATEGORIES))}.")
        lic = _s(p.get("license")) or "CC-BY-SA-4.0"
        if lic not in LICENSES:
            errors.append(f"{pp}: license must be one of {', '.join(sorted(LICENSES))}.")
        status = _s(p.get("status")) or "draft"
        if status not in STATUSES:
            errors.append(f"{pp}: status must be one of {', '.join(STATUSES)}.")
        author = _s(p.get("author"), 50)
        if author and author not in authors:
            errors.append(f"{pp}: no user named '{author}'.")
        tech = _s(p.get("technique"))
        raw = p.get("specs") if isinstance(p.get("specs"), dict) else {}
        specs = {}
        try:
            specs = clean_specs(tech, raw)
        except HTTPException as e:
            errors.append(f"{pp}: {e.detail}")
        try:
            price = float(p.get("printed_price") or 0)
            if price < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{pp}: printed_price must be a number of 0 or more.")
            price = 0.0
        image = _s(p.get("image_url"), 500)
        if image and not image.startswith(("http://", "https://")):
            errors.append(f"{pp}: image_url must start with http:// or https://.")

        fits = p.get("fits")
        if not isinstance(fits, list) or not fits:
            errors.append(f"{pp}: 'fits' must list at least one car.")
            continue
        fit_args = []
        for j, f in enumerate(fits):
            fp = f"{pp}, fit #{j + 1}"
            if not isinstance(f, dict):
                errors.append(f"{fp}: must be an object.")
                continue
            brand, model, gen = _s(f.get("brand"), 100), _s(f.get("model"), 100), _s(f.get("generation"), 100)
            yr = _int(f.get("start_year"))
            if not brand or not model or yr is None:
                errors.append(f"{fp}: brand, model and start_year are required.")
                continue
            x, y = f.get("x"), f.get("y")
            pin = (-1.0, -1.0)
            if (x is None) != (y is None):
                errors.append(f"{fp}: give both x and y, or neither.")
                continue
            if x is not None:
                try:
                    pin = (round(float(x), 1), round(float(y), 1))
                except (TypeError, ValueError):
                    errors.append(f"{fp}: x and y must be numbers.")
                    continue
                if not (0 <= pin[0] <= 100 and 0 <= pin[1] <= 100):
                    errors.append(f"{fp}: x and y must be between 0 and 100.")
                    continue
            key = (brand.lower(), model.lower(), gen.lower(), yr)
            if key not in cat["g"]:
                item = missing.setdefault(key, {"brand": brand, "model": model, "generation": gen,
                                                "start_year": yr, "parts": []})
                if sku not in item["parts"]:
                    item["parts"].append(sku)
                continue
            fit_args.append((pin, key))

        steps = _s(p.get("removal_steps"), 4000) or "The author has not added removal steps yet."
        material = specs.get("material", "")
        stmts.append((PART_INSERT, (sku, name, desc, _s(p.get("oem_part_number"), 100), category, steps,
                                    image, authors.get(author, admin_id), lic, status,
                                    1 if price > 0 else 0, price, tech, material, json.dumps(specs), sku)))
        for pin, key in fit_args:
            stmts.append((COMPAT_INSERT, (pin[0], pin[1], sku, cat["b"][key[0]],
                                          cat["m"][(key[0], key[1])], cat["g"][key], key[3])))
        n["parts"] += 1
        n["fits"] += len(fit_args)

@app.post("/api/admin/import")
async def admin_import(data: ImportIn, request: Request):
    env = request.scope["env"]
    admin = await require_admin(request)
    errors, stmts, missing = [], [], {}
    n = {"brands": 0, "models": 0, "generations": 0, "parts": 0, "fits": 0, "skipped": 0}
    if data.type == "vehicles":
        cat = await load_cat(env, [_s(b.get("name"), 100) for b in data.brands])
    else:
        cat = await load_cat(env)

    if data.type == "vehicles":
        if vehicle_records(data.brands) > MAX_VEHICLE_RECORDS:
            raise HTTPException(400, f"Too many records in one request (limit {MAX_VEHICLE_RECORDS}).")
        plan_vehicles(cat, data.brands, errors, stmts, n)
    elif data.type == "parts":
        if len(data.parts) > MAX_PARTS:
            raise HTTPException(400, f"A parts file can hold at most {MAX_PARTS} parts.")
        approved = [{"name": _s(c.get("brand"), 100), "models": [{"name": _s(c.get("model"), 100),
                     "generations": [{"name": _s(c.get("generation"), 100), "start_year": c.get("start_year")}]}]}
                    for c in data.add_cars]
        plan_vehicles(cat, approved, errors, stmts, n)
        await plan_parts(env, cat, data.parts, errors, stmts, n, missing, admin["user_id"])
    else:
        raise HTTPException(400, "Unknown file type. Use \"vehicles\" or \"parts\".")

    missing_out = []
    for item in missing.values():
        label = car_label(item["brand"], item["model"], item["generation"], item["start_year"]).lower()
        close = difflib.get_close_matches(label, list(cat["label"]), n=1, cutoff=0.8)
        missing_out.append({**item, "parts": item["parts"][:10],
                            "suggestion": cat["label"][close[0]] if close else ""})

    ok = not errors and not missing
    applied = False
    if ok and not data.dry_run and stmts:
        try:
            await run_batch(env, stmts)
            applied = True
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            raise HTTPException(500, "The import stopped with an error. Records saved before the error are "
                                     f"kept, and it is safe to run the same file again. Details: {e}")
    return {"ok": ok, "dry_run": data.dry_run, "applied": applied, "counts": n,
            "errors": errors[:50], "error_total": len(errors), "missing": missing_out}
# ---------- End JSON import ----------
# ---------- User-added cars (any logged-in user can add; only admins can edit or delete) ----------
USER_DAILY_LIMIT = 30

class UserBrandIn(BaseModel):
    name: str

class UserModelIn(BaseModel):
    brand_id: int
    name: str
    image_key: str = ""

class UserGenIn(BaseModel):
    model_id: int
    generation_name: str = ""
    start_year: int
    end_year: int = 0
    image_key: str = ""

def tidy(text, lo, hi, what):
    out = " ".join((text or "").split())
    if not (lo <= len(out) <= hi):
        raise HTTPException(400, f"Enter a {what} ({lo} to {hi} characters).")
    return out

async def user_quota(env, uid):
    rows = await q(env,
        "SELECT (SELECT COUNT(*) FROM Brands WHERE created_by = ? AND created_at > datetime('now', '-1 day')) "
        "+ (SELECT COUNT(*) FROM Models WHERE created_by = ? AND created_at > datetime('now', '-1 day')) "
        "+ (SELECT COUNT(*) FROM Vehicle_Generations WHERE created_by = ? AND created_at > datetime('now', '-1 day')) AS n",
        uid, uid, uid)
    if rows[0]["n"] >= USER_DAILY_LIMIT:
        raise HTTPException(429, f"You can add up to {USER_DAILY_LIMIT} items per day. Please try again tomorrow.")

async def user_image_url(env, uid, key):
    if not key:
        return ""
    if not key.startswith(f"images/{uid}/"):
        raise HTTPException(400, "Invalid picture reference.")
    if await env.BUCKET.head(key) is None:
        raise HTTPException(400, "The uploaded picture was not found. Please upload it again.")
    return public_url(env, key)

@app.post("/api/brands")
async def user_add_brand(data: UserBrandIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    name = tidy(data.name, 2, 60, "brand name")
    for r in await q(env, "SELECT name FROM Brands"):
        if r["name"].lower() == name.lower():
            raise HTTPException(409, f"The brand '{r['name']}' already exists. Pick it from the list.")
    await user_quota(env, user["user_id"])
    rows = await q(env, "INSERT INTO Brands(name, created_by, created_at) "
                        "VALUES(?, ?, datetime('now')) RETURNING brand_id", name, user["user_id"])
    return {"id": rows[0]["brand_id"]}

@app.post("/api/models")
async def user_add_model(data: UserModelIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    name = tidy(data.name, 1, 60, "model name")
    if not await q(env, "SELECT brand_id FROM Brands WHERE brand_id = ?", data.brand_id):
        raise HTTPException(400, "Choose a valid brand.")
    for r in await q(env, "SELECT name FROM Models WHERE brand_id = ?", data.brand_id):
        if r["name"].lower() == name.lower():
            raise HTTPException(409, f"The model '{r['name']}' already exists for this brand. Pick it from the list.")
    await user_quota(env, user["user_id"])
    pic = await user_image_url(env, user["user_id"], data.image_key)
    rows = await q(env, "INSERT INTO Models(brand_id, name, picture_url, created_by, created_at) "
                        "VALUES(?, ?, NULLIF(?, ''), ?, datetime('now')) RETURNING model_id",
                   data.brand_id, name, pic, user["user_id"])
    return {"id": rows[0]["model_id"]}

@app.post("/api/generations")
async def user_add_generation(data: UserGenIn, request: Request):
    env = request.scope["env"]
    user = await require_user(request)
    gname = " ".join((data.generation_name or "").split())[:60]
    if not (1900 <= data.start_year <= 2100):
        raise HTTPException(400, "Enter a valid start year.")
    if data.end_year and not (data.start_year <= data.end_year <= 2100):
        raise HTTPException(400, "The end year cannot be before the start year.")
    if not await q(env, "SELECT model_id FROM Models WHERE model_id = ?", data.model_id):
        raise HTTPException(400, "Choose a valid model.")
    for r in await q(env, "SELECT generation_name, start_year FROM Vehicle_Generations WHERE model_id = ?", data.model_id):
        if r["start_year"] == data.start_year and (r["generation_name"] or "").lower() == gname.lower():
            raise HTTPException(409, "This generation already exists for this model. Pick it from the list.")
    await user_quota(env, user["user_id"])
    pic = await user_image_url(env, user["user_id"], data.image_key)
    rows = await q(env, "INSERT INTO Vehicle_Generations(model_id, generation_name, start_year, end_year, "
                        "overview_image_url, created_by, created_at) "
                        "VALUES(?, ?, ?, NULLIF(?, 0), NULLIF(?, ''), ?, datetime('now')) RETURNING generation_id",
                   data.model_id, gname, data.start_year, data.end_year, pic, user["user_id"])
    return {"id": rows[0]["generation_id"]}
# ---------- End user-added cars ----------
# ---------- End admin ----------
SPEC_SCHEMA = {
    "FDM": {"label": "FDM (filament 3D printing)", "fields": [
        {"key": "material", "label": "Material", "type": "select", "required": True,
         "options": ["PLA", "PETG", "ABS", "ASA", "TPU", "Nylon", "PA-CF", "PC", "Other"]},
        {"key": "nozzle_mm", "label": "Nozzle diameter (mm)", "type": "select",
         "options": ["0.2", "0.4", "0.6", "0.8"]},
        {"key": "layer_height_mm", "label": "Layer height (mm)", "type": "number",
         "min": 0.05, "max": 0.6, "step": 0.01, "hint": "0.12 to 0.28 mm is typical on a 0.4 mm nozzle"},
        {"key": "walls", "label": "Wall count (perimeters)", "type": "number", "min": 1, "max": 20, "step": 1},
        {"key": "infill_pct", "label": "Infill (%)", "type": "number", "min": 0, "max": 100, "step": 1},
        {"key": "supports", "label": "Supports needed", "type": "select", "options": ["No", "Yes"]},
        {"key": "orientation", "label": "Print orientation", "type": "text", "max": 200,
         "hint": "e.g. flat on the bed, clip facing up"},
        {"key": "nozzle_temp_c", "label": "Nozzle temperature (°C)", "type": "number", "min": 150, "max": 450, "step": 1},
        {"key": "bed_temp_c", "label": "Bed temperature (°C)", "type": "number", "min": 0, "max": 150, "step": 1},
    ]},
    "SLS": {"label": "SLS (powder sintering)", "fields": [
        {"key": "material", "label": "Material", "type": "select", "required": True,
         "options": ["PA12", "PA11", "PA12 glass-filled", "PA12 carbon-filled", "TPU", "Other"]},
        {"key": "layer_um", "label": "Layer thickness (µm)", "type": "number", "min": 50, "max": 300, "step": 10},
        {"key": "min_wall_mm", "label": "Thinnest wall in the design (mm)", "type": "number",
         "min": 0.3, "max": 10, "step": 0.1,
         "hint": "About 0.7 mm is the usual minimum for PA12, about 2 mm for carbon-filled"},
        {"key": "escape_holes", "label": "Powder escape holes", "type": "select",
         "options": ["Not hollow", "Yes (3.5 mm or larger)", "No"]},
        {"key": "finish", "label": "Finish", "type": "select",
         "options": ["As printed", "Tumbled / bead blasted", "Dyed", "Vapor smoothed", "Painted"]},
        {"key": "orientation", "label": "Orientation notes", "type": "text", "max": 200},
    ]},
    "SLA": {"label": "SLA (resin printing)", "fields": [
        {"key": "material", "label": "Resin type", "type": "select", "required": True,
         "options": ["Standard", "Tough", "Flexible", "High-temperature", "Castable", "Clear", "Other"]},
        {"key": "layer_height_mm", "label": "Layer height (mm)", "type": "number",
         "min": 0.025, "max": 0.2, "step": 0.005, "hint": "0.025 to 0.2 mm is the usual range"},
        {"key": "angle_deg", "label": "Tilt angle from the plate (°)", "type": "number",
         "min": 0, "max": 90, "step": 5, "hint": "30 to 45° is common"},
        {"key": "supports", "label": "Supports needed", "type": "select", "options": ["No", "Yes"]},
        {"key": "drain_holes", "label": "Drain holes", "type": "select",
         "options": ["Not hollow", "Yes (4 mm or larger)", "No"]},
        {"key": "wash_min", "label": "Wash time (minutes)", "type": "number", "min": 0, "max": 180, "step": 1},
        {"key": "cure_min", "label": "UV post-cure time (minutes)", "type": "number", "min": 0, "max": 180, "step": 1},
    ]},
    "CNC": {"label": "CNC machining", "fields": [
        {"key": "material", "label": "Material", "type": "select", "required": True,
         "options": ["Aluminium 6061", "Aluminium 7075", "Brass", "Stainless steel", "Mild steel",
                     "POM (Delrin)", "ABS", "Nylon", "Other"]},
        {"key": "axes", "label": "Machine axes", "type": "select",
         "options": ["3-axis", "3+2 / 4-axis", "5-axis"]},
        {"key": "tolerance_mm", "label": "General tolerance (± mm)", "type": "number",
         "min": 0.005, "max": 1, "step": 0.005, "hint": "±0.1 mm is typical for non-critical features"},
        {"key": "finish_ra", "label": "Surface finish (Ra)", "type": "select",
         "options": ["3.2 µm (standard)", "1.6 µm (fine)", "0.8 µm (very fine)"]},
        {"key": "min_radius_mm", "label": "Smallest inside corner radius (mm)", "type": "number",
         "min": 0.1, "max": 20, "step": 0.1, "hint": "Must be at least the radius of the cutting tool"},
        {"key": "stock", "label": "Raw stock size", "type": "text", "max": 100, "hint": "e.g. 60 x 40 x 20 mm"},
        {"key": "surface_treatment", "label": "Surface treatment", "type": "select",
         "options": ["None", "Anodised", "Bead blasted", "Powder coated", "Polished", "Other"]},
    ]},
}

PART_FILE_MATERIALS = {"PLA": "PLA", "PETG": "PETG", "ABS": "ABS", "ASA": "ASA", "TPU": "TPU", "PA-CF": "PA_CF"}


def clean_specs(technique, raw):
    schema = SPEC_SCHEMA.get(technique)
    if not schema:
        raise HTTPException(400, "Choose a manufacturing technique.")
    out = {}
    for fld in schema["fields"]:
        val = raw.get(fld["key"])
        val = "" if val is None else str(val).strip()
        if val == "":
            if fld.get("required"):
                raise HTTPException(400, f"{fld['label']} is required.")
            continue
        if fld["type"] == "select":
            if val not in fld["options"]:
                raise HTTPException(400, f"Invalid value for {fld['label']}.")
            out[fld["key"]] = val
        elif fld["type"] == "number":
            try:
                n = float(val)
            except ValueError:
                raise HTTPException(400, f"{fld['label']} must be a number.")
            if not (fld["min"] <= n <= fld["max"]):
                raise HTTPException(400, f"{fld['label']} must be between {fld['min']} and {fld['max']}.")
            out[fld["key"]] = int(n) if n == int(n) else n
        else:
            out[fld["key"]] = val[: fld.get("max", 200)]
    return out
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request.js_object, self.env)
