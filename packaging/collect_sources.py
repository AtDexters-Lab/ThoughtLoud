#!/usr/bin/env python3
"""Collect the sources for the reviewed Linux bundle, without installing anything.

Run with the same Python environment used by PyInstaller.  ``lock`` deliberately
contacts upstreams; ``fetch`` only downloads hash-pinned files; ``verify`` is
offline.  The lock is a release input, not a claim of byte-reproducible upstream
wheels. See docs/licensing.md for the build and replacement instructions.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WHEELS = (
    "PyQt6", "PyQt6-Qt6", "PyQt6-sip", "numpy", "onnxruntime", "sounddevice",
    "cffi", "pycparser", "platformdirs", "PyYAML", "pyperclip", "requests",
    "urllib3", "certifi", "charset-normalizer", "idna", "dbus-next", "flatbuffers",
    "packaging", "protobuf", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph",
)
# NumPy's audited wheel embeds these renamed AlmaLinux runtime libraries. The
# executable section and GNU build IDs match the signed original RPMs; see the
# bundled provenance note. Do not infer their source from NumPy's compiler version.
NUMPY_RUNTIMES = {
    "numpy.libs/libgfortran-83c28eba-468e71e5.so.5.0.0":
        "a6a368328d02a5d8a149746f13f6268f8cc9192df5c18777b25c3c9b5ef52160",
    "numpy.libs/libquadmath-2284e583-a9307bba.so.0.0.0":
        "6bc3069003caec1f075be3c51ae8355332f081fa9465910c4b5724e7fe7d8ad4",
}
ALMA_SOURCE = {
    "component": "NumPy GCC runtimes (AlmaLinux)", "version": "8.5.0-28.el8_10.alma.1",
    "file": "gcc-8.5.0-28.el8_10.alma.1.src.rpm",
    "url": "https://repo.almalinux.org/vault/8.10/BaseOS/Source/Packages/gcc-8.5.0-28.el8_10.alma.1.src.rpm",
    "sha256": "c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0",
    "size": 65715094,
    "provenance": "packaging/licenses/NumPy-runtime-provenance.txt",
}
NATIVE_FILES = ("_internal/llama/llama-source.tar.gz", "_internal/llama/voxd-audio.patch",
                "_internal/llama/BUILD.txt", "_internal/llama/llama-server",
                "licenses/ydotool-source.tar.gz", "libexec/ydotool", "libexec/ydotoold")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def request(url: str) -> bytes:
    cache = ROOT / "build/source-metadata" / hashlib.sha256(url.encode()).hexdigest()
    if cache.is_file():
        return cache.read_bytes()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                content = response.read()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(content)
            return content
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def get_json(url: str):
    return json.loads(request(url))


def toc_rows(path: Path):
    def walk(value):
        if isinstance(value, (list, tuple)):
            if (len(value) == 3 and all(isinstance(x, str) for x in value)
                    and value[2] in ("BINARY", "EXTENSION")):
                yield value
            else:
                for child in value:
                    yield from walk(child)
    return list(walk(ast.literal_eval(path.read_text())))


def inventory(bundle: Path, analysis: Path) -> dict:
    """Bind dependency identities to the actual collected bytes, not filenames alone."""
    rows = toc_rows(analysis)
    paths = {candidate for _, source, _ in rows if "site-packages" not in source
             for candidate in (source, str(Path(source).resolve()))}
    query = subprocess.run(["dpkg-query", "-S", *sorted(paths)], capture_output=True,
                           text=True, check=False)
    owners = {}
    for line in query.stdout.splitlines():
        if ": " in line:
            package, name = line.split(": ", 1)
            if name in paths and not package.startswith("diversion "):
                owners[name] = package
    packages = sorted(set(owners.values()))
    result = subprocess.run([
        "dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n",
        *packages,
    ], check=True, capture_output=True, text=True)
    system = [dict(zip(("binary", "version", "source", "source_version"), line.split("\t")))
              for line in result.stdout.splitlines()]
    files = []
    for name, source, kind in rows:
        original = Path(source)
        installed = bundle / "_internal" / name
        if not installed.is_file():
            raise RuntimeError(f"Collected binary missing from bundle: {name}")
        digest = sha(original)
        if digest != sha(installed):
            raise RuntimeError(f"Bundle differs from build input: {name}")
        owner = owners.get(source) or owners.get(str(original.resolve()))
        if "site-packages" in source:
            owner = "wheel:" + source.split("site-packages/", 1)[1].split("/", 1)[0]
        elif ((original.parent / "llama-source.tar.gz").is_file()
              and sha(original.parent / "llama-source.tar.gz")
              == sha(bundle / "_internal/llama/llama-source.tar.gz")):
            owner = "native:llama.cpp (bundled source and BUILD.txt)"
        if not owner:
            raise RuntimeError(f"Unidentified binary provider: {source}")
        files.append({"path": name, "sha256": digest, "provider": owner, "kind": kind})
    wheels = [{"name": name, "version": metadata.version(name)} for name in WHEELS]
    for path, digest in NUMPY_RUNTIMES.items():
        if sha(bundle / "_internal" / path) != digest:
            raise RuntimeError(f"NumPy embedded runtime changed; re-establish its source provenance: {path}")
    native = [{"path": path, "sha256": sha(bundle / path)} for path in NATIVE_FILES]
    return {"wheels": wheels, "system_packages": system, "binaries": files, "native": native,
            "executable": {"path": "thoughtloud", "sha256": sha(bundle / "thoughtloud")}}


def pypi_source(package: dict) -> list[dict]:
    name, version = package["name"], package["version"]
    if name == "PyQt6-Qt6":
        url = f"https://download.qt.io/official_releases/qt/{'.'.join(version.split('.')[:2])}/{version}/single/qt-everywhere-src-{version}.tar.xz"
        digest = request(url + ".sha256").decode().split()[0]
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise RuntimeError("Invalid official Qt checksum")
        return [{"component": "Qt", "version": version, "url": url,
                 "file": url.rsplit("/", 1)[1], "sha256": digest,
                 "provenance": url + ".sha256"}]
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    info = get_json(url)
    sdists = [entry for entry in info["urls"] if entry["packagetype"] == "sdist"]
    if not sdists:
        # ORT publishes binary wheels. MIT and third-party notices travel in
        # the wheel; its release-tag URL is inventoried separately, not passed
        # off as a complete recursive source distribution.
        if name in ("onnxruntime", "flatbuffers"):
            return []
        raise RuntimeError(f"No published source distribution: {name} {version}")
    return [{"component": name, "version": version, "url": entry["url"],
             "file": entry["filename"], "sha256": entry["digests"]["sha256"],
             "size": entry["size"], "provenance": url} for entry in sdists]


def debian_field(text: str, field: str) -> str:
    match = re.search(r"^" + re.escape(field) + r":\s*(.*(?:\n[ \t].*)*)", text, re.M)
    if not match:
        raise RuntimeError(f"Source descriptor lacks {field}")
    return match.group(1)


def ubuntu_source(package: tuple[str, str]) -> list[dict]:
    name, version = package
    query = urllib.parse.urlencode({"ws.op": "getPublishedSources", "source_name": name,
                                   "version": version, "exact_match": "true"})
    provenance = "https://api.launchpad.net/1.0/ubuntu/+archive/primary?" + query
    entries = get_json(provenance)["entries"]
    entries = [entry for entry in entries if entry["source_package_version"] == version]
    if not entries:
        raise RuntimeError(f"Ubuntu source publication not found: {name} {version}")
    # Prefer the actual Ubuntu 24.04 publication when several suites share it.
    entries.sort(key=lambda entry: not entry["distro_series_link"].endswith("/noble"))
    urls = get_json(entries[0]["self_link"] + "?ws.op=sourceFileUrls")
    dsc_url = next(url for url in urls if url.endswith(".dsc"))
    dsc = request(dsc_url)
    text = dsc.decode()
    if debian_field(text, "Source").strip() != name or debian_field(text, "Version").strip() != version:
        raise RuntimeError(f"Ubuntu source descriptor identity mismatch: {name}")
    by_name = {urllib.parse.unquote(url.rsplit("/", 1)[1]): url for url in urls}
    result = [{"component": name, "version": version, "url": dsc_url,
               "file": dsc_url.rsplit("/", 1)[1], "sha256": hashlib.sha256(dsc).hexdigest(),
               "size": len(dsc), "provenance": provenance}]
    for line in debian_field(text, "Checksums-Sha256").splitlines():
        if not line.strip():
            continue
        digest, size, filename = line.split()
        result.append({"component": name, "version": version, "url": by_name[filename],
                       "file": filename, "sha256": digest, "size": int(size),
                       "provenance": dsc_url})
    return result


def make_lock(bundle: Path, analysis: Path, destination: Path):
    data = inventory(bundle, analysis)
    sources = sorted({(p["source"], p["source_version"]) for p in data["system_packages"]})
    work = [(pypi_source, package) for package in data["wheels"]]
    work += [(ubuntu_source, package) for package in sources]
    archives = [ALMA_SOURCE]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn, arg): arg for fn, arg in work}
        for future in concurrent.futures.as_completed(futures):
            archives.extend(future.result())
            print(f"Resolved {futures[future]}", flush=True)
    data["archives"] = sorted(archives, key=lambda entry: entry["file"])
    data["format"] = 1
    data["notice_only"] = [{"component": "onnxruntime", "version": metadata.version("onnxruntime"),
                            "reason": "MIT binary redistribution with bundled third-party notices",
                            "source_reference": "https://github.com/microsoft/onnxruntime"},
                           {"component": "flatbuffers", "version": metadata.version("flatbuffers"),
                            "reason": "Apache-2.0; PyPI provides only a pure Python wheel with its source and license",
                            "source_reference": "https://github.com/google/flatbuffers"}]
    destination.write_text(json.dumps(data, indent=2) + "\n")


def validate_lock(lock: dict):
    if lock.get("format") != 1:
        raise RuntimeError("Unsupported source lock format")
    names = set()
    for entry in lock["archives"]:
        name = entry["file"]
        if name in names or Path(name).name != name or name in ("", ".", ".."):
            raise RuntimeError(f"Unsafe or duplicate archive filename: {name}")
        if not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) or not entry["url"].startswith("https://"):
            raise RuntimeError(f"Invalid source pin: {name}")
        names.add(name)


def download(entry: dict, directory: Path):
    destination = directory / entry["file"]
    if destination.is_file() and sha(destination) == entry["sha256"]:
        return
    temporary = destination.with_name(destination.name + ".part")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(entry["url"], timeout=90) as stream, temporary.open("wb") as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
            if sha(temporary) != entry["sha256"]:
                raise RuntimeError(f"Source archive checksum mismatch: {entry['file']}")
            if "size" in entry and temporary.stat().st_size != entry["size"]:
                raise RuntimeError(f"Source archive size mismatch: {entry['file']}")
            temporary.replace(destination)
            print(f"Verified {entry['file']}", flush=True)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
        finally:
            temporary.unlink(missing_ok=True)


def verify(lock: dict, bundle: Path, analysis: Path, directory: Path):
    current = inventory(bundle, analysis)
    for field in ("wheels", "system_packages", "binaries", "native", "executable"):
        if current[field] != lock[field]:
            raise RuntimeError(f"Build provenance changed ({field}); regenerate and review the source lock")
    for entry in lock["archives"]:
        file = directory / entry["file"]
        if not file.is_file() or sha(file) != entry["sha256"]:
            raise RuntimeError(f"Missing or changed source archive: {entry['file']}")
    qt = next(entry for entry in lock["archives"] if entry["component"] == "Qt")
    notices = ROOT / "packaging/licenses"
    qt_manifest = json.loads((notices / "Qt-notices-manifest.json").read_text())
    if (qt_manifest["version"] != qt["version"]
            or qt_manifest["source_sha256"] != qt["sha256"]
            or sha(notices / qt_manifest["notice_archive"]) != qt_manifest["notice_sha256"]):
        raise RuntimeError("Qt notices do not match the locked Qt source; regenerate notices and rebuild")
    for file in notices.rglob("*"):
        if file.is_file():
            installed = bundle / "_internal/licenses/release" / file.relative_to(notices)
            if not installed.is_file() or sha(file) != sha(installed):
                raise RuntimeError(f"Bundled release notice is missing or stale: {file.name}; rebuild the bundle")
    for name, source in (("licensing.md", ROOT / "docs/licensing.md"),
                         ("THIRD_PARTY_NOTICES.md", ROOT / "THIRD_PARTY_NOTICES.md")):
        installed = bundle / "_internal/licenses/thoughtloud" / name
        if not installed.is_file() or sha(installed) != sha(source):
            raise RuntimeError(f"Bundled licensing documentation is missing or stale: {name}; rebuild the bundle")
    print(f"Verified {len(lock['binaries'])} binaries, {len(lock['archives'])} archives", flush=True)


def package(lock: dict, lock_path: Path, bundle: Path, directory: Path, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    with tarfile.open(temporary, "w") as archive:
        archive.add(lock_path, arcname="dependency-sources/source-lock.json")
        for entry in lock["archives"]:
            archive.add(directory / entry["file"], arcname="dependency-sources/archives/" + entry["file"])
        for path in ("_internal/llama/llama-source.tar.gz", "_internal/llama/voxd-audio.patch",
                     "_internal/llama/BUILD.txt", "licenses/ydotool-source.tar.gz"):
            archive.add(bundle / path, arcname="dependency-sources/native/" + Path(path).name)
        for path in ("runtime/linux/build.sh", "packaging/build_ydotool.sh", "packaging/build_bundle.sh",
                     "packaging/requirements-build.txt", "packaging/collect_sources.py", "docs/licensing.md",
                     "THIRD_PARTY_NOTICES.md", "LICENSE", "packaging/licenses"):
            archive.add(ROOT / path, arcname="dependency-sources/project/" + path)
    temporary.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{sha(output)}  {output.name}\n")
    print(output, flush=True)


def qt_notices(lock: dict, directory: Path):
    """Regenerate the reviewable notice corpus from the verified full Qt source."""
    entry = next(item for item in lock["archives"] if item["component"] == "Qt")
    source = directory / entry["file"]
    if sha(source) != entry["sha256"]:
        raise RuntimeError("Qt source checksum mismatch")
    modules = {"qtbase", "qtsvg", "qtwayland", "qtimageformats", "qtwebengine"}
    records = []
    destination = ROOT / "packaging/licenses" / f"Qt-{entry['version']}-notices.tar.gz"
    with tarfile.open(source, "r|xz") as archive, tarfile.open(destination, "w:gz") as output:
        for member in archive:
            parts = Path(member.name).parts
            if not member.isfile() or len(parts) < 2:
                continue
            relative = Path(*parts[1:])
            if relative.parts[0] not in modules and relative.parts[0] != "LICENSES":
                continue
            name = relative.name.lower()
            if not (name.startswith(("license", "copying", "notice", "copyright", "readme",
                                     "patents", "authors", "credits", "unlicense", "ofl"))
                    or "LICENSES" in relative.parts or name in (
                        "qt_attribution.json", "ftl.txt", "png.h", "zlib.h", "jpeglib.h", "jmorecfg.h")):
                continue
            content = archive.extractfile(member).read()
            info = tarfile.TarInfo(str(relative))
            info.size = len(content)
            info.mode = 0o644
            output.addfile(info, io.BytesIO(content))
            records.append({"path": str(relative), "sha256": hashlib.sha256(content).hexdigest()})
    if not records:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Qt notice selection was empty")
    manifest = {"version": entry["version"], "source_sha256": entry["sha256"],
                "modules": sorted(modules), "notice_archive": destination.name,
                "notice_sha256": sha(destination), "files": records}
    (destination.parent / "Qt-notices-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Collected {len(records)} Qt notice/attribution files: {destination}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("lock", "fetch", "verify", "package", "qt-notices"))
    parser.add_argument("--bundle", type=Path, default=ROOT / "dist/thoughtloud")
    parser.add_argument("--analysis", type=Path, default=ROOT / "build/thoughtloud/Analysis-00.toc")
    parser.add_argument("--lock", type=Path, default=ROOT / "packaging/source-lock.json")
    parser.add_argument("--directory", type=Path, default=ROOT / "build/release-sources/archives")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/packages/thoughtloud-1.4.0-dependency-sources.tar")
    args = parser.parse_args()
    if args.command == "lock":
        make_lock(args.bundle, args.analysis, args.lock)
        return
    lock = json.loads(args.lock.read_text())
    validate_lock(lock)
    if args.command == "qt-notices":
        qt_notices(lock, args.directory)
        return
    if args.command == "fetch":
        args.directory.mkdir(parents=True, exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda entry: download(entry, args.directory), lock["archives"]))
    verify(lock, args.bundle, args.analysis, args.directory)
    if args.command == "package":
        package(lock, args.lock, args.bundle, args.directory, args.output)


if __name__ == "__main__":
    main()
