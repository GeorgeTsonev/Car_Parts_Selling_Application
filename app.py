import os, hashlib, re
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, jsonify, send_from_directory, abort)
from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
engine = create_engine(os.environ.get("DATABASE_URL", f"sqlite:///{BASE}/carparts.db"))
ALLOWED = {"stl": "mesh_stl", "3mf": "mesh_3mf", "step": "cad_step", "stp": "cad_step", "pdf": "assembly_manual_pdf"}

def q(sql, **p):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), p)]
def q1(sql, **p):
    r = q(sql, **p); return r[0] if r else None
def x(sql, **p):
    with engine.begin() as c:
        r = c.execute(text(sql), p); return r.lastrowid

def store_file(fs, key):
    """Dev: local disk. Production: upload to Cloudflare R2 via boto3 (S3 API)."""
    path = os.path.join(UPLOADS, key); os.makedirs(os.path.dirname(path), exist_ok=True)
    fs.save(path); h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return os.path.getsize(path), h

def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get("uid"):
            flash("Please log in first."); return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return wrap

@app.context_processor
def inject():
    n = 0
    if session.get("uid"):
        n = q1("SELECT COALESCE(SUM(quantity),0) n FROM Cart_Items WHERE user_id=:u", u=session["uid"])["n"]
    return dict(cart_count=n, me=session.get("uname"))

@app.route("/")
def home():
    return render_template("home.html", brands=q("SELECT * FROM Brands ORDER BY name"))

@app.route("/api/models")
def api_models():
    return jsonify(q("SELECT model_id, name FROM Models WHERE brand_id=:b ORDER BY name", b=request.args.get("brand_id", 0, int)))

@app.route("/car/<int:model_id>")
def car(model_id):
    m = q1("SELECT m.*, b.name brand FROM Models m JOIN Brands b USING(brand_id) WHERE model_id=:m", m=model_id) or abort(404)
    gens = q("SELECT * FROM Vehicle_Generations WHERE model_id=:m ORDER BY start_year", m=model_id)
    if not gens: return render_template("car.html", m=m, gens=[], gen=None, parts=[])
    gid = request.args.get("gen", gens[0]["generation_id"], int)
    gen = next((g for g in gens if g["generation_id"] == gid), gens[0])
    parts = q("""SELECT p.part_id,p.sku,p.name,p.description,p.is_available_physical,p.physical_print_price,
                 p.status,pc.hotspot_x,pc.hotspot_y,pc.notes
                 FROM Part_Compatibilities pc JOIN Parts p USING(part_id)
                 WHERE pc.generation_id=:g AND p.parent_part_id IS NULL ORDER BY p.name""", g=gen["generation_id"])
    return render_template("car.html", m=m, gens=gens, gen=gen, parts=parts)

@app.route("/search")
def search():
    term = request.args.get("q", "").strip(); like = f"%{term.lower()}%"
    rows = q("""SELECT DISTINCT p.part_id,p.sku,p.name,p.description,p.oem_part_number,p.status,
                p.is_available_physical,p.physical_print_price
                FROM Parts p LEFT JOIN Part_Compatibilities pc USING(part_id)
                LEFT JOIN Vehicle_Generations g USING(generation_id)
                LEFT JOIN Models m ON m.model_id=g.model_id LEFT JOIN Brands b ON b.brand_id=m.brand_id
                WHERE p.parent_part_id IS NULL AND (LOWER(p.name) LIKE :l OR LOWER(p.description) LIKE :l
                OR LOWER(COALESCE(p.oem_part_number,'')) LIKE :l OR LOWER(p.sku) LIKE :l
                OR LOWER(COALESCE(m.name,'')) LIKE :l OR LOWER(COALESCE(b.name,'')) LIKE :l)
                ORDER BY p.name""", l=like) if term else []
    return render_template("search.html", term=term, rows=rows)

@app.route("/part/<int:pid>")
def part(pid):
    p = q1("SELECT p.*, u.username FROM Parts p JOIN Users u ON u.user_id=p.author_user_id WHERE part_id=:p", p=pid) or abort(404)
    return render_template("part.html", p=p,
        spec=q1("SELECT * FROM Part_3D_Specifications WHERE part_id=:p", p=pid),
        files=q("SELECT * FROM Part_Files WHERE part_id=:p", p=pid),
        compat=q("""SELECT b.name brand,m.name model,g.generation_name,g.start_year,g.end_year,pc.notes
                    FROM Part_Compatibilities pc JOIN Vehicle_Generations g USING(generation_id)
                    JOIN Models m USING(model_id) JOIN Brands b USING(brand_id) WHERE pc.part_id=:p""", p=pid),
        children=q("""SELECT c.part_id,c.name,a.quantity_required,a.assembly_sequence_order FROM Part_Assemblies a
                      JOIN Parts c ON c.part_id=a.child_part_id WHERE a.parent_part_id=:p ORDER BY a.assembly_sequence_order""", p=pid),
        reviews=q("""SELECT r.*,u.username FROM Part_Reviews r JOIN Users u USING(user_id)
                     WHERE part_id=:p ORDER BY created_at DESC""", p=pid))

