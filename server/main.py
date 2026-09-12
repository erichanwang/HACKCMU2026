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


# socketTimeoutMS: a Mongo that accepts the socket and then stops answering (paused container,
# laptop sleep) must fail the request, not hang every handler; serverSelection only covers discovery.
db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=8000,
                 connectTimeoutMS=8000, socketTimeoutMS=15000)[os.environ.get("MONGO_DB", "suitcase")]
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


def _parse_guess(out: dict, source: str) -> dict:
    if not isinstance(out, dict):  # a JSON array or bare string is garbage like any other bad answer
        raise ValueError(f"expected a JSON object from {source}, got {type(out).__name__}")
    rigidity = out.get("rigidity") if out.get("rigidity") in RIGIDITIES else "rigid"
    try:
        mass = min(50.0, max(0.0, float(out.get("mass", 0))))
    except (TypeError, ValueError):
        mass = 0.0
    return {"label": str(out.get("label", "unknown"))[:60], "description": str(out.get("description", ""))[:200],
            "rigidity": rigidity, "compressibility": compressibility(out.get("compressibility"), rigidity),
            "mass": mass, "keepUpright": out.get("keepUpright") is True}


def _grok_detect(jpeg: bytes, key: str) -> dict:
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
    return _parse_guess(json.loads(r.json()["choices"][0]["message"]["content"]), "Grok")


def _claude_detect(jpeg: bytes, key: str) -> dict:
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": PROMPT},
            ]}],
        },
        timeout=30,
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"].strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return _parse_guess(json.loads(text), "Claude")


def _gemini_detect(jpeg: bytes, key: str) -> dict:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json={
            "contents": [{"parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                {"text": PROMPT},
            ]}],
            # thinkingBudget 0: this is a classification call, and 2.5-flash otherwise
            # spends its thinking budget before answering — enough to hit the timeout and
            # add half a minute of dead latency to every scan in the mixture.
            "generationConfig": {"responseMimeType": "application/json",
                                 "thinkingConfig": {"thinkingBudget": 0}},
        },
        timeout=30,
    )
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return _parse_guess(json.loads(text), "Gemini")


def _is_unknown(guess: dict) -> bool:
    return guess["label"].strip().lower() == "unknown"


LABEL_MODELS = ("both", "grok", "claude", "gemini")

# Ask order, which is also the tie-break order when the vote splits evenly: the model
# most trusted on a disagreement comes first. Each entry is (choice, env key, function
# name) — the name rather than the function, so it is resolved at call time and a
# detector stays substitutable.
_DETECTORS = (
    ("claude", "ANTHROPIC_API_KEY", "_claude_detect"),
    ("gemini", "GEMINI_API_KEY", "_gemini_detect"),
    ("grok", "XAI_API_KEY", "_grok_detect"),
)


def detect(jpeg: bytes, prefer: str = "both") -> dict:
    """Ask every configured model, or just the one `prefer` names, and reconcile.

    `prefer` is "both" (the mixture: ask each configured model and arbitrate) or the name
    of a single model. It narrows which keys are consulted; it cannot conjure a key that is
    not configured, so asking for a model with no key set reads the same as having no model
    configured at all.

    Reconciling, in order: a model that declines ("unknown") yields to any model that
    identified the item, since declining is not a disagreement. Among the rest the most
    common label wins, and an even split goes to whichever of those models comes first in
    `_DETECTORS` — Claude, then Gemini, then Grok. Only when every configured model
    declines does this return "unknown"; the caller turns that into a distinct, terminal
    labelStatus, since a retry against the same stored photo cannot change a model's mind.
    """
    wanted = [d for d in _DETECTORS if prefer in ("both", d[0]) and os.environ.get(d[1])]
    if not wanted:
        return dict(UNKNOWN)

    guesses: list[tuple[str, dict]] = []
    first_error: Exception | None = None
    for name, env_key, func_name in wanted:
        try:
            guesses.append((name, globals()[func_name](jpeg, os.environ[env_key])))
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            first_error = first_error or exc
    if not guesses:
        raise first_error  # every configured model's call failed; this is not a decline

    answered = [g for g in guesses if not _is_unknown(g[1])]
    if not answered:
        return guesses[0][1]  # everyone declined
    if len(answered) == 1:
        return answered[0][1]

    counts: dict[str, int] = {}
    for _, guess in answered:
        counts[guess["label"].strip().lower()] = counts.get(guess["label"].strip().lower(), 0) + 1
    best = max(counts.values())
    winners = {label for label, n in counts.items() if n == best}
    if len(counts) > 1:
        logger.info("label vote %s, taking %s",
                    {n: g["label"] for n, g in answered}, sorted(winners))
    # _DETECTORS order is the tie-break: the first model holding a winning label wins.
    for name, guess in answered:
        if guess["label"].strip().lower() in winners:
            return guess
    return answered[0][1]


