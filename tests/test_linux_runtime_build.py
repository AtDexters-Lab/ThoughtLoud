"""Check the build recipe refuses to distribute an incomplete backend set."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("missing_backend", [
    None, "libggml-cpu-x64.so", "libggml-cpu-haswell.so", "libggml-vulkan.so",
])
def test_build_requires_portable_and_accelerated_backends(tmp_path, missing_backend):
    fake_bin = tmp_path / "commands"
    fake_bin.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    build = tmp_path / "native"
    git = fake_bin / "git"
    git.write_text(f"#!{sys.executable}\n" + '''
import sys
from pathlib import Path
args = sys.argv[3:]
if args[:1] == ["rev-parse"]:
    print("0a635dcd92ba66c75fccfef91c3e106f4668f367")
elif args[:2] == ["worktree", "add"]:
    source = Path(args[3])
    (source / "vendor").mkdir(parents=True)
    (source / "LICENSE").write_text("license fixture")
elif args[:1] == ["archive"]:
    print("archive fixture")
''', encoding="utf-8")
    git.chmod(0o755)
    cmake = fake_bin / "cmake"
    cmake.write_text(f"#!{sys.executable}\n" + '''
import os
import sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == "--build":
    output = Path(args[1]) / "bin"
    output.mkdir(parents=True)
    for name in ("llama-server", "libggml-cpu-x64.so", "libggml-cpu-haswell.so", "libggml-vulkan.so"):
        if name != os.environ.get("MISSING_BACKEND"):
            (output / name).write_bytes(b"native fixture")
''', encoding="utf-8")
    cmake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["MISSING_BACKEND"] = missing_backend or ""
    script = Path(__file__).parents[1] / "runtime/linux/build.sh"

    result = subprocess.run(
        ["bash", str(script), str(source), str(build)],
        env=env, text=True, capture_output=True,
    )

    if missing_backend:
        assert result.returncode != 0
        assert f"missing {missing_backend}" in result.stderr
        assert not (build / "bin/BUILD.txt").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert (build / "bin/BUILD.txt").is_file()
        assert (build / "bin/LICENSE.llama.cpp").read_text() == "license fixture"
