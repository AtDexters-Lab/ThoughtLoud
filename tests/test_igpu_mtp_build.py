import subprocess
from pathlib import Path


def test_build_directory_inside_source_checkout_is_refused(tmp_path):
    source = tmp_path / "atomic-source"
    (source / ".git").mkdir(parents=True)
    build = source / "build"
    script = Path(__file__).parents[1] / "runtime/igpu/build-atomic-mtp.sh"

    result = subprocess.run(
        ["bash", str(script), str(source), str(build)],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "build directory must be outside the Atomic source checkout" in result.stderr
    assert not build.exists()
