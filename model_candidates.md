# Model candidates for nlp-study on Argon

Date of evaluation: 2026-10-13. Scope: which local LLM the pipeline should call
through Ollama, given the hardware and software constraints of the University of
Iowa Argon cluster recorded in `ARGON_RUNBOOK.md`.

**Outcome: the pipeline runs three of these models.** `medgemma:27b` was adopted first
(2026-10-13, commit `9ad3a13`); on 2026-10-14 `gemma4:26b-a4b-it-q4_K_M` (the
recommendation in section 4) and `gpt-oss:20b` (the fallback) were added as the second and
third models, so every note is answered by all three and each model's answers go to their
own labelled columns for a comparison. `MODELS` in `llm_analysis.py` is the authority;
`README.md` and `ARGON_RUNBOOK.md` section 5.4 describe the current setup. Every call
passes `format=` the JSON schema and `options={"temperature": 0, "seed": 0,
"num_ctx": 8192, "num_predict": 2048}`, with `think=False` for the two Gemma models and
`think="low"` for gpt-oss (section 3). The context stays at 8192 for all three, so they
see the same notes, and because medgemma:27b fits the A10 only at that window (section 1).
The models run one after another (`OLLAMA_MAX_LOADED_MODELS=1`): each fits the A10 alone.
Sections 3 to 6 are kept as the record of the evaluation.

## 1. Constraints that decide the answer

| Constraint | Value | Consequence |
| --- | --- | --- |
| GPU seen on the UI-GPU queue | NVIDIA A10, 24 GB GDDR6, compute capability 8.6 | Weights plus KV cache plus compute buffers must stay under roughly 23.5 GB |
| NVIDIA driver on that node | 535 (not expected to change) | Fixes the newest usable Ollama release, see section 2 |
| Container runtime | Apptainer 1.3.2, image pulled from Docker Hub | Any `ollama/ollama:<tag>` works; the tag is set with `NLP_OLLAMA_IMAGE` |
| CPU fallback | 8 SGE slots, 251 GB RAM | A model that falls back to CPU must still be usable, which favours mixture-of-experts models |
| Context | `OLLAMA_CONTEXT_LENGTH=8192`; notes reach ~13K tokens at the tail, and a note that does not fit (about 2% of the corpus) is failed without a model call | KV cache sized for 8K: medgemma:27b fits the A10 at 8192 and spills at anything larger (checked 2026-10-14) |
| Task | 10-field JSON extraction per note, boolean fields as 0/1 | Structured output (`format=` JSON schema) and deterministic decoding matter more than reasoning depth |

## 2. The Ollama version is fixed by the driver

Ollama 0.30.0 (2026-05-13) switched its CUDA 12 build to compressed kernels that
require driver 550 or newer. On driver 535, releases 0.30.0 to 0.30.10 crash
with `CUDA error: device kernel image is invalid`; from 0.30.11 onward (PR
#16895, merged 2026-06-25) Ollama drops the GPU and runs on CPU while logging
`NVIDIA driver too old ... required_driver="550 or newer"`, which is exactly what
the September 2026 Argon logs show for `ollama/ollama:latest` (0.32.14).

**v0.24.0 (2026-05-14) is the last release that uses a driver-535 GPU.**
Evidence: the maintainers' explanation in issue #16449, users on driver 535 who
pinned 0.24.0 to recover GPU use, the v0.24.0 GPU docs (driver 531 or newer),
the v0.24.0 build presets (CUDA 12.8 runner with sm_86 compiled in, so no PTX
JIT is needed on the A10), and NVIDIA's minor-version compatibility rule (any
CUDA 12.x build runs on driver 525 or newer with a reduced feature set).
The tag `ollama/ollama:0.24.0` exists on Docker Hub for amd64.

Setting to use on a login node before the next submission:

```bash
NLP_OLLAMA_IMAGE=docker://ollama/ollama:0.24.0 ./setup.sh --force sif
```

Consequence: every model must load on v0.24.0 itself. Newer releases cannot be
used without a driver upgrade or a from-source build of Ollama against an older
CUDA toolkit.

## 3. Which model tags load on v0.24.0

The Ollama library has re-published its gemma4 and qwen3.6 tags for the
llama.cpp-based runner that arrived in 0.30: the vision projector is a separate
layer and the config carries `requires: 0.30.x`. v0.24.0 ignores the `requires`
field, but its Go engine refuses any manifest with a separate projector for an
architecture only that engine knows ("split vision models aren't supported") and
falls back to its bundled llama.cpp, which predates Gemma 4 and the Qwen 3.5
architecture. The load then fails with `unknown model architecture`.