def identify_hint(dims: list[float], heights: list[list[float]]) -> str:
    """Best guess at why identification failed. Nothing was even tried if no model is configured —
    that reads as a broken demo, not scan advice, so it gets its own honest message. Otherwise, the
    crop is only as good as the scan, so a degenerate scan gets "rescan" advice rather than the
    misleading "rotate it"."""
    if not any(os.environ.get(env_key) for _, env_key, _ in _DETECTORS):
        return "labelling is off — type it in"
    flat = [h for row in heights for h in row]
    relief = max(flat) - min(flat) if flat else 0.0
    longest, shortest = max(dims), min(dims)
    # Each case names the move that would fix it, because "try again" tells nobody what
    # to do differently. The scan's own geometry says which move that is.
    if longest > 0.8:
        return "it filled the frame — step back about a metre and scan it again"
    if shortest < 0.03:
        return "too small in frame to make out — move closer, about an arm's length away, and scan it again"
    if relief < 0.01:
        return "the scan came out flat — lower the phone towards the item's own height and scan it again"
    if relief < 0.04:
        return "only one face came through — step left or right and scan it again from the side"
    return "couldn't tell what this is — move a step closer, or round to one side, and scan it again"


def resolve_label_status(doc: dict, guess: dict) -> str:
    """"done" once a real label sticks (ours or one the user already typed), else the terminal "unidentified"

    — a retry against the same stored photo cannot change a model's mind, so this never goes back to
    "pending"."""
    return "done" if doc.get("labelSource", "auto") != "auto" or not _is_unknown(guess) else "unidentified"


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
        guess = detect(doc["photo"], doc.get("labelModel", "both"))
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        attempts = doc.get("labelAttempts", 0) + 1
        logging.warning("labelling item %s failed (attempt %d): %s", item_id, attempts, exc)
        status = "failed" if attempts >= LABEL_MAX_ATTEMPTS else "pending"
        db.items.update_one({"_id": item_id}, {"$inc": {"labelAttempts": 1}, "$set": {"labelStatus": status}})
        return False
    fields = {k: v for k, v in guess.items() if k not in SOURCES or doc.get(SOURCES[k], "auto") == "auto"}
    status = resolve_label_status(doc, guess)
    if status == "unidentified":
        fields["identifyHint"] = identify_hint(doc["dimensions"], doc["heights"])
    db.items.update_one({"_id": item_id}, {"$set": fields | {"labelStatus": status}})
    return status == "done"


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


# Reads are owner-scoped like the writes. In open mode every caller is the one "local" user, so
# the scoping is vacuous there by design; with Auth0 configured nobody reads another user's rows.
@app.get("/suitcases")
def list_suitcases(user: str = Depends(current_user)):
    return [public(d) for d in db.suitcases.find({"owner_id": user}).sort("createdAt", -1)]


@app.get("/suitcases/{suitcase_id}")
def get_suitcase(suitcase_id: str, user: str = Depends(current_user)):
    doc = owned(db.suitcases.find_one({"_id": suitcase_id}), user)
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
    try:
        result = solve(suitcase, items)
    except (ValueError, TypeError) as exc:
        # A stored scan the solver cannot read (Scan validates uploads, but not documents that
        # predate a check). Refuse this plan, never 500 it; str(exc)[:200] because packer3d's
        # message can quote the whole document, photo bytes included.
        logger.warning("plan for %s refused: %s", suitcase_id, str(exc)[:200])
        raise HTTPException(422, f"a scanned item is unusable, delete it and rescan: {str(exc)[:200]}")
    doc = {"_id": suitcase_id, "suitcaseId": suitcase_id, "createdAt": now()} | result | {"pendingLabels": pending}
    db.plans.replace_one({"_id": suitcase_id}, doc, upsert=True)
    return public(doc)


