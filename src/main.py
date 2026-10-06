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
        "INSERT INTO Parts(sku, name, description, category, disassembly_instructions, "
        "author_user_id, license, image_url, manufacturing_technique, material, "
        "manufacturing_specs, status) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?, 'draft') RETURNING part_id",
        "U-" + secrets.token_hex(6), name, desc, data.category, steps, uid,
        data.license, image_url, data.technique, material, json.dumps(specs))
    pid = rows[0]["part_id"]

    await run(env, "INSERT INTO Part_Compatibilities(part_id, generation_id, hotspot_x, hotspot_y) "
                   "VALUES(?, ?, 50, 50)", pid, data.generation_id)
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
    rows = await q(env, "SELECT file_id, part_id, file_type, file_name, r2_object_key, "
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