Only the conversions uploaded for the old engine, with vision merged into the
single GGUF, still load. Each candidate was load-tested on the real v0.24.0 Linux
build (CPU only, 16 cores) by sending the preflight's synthetic PHI-free note
through the repo's own `llm_analysis()` unchanged, then through the planned call
(`format=` JSON schema, `think=False`, temperature 0). "6/6" means every scored
field matched the expected answer.

| Model tag | Loads on v0.24.0 | Repo call | Schema call | CPU decode tok/s | Est. VRAM at 8K ctx | Fits A10 24 GB |
| --- | --- | --- | --- | --- | --- | --- |
| `gemma4:26b` (default tag) | **no** | fails | fails | n/a | n/a | n/a |
| `gemma4:26b-a4b-it-q4_K_M` (current, second) | yes (Go engine) | 6/6 | 6/6, schema enforced | 17.6 | ~20 GB | yes |
| `gpt-oss:20b` (current, third) | yes (Go engine; its config says `requires` 0.30.0, which v0.24.0 ignores) | 6/6 | works with `think` unset or `low`; empty content with boolean `think=False` | n/a | ~15 GB | yes |
| `medgemma:27b` (current, first) | yes (Go engine) | 6/6 | 6/6, schema enforced | 3.2 | ~20 GB | yes |
| `gemma3:12b` (previous) | yes (llama.cpp fallback) | 6/6 | 6/6, schema enforced | 7.2 | ~10.5 GB | yes |
| `qwen3.6:27b` (default tag) | **no** (same cause as gemma4:26b) | not run | not run | n/a | n/a | n/a |
| `qwen3.6:27b-q4_K_M` | yes (Go engine) | 6/6, but 20 min on CPU with thinking on | schema **not enforced** (model-chosen keys; only follows the schema when it is pasted into the prompt) | 2.4 | ~19 GB | yes |
| `gemma4:31b` (every tag) | **no** (all ship a projector) | not run | not run | n/a | ~23.7 GB | no |
| `llama3.3:70b` | not load-tested (conventional manifest, would load) | n/a | n/a | n/a | ~46 GB | no (needs 2 to 3 GPUs or an 80 GB card) |

VRAM estimates: weights from the registry manifest, f16 KV cache from the GGUF
attention geometry with sliding-window layers holding only their window (v0.24.0
uses a sliding-window cache for Gemma), plus 1.0 to 1.5 GB of compute buffers.
The A10 usable budget is taken as 23.5 GB.

Attention geometry read from the GGUF headers:

| Model | Layers | KV heads | Head dim | Sliding window | Experts (active) |
| --- | --- | --- | --- | --- | --- |
| gemma4 26B-A4B | 30 | 8 local / 2 global | 512 | 1024, 5 local : 1 global | 128 (8) |
| gemma4 31B | 60 | 16 local / 4 global | 512 | 1024, 5 local : 1 global | dense |
| gpt-oss 20B | 24 | 8 | 64 | 128 on alternate layers | 32 (4) |
| medgemma 27B (Gemma 3) | 62 | 16 | 128 | 1024, 5 local : 1 global | dense |
| gemma3 12B | 48 | 8 | 256 | 1024 | dense |
| qwen3.6 27B | 64 | 4 on every 4th layer, linear attention otherwise | 256 | none | dense |
| llama3.3 70B | 80 | 8 | 128 | none | dense |

## 4. Recommendation

**Use `gemma4:26b-a4b-it-q4_K_M` on `ollama/ollama:0.24.0`.** It is the only
candidate that loads on the pinned release, passes both call styles with the
schema actually enforced, honours `think=False`, fits the A10 with about 3 GB
to spare, and remains usable on the CPU fallback because only 3.8B of its 25.2B
parameters are active per token. On the model card it scores MMLU-Pro 82.6 and
GPQA Diamond 82.3 against 67.6 and 42.4 for Gemma 3 27B, and it is Apache 2.0,
which removes the Gemma Terms of Use review noted in the runbook.

**Fallback: `gpt-oss:20b`.** Loads, passes, is the smallest fit (14 GB), and has
the most direct published evidence on clinical extraction (npj Precision
Oncology 2026: 89.4% F1 on 30 variables from 1,360 head-and-neck reports, ahead
of MedGemma-27B at 88.5%). Its schema mode on v0.24.0 needs `think` left unset
or set to a level such as `"low"`; a boolean `think=False` returns empty content.

Not recommended: `medgemma:27b` (works, but Gemma 3 generation, dense, 3.2 tok/s
on CPU, non-Apache licence); `qwen3.6:27b-q4_K_M` (schema not enforced on
v0.24.0, dense, very slow with thinking on); `gemma4:31b` (no loadable tag, over
budget); `llama3.3:70b` (over budget on one card).

## 5. Changes proposed, with their status