@app.get("/suitcases/{suitcase_id}/plan")
def get_plan(suitcase_id: str, user: str = Depends(current_user)):
    owned(db.suitcases.find_one({"_id": suitcase_id}), user)
    doc = db.plans.find_one({"_id": suitcase_id})
    if doc is None:
        raise HTTPException(404, "no plan for this suitcase yet; POST this URL to make one")
    return public(doc)


Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class Scan(BaseModel, extra="ignore"):
    """The phone's ScannedItem (SCAN_OUTPUT.md); anything malformed is refused at upload, not inside the solver.

    extra="ignore", not "allow": every field below is what the solver reads, and an undeclared one
    (`count`, `priority`, `width`, a malformed `footprint`) used to land in packer3d unchecked, where
    it either 500s every plan for this suitcase or multiplies the item without a cap.
    """
    id: str = Field(min_length=1, max_length=100)
    suitcaseId: str = Field(min_length=1, max_length=100)
    dimensions: tuple[Positive, Positive, Positive]
    cellSize: Positive
    heights: list[list[Annotated[float, Field(ge=0, allow_inf_nan=False)]]]
    # The LiDAR hull, local (x, z) in metres, 3+ vertices; convexity is checked where the geometry is built.
    footprint: Annotated[list[tuple[Finite, Finite]], Field(min_length=3, max_length=256)] | None = None
    # Base64 PNG baked client-side (Spike/ScanView.swift's bakeColorMap); stored and returned
    # as-is, never read by the solver. 2MB covers a grid at the maxItemDimensionMeters cap.
    colorMap: Annotated[str, Field(max_length=2_000_000)] | None = None

    @model_validator(mode="after")
    def rectangular(self):
        widths = {len(row) for row in self.heights}
        if len(widths) != 1 or 0 in widths or len(self.heights) * widths.pop() > 100_000:
            raise ValueError("heights must be a nonempty rectangular grid of at most 100000 cells")
        return self


@app.post("/items")
def create_item(background: BackgroundTasks, item: str = Form(...), image: UploadFile = File(...),
                model: str = Form("both"), later: bool = Query(False, alias="async"),
                user: str = Depends(current_user)):
    try:
        doc = Scan.model_validate_json(item).model_dump(mode="json", exclude_none=True)  # no footprint: no key, not null
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    if model not in LABEL_MODELS:
        raise HTTPException(422, f"model must be one of {', '.join(LABEL_MODELS)}")
    item_id, suitcase_id = doc["id"], doc["suitcaseId"]
    owned(db.suitcases.find_one({"_id": suitcase_id}), user)
    existing = db.items.find_one({"_id": item_id})
    if existing is not None:
        owned(existing, user)
    jpeg = image.file.read(MAX_IMAGE_BYTES + 1)
    if len(jpeg) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"image must be at most {MAX_IMAGE_BYTES} bytes")
    # ?async=1: don't make the phone wait for a labelling model to answer.
    background_label = later and any(os.environ.get(env_key) for _, env_key, _ in _DETECTORS)
    if background_label:
        guess, status = dict(UNKNOWN), "pending"
    else:
        try:
            guess = detect(jpeg, model)
            status = resolve_label_status(doc, guess)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            # every configured model's call itself failed (down, rate-limited, garbage) — this is not
            # the same as a model confidently declining, and relabel_pending can still retry it.
            logging.warning("labelling failed, item %s saved as unknown: %s", item_id, exc)
            guess, status = dict(UNKNOWN), "pending"
    doc |= guess | {
        "_id": item_id, "owner_id": user, "photo": jpeg, "labelStatus": status,
        "labelModel": model,  # the background sweep re-asks the same model the scan chose
        "labelSource": "auto", "rigiditySource": "auto", "compressibilitySource": "auto",
        "massSource": "auto", "keepUprightSource": "auto",
        "createdAt": now(),
    }
    if status == "unidentified":
        doc["identifyHint"] = identify_hint(doc["dimensions"], doc["heights"])
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
    suitcaseId: str | None = None  # present-and-null takes the item out of any bag; absent leaves it alone


