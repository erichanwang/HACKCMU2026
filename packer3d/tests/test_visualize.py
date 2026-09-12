"""Both example results render to PNG, quickly (matplotlib is a dev-only dependency)."""
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
from packer3d.visualize import main, render  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("name", ["suitcase", "dragon"])
def test_example_renders_fast(tmp_path, name):
    src = EXAMPLES / f"{name}_result.json"
    assert main([str(src), str(tmp_path / "naive.png"), "naive"]) == 0  # CLI path; also warms lazy imports
    out = tmp_path / "optimized.png"
    t = time.perf_counter()
    render(json.loads(src.read_text())["optimized"], str(out), "optimized")
    assert time.perf_counter() - t < 2.0
    assert out.stat().st_size > 10_000
