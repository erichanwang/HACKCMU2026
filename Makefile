SHELL := /bin/bash

.PHONY: test-python test-swift test bench demo docker-test setup-linux

test-python:
	python3 -m unittest discover

test-swift:
	@if ! command -v swift >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi; \
	cd swift/PackPhysics && swift test

test: test-python test-swift

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