@app.patch("/items/{item_id}")
def update_item(item_id: str, patch: Patch, user: str = Depends(current_user)):
    doc = owned(db.items.find_one({"_id": item_id}), user)
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
    if "suitcaseId" in patch.model_fields_set and patch.suitcaseId != doc.get("suitcaseId"):
        if patch.suitcaseId is not None:
            owned(db.suitcases.find_one({"_id": patch.suitcaseId}), user)
        fields |= {"suitcaseId": patch.suitcaseId}
        # Neither bag's stored plan matches its items any more. None is "no bag", not a plan id.
        for bag in {doc.get("suitcaseId"), patch.suitcaseId} - {None}:
            db.plans.delete_one({"_id": bag})
    doc = db.items.find_one_and_update({"_id": item_id}, {"$set": fields}, return_document=True) if fields else db.items.find_one({"_id": item_id})
    if doc is None:
        raise HTTPException(404, "no such item")
    return public(doc)


@app.get("/items")
def list_items(suitcaseId: str | None = None, user: str = Depends(current_user)):
    return [public(d) for d in db.items.find({"owner_id": user} | ({"suitcaseId": suitcaseId} if suitcaseId else {}))]


@app.get("/inventory")
def list_inventory(user: str = Depends(current_user)):
    """The caller's items across every suitcase, newest first; survives DELETE /suitcases."""
    return [public(d) for d in db.items.find({"owner_id": user}).sort("createdAt", -1)]


@app.post("/inventory/relabel")
def relabel_inventory(user: str = Depends(current_user)):
    """Re-ask every configured model about everything still carrying no usable label.

    `resolve_label_status` makes "unidentified" terminal because re-asking the same model
    about the same photo cannot change its mind. The mixture is not the same model, so this
    route re-opens those items against all of them at once, which is the one thing that can
    change the answer. Synchronous: it is a handful of items in a demo, and the caller wants
    to know what it got.
    """
    # On the label, not on labelStatus: an item the user named themselves keeps the
    # "unidentified" status its upload gave it, and re-asking models about a name a human
    # already supplied is exactly what this must not do.
    stuck = list(db.items.find({"owner_id": user,
                                "label": {"$in": [None, "", "unknown", "Unknown", "UNKNOWN"]}}))
    labelled = 0
    for doc in stuck:
        reopened = {"labelModel": "both", "labelStatus": "pending", "labelAttempts": 0}
        db.items.update_one({"_id": doc["_id"]}, {"$set": reopened})
        if label_item(doc | reopened):
            labelled += 1
    logger.info("relabel: %d considered, %d now labelled", len(stuck), labelled)
    return {"considered": len(stuck), "labelled": labelled}


@app.delete("/inventory")
def clear_inventory(user: str = Depends(current_user)):
    """Everything this user has scanned, gone: items, suitcases and their stored plans.

    Irreversible, and the whole point — the app calls it at launch so a demo starts from an
    empty bag rather than yesterday's.
    """
    bags = [b["_id"] for b in db.suitcases.find({"owner_id": user}, {"_id": 1})]
    items = db.items.delete_many({"owner_id": user}).deleted_count
    db.suitcases.delete_many({"owner_id": user})
    if bags:
        db.plans.delete_many({"_id": {"$in": bags}})
    logger.info("cleared inventory: %d items, %d suitcases", items, len(bags))
    return {"items": items, "suitcases": len(bags)}


@app.get("/items/{item_id}")
def get_item(item_id: str, user: str = Depends(current_user)):
    """One item; the app polls this while `labelStatus` is "pending"."""
    return public(owned(db.items.find_one({"_id": item_id}), user))


@app.delete("/items/{item_id}")
def delete_item(item_id: str, user: str = Depends(current_user)):
    doc = owned(db.items.find_one({"_id": item_id}), user)
    db.items.delete_one({"_id": item_id})
    db.plans.delete_one({"_id": doc["suitcaseId"]})  # the stored plan no longer matches the items
    return {"deleted": item_id}
