# scripts/

`pipeline_check.sh` — one command that exercises the whole pipeline: the root `unittest`
suite, the packer3d pytest suite, `server/check.py`, `make test-swift`, every
`tests/swift/*/run.sh`, and the live end-to-end demo below (starting Mongo/the server if
nothing is already listening). Every step runs even if an earlier one failed; it prints a
PASS/FAIL/SKIP table and exits 1 if anything FAILed. Run it as `make pipeline` or
`bash scripts/pipeline_check.sh` from the repo root. `SKIP_SWIFT=1` skips the `swift test`
step (a first build in a fresh worktree can take many minutes); everything else always runs.

`demo_e2e.py` — the live demo in one process: fixture scan → server → solver + physics
validator → PAN rollout → the iOS `plan.json`. Run it from the repo root.

Prerequisites (two terminals, then the script):

```sh
docker run --rm -d --name suitcase-mongo -p 27017:27017 mongo:7
cd server && uv run uvicorn main:app --port 8000 --env-file ../.env   # drop --env-file to skip Grok
python3 scripts/demo_e2e.py                                          # mock PAN, no network
PAN_API_KEY=... python3 scripts/demo_e2e.py --backend real            # IFM K2-Horizon (network)
python3 scripts/demo_e2e.py --photos photos/                          # Grok labels real photos (server needs XAI_API_KEY)
```

`--photos DIR` posts `DIR/<book|tshirts|camera|bottle|shoes>.jpg` instead of a grey placeholder
and keeps whatever Grok answered (label, rigidity, compressibility, mass, keepUpright, all
stored with the item in Mongo and used by the solver), so the packing follows the photos.
Any five photos named that way work, e.g. `curl -o photos/shoes.jpg https://loremflickr.com/640/480/sneaker`.

`--server` / `--out` override the defaults (`http://127.0.0.1:8000`, `out/e2e`), which is
where `plan.json`, `solver.json`, `validation.json` and the PAN rollout assets land.
Without an `XAI_API_KEY` the server labels every item "unknown" and drops `keepUpright`;
the script PATCHes each fixture's label/rigidity/compressibility/mass/keepUpright back in, so the plan is
meaningful either way.

`--async` posts each item with `?async=1` and polls `GET /items/{id}` every 2s until its label
is done instead of waiting on the POST -- with `--photos` this shows Grok labelling in the
background.

If the solver leaves items out of the plan it prints "left out: <label>, <label>"; while any
item is still being labelled it prints "N item(s) still labelling".
