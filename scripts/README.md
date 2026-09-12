# scripts/

`demo_e2e.py` — the live demo in one process: fixture scan → server → solver + physics
validator → PAN rollout → the iOS `plan.json`. Run it from the repo root.

Prerequisites (two terminals, then the script):

```sh
docker run --rm -d --name suitcase-mongo -p 27017:27017 mongo:7
cd server && uv run uvicorn main:app --port 8000 --env-file ../.env   # drop --env-file to skip Grok
python3 scripts/demo_e2e.py                                          # mock PAN, no network
PAN_API_KEY=... python3 scripts/demo_e2e.py --backend real            # IFM K2-Horizon (network)
```

`--server` / `--out` override the defaults (`http://127.0.0.1:8000`, `out/e2e`), which is
where `plan.json`, `solver.json`, `validation.json` and the PAN rollout assets land.
Without an `XAI_API_KEY` the server labels every item "unknown" and drops `keepUpright`;
the script PATCHes each fixture's label/rigidity/compressibility back in, so the plan is
meaningful either way.
