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
    'Reply with JSON only: {"label": <2-4 word name>, "rigidity": <"rigid"|"soft"|"fragile">}. '
    "fragile = breaks if crushed or dropped; soft = compresses (clothes, bags); rigid = everything else."
)

db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"))[os.environ.get("MONGO_DB", "suitcase")]
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


@app.post("/items")
def create_item(item: str = Form(...), image: UploadFile = File(...)):
    try:
        doc = json.loads(item)
        item_id = str(doc["id"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "item must be JSON with an id")
    guess = detect(image.file.read())
    doc |= {
        "_id": item_id,
        "label": guess["label"], "labelSource": "auto",
        "rigidity": guess["rigidity"], "rigiditySource": "auto",
        "createdAt": datetime.now(timezone.utc).isoformat(),
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
def list_items():
    return [public(d) for d in db.items.find()]
