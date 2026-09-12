import asyncio
import base64
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError, model_validator
from pymongo import MongoClient

import auth
from planner import plan as solve

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("suitcase")

RIGIDITIES = ("rigid", "soft", "fragile")
PROMPT = (
    "Identify the main object in this photo; it is about to be packed in a suitcase. "
    'Reply with JSON only: {"label": <2-4 word name>, "description": <one sentence: what it is, material, '
    'anything that matters for packing>, "rigidity": <"rigid"|"soft"|"fragile">, "compressibility": <number>, '
    '"mass": <estimated kg>, "keepUpright": <true|false>}. '
    "fragile = breaks if crushed or dropped; soft = compresses (clothes, bags); rigid = everything else. "
    "compressibility = the item's loose volume divided by its volume when squeezed hard into a suitcase: "
    "1 for rigid or fragile items; for soft items roughly 1.3 (jeans, towel), 2 (t-shirt, socks), 3 (down jacket, pillow). "
    "mass = your best estimate in kg. keepUpright = true only for liquid containers or open vessels that must stay "
    "upright (bottles, cups, jars, vases); false for every other item no matter how fragile. "
    "If the photo has no clear packable object, or is too small or blurry to identify one confidently, reply "
    '{"label": "unknown", "rigidity": "rigid", "compressibility": 1, "mass": 0, "keepUpright": false} rather than guessing.'
)
UNKNOWN = {"label": "unknown", "description": "", "rigidity": "rigid", "compressibility": 1.0, "mass": 0.0, "keepUpright": False}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # a LiDAR scan's photo has no business being bigger than this
LABEL_RETRY_S = float(os.environ.get("LABEL_RETRY_S", 10))
LABEL_MAX_ATTEMPTS = int(os.environ.get("LABEL_MAX_ATTEMPTS", 5))
SOURCES = {"label": "labelSource", "rigidity": "rigiditySource", "compressibility": "compressibilitySource",
           "mass": "massSource", "keepUpright": "keepUprightSource"}


def compressibility(value, rigidity: str) -> float:
    """k = loose volume / squeezed volume. 1.0 unless soft; clamped to [1, 10] since it comes from an LLM."""
    if rigidity != "soft":
        return 1.0
    try:
        return min(10.0, max(1.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "suitcase")]
try:
    db.client.admin.command("ping")  # fail at startup, not on the first request
