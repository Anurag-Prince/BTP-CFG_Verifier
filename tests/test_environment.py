"""Part 1: the toolchain exists and its pieces work together."""

import importlib

import pytest

from cfgverify import config, toolchain


@pytest.mark.parametrize("tool", sorted(config.REQUIRED_TOOLS))
def test_required_tool_is_installed(tool):
    assert toolchain.find_tool(tool), f"{tool} not found; run scripts/setup_wsl.sh"


@pytest.mark.parametrize("package", config.REQUIRED_PYTHON_PACKAGES)
def test_python_package_importable(package):
    importlib.import_module(package)


def test_environment_snapshot_has_no_problems():
    env = toolchain.collect_environment()
    assert env["problems"] == []
    assert env["required_tools"]["clang"]["version"]


def test_clang_not_newer_than_llvmlite_llvm():
    env = toolchain.collect_environment()
    clang_major = int(env["required_tools"]["clang"]["version"].split(".")[0])
    lite_major = int(env["llvmlite_llvm_version"].split(".")[0])
    assert clang_major <= lite_major, "llvmlite may not be able to read this clang's IR"
