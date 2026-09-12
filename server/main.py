import base64
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pymongo import MongoClient

import packing

# Secrets live in .env.local at the repo root (git-ignored). `override=False` so a
# real environment variable always beats the file -- deployments set env vars, and
# nobody has to edit a checked-out file to point at a different cluster.
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local", override=False)

RIGIDITIES = ("rigid", "soft", "fragile")
PROMPT = (
    "Identify the main object in this photo; it is about to be packed in a suitcase. "
    'Reply with JSON only: {"label": <2-4 word name>, "rigidity": <"rigid"|"soft"|"fragile">}. '
    "fragile = breaks if crushed or dropped; soft = compresses (clothes, bags); rigid = everything else."
)

db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "suitcase")]
db.client.admin.command("ping")  # fail at startup, not on the first request
app = FastAPI()


def detect(jpeg: bytes) -> dict:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        return {"label": "unknown", "rigidity": "rigid"}
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
    rigidity = out.get("rigidity")
    return {"label": str(out.get("label", "unknown"))[:60], "rigidity": rigidity if rigidity in RIGIDITIES else "rigid"}


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


@app.post("/items")
def create_item(item: str = Form(...), image: UploadFile = File(...)):
    try:
        doc = json.loads(item)
        item_id, suitcase_id = str(doc["id"]), str(doc["suitcaseId"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "item must be JSON with id and suitcaseId")
    if db.suitcases.find_one({"_id": suitcase_id}) is None:
        raise HTTPException(404, "no such suitcase")
    guess = detect(image.file.read())
    doc |= {
        "_id": item_id,
        "label": guess["label"], "labelSource": "auto",
        "rigidity": guess["rigidity"], "rigiditySource": "auto",
        "createdAt": now(),
    }
    db.items.replace_one({"_id": item_id}, doc, upsert=True)
    return public(doc)


class Patch(BaseModel):
    label: str | None = None
    rigidity: str | None = None


@app.patch("/items/{item_id}")
def update_item(item_id: str, patch: Patch):
    if patch.rigidity is not None and patch.rigidity not in RIGIDITIES:
        raise HTTPException(422, f"rigidity must be one of {RIGIDITIES}")
    fields = {}
    if patch.label is not None:
        fields |= {"label": patch.label.strip()[:60], "labelSource": "user"}
    if patch.rigidity is not None:
        fields |= {"rigidity": patch.rigidity, "rigiditySource": "user"}
    doc = db.items.find_one_and_update({"_id": item_id}, {"$set": fields}, return_document=True) if fields else db.items.find_one({"_id": item_id})
    if doc is None:
        raise HTTPException(404, "no such item")
    return public(doc)


@app.get("/items")
def list_items(suitcaseId: str | None = None):
    return [public(d) for d in db.items.find({"suitcaseId": suitcaseId} if suitcaseId else {})]


class Voxels(BaseModel):
    """Sparse coloured occupancy from the scanner (see Spike/Voxels.swift).

    Parallel arrays rather than a list of objects: three ints per voxel in `indices`,
    three bytes per voxel in `colours`, one count per voxel in `observations`.
    """
    voxelSize: float
    origin: list[float]
    indices: list[int]
    colours: list[int] = []
    observations: list[int] = []
    colourCoverage: float = 0.0


class VoxelUpload(BaseModel):
    voxels: Voxels
    viewCoverage: float | None = None


# Voxels ride in their own JSON body rather than the multipart form that carries the
# photo, because Starlette caps a single form part at 1 MB and a carry-on scan is already
# ~0.6 MB. Splitting them keeps labelling fast, lets a rescan improve geometry without
# re-running vision, and sidesteps a limit that is not configurable per route in FastAPI.
@app.put("/items/{item_id}/voxels")
def put_voxels(item_id: str, body: VoxelUpload):
    v = body.voxels
    n = len(v.indices) // 3
    if len(v.indices) % 3:
        raise HTTPException(422, "indices must hold three values per voxel")
    if v.observations and len(v.observations) != n:
        raise HTTPException(422, f"observations has {len(v.observations)} entries for {n} voxels")
    if v.colours and len(v.colours) != n * 3:
        raise HTTPException(422, f"colours has {len(v.colours)} entries for {n} voxels")
    if v.voxelSize <= 0:
        raise HTTPException(422, "voxelSize must be positive")
    # Past this size the scan captured the room rather than the object; say so plainly
    # instead of letting Mongo's 16 MB document ceiling produce a driver error.
    if n > 400_000:
        raise HTTPException(413, f"voxel payload too large ({n} voxels) — the scan likely captured "
                                 "the surroundings; rescan closer to the object")

    fields: dict = {"voxels": v.model_dump(), "voxelCount": n}
    if body.viewCoverage is not None:
        fields["viewCoverage"] = body.viewCoverage
    doc = db.items.find_one_and_update({"_id": item_id}, {"$set": fields}, return_document=True)
    if doc is None:
        raise HTTPException(404, "no such item")
    return public(doc)


class PackRequest(BaseModel):
    dimensions: list[float]  # [width, height, depth] in metres, team frame
    maxMassKg: float | None = None
    suitcaseId: str | None = None
    itemIds: list[str] | None = None
    timeBudgetS: float = 3.0


@app.post("/pack")
def pack(req: PackRequest):
    """Solve a layout for the scanned items and return it in the team frame.

    Items default to the whole collection; narrow with `suitcaseId` and/or `itemIds`.
    The response is the optimised layout only (no naive/optimized wrapper) and states
    its own coordinate convention, since packer3d solves in a different frame.
    """
    if len(req.dimensions) != 3 or min(req.dimensions) <= 0:
        raise HTTPException(422, "dimensions must be three positive numbers in metres")
    # A phone is waiting on this, so the solver is not allowed to think for long.
    budget = max(0.1, min(float(req.timeBudgetS), 10.0))

    query: dict = {}
    if req.suitcaseId is not None:
        if db.suitcases.find_one({"_id": req.suitcaseId}) is None:
            raise HTTPException(404, "no such suitcase")
        query["suitcaseId"] = req.suitcaseId
    if req.itemIds is not None:
        query["_id"] = {"$in": req.itemIds}
    docs = [public(d) for d in db.items.find(query)]
    if not docs:
        raise HTTPException(404, "no items to pack")

    try:
        return packing.pack_documents(
            docs, req.dimensions,
            max_mass=req.maxMassKg, time_budget_s=budget,
        )
    except ValueError as e:  # unusable scan data, not a server fault
        raise HTTPException(422, str(e))