@app.route("/part/<int:pid>/review", methods=["POST"])
@login_required
def review(pid):
    f = request.form
    x("""INSERT INTO Part_Reviews (part_id,user_id,fitment_rating,print_success_rating,material_used,comment)
         VALUES (:p,:u,:f,:s,:m,:c)""", p=pid, u=session["uid"], f=int(f["fit"]), s=int(f["print"]),
      m=f.get("material"), c=f.get("comment"))
    return redirect(url_for("part", pid=pid))

@app.route("/download/<int:fid>")
@login_required
def download(fid):
    f = q1("SELECT * FROM Part_Files WHERE file_id=:f", f=fid) or abort(404)
    x("INSERT INTO Part_Download_Logs (part_id,file_id,user_id,ip_country) VALUES (:p,:f,:u,NULL)",
      p=f["part_id"], f=fid, u=session["uid"])
    key = f["r2_object_key"]
    return send_from_directory(UPLOADS, key, as_attachment=True, download_name=f["file_name"])

@app.route("/cart")
@login_required
def cart():
    items = q("""SELECT c.*,p.name,p.sku,p.physical_print_price price FROM Cart_Items c
                 JOIN Parts p USING(part_id) WHERE c.user_id=:u""", u=session["uid"])
    total = sum(float(i["price"]) * i["quantity"] for i in items)
    return render_template("cart.html", items=items, total=total)

@app.route("/cart/add/<int:pid>", methods=["POST"])
@login_required
def cart_add(pid):
    p = q1("SELECT is_available_physical FROM Parts WHERE part_id=:p", p=pid)
    if not p or not p["is_available_physical"]: abort(400)
    mat = request.form.get("material", "ASA"); mat = mat if mat in ("PETG", "ASA", "PA_CF") else "ASA"
    ex = q1("SELECT cart_item_id FROM Cart_Items WHERE user_id=:u AND part_id=:p", u=session["uid"], p=pid)
    if ex: x("UPDATE Cart_Items SET quantity=quantity+1 WHERE cart_item_id=:i", i=ex["cart_item_id"])
    else: x("INSERT INTO Cart_Items (user_id,part_id,material_printed,color) VALUES (:u,:p,:m,:c)",
            u=session["uid"], p=pid, m=mat, c=request.form.get("color", "Black")[:30])
    return redirect(url_for("cart"))

@app.route("/cart/remove/<int:iid>", methods=["POST"])
@login_required
def cart_remove(iid):
    x("DELETE FROM Cart_Items WHERE cart_item_id=:i AND user_id=:u", i=iid, u=session["uid"])
    return redirect(url_for("cart"))

@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    items = q("""SELECT c.*,p.physical_print_price price FROM Cart_Items c JOIN Parts p USING(part_id)
                 WHERE c.user_id=:u""", u=session["uid"])
    addr = request.form.get("address", "").strip()
    if not items or not addr:
        flash("Cart empty or address missing."); return redirect(url_for("cart"))
    total = sum(float(i["price"]) * i["quantity"] for i in items)
    oid = x("INSERT INTO Physical_Orders (user_id,total_amount,shipping_address) VALUES (:u,:t,:a)",
            u=session["uid"], t=total, a=addr)
    for i in items:
        x("""INSERT INTO Physical_Order_Items (order_id,part_id,quantity,unit_price,material_printed,color)
             VALUES (:o,:p,:q,:pr,:m,:c)""", o=oid, p=i["part_id"], q=i["quantity"], pr=i["price"],
          m=i["material_printed"], c=i["color"])
    x("DELETE FROM Cart_Items WHERE user_id=:u", u=session["uid"])
    flash(f"Order #{oid} created. Payment integration (e.g. Stripe) is the next step.")
    return redirect(url_for("profile", uid=session["uid"]))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        f = request.form; u, e, pw = f["username"].strip(), f["email"].strip().lower(), f["password"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", u) or "@" not in e or len(pw) < 8:
            flash("Valid username (3-50 chars), email and 8+ char password required.")
        elif q1("SELECT 1 x FROM Users WHERE username=:u OR email=:e", u=u, e=e):
            flash("Username or email already used.")
        else:
            uid = x("INSERT INTO Users (username,name,email,password_hash,role) VALUES (:u,:u,:e,:h,'contributor')",
                    u=u, e=e, h=generate_password_hash(pw))
            session.update(uid=uid, uname=u); return redirect(url_for("profile", uid=uid))
    return render_template("auth.html", mode="signup")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        f = request.form
        u = q1("SELECT * FROM Users WHERE email=:e OR username=:e", e=f["login"].strip().lower()) or \
            q1("SELECT * FROM Users WHERE username=:e", e=f["login"].strip())
        if u and check_password_hash(u["password_hash"], f["password"]):
            session.update(uid=u["user_id"], uname=u["username"])
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        flash("Invalid credentials.")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("home"))

