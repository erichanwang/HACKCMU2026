# PAN Access Reconnaissance

Recon date: 2026-09-12. Every claim below is tagged `[VERIFIED: source]` or `[UNKNOWN]`.
Nothing here is inferred. No endpoint, SDK name, parameter, or auth scheme was invented.

## (a) Executive answer

1. **No.** There is no programmatic PAN interface available to us right now — no public API,
   no SDK, no downloadable weights, no hosted inference demo, and no HackCMU-specific access
   path found locally or on the network.
2. **Via nothing.** The only contact route IFM publishes is a generic "Collaborate With Us"
   web form (name / email / message); PAN's own site exposes only a blog link and the paper.
3. **Verified how:** enumerated all 31 links on ifm.ai (no api/docs/sdk/playground/signup);
   traced panworld.ai network traffic (pre-rendered video, zero POST, no inference call);
   PyPI `ifm-ai` is an explicit "SDK coming soon" placeholder; no PAN repo on any IFM GitHub
   org; no PAN checkpoint on huggingface.co/IFM.

## (b) Sources checked

| Source | Finding | Tag |
|---|---|---|
| `env` var names matching pan/ifm/hackcmu/world/model | No PAN/IFM vars. 3 substring false positives only: `HERDR_PANE_ID`, `CODEX_COMPANION_SESSION_ID`, `CODEX_COMPANION_TRANSCRIPT_PATH` | [VERIFIED: `env \| sed 's/=.*//' \| grep -i`] |
| `python3 -m pip list` (147 pkgs) | Zero matches for pan/ifm/world/model/cosmos/genesis | [VERIFIED: pip list] |
| `HACKCMU2026` + `HACKCMU2026-pan` trees | *(stale as of later 2026-09-12 — see below)* At recon time, only `PAN.md` mentioned PAN/IFM and no `.env*` existed. Since then a `pan/` package, `physics/pan.py`, and their tests now exist; `.env.example` is checked in (still gitignored: `.env` itself is not in this tree) | [VERIFIED: find + grep -ril, originally; updated by re-running the same grep] |
| `~/Downloads/hackcmu26{,.zip}` | Unrelated project ("CT Proximity Risk Viewer"). Zero PAN/IFM references, no `.env` | [VERIFIED: grep -rilE over tree] |
| `~/Downloads`, `~/Documents`, `~/Desktop`, `~/.config`, `~/.local/share` | No sponsor starter kit, notebook, or PDF mentioning PAN/IFM | [VERIFIED: find -iregex] |
| Discord / Slack export | None present (only `~/.config/discord` app config, not an export) | [VERIFIED: find -itype d] |
| arXiv 2511.09057 (abs + full HTML) | Paper exists, 4 versions (v1–v4). Interface shape extracted — see (c) | [VERIFIED: arxiv.org/abs/2511.09057, arxiv.org/html/2511.09057v1] |
| `ifm.ai/` | 31 links total. Nav = About / Our Models / Collaborate. **No** api, developer, docs, sdk, playground, console, platform, waitlist, or sign-up link | [VERIFIED: DOM anchor enumeration via browser] |
| `ifm.ai/collaborate/` | Only access route: WordPress contact form, fields = name / email / message. No API signup, no PAN waitlist, no published email | [VERIFIED: form DOM inspection] |
| `ifm.ai/pan/` | HTTP 301 → `panworld.ai/` (403 to non-browser UA) | [VERIFIED: browser navigation] |
| `panworld.ai/` (the real PAN site) | Research showcase. Outbound links: Blog (MBZUAI news), Paper (arXiv PDF), socials. No API/docs/SDK/demo-access/pricing | [VERIFIED: DOM snapshot] |
| panworld.ai "Interactive world simulation" widget | **Pre-rendered, not live inference.** Clicking a world issued 759/760 requests to `customer-cj3dhsc8puv3jkww.cloudflarestream.com`; zero non-GET requests; no `/api/` or generate/infer/predict path | [VERIFIED: browser network trace] |
| `ifm.mbzuai.ac.ae/pan/` | 2.6 KB shell that iframes `https://panworld.ai/` | [VERIFIED: curl + link extraction] |
| `github.com/ifm-ai` | 7 repos (uno, PRism-synthesis, search360, horizon-post-train, xllm, PRism-annotator, PRism-curator). **No PAN repo** | [VERIFIED: org repo listing] |
| `github.com/MBZUAI-IFM` | 8 repos (incl. `WR-Arena`, a world-model *diagnostic/benchmark* tool). **No PAN repo** | [VERIFIED: GitHub API] |
| `github.com/mbzuai-oryx` | 55 repos, no name matching pan/world | [VERIFIED: GitHub API] |
| GitHub repo search for `2511.09057` | 0 results — no public reimplementation citing the paper | [VERIFIED: GitHub search API] |
| `huggingface.co/IFM` | 10 models (all K2-Horizon) + 10 datasets. **No PAN checkpoint.** Org bio mentions PAN only as a description | [VERIFIED: HF org page] |
| HF model search "PAN world model" | No IFM/PAN checkpoint | [VERIFIED: HF search] |
| PyPI `ifm-ai` | **EXISTS, v0.0.1, 1535-byte wheel.** Author "Institute of Foundation Models"; summary: *"Placeholder reserving the ifm-ai package name. Full IFM Python SDK coming soon."*; `Development Status :: 1 - Planning`; homepage `https://ifm.ai`; uploaded 2026-07-17 | [VERIFIED: pypi.org/pypi/ifm-ai/json] |
| PyPI `ifm`, `pan-world-model`, `ifm-pan`, `panwm`, `pan-sdk`, `ifm-sdk` | All HTTP 404 — not published | [VERIFIED: PyPI JSON API] |
| PyPI `pan` | Exists but unrelated (a pandoc/markdown article builder) | [VERIFIED: pypi.org/pypi/pan/json] |
| `hackcmu.org` | DNS does not resolve (`getaddrinfo ENOTFOUND`) | [VERIFIED: WebFetch + curl] |
| `hackcmu-2026.devpost.com` | HTTP 404 | [VERIFIED: WebFetch] |
| `hackcmu20.devpost.com` | HackCMU **2020** page. Sponsors: Aptiv, ASML, CMU, EchoAR, Facebook, Microsoft, Sandia, Stevens Capital. No IFM/PAN | [VERIFIED: WebFetch] |
| HackCMU 2026 sponsor list / PAN workshop materials | Not found on any public page | [UNKNOWN] |
| IFM/PAN as a confirmed HackCMU 2026 sponsor | Could not confirm from any public source | [UNKNOWN] |

