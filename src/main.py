from workers import WorkerEntrypoint
from fastapi import FastAPI, Request
import asgi

app = FastAPI()


async def q(env, sql, *args):
    stmt = env.DB.prepare(sql)
    if args:
        stmt = stmt.bind(*args)
    res = await stmt.all()
    return res.results.to_py()


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/catalog")
async def catalog(request: Request):
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


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request.js_object, self.env)