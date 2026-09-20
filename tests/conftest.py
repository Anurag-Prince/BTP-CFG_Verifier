from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE_C = REPO / "examples" / "c" / "hello_cfg.c"

# Decisions counted by hand from the C source (see comments in hello_cfg.c).
EXPECTED_DECISIONS = {"sum_even_upto": 3, "classify": 3, "count_digits": 2, "main": 0}


@pytest.fixture(scope="session")
def example_c() -> Path:
    return EXAMPLE_C


@pytest.fixture(scope="session")
def example_ir(tmp_path_factory, example_c) -> Path:
    from cfgverify.hll.frontend import emit_llvm_ir

    return emit_llvm_ir(example_c, tmp_path_factory.mktemp("ir") / "hello_cfg.ll")
