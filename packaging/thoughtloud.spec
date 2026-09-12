# Linux application bundle. Build in the oldest supported Linux userspace.
from importlib.metadata import distribution
import os
from pathlib import Path
import subprocess

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH).parent
datas = collect_data_files("voxd", includes=["assets/thoughtloud*.svg", "assets/thoughtloud*.png", "assets/*.onnx", "defaults/*"])
datas += copy_metadata("thoughtloud")
native_runtime = os.environ.get("VOXD_LLAMA_RUNTIME")
if native_runtime:
    runtime_dir = Path(native_runtime).resolve()
    if not (runtime_dir / "llama-server").is_file():
        raise SystemExit("VOXD_LLAMA_RUNTIME must contain llama-server")
    for item in runtime_dir.iterdir():
        if item.name == "llama-server" or item.name.startswith("lib") or item.name in (
            "LICENSE.llama.cpp", "BUILD.txt", "voxd-audio.patch", "llama-source.tar.gz",
        ):
            datas.append((str(item), "llama"))
    if (runtime_dir / "licenses").is_dir():
        datas.append((str(runtime_dir / "licenses"), "licenses/llama-vendors"))
datas.append((str(root / "runtime/linux/LICENSE.gemma"), "licenses/gemma"))
for filename in ("LICENSE", "ASSETS_LICENSE", "TRADEMARKS.md", "THIRD_PARTY_NOTICES.md"):
    datas.append((str(root / filename), "licenses/thoughtloud"))

datas.append((str(root / "packaging/licenses"), "licenses/release"))
datas.append((str(root / "docs/licensing.md"), "licenses/thoughtloud"))

# Preserve license/notice files shipped by runtime dependency wheels.
for name in (
    "PyQt6", "PyQt6-Qt6", "PyQt6-sip", "numpy", "onnxruntime", "sounddevice",
    "cffi", "pycparser", "platformdirs", "PyYAML", "pyperclip", "requests",
    "urllib3", "certifi", "charset-normalizer", "idna", "dbus-next", "flatbuffers",
    "packaging", "protobuf",
):
    dep = distribution(name)
    for entry in dep.files or ():
        if any(part.upper().startswith(("LICENSE", "COPYING", "NOTICE")) for part in entry.parts):
            source = Path(dep.locate_file(entry))
            if source.is_file():
                datas.append((str(source), str(Path("licenses") / name / Path(*entry.parts).parent)))

a = Analysis(
    [str(root / "packaging/entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=collect_dynamic_libs("onnxruntime"),
    datas=datas,
    hiddenimports=["PyQt6.QtDBus", "dbus_next.aio", "sounddevice", "PyQt6.QtSvg"],
    excludes=["tkinter", "matplotlib", "torch", "tensorflow"],
    noarchive=False,
)
# The Linux builder also contributes Python and system shared libraries.
# Include the distribution's copyright notices for their actual source files.
seen_packages = set()
for _name, source, _kind in list(a.binaries):
    if "site-packages" in source:
        continue
    for candidate in {source, str(Path(source).resolve())}:
        query = subprocess.run(["dpkg-query", "-S", candidate], capture_output=True, text=True)
        for line in query.stdout.splitlines():
            package = line.split(": ", 1)[0]
            if package in seen_packages:
                continue
            notice = Path("/usr/share/doc") / package.split(":")[0] / "copyright"
            if notice.is_file():
                a.datas.append((f"licenses/system/{package}/copyright", str(notice), "DATA"))
                seen_packages.add(package)
for notice in Path("/usr/share/common-licenses").glob("*"):
    if notice.is_file():
        a.datas.append((f"licenses/common/{notice.name}", str(notice), "DATA"))
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="thoughtloud", debug=False,
    strip=False, upx=False, console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="thoughtloud")