except Exception as exc:  # pymongo's ServerSelectionTimeoutError, or a bad URI
    raise SystemExit(f"cannot reach MongoDB ({exc}); start it with `make mongo` or set SUITCASE_MONGODB_URI") from None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Background labeller: keep sweeping the pending items, never dying on one bad sweep."""
    async def loop():
        while True:
            try:
                n = await asyncio.to_thread(relabel_pending)
                if n:
                    logging.info("background labeller: labelled %d item(s)", n)
            except Exception:
                logging.exception("background labeller: sweep failed")
            await asyncio.sleep(LABEL_RETRY_S)

    task = asyncio.create_task(loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    logger.info("%s %s %d %.1fms", request.method, request.url.path, response.status_code, (time.monotonic() - start) * 1000)
    return response


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
    if not isinstance(out, dict):  # a JSON array or bare string is garbage like any other bad answer
        raise ValueError(f"expected a JSON object from Grok, got {type(out).__name__}")
    rigidity = out.get("rigidity") if out.get("rigidity") in RIGIDITIES else "rigid"
    try:
        mass = min(50.0, max(0.0, float(out.get("mass", 0))))
    except (TypeError, ValueError):
        mass = 0.0
    return {"label": str(out.get("label", "unknown"))[:60], "description": str(out.get("description", ""))[:200],
            "rigidity": rigidity, "compressibility": compressibility(out.get("compressibility"), rigidity),
            "mass": mass, "keepUpright": out.get("keepUpright") is True}


def label_item(doc: dict) -> bool:
    """Label one pending item from its stored photo; True if it came back labelled.

    A guessed field is overwritten only while its `*Source` is still "auto" (a missing source
    counts as auto, for items stored before sources existed); `description` has no `*Source`
    and no PATCH route, so it is always auto. No stored photo, or LABEL_MAX_ATTEMPTS failed
    attempts, marks the item "failed" and stops the retries.
    """
    item_id = doc["_id"]
    if not doc.get("photo"):
        db.items.update_one({"_id": item_id}, {"$set": {"labelStatus": "failed"}})
        return False
    try:
        guess = detect(doc["photo"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        attempts = doc.get("labelAttempts", 0) + 1
        logging.warning("labelling item %s failed (attempt %d): %s", item_id, attempts, exc)
        status = "failed" if attempts >= LABEL_MAX_ATTEMPTS else "pending"
        db.items.update_one({"_id": item_id}, {"$inc": {"labelAttempts": 1}, "$set": {"labelStatus": status}})
        return False
    fields = {k: v for k, v in guess.items() if k not in SOURCES or doc.get(SOURCES[k], "auto") == "auto"}
    db.items.update_one({"_id": item_id}, {"$set": fields | {"labelStatus": "done"}})
    return True


def relabel_pending() -> int:
    """Retry Grok for every item whose labelling failed at upload; returns how many got labelled."""
    return sum(label_item(doc) for doc in list(db.items.find({"labelStatus": "pending"})))


def public(doc: dict) -> dict:
    """What the routes return: never the Mongo id, never the stored photo bytes."""
    return {k: v for k, v in doc.items() if k not in ("_id", "photo")}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_user(claims: dict = Depends(auth.require_auth)) -> str:
    """Verified Auth0 claims -> the user's sub, upserting a users record on every authenticated request."""
    sub = claims["sub"]
    db.users.update_one({"_id": sub}, {"$set": {"email": claims.get("email"), "last_seen": now()}}, upsert=True)
    return sub


def owned(doc: dict | None, user: str) -> dict:
    """A found document the requesting user is allowed to modify, or the matching HTTPException."""
    if doc is None:
        raise HTTPException(404, "no such resource")
    if doc.get("owner_id") != user:
        logger.warning("auth rejected: user %s is not the owner of %s", user, doc.get("_id"))
        raise HTTPException(403, "not the owner of this resource")
    return doc


class NewSuitcase(BaseModel):
    name: str
    dimensions: list[float]  # [width, height, depth] in metres


@app.post("/suitcases")
def create_suitcase(s: NewSuitcase, user: str = Depends(current_user)):
    if len(s.dimensions) != 3 or min(s.dimensions) <= 0:
        raise HTTPException(422, "dimensions must be three positive numbers in metres")
    doc = {"_id": str(uuid.uuid4()), "id": None, "name": s.name.strip()[:60], "dimensions": s.dimensions,
           "owner_id": user, "createdAt": now()}
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
def delete_suitcase(suitcase_id: str, user: str = Depends(current_user)):
    """Remove a suitcase and its plan; its items are detached, so they stay in the owner's inventory."""
    owned(db.suitcases.find_one({"_id": suitcase_id}), user)
    db.suitcases.delete_one({"_id": suitcase_id})
    db.items.update_many({"suitcaseId": suitcase_id}, {"$set": {"suitcaseId": None}})
    db.plans.delete_one({"_id": suitcase_id})
    return {"deleted": suitcase_id}