@app.route("/profile/<int:uid>", methods=["GET", "POST"])
def profile(uid):
    u = q1("SELECT * FROM Users WHERE user_id=:u", u=uid) or abort(404)
    if request.method == "POST":
        if session.get("uid") != uid: abort(403)
        av = u["avatar_url"]; fs = request.files.get("avatar")
        if fs and fs.filename and fs.filename.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "webp"):
            key = f"avatars/{uid}_{secure_filename(fs.filename)}"; store_file(fs, key); av = "/media/" + key
        x("UPDATE Users SET name=:n,bio=:b,contact_info=:c,avatar_url=:a WHERE user_id=:u",
          n=request.form["name"], b=request.form["bio"], c=request.form["contact"], a=av, u=uid)
        return redirect(url_for("profile", uid=uid))
    creations = q("SELECT part_id,name,status,version FROM Parts WHERE author_user_id=:u ORDER BY created_at DESC", u=uid)
    orders = q("SELECT * FROM Physical_Orders WHERE user_id=:u ORDER BY created_at DESC", u=uid) if session.get("uid") == uid else []
    return render_template("profile.html", u=u, creations=creations, orders=orders, own=session.get("uid") == uid)

@app.route("/media/<path:key>")
def media(key): return send_from_directory(UPLOADS, key)

@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    cats = ["interior", "exterior", "underhood", "trim_clip", "lighting_bracket"]
    gens = q("""SELECT g.generation_id, b.name||' '||m.name||' '||COALESCE(g.generation_name,'')||' ('||g.start_year||')' label
                FROM Vehicle_Generations g JOIN Models m USING(model_id) JOIN Brands b USING(brand_id)
                ORDER BY 2""") if engine.dialect.name == "sqlite" else q("""
                SELECT g.generation_id, CONCAT(b.name,' ',m.name,' ',COALESCE(g.generation_name,''),' (',g.start_year,')') label
                FROM Vehicle_Generations g JOIN Models m USING(model_id) JOIN Brands b USING(brand_id) ORDER BY 2""")
    if request.method == "POST":
        f = request.form; fs = request.files.get("file")
        ext = fs.filename.rsplit(".", 1)[-1].lower() if fs and fs.filename and "." in fs.filename else ""
        if ext not in ALLOWED or f["category"] not in cats:
            flash("Upload a .stl, .3mf, .step or .pdf file and choose a category."); return redirect(request.url)
        lic = f.get("license", "CC-BY-SA-4.0")
        pid = x("""INSERT INTO Parts (sku,name,description,oem_part_number,category,disassembly_instructions,
                   author_user_id,license,status) VALUES (:s,:n,:d,:o,:c,:i,:u,:l,'draft')""",
                s="UP-" + os.urandom(4).hex().upper(), n=f["name"], d=f["description"], o=f.get("oem") or None,
                c=f["category"], i=f.get("disassembly"), u=session["uid"], l=lic)
        key = f"schematics/{pid}/{secure_filename(fs.filename)}"; size, h = store_file(fs, key)
        x("""INSERT INTO Part_Files (part_id,file_type,r2_object_key,file_name,file_size_bytes,sha256_checksum,
             recommended_material,recommended_infill_pct,supports_required) VALUES (:p,:t,:k,:n,:s,:h,:m,:i,:sp)""",
          p=pid, t=ALLOWED[ext], k=key, n=secure_filename(fs.filename), s=size, h=h,
          m=f.get("material", "ASA"), i=int(f.get("infill", 40)), sp=1 if f.get("supports") else 0)
        for g in request.form.getlist("generation"):
            x("INSERT INTO Part_Compatibilities (part_id,generation_id) VALUES (:p,:g)", p=pid, g=int(g))
        return redirect(url_for("part", pid=pid))
    return render_template("upload.html", cats=cats, gens=gens)

if __name__ == "__main__":
    app.run(debug=True)
