SHELL := /bin/bash

.PHONY: test-python test-swift test-swift-app test bench demo docker-test setup-linux mongo server pipeline

test-python:
	python3 -m unittest discover

test-swift:
	@if ! command -v swift >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi; \
	cd swift/PackPhysics && swift test

test-swift-app:
	@ok=1; for f in tests/swift/*/run.sh; do [ -e "$$f" ] || continue; bash "$$f" || ok=0; done; [ "$$ok" = 1 ]

test: test-python test-swift

mongo:
	@if docker ps --format '{{.Names}}' | grep -qx suitcase-mongo; then \
		echo "suitcase-mongo already running"; \
	elif docker ps -a --format '{{.Names}}' | grep -qx suitcase-mongo; then \
		docker start suitcase-mongo; \
	else \
		docker run -d --name suitcase-mongo -p 27017:27017 mongo:7; \
	fi

server:
	@if [ -f .env ]; then \
		cd server && uv run uvicorn main:app --port 8000 --env-file ../.env; \
	else \
		cd server && uv run uvicorn main:app --port 8000; \
	fi

pipeline:
	bash scripts/pipeline_check.sh

bench:
	@for f in tests/benchmark_*.py; do [ -e "$$f" ] && PYTHONPATH=. python3 "$$f"; done
	@if ! command -v swift >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi; \
	cd swift/PackPhysics && \
	if swift package describe --type json 2>/dev/null | grep -q '"packphysics"'; then \
		swift run -c release packphysics bench; \
	else \
		echo "packphysics executable not present yet; skipping swift bench"; \
	fi

demo:
	PYTHONPATH=. python3 -m pan demo --backend mock --frames 3 --steps 1 --out out/pan_demo

docker-test:
	docker build -t packphysics . && docker run --rm packphysics

setup-linux:
	bash swift/PackPhysics/scripts/setup-linux.sh