## (c) PAN's published interface shape (from the paper)

All items `[VERIFIED: arxiv.org/html/2511.09057v1]` unless noted.

- **Title:** "PAN: A World Model for General, Actionable, and Long-Horizon World Simulation",
  PAN Team, Institute of Foundation Models. (The HuggingFace papers page renders the title as
  "General, **Interactable**, and Long-Horizon" — the title changed across v1–v4.
  [VERIFIED: huggingface.co/papers/2511.09057])
- **Per-step input:** an observation `o_t` — quote: *"(e.g., images or video frames)"* — plus
  *"the proposed action `a_t` represented by natural language."*
- **Per-step output:** *"the next predicted observation `ô_{t+1}`"*, emitted as video frames.
- **Action conditioning:** natural language, one action per step. The official blog confirms
  the backbone *"ingests the accumulated world history, the current observation, and the next
  proposed action"*, e.g. *"grasp the yellow can from the middle tray"*.
  [VERIFIED: mbzuai.ac.ae news article on PAN]
- **Multi-step / continuation:** supported. *"closed-loop rollouts by recursively feeding back
  its state prediction"*, via a *"chunk-wise causal attention mask"* where *"the predicted
  output from the previous chunk becomes conditioning for the next."*
- **Chunking:** *"the window size is 21, which corresponds to 81 real video frames"*, and
  *"shifts by 10 latent frames"* per step; referred to as *"81-frame clip[s]"*.
- **Architecture:** Generative Latent Prediction (GLP) — vision encoder and backbone from
  `Qwen2.5-VL-7B-Instruct`; video diffusion decoder *"adapted from Wan2.1-T2V-14B"* with
  *"Causal Swin-DPM"*.
