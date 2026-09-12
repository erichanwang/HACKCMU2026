import base64
import json
import os
import uuid
from datetime import datetime, timezone

from typing import Literal

import anthropic
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pymongo import MongoClient

from planner import plan as solve

RIGIDITIES = ("rigid", "soft", "fragile")
PROMPT = (
    "Identify the main object in this photo; it is about to be packed in a suitcase. "
    "label: a 2-4 word name. description: one sentence — what it is, material, anything that matters for packing. "
    "rigidity: fragile = breaks if crushed or dropped; soft = compresses (clothes, bags); rigid = everything else. "
    "compressibility = the item's loose volume divided by its volume when squeezed hard into a suitcase: "
    "1 for rigid or fragile items; for soft items roughly 1.3 (jeans, towel), 2 (t-shirt, socks), 3 (down jacket, pillow). "
    "mass: your best estimate in kg. keepUpright: true only if it must stay this side up (liquids, open containers)."
)
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
UNKNOWN = {"label": "unknown", "description": "", "rigidity": "rigid", "compressibility": 1.0, "mass": 0.0, "keepUpright": False}


def compressibility(value, rigidity: str) -> float:
    """k = loose volume / squeezed volume. 1.0 unless soft; clamped to [1, 10] since it comes from an LLM."""
    if rigidity != "soft":
        return 1.0
    try:
        return min(10.0, max(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


if "<db_password>" in os.environ.get("SUITCASE_MONGODB_URI", ""):
    raise SystemExit("SUITCASE_MONGODB_URI still contains <db_password> — a stale `export` in this shell? Run: unset SUITCASE_MONGODB_URI")
db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "suitcase")]
db.client.admin.command("ping")  # fail at startup, not on the first request
app = FastAPI()


class Guess(BaseModel):
    label: str
    description: str
    rigidity: Literal["rigid", "soft", "fragile"]
    compressibility: float
    mass: float
    keepUpright: bool


def detect(image: bytes) -> dict:
    """Ask Claude what the object is. 502 with the reason when it can't answer; the app shows that."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return dict(UNKNOWN)
    media_type = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
    try:
        r = anthropic.Anthropic().messages.parse(
            model=MODEL,
            max_tokens=1024,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": PROMPT},
            ]}],
            output_format=Guess,
        )
        out = r.parsed_output
        if out is None:
            raise ValueError(f"no parsed output (stop_reason={r.stop_reason})")
    except (anthropic.APIError, ValueError) as e:
        msg = f"labelling failed ({type(e).__name__}): {str(e)[:200]}"
        print(msg, flush=True)
        raise HTTPException(502, msg)
    return {"label": out.label[:60], "description": out.description[:200], "rigidity": out.rigidity,
            "compressibility": compressibility(out.compressibility, out.rigidity),
            "mass": min(50.0, max(0.0, out.mass)), "keepUpright": out.keepUpright}


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


@app.post("/label")
def label_image(image: UploadFile = File(...)):
    """Identify an object from a photo without storing anything; the app shows this for confirmation first."""
    return detect(image.file.read())


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


@app.post("/items")
def create_item(item: str = Form(...), image: UploadFile | None = File(None)):
    try:
        doc = json.loads(item)
        item_id, suitcase_id = str(doc["id"]), str(doc["suitcaseId"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "item must be JSON with id and suitcaseId")
    if db.suitcases.find_one({"_id": suitcase_id}) is None:
        raise HTTPException(404, "no such suitcase")
    try:
        guess = detect(image.file.read()) if image is not None else dict(UNKNOWN)
    except HTTPException:
        guess = dict(UNKNOWN)  # the scan is still worth storing; the user can fix the label by hand
    sources = {"labelSource": "auto", "rigiditySource": "auto", "compressibilitySource": "auto"}
    if image is None:  # confirmed or typed-in item: the fields the app sends are authoritative
        for field in ("label", "description", "rigidity", "compressibility", "mass", "keepUpright"):
            if doc.get(field) is not None:
                guess[field] = doc[field]
        for field in ("label", "rigidity", "compressibility"):
            if doc.get(field) is not None:
                sources[field + "Source"] = doc.get(field + "Source") if doc.get(field + "Source") in ("auto", "user") else "user"
        guess["rigidity"] = guess["rigidity"] if guess["rigidity"] in RIGIDITIES else "rigid"
        guess["compressibility"] = compressibility(guess["compressibility"], guess["rigidity"])
        try:
            guess["mass"] = min(50.0, max(0.0, float(guess["mass"])))
        except (TypeError, ValueError):
            guess["mass"] = 0.0
        guess["keepUpright"] = guess["keepUpright"] is True
    prev = db.items.find_one({"_id": item_id}) or {}  # a rescan keeps what the user typed
    for field in ("label", "rigidity", "compressibility"):
        if prev.get(field + "Source") == "user":
            guess[field] = prev[field]
            sources[field + "Source"] = "user"
    doc |= guess | sources | {"_id": item_id, "createdAt": prev.get("createdAt") or now()}
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
    return [public(d) for d in db.items.find({"suitcaseId": suitcaseId} if suitcaseId else {}).sort("createdAt", 1)]


@app.delete("/items/{item_id}")
def delete_item(item_id: str):
    if db.items.delete_one({"_id": item_id}).deleted_count == 0:
        raise HTTPException(404, "no such item")
    return {"deleted": item_id}


@app.delete("/suitcases/{suitcase_id}")
def delete_suitcase(suitcase_id: str):
    if db.suitcases.delete_one({"_id": suitcase_id}).deleted_count == 0:
        raise HTTPException(404, "no such suitcase")
    db.items.delete_many({"suitcaseId": suitcase_id})
    return {"deleted": suitcase_id}
