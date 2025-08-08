import json
import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

DATA_FILE = "/data/homegpt_settings.json"

app = FastAPI()

# Load/save helpers
def load_settings():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"mode": "passive", "exclude": []}

def save_settings(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

# Serve static UI
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

@app.get("/")
async def index():
    return FileResponse("/app/static/index.html")

@app.get("/api/settings")
async def get_settings():
    return load_settings()

@app.post("/api/settings")
async def update_settings(req: Request):
    data = await req.json()
    save_settings(data)
    return {"status": "ok"}

@app.post("/api/run_analysis")
async def run_analysis():
    # You'd trigger your analysis logic here
    return {"status": "triggered"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8099)