- **Resolution:** [UNKNOWN] — not stated in the paper.
- **FPS / seconds per rollout step:** [UNKNOWN] — not stated. (81 frames per chunk is stated;
  the frame rate is not, so wall-clock duration cannot be derived.)
- **Max rollout steps / total horizon:** [UNKNOWN] — no quantitative "up to N steps" claim.
- **Training cost (context only):** *"trained for 5 epochs using 960 NVIDIA H200 Tensor Core
  GPUs."*

## (d) Configuration contract — OUR convention, not IFM's

**As implemented today, two separate seams exist, with two separate variable sets — do not
conflate them.**

**`pan/world_model.py`'s `RealPanBackend`** is a generic, still-unconfigured HTTP seam for a
hypothetical future visual-PAN endpoint (may be removed by another agent concurrently —
check whether `pan/world_model.py` still exists before relying on this). These five variable
names are our own invention for this repo; IFM publishes no configuration contract, no auth
scheme, and no base URL for this seam. Nothing below is an IFM standard:

| Var | Role |
|---|---|
| `PAN_API_KEY` | **Secret.** Never logged, printed, or committed. Absent ⇒ fall back to the mock backend. |
| `PAN_BASE_URL` | Base URL of whatever endpoint we are eventually given. No default — we have no verified endpoint. |
| `PAN_MODEL` | Model/deployment identifier string, if the real interface takes one. |
| `PAN_ENDPOINT_PATH` | e.g. `/v1/simulate`. Absent ⇒ `available()` stays `False` — nobody has confirmed a real route, so this seam refuses to guess a URL and never fires. |
| `PAN_TIMEOUT_S` | Per-request timeout in seconds, so PAN latency can never block the solver. Default 60. |

Since `PAN_ENDPOINT_PATH` has never been set, this backend is dead: every call falls through
to the mock.

**`physics/pan.py`'s `RealPanBackend`** is the actually-live seam (see the K2-Horizon
investigation below) — it hardcodes `IFM_BASE_URL = "https://api.ifm.ai/v1"` and
`IFM_MODEL = "IFM/K2-Horizon-375B-A23B"` (no base-URL/model env vars) and reads one secret:

| Var | Role |
|---|---|
| `IFM_API_KEY` | **Secret.** Read from the `IFM_API_KEY` env var, else a bare-token or `IFM_API_KEY=` line in a root `.env`. Absent ⇒ falls back to the mock backend. |

This is the one real, working credential in the repo, and it authenticates a text-only LLM
(K2-Horizon), not the visual PAN world model — see the investigation below.

## (e) Next steps for whoever obtains real access

1. Add a visual backend class beside `RealPanBackend` in `pan/world_model.py` that satisfies
   the `WorldModel` protocol in `pan/types.py` (`name`, `supports_continuation`, `available()`,
   `simulate(request: SimulationRequest) -> SimulationResult`). Its request mapping takes our
   `Observation` (an image) plus `PackingAction.text` onto the real wire format, which matches
   the paper's per-step contract in (c).
2. Its response mapping fills `SimulationResult` (`video_path`, `final_frame_path`,
   `metadata`, `latency_ms`, `backend`) from the returned frames or video.
3. Put the key in a local `.env` (git-ignored); the client reads `IFM_API_KEY` or `PAN_API_KEY`
   (`pan/__main__.py` lists the names). Nothing else in the codebase should need to change:
   the rest of the app depends only on the `WorldModel` protocol, and `get_world_model` in
   `pan/world_model.py` picks the first available backend, falling back to the mock.
4. If the real interface accepts the previous prediction as the next input, set
   `supports_continuation` and enable multi-step chaining; the paper says PAN supports it, but
   our client must not assume it until tested.
5. The HTTP seam this section used to describe (`_build_payload`, `_parse_response`, an
   endpoint path) was deleted in 53a6b97 as dead code. Today's only real backend is
   `RealPanBackend`, the text-only K2-Horizon client (investigation below); there is no
   half-wired visual client left to fill in.

