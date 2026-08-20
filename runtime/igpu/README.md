# Q8 iGPU runtime

This optional local runtime keeps VOXD off the discrete GPU. It exposes only the
Radeon 780M render node, serves the cached E4B Q8 target with an 8K context, and
uses llama-swap to unload the model after five idle minutes. MTP and llama.cpp's
prompt cache are disabled.

Build a Vulkan `llama-server` with current Gemma 4 audio support. The validated
source revision on this machine is upstream llama.cpp
`15ad8f4201d05fee7be94e42ac73fc934ff20235`:

```bash
cmake -S /path/to/llama.cpp -B /tmp/voxd-llama-vulkan \
  -DGGML_VULKAN=ON -DGGML_CCACHE=OFF -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_NUMBER=9595 \
  -DLLAMA_BUILD_COMMIT=15ad8f4201d05fee7be94e42ac73fc934ff20235 \
  -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/voxd-llama-vulkan --target llama-server -j4
```

Then install the runtime and launch its restartable container:

```bash
runtime/igpu/install.sh \
  /tmp/voxd-llama-vulkan/bin \
  /path/to/llama-swap
```

The installer uses the repository's `.venv/bin/python`, created by `./setup.sh`,
for configuration and smoke-test helpers. Set `VOXD_PYTHON` to an alternative
VOXD environment interpreter when using a different source-install layout.

The launcher reuses the `lemonade-docker_hf-cache` Docker volume and the existing
`gemma-mtp-serve` Ubuntu runtime image without modifying either dGPU container.
It installs only the Vulkan binaries and config under the user's VOXD data and
config directories:

- `~/.local/share/voxd/llama-vulkan/`
- `~/.config/voxd/igpu-runtime/`
- Docker container `voxd-gemma-igpu`

The installer sends a short WAV through the same `input_audio` request shape as
VOXD, so a successful exit proves the model and audio-capable projector can
load, not merely that llama-swap started.
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
build. During a fresh install, any failure rolls back only the managed runtime
directories and container created by that invocation; shared VOXD data/config
parent directories are never removed. An invalid existing VOXD config is
refused before staging begins.
