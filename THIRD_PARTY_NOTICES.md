# Third-party notices

The source in this repository is MIT licensed — see `LICENSE`.

The **release zip** is not only this source. To make the application run by being
unzipped, it also carries a Python runtime, the llama.cpp inference binaries, and the
vision projector, and `DOWNLOAD_MODELS.bat` fetches two model files on top of that. Those
components are the work of others and keep their own licences, listed below.

Nothing here is statically linked or modified. The binaries are invoked as separate
processes over local HTTP, and the models are read as data.

---

## Shipped inside the release zip

### llama.cpp — MIT

`bin\llama-cuda\`, `bin\llama-vulkan\`, `bin\llama-cpu\`, and the ggml libraries beside
them, are the official Windows release binaries of llama.cpp, build `b10852`.

- Source: https://github.com/ggml-org/llama.cpp
- Licence: MIT, Copyright (c) 2023-2024 The ggml authors
- Includes **ggml** (MIT) and **mtmd**, llama.cpp's multimodal support, which is what
  reads the page images.

### NVIDIA CUDA runtime — NVIDIA Software Licence Agreement

`bin\cudart64_12.dll`, `bin\cublas64_12.dll`, `bin\cublasLt64_12.dll`.

Redistributed under the redistribution rights granted in the NVIDIA CUDA Toolkit End
User Licence Agreement. Used only by the CUDA build, on machines with an NVIDIA card.

- https://docs.nvidia.com/cuda/eula/

### CPython — Python Software Foundation Licence 2.0

`runtime\` is a standalone CPython 3.12.14 with the packages below vendored into
`runtime\Lib\site-packages`. No interpreter is installed on the user's machine and
nothing is compiled at install time.

- https://docs.python.org/3/license.html

Vendored packages, all permissively licensed:

| Package | Licence |
|---|---|
| fastapi | MIT |
| starlette | BSD-3-Clause |
| uvicorn | BSD-3-Clause |
| pydantic, pydantic-core | MIT |
| anyio | MIT |
| h11 | MIT |
| click | BSD-3-Clause |
| idna | BSD-3-Clause |
| typing-extensions | PSF-2.0 |
| annotated-types | MIT |
| python-multipart | Apache-2.0 |

### Qwen3.8-27B vision projector — Apache-2.0

`models\mmproj-F16.gguf`, quantised and published by Unsloth from Alibaba Cloud's
Qwen3.8-27B. Ships in the zip because nothing in the application works without it.

- Model: https://huggingface.co/Qwen/Qwen3.8-27B
- GGUF conversion: https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
- Licence: Apache-2.0

---

## Downloaded by DOWNLOAD_MODELS.bat

### Qwen3.8-27B language models — Apache-2.0

`models\Qwen3.8-27B-UD-Q4_K_M.gguf` and `models\Qwen3.8-27B-UD-IQ2_XXS.gguf`, from the
same Unsloth repository, pinned to commit `4ca72078`.

- Licence: Apache-2.0
- The weights are Alibaba Cloud's; the GGUF quantisations are Unsloth's.

---

## Not included

This application contains no PyTorch, no transformers, and no HuggingFace hub client.
`DOWNLOAD_MODELS.bat` fetches the model files over plain HTTPS with `curl`, and the
application itself makes no network request of any kind at runtime.