## (f) Rate limits and latency expectations

- **Rate limits:** [UNKNOWN]. No public quota, pricing, or throttling documentation exists.
- **Per-request latency:** [UNKNOWN]. The paper states no inference latency, throughput, or
  real-time numbers, and there is no endpoint to measure.
- Practical consequence: treat PAN as unbounded-latency and strictly asynchronous. Keep the
  `pending / complete / failed / unavailable` status model and cache every completed rollout.

## K2-Horizon / PAN_API_KEY investigation (2026-09-12, loop iteration 1)

**Bottom line: no real PAN access. K2-Horizon is a plain LLM family, not a world model, and
the PAN_API_KEY value only unlocks an LLM endpoint that has no PAN/world-model route on it.**

### K2-Horizon on HuggingFace

- `huggingface.co/IFM` lists 8 K2-Horizon checkpoints (375B-A23B, MoVA-36B-A4B, 32B, 7B,
  3.7B, 0.9B, 7B-Uno, 0.9B-Uno) plus GGUF quantizations. **Every single one is tagged
  Text Generation.** [VERIFIED: huggingface.co/IFM org page]
- None are tagged image-to-video, video generation, or world-model/simulation. Nothing in
  the visible model cards mentions images, video, or actions — this is "the fully
  open-source fleet of LLMs," i.e. a chat/completion model family, unrelated in function to
  PAN despite living in the same HF org. [VERIFIED: huggingface.co/IFM org page]
- Conclusion: K2-Horizon's input/output shape (text in, text out) does **not** map onto
  `pan/types.py`'s `Observation` (image) → `SimulationResult` (video frames) contract. There
  is no plausible adapter here — it's not a world model at all, so nothing to wire in.

### PAN_API_KEY / base URL

- Tried a small set of documented-looking hosts: `api.ifm.ai`, `ifm.ai`, `api.panworld.ai`,
  `panworld.ai`, `api.ifm.mbzuai.ac.ae`, `ifm.mbzuai.ac.ae`. [VERIFIED: curl, this session]
- **`api.ifm.ai` is live** (`server: ifm-ai/0.1.0`, HTTP/2). `GET /v1/models` returns 200
  with an OpenAI-style model list:
  `{"object":"list","data":[{"id":"IFM/K2-Think-v2",...},{"id":"IFM/K2-Horizon-375B-A23B",...}]}`.
  [VERIFIED: curl https://api.ifm.ai/v1/models, this session]
- Sent the same request with `Authorization: Bearer <the PAN_API_KEY value>` — **byte-identical
  response**, same as unauthenticated. This endpoint does not appear to gate on auth at all,
  so this call neither confirms nor denies the key is valid; it only proves the host is real
  and serves an OpenAI-compatible `/v1/models` listing. [VERIFIED: curl, this session]
- Only two models are exposed: `IFM/K2-Think-v2` and `IFM/K2-Horizon-375B-A23B` — both LLMs.
  **No PAN model, no world-model/video-generation route, nothing resembling the
  observation+action→frames contract exists on this host's `/v1/models` list.**
  [VERIFIED: curl, this session]
- Did not attempt an authenticated POST (chat/completions or otherwise) — out of scope for
  this read-only recon and unnecessary once `/v1/models` showed no PAN-shaped endpoint.
- `api.panworld.ai` and `api.ifm.mbzuai.ac.ae` did not resolve/respond (curl exit, no TCP
  connection). `ifm.ai` (403) and `panworld.ai` (200, static site) behave as already
  documented in (b) above — no change. [VERIFIED: curl, this session]

### Answer to "is there a real integration opportunity"

**No.** The PAN_API_KEY value is real in the sense that it points at a genuinely live IFM
API host (`api.ifm.ai`), but that host only serves K2 text-generation models, which is a
different product from PAN (world-model video simulation) despite the shared "IFM" branding
and the superficially PAN-adjacent "Horizon" name. There is no PAN endpoint to call, and
K2-Horizon's I/O shape cannot stand in for PAN's image+action→video contract. Do not wire
this key into `pan/world_model.py`; the mock backend remains the only working path.
