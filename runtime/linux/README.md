# Managed Linux runtime

New managed installations use the same Gemma E4B Q8 target and F16 audio projector
as the existing iGPU setup. CPU uses those two files without MTP. Vulkan adds
the pinned Q8 assistant and enables MTP with three draft tokens; target, projector
and assistant use Vulkan0. Existing external endpoints and the optional
`runtime/igpu` installation remain independently managed.

The application downloads the model files from a pinned public revision and
verifies their sizes and SHA-256 hashes. Model files total approximately 9.2 GB
for CPU or 9.3 GB for Vulkan. Switching to Vulkan reuses verified CPU files and
downloads only the 100 MB assistant. The manifest pins AtomicChat revision
`69e1c34ad06437c136b935f6bf53ff80540c2361`; newer assistant exports use a different
format. They are under Apache-2.0; the model license must accompany the release.

## Building the bundled server

Use an Ubuntu 24.04 x86_64 builder with CMake, a C++ compiler, Vulkan development
headers and `glslc`. The build includes a portable x64 CPU backend and optimized
CPU variants. The native library detects the available CPU features and selects
a compatible backend; AVX2 is not required. Shared code is built without the
builder's native CPU tuning. Vulkan acceleration requires a working host driver
and is verified separately from the CPU profile. See the
[release evidence](../../docs/linux-release.md) for the tested CPU environments.
The desktop bundle's Qt requires SSSE3, SSE4.1, SSE4.2 and POPCNT even though
the native inference library includes an older x64 fallback. Removing AVX2
does not remove that separate desktop dependency baseline (x86-64-v2).

```sh
git clone https://github.com/AtomicBot-ai/atomic-llama-cpp-turboquant.git /tmp/voxd-llama-source
git -C /tmp/voxd-llama-source checkout --detach 0a635dcd92ba66c75fccfef91c3e106f4668f367
runtime/linux/build.sh /tmp/voxd-llama-source /tmp/voxd-llama-build
```

Continue with the [complete packaging recipe](../../packaging/README.md), which
also builds the pinned typing helpers and passes both native directories to the
bundle builder.

The builder applies the repository's audio patch in a temporary worktree and
preserves the supplied clean source checkout. Source, patch and license details
travel with the binary. The model download and runtime lifecycle are implemented
in `src/voxd/runtime`; the application owns only its child server process.

Keep all `libggml-cpu-*.so` modules and `libggml-vulkan.so` beside `llama-server`.
The recipe enables the pinned library's dynamic backends and CPU variants, and
rejects a build missing its x64, Haswell or Vulkan module. The application bundle
collects these modules along with the server's other libraries. Compatibility
testing must exercise the bundled VAD/audio dependencies as well as the native
server on a CPU without AVX2; a CLI version check alone is insufficient.

The package builder requires both native runtimes. After installation the app
can use either its managed runtime or an existing audio-compatible endpoint.
