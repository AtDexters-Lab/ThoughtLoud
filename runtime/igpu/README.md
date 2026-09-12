# Q8 iGPU runtime

This optional local runtime keeps VOXD off the discrete GPU. It exposes only the
Radeon 780M render node, serves the cached E4B Q8 target with an 8K context, and
uses llama-swap to unload the model after five idle minutes. The repository runtime
enables E4B MTP; llama.cpp's prompt cache remains disabled.

## MTP validation and deployment status (2026-09-01)

MTP is enabled in the deployed iGPU runtime using the formal build and installer
documented below. The preceding controlled spike used AtomicBot's exact working revision
`0a635dcd92ba66c75fccfef91c3e106f4668f367`, rebuilt with Vulkan and exposed only
to `/dev/dri/renderD129` (`AMD Radeon 780M`). It reused the deployed E4B target,
projector, prompts, and the existing 96 MiB `gemma4_assistant` Q8 head. The dGPU
service was not benchmarked or used to estimate iGPU latency.

The validated assistant artifact is
`gemma-4-E4B-it-assistant.Q8_0.gguf`, SHA-256
`eb576734fe210b551d091761fe83ab701c8e01ff708015a51172a4c0b04459e3`.
The installer rejects any other head; a merely load-compatible assistant does not
inherit the acceptance-rate or latency evidence below.

The unmodified AtomicBot server loaded MTP successfully, but deliberately skipped
speculative decoding for the tested multimodal audio requests. All 13 requests
reported zero draft and acceptance calls. Merely loading the unused assistant increased
5/15/25-second median wall time by 4.0%, 5.5%, and 5.0%. This explains why a working
MTP configuration on the other service did not automatically accelerate audio.

A temporary experiment-only patch allowed a sole MTP implementation to begin and
draft after multimodal prefill, and reset its accepted-output index at each new
request. The `off -> on -> off` run used three warm repetitions per phase; each off
median below pools both off phases (six measurements):

| Audio | MTP off median | MTP on median | Change |
| --- | ---: | ---: | ---: |
| 5 seconds | 1.809s | 1.413s | -21.9% |
| 15 seconds | 3.827s | 2.692s | -29.7% |
| 25 seconds | 6.357s | 4.566s | -28.2% |

All measured off/on/off transcripts were byte-identical at temperature zero. MTP
generated 434 draft tokens and accepted 173 (39.9%); every request produced drafts,
hidden-state norms were finite and non-zero, expanded attention positions were
consistent, and no async, KV, RoPE, or decode errors appeared. Alternating audio and
text requests on one patched server also remained identical across repetitions.

This is an implementation result, not a config-only win. The formal runtime path
below packages the tested multimodal-start and per-request-state fixes against the
exact AtomicBot source revision. Adaptive skip remains explicitly disabled because
its counters are not request-scoped in this revision.

The tested upstream llama.cpp revision
`8887a48f050554f0ee59f56753860c061836b02d` used a different `draft-mtp` loader
contract and could not load this assistant GGUF without a compatible export or loader
implementation.

Clone AtomicBot's fork and check out the validated source revision. The build helper
requires an exact, clean checkout; it applies `atomic-mtp-audio.patch` only inside an
isolated detached worktree, leaving the supplied checkout untouched:

```bash
git clone https://github.com/AtomicBot-ai/atomic-llama-cpp-turboquant.git \
  /path/to/atomic-llama-cpp-turboquant
git -C /path/to/atomic-llama-cpp-turboquant checkout --detach \
  0a635dcd92ba66c75fccfef91c3e106f4668f367

runtime/igpu/build-atomic-mtp.sh \
  /path/to/atomic-llama-cpp-turboquant \
  /tmp/voxd-atomic-mtp-vulkan
```

The helper builds Vulkan `llama-server` and writes
`voxd-mtp-build-manifest.json` beside it. The manifest binds the exact source commit,
VOXD patch hash, CMake options, assistant identity, server hash, and every runtime
shared-library hash and symlink target. The installer refuses an absent, mismatched,
or locally substituted manifest, copies only that declared runtime set, and verifies
the staged files again before creating the container.

Then install the runtime with the matching E4B assistant GGUF and launch its
restartable container:

```bash
runtime/igpu/install.sh \
  /tmp/voxd-atomic-mtp-vulkan/bin \
  /path/to/llama-swap \
  /path/to/gemma-4-E4B-it-assistant.Q8_0.gguf
```

The installer uses the repository's `.venv/bin/python`, created by `./setup.sh`,
for configuration and smoke-test helpers. Set `VOXD_PYTHON` to an alternative
VOXD environment interpreter when using a different source-install layout.

The launcher reuses the `lemonade-docker_hf-cache` Docker volume and the existing
`gemma-mtp-serve` Ubuntu runtime image without modifying either dGPU container.
It installs only the Vulkan binaries, build manifest, assistant head, and config
under the user's VOXD data and config directories:

- `~/.local/share/voxd/llama-vulkan/`
- `~/.config/voxd/igpu-runtime/`
- Docker container `voxd-gemma-igpu`

The installer identifies the Radeon 780M by its PCI vendor/device IDs
(`1002:1900`) and requires its matching `/dev/dri/by-path/pci-...-render` symlink.
It stores that stable path through an `igpu-render` symlink in the owned runtime
configuration directory, and maps only that device to `/dev/dri/renderD128`
inside the container. The alias avoids Docker CLI's colon-separated device
syntax; it continues to resolve the same PCI device when host `renderD` numbers
change after a reboot. Missing or mismatched PCI links stop installation before
any resources are created. If more than one matching GPU exists, `RENDER_NODE`
selects among them; it cannot select a different GPU model. Existing containers
retain their original device mapping until explicitly replaced.

The installer sends a short WAV through the same `input_audio` request shape as
VOXD and requires the response to report non-zero MTP drafts. It then sends the
same deterministic text request twice on the already-loaded multimodal server,
requiring the exact response and non-zero drafts each time. A successful exit thus
proves audio loading, active MTP, and clean per-request state—not merely that
llama-swap started or that the binary exposes MTP flags.
Only after that health check succeeds, it atomically updates
`~/.config/voxd/config.yaml` to use `http://127.0.0.1:9394` and the runtime's
fixed `gemma-e4b` model alias. If
`voxd-tray.service` is active, the installer restarts it; otherwise the new
endpoint takes effect the next time VOXD starts. Set `HOST_PORT` before running
the installer to use a different loopback port; the runtime and VOXD config are
updated together.

This is deliberately a fresh-install helper, not an upgrader. If its container
or either managed directory already exists, it exits without changing them.
Review and remove an existing deployment explicitly before installing a new
build; this repository change does not replace an already-running 9394 container.
During a fresh install, any failure rolls back only the managed runtime
directories and container created by that invocation; shared VOXD data/config
parent directories are never removed. An invalid existing VOXD config is
refused before staging begins.
