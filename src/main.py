from workers import WorkerEntrypoint
from fastapi import FastAPI
import asgi

app = FastAPI()

@app.get("/api/health")
async def health():
    return {"ok": True}

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request.js_object, self.env)