1. `LOCAL_MODEL = "gemma4:26b-a4b-it-q4_K_M"` in `llm_analysis.py`. The model
   blob digest is `sha256:7121486771cb...` (17.99 GB); record it, because the
   library has already re-pointed the `gemma4:26b` tag once. Blobs already on
   scratch are never re-fetched by setup.sh. **Adopted 2026-10-14 as the second
   model**, after `medgemma:27b` (`LOCAL_MODEL`, blob `sha256:4b97da6b21a8...`, 17.4 GB)
   and before `gpt-oss:20b`; the names are listed in the `MODELS` block.
2. `NLP_OLLAMA_IMAGE=docker://ollama/ollama:0.24.0`, then `./setup.sh --force sif`
   on a login node. Consider making this tag the setup.sh default (backlog B25).
   **Done:** `setup.sh` defaults to `0.24.0`.
3. In the chat call: `think=False`, `format=<JSON schema dict>`,
   `options={"temperature": 0, "seed": 0, "num_ctx": 8192, "num_predict": 512}`
   (backlog B9/F7). Without `think=False` the repo call took 98 s on CPU because
   Gemma 4 reasons first; with it, 16 s. Gemma 4's 256K window makes a 16K
   context cheap (about 0.75 GB of KV cache) and covers the longest notes.
   **Done for all three models**, with `num_predict` 2048 rather than 512 (the
   1000-character schema cap and the retries stop a looping model) and the context
   kept at 8192 for every model, so they see the same notes: medgemma:27b's dense KV
   cache does not fit the A10 at 16K. gpt-oss is called with `think="low"`.
4. Pin the card type to the A10 or newer with a `#$ -l <complex>` line. The P100
   has 16 GB and the V100 line had kernel problems on this Ollama version. **Open.**
5. Confirm with the tier 1 preflight that placement reads 100% on GPU. **Open:**
   unconfirmed on Argon (runbook section 12, item 5).

## 6. What was not verified directly

- The GPU path itself: the test machine has no GPU, so GPU use on driver 535
  rests on the v0.24.0 source and docs, NVIDIA's compatibility rule, and the user
  reports in Ollama issue #16449.
- `llama3.3:70b` was not load-tested after the memory estimate ruled it out.
- Quality on the real notes: the synthetic note is one easy case. Run the
  10-note sample in `samples/notes.csv` against the researcher's highlighted
  answers (see `BACKLOG.md`) before trusting any model on the 5,000-note corpus.

Test artefacts (92 GB of model blobs, the v0.24.0 build, server logs and result
JSON) are in `~/.cache/nlp-study-verify` on the workstation and can be deleted.

## 7. Sources

- Ollama issue 16449, 0.30 CUDA builds fail on driver 535, v0.24.0 last working: https://github.com/ollama/ollama/issues/16449
- Ollama PR 16895, require driver 550 for the CUDA v12 runner: https://github.com/ollama/ollama/pull/16895
- Ollama v0.24.0 engine selection, `llm/server.go`: https://github.com/ollama/ollama/blob/v0.24.0/llm/server.go
- Ollama v0.24.0 GPU docs (driver 531 or newer): https://raw.githubusercontent.com/ollama/ollama/v0.24.0/docs/gpu.mdx
- Ollama v0.24.0 CMake presets (CUDA 12 architectures): https://raw.githubusercontent.com/ollama/ollama/v0.24.0/CMakePresets.json
- Ollama v0.24.0 release notes: https://github.com/ollama/ollama/releases/tag/v0.24.0
- Ollama current hardware docs (driver 550 or newer): https://docs.ollama.com/gpu
- NVIDIA CUDA minor version compatibility: https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html
- `ollama/ollama:0.24.0` on Docker Hub: https://hub.docker.com/r/ollama/ollama/tags?name=0.24.0
- gemma4 tags on Ollama: https://ollama.com/library/gemma4/tags
- qwen3.6 tags on Ollama: https://ollama.com/library/qwen3.6/tags
- gpt-oss on Ollama: https://ollama.com/library/gpt-oss
- Gemma 4 26B A4B model card and benchmark table: https://huggingface.co/google/gemma-4-26b-a4b-it
- Gemma 4 under Apache 2.0, Google Open Source Blog: https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html
- Prognostic data extraction with a privacy-preserving LLM, npj Precision Oncology 2026: https://doi.org/10.1038/s41698-026-01521-y
- Medication attribute extraction across EHR systems, arXiv 2510.21027: https://arxiv.org/abs/2510.21027
- Medmarks medical benchmark suite, arXiv 2605.01417: https://arxiv.org/html/2605.01417v1
- UIowa HPC queues page (UI-GPU hardware): https://wiki.uiowa.edu/display/hpcdocs/Queues+and+Policies
- NVIDIA A10 datasheet, 24 GB GDDR6: https://bxin.cisco.com/c/dam/en/us/products/collateral/servers-unified-computing/ucs-c-series-rack-servers/nvidia-a10-gpu.pdf
