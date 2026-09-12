import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError, model_validator
from pymongo import MongoClient

from planner import plan as solve

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


db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "suitcase")]
db.client.admin.command("ping")  # fail at startup, not on the first request
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


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NewSuitcase(BaseModel):
    name: str
    dimensions: list[float]  # [width, height, depth] in metres


@app.post("/suitcases")
def create_suitcase(s: NewSuitcase):
    if len(s.dimensions) != 3 or min(s.dimensions) <= 0:
        raise HTTPException(422, "dimensions must be three positive numbers in metres")
    doc = {"_id": str(uuid.uuid4()), "id": None, "name": s.name.strip()[:60], "dimensions": s.dimensions, "createdAt": now()}
    doc["id"] = doc["_id"]
    db.suitcases.insert_one(doc)
    return public(doc)


@app.get("/suitcases")
def list_suitcases():
    return [public(d) for d in db.suitcases.find().sort("createdAt", -1)]


@app.get("/suitcases/{suitcase_id}")
def get_suitcase(suitcase_id: str):
    doc = db.suitcases.find_one({"_id": suitcase_id})
    if doc is None:
        raise HTTPException(404, "no such suitcase")
    return public(doc) | {"items": [public(d) for d in db.items.find({"suitcaseId": suitcase_id})]}


@app.delete("/suitcases/{suitcase_id}")
def delete_suitcase(suitcase_id: str):
    """Remove a suitcase with its items and plan, so demo runs do not pile up."""
    if db.suitcases.delete_one({"_id": suitcase_id}).deleted_count == 0:
        raise HTTPException(404, "no such suitcase")
    db.items.delete_many({"suitcaseId": suitcase_id})
    db.plans.delete_one({"_id": suitcase_id})
    return {"deleted": suitcase_id}


@app.post("/suitcases/{suitcase_id}/plan")
def create_plan(suitcase_id: str):
    suitcase = db.suitcases.find_one({"_id": suitcase_id})
    if suitcase is None:
        raise HTTPException(404, "no such suitcase")
    items = list(db.items.find({"suitcaseId": suitcase_id}))
    if not items:
        raise HTTPException(409, "suitcase has no scanned items to pack")
    doc = {"_id": suitcase_id, "suitcaseId": suitcase_id, "createdAt": now()} | solve(suitcase, items)
    db.plans.replace_one({"_id": suitcase_id}, doc, upsert=True)
    return public(doc)


@app.get("/suitcases/{suitcase_id}/plan")
def get_plan(suitcase_id: str):
    doc = db.plans.find_one({"_id": suitcase_id})
    if doc is None:
        raise HTTPException(404, "no plan for this suitcase yet; POST this URL to make one")
    return public(doc)


Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class Scan(BaseModel, extra="allow"):
    """The phone's ScannedItem (SCAN_OUTPUT.md); anything malformed is refused at upload, not inside the solver."""
    id: str = Field(min_length=1, max_length=100)
    suitcaseId: str = Field(min_length=1, max_length=100)
    dimensions: tuple[Positive, Positive, Positive]
    cellSize: Positive
    heights: list[list[Annotated[float, Field(ge=0, allow_inf_nan=False)]]]

    @model_validator(mode="after")
    def rectangular(self):
        widths = {len(row) for row in self.heights}
        if len(widths) != 1 or 0 in widths or len(self.heights) * widths.pop() > 100_000:
            raise ValueError("heights must be a nonempty rectangular grid of at most 100000 cells")
        return self


@app.post("/items")
def create_item(item: str = Form(...), image: UploadFile = File(...)):
    try:
        doc = Scan.model_validate_json(item).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    item_id, suitcase_id = doc["id"], doc["suitcaseId"]
    if db.suitcases.find_one({"_id": suitcase_id}) is None:
        raise HTTPException(404, "no such suitcase")
    try:
        guess = detect(image.file.read())
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # x.ai down, rate-limited or returning garbage must not lose the scan; the app's editor fixes the label.
        logging.warning("grok labelling failed, item %s saved as unknown: %s", item_id, exc)
        guess = dict(UNKNOWN)
    doc |= guess | {
        "_id": item_id,
        "labelSource": "auto", "rigiditySource": "auto", "compressibilitySource": "auto",
        "createdAt": now(),
    }
    db.items.replace_one({"_id": item_id}, doc, upsert=True)
    return public(doc)


class Patch(BaseModel):
    label: str | None = None
    rigidity: str | None = None
    compressibility: float | None = None
    mass: float | None = None
    keepUpright: bool | None = None


@app.patch("/items/{item_id}")
def update_item(item_id: str, patch: Patch):
    if patch.rigidity is not None and patch.rigidity not in RIGIDITIES:
        raise HTTPException(422, f"rigidity must be one of {RIGIDITIES}")
    if patch.compressibility is not None and not patch.compressibility >= 1:
        raise HTTPException(422, "compressibility must be >= 1")
    if patch.mass is not None and not patch.mass >= 0:
        raise HTTPException(422, "mass must be >= 0")
    fields = {}
    if patch.mass is not None:
        fields |= {"mass": float(patch.mass)}
    if patch.keepUpright is not None:
        fields |= {"keepUpright": patch.keepUpright}
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
def list_items(suitcaseId: str | None = None):
    return [public(d) for d in db.items.find({"suitcaseId": suitcaseId} if suitcaseId else {})]


@app.delete("/items/{item_id}")
def delete_item(item_id: str):
    doc = db.items.find_one_and_delete({"_id": item_id})
    if doc is None:
        raise HTTPException(404, "no such item")
    db.plans.delete_one({"_id": doc["suitcaseId"]})  # the stored plan no longer matches the items
    return {"deleted": item_id}