@app.post("/suitcases/{suitcase_id}/plan")
def create_plan(suitcase_id: str, user: str = Depends(current_user)):
    suitcase = owned(db.suitcases.find_one({"_id": suitcase_id}), user)
    items = list(db.items.find({"suitcaseId": suitcase_id}))
    if not items:
        raise HTTPException(409, "suitcase has no scanned items to pack")
    # labelling is not worth blocking a plan for; the app shows how many labels are still coming
    pending = sum(i.get("labelStatus") == "pending" for i in items)
    doc = {"_id": suitcase_id, "suitcaseId": suitcase_id, "createdAt": now()} | solve(suitcase, items) | {"pendingLabels": pending}
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
def create_item(background: BackgroundTasks, item: str = Form(...), image: UploadFile = File(...),
                later: bool = Query(False, alias="async"), user: str = Depends(current_user)):
    try:
        doc = Scan.model_validate_json(item).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    item_id, suitcase_id = doc["id"], doc["suitcaseId"]
    owned(db.suitcases.find_one({"_id": suitcase_id}), user)
    existing = db.items.find_one({"_id": item_id})
    if existing is not None:
        owned(existing, user)
    jpeg = image.file.read(MAX_IMAGE_BYTES + 1)
    if len(jpeg) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"image must be at most {MAX_IMAGE_BYTES} bytes")
    background_label = later and bool(os.environ.get("XAI_API_KEY"))  # ?async=1: don't make the phone wait 6 s for Grok
    if background_label:
        guess, status = dict(UNKNOWN), "pending"
    else:
        try:
            guess, status = detect(jpeg), "done"
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            # x.ai down, rate-limited or returning garbage must not lose the scan; relabel_pending retries it.
            logging.warning("grok labelling failed, item %s saved as unknown: %s", item_id, exc)
            guess, status = dict(UNKNOWN), "pending"
    doc |= guess | {
        "_id": item_id, "owner_id": user, "photo": jpeg, "labelStatus": status,
        "labelSource": "auto", "rigiditySource": "auto", "compressibilitySource": "auto",
        "massSource": "auto", "keepUprightSource": "auto",
        "createdAt": now(),
    }
    if background_label:
        doc["labelAttempts"] = 0
        background.add_task(label_item, doc)  # runs after this response is sent; a failure leaves it to the sweep
    db.items.replace_one({"_id": item_id}, doc, upsert=True)
    return public(doc)


class Patch(BaseModel):
    label: str | None = None
    rigidity: str | None = None
    compressibility: float | None = None
    mass: float | None = None
    keepUpright: bool | None = None


@app.patch("/items/{item_id}")
def update_item(item_id: str, patch: Patch, user: str = Depends(current_user)):
    owned(db.items.find_one({"_id": item_id}), user)
    if patch.rigidity is not None and patch.rigidity not in RIGIDITIES:
        raise HTTPException(422, f"rigidity must be one of {RIGIDITIES}")
    if patch.compressibility is not None and not patch.compressibility >= 1:
        raise HTTPException(422, "compressibility must be >= 1")
    if patch.mass is not None and not patch.mass >= 0:
        raise HTTPException(422, "mass must be >= 0")
    fields = {}
    if patch.mass is not None:
        fields |= {"mass": float(patch.mass), "massSource": "user"}
    if patch.keepUpright is not None:
        fields |= {"keepUpright": patch.keepUpright, "keepUprightSource": "user"}
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


@app.get("/inventory")
def list_inventory(user: str = Depends(current_user)):
    """The caller's items across every suitcase, newest first; survives DELETE /suitcases."""
    return [public(d) for d in db.items.find({"owner_id": user}).sort("createdAt", -1)]


@app.get("/items/{item_id}")
def get_item(item_id: str):
    """One item; the app polls this while `labelStatus` is "pending"."""
    doc = db.items.find_one({"_id": item_id})
    if doc is None:
        raise HTTPException(404, "no such item")
    return public(doc)


@app.delete("/items/{item_id}")
def delete_item(item_id: str, user: str = Depends(current_user)):
    doc = owned(db.items.find_one({"_id": item_id}), user)
    db.items.delete_one({"_id": item_id})
    db.plans.delete_one({"_id": doc["suitcaseId"]})  # the stored plan no longer matches the items
    return {"deleted": item_id}
