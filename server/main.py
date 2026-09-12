import base64
import json
import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pymongo import MongoClient

RIGIDITIES = ("rigid", "soft", "fragile")
PROMPT = (
    "Identify the main object in this photo; it is about to be packed in a suitcase. "
    'Reply with JSON only: {"label": <2-4 word name>, "description": <one sentence: what it is, material, '
    'anything that matters for packing>, "rigidity": <"rigid"|"soft"|"fragile">, "compressibility": <number>, '
    '"mass": <estimated kg>, "keepUpright": <true|false>}. '
    "fragile = breaks if crushed or dropped; soft = compresses (clothes, bags); rigid = everything else. "
    "compressibility = the item's loose volume divided by its volume when squeezed hard into a suitcase: "
    "1 for rigid or fragile items; for soft items roughly 1.3 (jeans, towel), 2 (t-shirt, socks), 3 (down jacket, pillow). "
    "mass = your best estimate in kg. keepUpright = true only if it must stay this side up (liquids, open containers)."
)
UNKNOWN = {"label": "unknown", "description": "", "rigidity": "rigid", "compressibility": 1.0, "mass": 0.0, "keepUpright": False}


def compressibility(value, rigidity: str) -> float:
    """k = loose volume / squeezed volume. 1.0 unless soft; clamped to [1, 10] since it comes from an LLM."""
    if rigidity != "soft":
        return 1.0
    try:
        return min(10.0, max(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0

db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"))[os.environ.get("MONGO_DB", "suitcase")]
app = FastAPI()


def detect(jpeg: bytes) -> dict:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return dict(UNKNOWN)
    r = httpx.post(
        "https://api.x.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("GROK_MODEL", "grok-4"),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": PROMPT},
            ]}],
        },
        timeout=30,
    )
    r.raise_for_status()
    out = json.loads(r.json()["choices"][0]["message"]["content"])
    rigidity = out.get("rigidity") if out.get("rigidity") in RIGIDITIES else "rigid"
    try:
        mass = min(50.0, max(0.0, float(out.get("mass", 0))))
    except (TypeError, ValueError):
        mass = 0.0
    return {"label": str(out.get("label", "unknown"))[:60], "description": str(out.get("description", ""))[:200],
            "rigidity": rigidity, "compressibility": compressibility(out.get("compressibility"), rigidity),
            "mass": mass, "keepUpright": out.get("keepUpright") is True}


def public(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


@app.post("/items")
def create_item(item: str = Form(...), image: UploadFile = File(...)):
    try:
        doc = json.loads(item)
        item_id = str(doc["id"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "item must be JSON with an id")
    guess = detect(image.file.read())
    doc |= guess | {
        "_id": item_id,
        "labelSource": "auto", "rigiditySource": "auto", "compressibilitySource": "auto",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    db.items.replace_one({"_id": item_id}, doc, upsert=True)
    return public(doc)


class Patch(BaseModel):
    label: str | None = None
    rigidity: str | None = None
    compressibility: float | None = None


@app.patch("/items/{item_id}")
def update_item(item_id: str, patch: Patch):
    if patch.rigidity is not None and patch.rigidity not in RIGIDITIES:
        raise HTTPException(422, f"rigidity must be one of {RIGIDITIES}")
    if patch.compressibility is not None and not patch.compressibility >= 1:
        raise HTTPException(422, "compressibility must be >= 1")
    fields = {}
    if patch.label is not None:
        fields |= {"label": patch.label.strip()[:60], "labelSource": "user"}
    if patch.rigidity is not None:
        fields |= {"rigidity": patch.rigidity, "rigiditySource": "user"}
    if patch.compressibility is not None:
        fields |= {"compressibility": float(patch.compressibility), "compressibilitySource": "user"}
    doc = db.items.find_one_and_update({"_id": item_id}, {"$set": fields}, return_document=True) if fields else db.items.find_one({"_id": item_id})
    if doc is None:
        raise HTTPException(404, "no such item")
    return public(doc)


@app.get("/items")
def list_items():
    return [public(d) for d in db.items.find()]
