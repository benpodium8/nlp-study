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
Verified 2026-10-14 on the v0.24.0 Linux build kept from this evaluation (CPU only,
`OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=1`, context 8192): the repo's own
`llm_analysis()` call returned schema-valid JSON scoring 6/6 on the synthetic note for all
three models, each on the Go engine, in 197 s (medgemma:27b), 41 s (gemma4) and 59 s
(gpt-oss, `think="low"`); loaded sizes 20.0, 19.8 and 14.2 GB with the 8K cache.
Sections 3 to 6 are kept as the record of the evaluation. The three small stand-ins that
`--location local` runs on a workstation (`ministral-3:3b`, `gemma4:e2b`, `qwen3.5:4b`;
`LOCAL_MODELS` in `llm_analysis.py`) were not part of it and are not meant for Argon.

**Update 2026-10-16: `medgemma:27b` is out.** Section 8 re-examines the candidates after the first
complete run of the 275-note sample and under two constraints the study added (no models from
Non-US labs; workstation stand-ins that fit an 8 GB GPU). The study models are now
`mistral-small3.2:24b-instruct-2506-q4_K_M`, `gemma4:26b-a4b-it-q4_K_M` and `gpt-oss:20b`, and the
stand-ins `ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat`, `nemotron-3-nano:4b`.
Sections 1 to 7 are kept as the record of the first evaluation; where they say what the pipeline
runs, section 8 supersedes them.

## 1. Constraints that decide the answer

| Constraint                   | Value                                                                                                                                                   | Consequence                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| GPU seen on the UI-GPU queue | NVIDIA A10, 24 GB GDDR6, compute capability 8.6                                                                                                         | Weights plus KV cache plus compute buffers must stay under roughly 23.5 GB                                  |
| NVIDIA driver on that node   | 535 (not expected to change)                                                                                                                            | Fixes the newest usable Ollama release, see section 2                                                       |
| Container runtime            | Apptainer 1.3.2, image pulled from Docker Hub                                                                                                           | Any `ollama/ollama:<tag>` works; the tag is set with `NLP_OLLAMA_IMAGE`                                     |
| CPU fallback                 | 8 SGE slots, 251 GB RAM                                                                                                                                 | A model that falls back to CPU must still be usable, which favours mixture-of-experts models                |
| Context                      | `OLLAMA_CONTEXT_LENGTH=8192`; notes reach ~13K tokens at the tail, and a note that does not fit (about 2% of the corpus) is failed without a model call | KV cache sized for 8K: medgemma:27b fits the A10 at 8192 and spills at anything larger (checked 2026-10-14) |
| Task                         | 10-field JSON extraction per note, boolean fields as 0/1                                                                                                | Structured output (`format=` JSON schema) and deterministic decoding matter more than reasoning depth       |

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

| Model tag                                    | Loads on v0.24.0                                                          | Repo call                               | Schema call                                                                                            | CPU decode tok/s | Est. VRAM at 8K ctx | Fits A10 24 GB                          |
| -------------------------------------------- | ------------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------ | ---------------- | ------------------- | --------------------------------------- |
| `gemma4:26b` (default tag)                   | **no**                                                                    | fails                                   | fails                                                                                                  | n/a              | n/a                 | n/a                                     |
| `gemma4:26b-a4b-it-q4_K_M` (current, second) | yes (Go engine)                                                           | 6/6                                     | 6/6, schema enforced                                                                                   | 17.6             | ~20 GB              | yes                                     |
| `gpt-oss:20b` (current, third)               | yes (Go engine; its config says `requires` 0.30.0, which v0.24.0 ignores) | 6/6                                     | works with `think` unset or `low`; empty content with boolean `think=False`                            | n/a              | ~15 GB              | yes                                     |
| `medgemma:27b` (current, first)              | yes (Go engine)                                                           | 6/6                                     | 6/6, schema enforced                                                                                   | 3.2              | ~20 GB              | yes                                     |
| `gemma3:12b` (previous)                      | yes (llama.cpp fallback)                                                  | 6/6                                     | 6/6, schema enforced                                                                                   | 7.2              | ~10.5 GB            | yes                                     |
| `qwen3.6:27b` (default tag)                  | **no** (same cause as gemma4:26b)                                         | not run                                 | not run                                                                                                | n/a              | n/a                 | n/a                                     |
| `qwen3.6:27b-q4_K_M`                         | yes (Go engine)                                                           | 6/6, but 20 min on CPU with thinking on | schema **not enforced** (model-chosen keys; only follows the schema when it is pasted into the prompt) | 2.4              | ~19 GB              | yes                                     |
| `gemma4:31b` (every tag)                     | **no** (all ship a projector)                                             | not run                                 | not run                                                                                                | n/a              | ~23.7 GB            | no                                      |
| `llama3.3:70b`                               | not load-tested (conventional manifest, would load)                       | n/a                                     | n/a                                                                                                    | n/a              | ~46 GB              | no (needs 2 to 3 GPUs or an 80 GB card) |

VRAM estimates: weights from the registry manifest, f16 KV cache from the GGUF
attention geometry with sliding-window layers holding only their window (v0.24.0
uses a sliding-window cache for Gemma), plus 1.0 to 1.5 GB of compute buffers.
The A10 usable budget is taken as 23.5 GB.

Attention geometry read from the GGUF headers:

| Model                  | Layers | KV heads                                         | Head dim | Sliding window           | Experts (active) |
| ---------------------- | ------ | ------------------------------------------------ | -------- | ------------------------ | ---------------- |
| gemma4 26B-A4B         | 30     | 8 local / 2 global                               | 512      | 1024, 5 local : 1 global | 128 (8)          |
| gemma4 31B             | 60     | 16 local / 4 global                              | 512      | 1024, 5 local : 1 global | dense            |
| gpt-oss 20B            | 24     | 8                                                | 64       | 128 on alternate layers  | 32 (4)           |
| medgemma 27B (Gemma 3) | 62     | 16                                               | 128      | 1024, 5 local : 1 global | dense            |
| gemma3 12B             | 48     | 8                                                | 256      | 1024                     | dense            |
| qwen3.6 27B            | 64     | 4 on every 4th layer, linear attention otherwise | 256      | none                     | dense            |
| llama3.3 70B           | 80     | 8                                                | 128      | none                     | dense            |

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
   has 16 GB and the V100 line had kernel problems on this Ollama version. **Partly done
   2026-10-16:** the job requests a GPU by default (`UI-GPU`, `ngpus=1`) and
   `NLP_JOB_GPU_TYPE=<complex>` pins the card type; the complex name for the A10 nodes
   still has to be read from `qconf -sc` on Argon before it can be set.
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

## 8. Re-evaluation of 2026-10-16: medgemma:27b replaced, the stand-ins replaced

Trigger: the first complete run of the 275-note sample (2026-10-15, three models, 1:34:44 on
the workstation), and two constraints the study added: no models from Non-US labs (Qwen,
DeepSeek, GLM, Kimi, MiniMax, and models post-trained from them), and workstation stand-ins
that fit an 8 GB GPU. The HPC constraints of section 1 are unchanged: Ollama 0.24.0, one A10
(24 GB), the 8192-token window for every model, one Ollama library tag per model
(`ollama pull <name:tag>`, nothing to convert or merge). `gemma4:26b-a4b-it-q4_K_M` and
`gpt-oss:20b` stay.

**Outcome.** `medgemma:27b` is replaced by `mistral-small3.2:24b-instruct-2506-q4_K_M`
(section 8.5); the stand-ins `ministral-3:3b`, `gemma4:e2b` and `qwen3.5:4b` by
`ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat` and `nemotron-3-nano:4b`
(section 8.4). Adopted in `llm_analysis.py` the same day; neither set has run yet (section 8.6).

### 8.1 What the run showed about medgemma:27b

| model                    | failed | of which too long (no model read them) | flagged | rejected attempts | notes failed at the 5-attempt cap | mean s/note | median |
| ------------------------ | ------ | -------------------------------------- | ------- | ----------------- | --------------------------------- | ----------- | ------ |
| medgemma:27b             | 28     | 7                                      | 75      | 157 (on 69 notes) | 21                                | 14.4        | 8.2    |
| gemma4:26b-a4b-it-q4_K_M | 9      | 7                                      | 26      | 27 (on 18 notes)  | 2                                 | 2.7         | 2.4    |
| gpt-oss:20b              | 11     | 7                                      | 20      | 33 (on 17 notes)  | 4                                 | 3.2         | 2.3    |

The rejections were rule-following, not judgment: 59 answers quoted a sentence that names an
upper endoscopy without saying it was carried out, 34 set FellowPresent with no procedure in
the note, 18 gave a scope type the note does not state, 16 quoted text that is not a sentence
of the note, 12 repeated text up to the 1000-character cap. Where medgemma did answer, it
disagreed with the consensus on 1 of 1,250 field votes. The 17 notes only medgemma failed are
ordinary procedure, progress and consult notes of 1,800 to 13,500 characters. So the
replacement has to follow the quoting rules and not loop; it does not need medical
fine-tuning (every field here is a reading-comprehension question about one note, and the
consensus of three models catches the rest), and a model that was slow because it is a dense
27B is better replaced by something lighter or sparser. The per-note times above are the
workstation's, where the 17 to 18 GB models spill to the CPU; on the A10 every model is on the
GPU, but a dense model stays the slowest of the three.

### 8.2 Method

Every candidate tag was read from `registry.ollama.ai` directly (the manifest, the config
blob, and the first 48 MB of the model blob for the GGUF header), the way `setup.sh` fetches
models: weight size, quantization, the config's `requires` field, whether the manifest ships a
separate projector layer (which 0.24.0's engine refuses, section 3), and the attention geometry
(layers, KV heads, head size, sliding windows, hybrid layers), from which the f16 KV cache at
8192 tokens follows. "Loaded" below is weights + KV cache + 1.2 GB of compute buffers; the A10
budget is taken as 23.5 GB and an 8 GB workstation card as 7.5 GB. A tag published before
0.24.0 (2026-05-14) for a then-current Ollama and not re-published since loads on it; a tag with
`requires` above 0.24.0 or a separate projector does not, unless load-tested (gpt-oss, section
3). No model was run in this re-evaluation: the machine it was done on has no Ollama, so the
quality evidence is published material, and the first load of the new tags is the preflight's
job (on Argon) and the `load` line's (on the workstation).

### 8.3 Study-model candidates (A10, 24 GB, Ollama 0.24.0)

| Tag                                                                                                 | Lab, licence                        | Weights               | Shape                                             | `requires` | Projector | Loaded at 8K   | Loads on 0.24.0                                                                                          | Verdict                                                                                                                                                            |
| --------------------------------------------------------------------------------------------------- | ----------------------------------- | --------------------- | ------------------------------------------------- | ---------- | --------- | -------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mistral-small3.2:24b-instruct-2506-q4_K_M` (`mistral-small3.2:24b`)                                | Mistral AI, Apache 2.0              | 15.2 GB Q4_K_M        | dense 24B, 40 layers, 8 KV heads, 128K            | none       | no        | 17.7 GB        | yes (June 2025 tag for Ollama 0.9, `mistral3` on the engine since 0.6; not yet loaded on a 0.24.0 build) | **chosen**                                                                                                                                                         |
| `ministral-3:14b-instruct-2512-q4_K_M` (`ministral-3:14b`)                                          | Mistral AI, Apache 2.0              | 9.1 GB Q4_K_M         | dense 14B, 40 layers, 8 KV heads, 256K            | none       | no        | 11.6 GB        | yes (Dec 2025 tag, same architecture)                                                                    | fallback: fits a 16 GB P100 too, about 1.6 times faster, slightly less knowledge                                                                                   |
| `granite4.1:30b` / `granite4.2:30b`                                                                 | IBM, Apache 2.0                     | 17.5 / 17.7 GB Q4_K_M | dense 30B, 64 layers, 128K                        | none       | no        | 20.8 / 21.1 GB | yes (`granite` architecture)                                                                             | no: dense 30B (3 tok/s on CPU), 2.5 GB of headroom, weak independent benchmarks, no extraction literature                                                          |
| `phi4:14b`                                                                                          | Microsoft, MIT                      | 9.1 GB                | dense 14B, 16K context                            | none       | no        | 11.9 GB        | yes                                                                                                      | no: December 2024, weaker instruction following than the 2025 to 2026 models                                                                                       |
| `magistral:24b`                                                                                     | Mistral AI, Apache 2.0              | 14.3 GB               | dense 24B, reasoning                              | none       | no        | 16.9 GB        | yes                                                                                                      | no: reasoning model, slower, `think` handling on 0.24.0 unverified                                                                                                 |
| `olmo-3.1:32b`                                                                                      | Ai2, Apache 2.0                     | 19.5 GB               | dense 32B                                         | none       | no        | 22.0 GB        | yes (`olmo3`)                                                                                            | no: 1.5 GB of headroom, dense 32B                                                                                                                                  |
| `nemotron-3-nano:30b` (`30b-a3b-q4_K_M`)                                                            | NVIDIA, Nemotron Open Model Licence | 24.3 GB Q4_K_M        | hybrid Mamba MoE, 3.5B active, 6 attention layers | none       | no        | 25.5 GB        | yes                                                                                                      | **does not fit**: the library's smallest build is 24.3 GB (the Mamba layers stay in high precision); the model to want if a smaller quantization is ever published |
| `nemotron-cascade-2:30b`                                                                            | NVIDIA                              | 24.3 GB               | as above                                          | 0.17.1     | no        | 25.5 GB        | yes                                                                                                      | does not fit                                                                                                                                                       |
| `nemotron-3.5-lightning:30b`                                                                        | NVIDIA                              | 25.4 GB               | hybrid MoE                                        | 0.32.9     | no        | 26.7 GB        | no                                                                                                       | no                                                                                                                                                                 |
| `gemma4:12b` (`12b-it-q4_K_M`)                                                                      | Google, Apache 2.0                  | 8.0 GB                | dense 12B                                         | 0.30.9     | yes       | 9.4 GB         | **no** (re-published for the 0.30 runner, section 3)                                                     | no                                                                                                                                                                 |
| `gemma4:31b-it-q4_K_M`                                                                              | Google                              | 20 GB                 | dense 31B                                         | 0.30.x     | yes       | about 23.7 GB  | no                                                                                                       | no                                                                                                                                                                 |
| `muse-glimmer:30b`                                                                                  | Meta, Apache 2.0 (September 2026)   | 18.2 GB               | 28B, 2 KV heads, sliding window                   | 0.32.8     | yes       | 18.1 GB        | **no**                                                                                                   | the one to revisit if Argon's driver is ever upgraded (BACKLOG B25)                                                                                                |
| `lfm2.5:24b`                                                                                        | Liquid AI, LFM licence              | 14.5 GB               | MoE, 2B active                                    | 0.40.4     | no        | 15.9 GB        | no                                                                                                       | no                                                                                                                                                                 |
| `llama3.3:70b`, `llama4`, `gpt-oss:120b`, `mistral-medium-3.5`, `mistral-large-4`                   |                                     | 40 GB and up          |                                                   |            |           |                |                                                                                                          | too big for one A10                                                                                                                                                |
| `qwen3.6`, `qwen3.5`, `deepseek-*`, `glm-*`, `kimi-*`, `minimax-*`, `pplx-decider` (Qwen 3.8 based) |                                     |                       |                                                   |            |           |                |                                                                                                          | excluded by the study (Non-US labs)                                                                                                                                |

The October 2026 library also carries a new class of "decision" models (`kev`, `nimble`,
`clef`, `tev1`, `d1`, `strands-decider`, `laya`): days old, built for a different API than the
chat call with a JSON schema this pipeline makes, and published for the newest Ollama, so not
usable on 0.24.0 or with the code as it is.

### 8.4 Stand-in candidates (8 GB GPU, current Ollama; `--location local`)

| Tag                                                                | Lab                                 | Weights                                 | `requires` | KV at 8K                           | Loaded at 8K | Verdict                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------ | ----------------------------------- | --------------------------------------- | ---------- | ---------------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gemma4:e4b-it-qat`                                                | Google                              | 6.2 GB Q4_0, quantization-aware trained | 0.30.5     | 0.3 GB                             | 6.7 GB       | **chosen** for Gemma 4 26B A4B's slot: E4B scores MMLU-Pro 69.4 and GPQA Diamond 58.6 against E2B's 60.0 and 43.4 on the model card, and the QAT build keeps that at Q4                                                                                                 |
| `gemma4:e4b-it-q4_K_M`                                             | Google                              | 6.6 GB                                  | 0.30.9     | 0.3 GB                             | 7.0 GB       | no: half a gigabyte from the edge of the card                                                                                                                                                                                                                           |
| `gemma4:e2b-it-q4_K_M` (`gemma4:e2b`, previous)                    | Google                              | 4.6 GB                                  | 0.30.9     | 0.15 GB                            | 4.8 GB       | replaced; the fallback if the `load` line shows E4B spilling (`gemma4:e2b-it-qat`, 4.3 GB, is the matching QAT build)                                                                                                                                                   |
| `ministral-3:3b-instruct-2512-q4_K_M` (`ministral-3:3b`, previous) | Mistral AI                          | 3.0 GB                                  | none       | 0.9 GB                             | 5.0 GB       | **kept, pinned to the exact tag**: the small sibling of Mistral Small 3.2 (distilled from Mistral Small 3.1), the right stand-in for the new first model                                                                                                                |
| `ministral-3:8b`                                                   | Mistral AI                          | 6.0 GB                                  | none       | 1.1 GB (full attention)            | 8.4 GB       | does not fit                                                                                                                                                                                                                                                            |
| `nemotron-3-nano:4b`                                               | NVIDIA, Nemotron Open Model Licence | 2.8 GB Q4_K_M                           | 0.17.1     | 0.13 GB (4 attention layers of 42) | 4.2 GB       | **chosen** for gpt-oss's slot: a reasoning model run with reasoning off, IFEval 82.8 (prompt) and 88.0 (instruction) in that mode, 256K context, released 2026-03-16; `nemotron-3-nano:4b-q8_0` (4.2 GB, 5.6 GB loaded) also fits if a higher-precision build is wanted |
| `granite4.1:3b` / `granite4.1:8b`                                  | IBM                                 | 2.1 / 5.4 GB                            | none       | 0.7 / 1.3 GB                       | 4.0 / 7.9 GB | no: the 8B is at the edge, the 3B is weaker than the choices above                                                                                                                                                                                                      |
| `phi4-mini:3.8b`                                                   | Microsoft                           | 2.5 GB                                  | none       | 1.1 GB                             | 4.8 GB       | no: February 2025, weaker                                                                                                                                                                                                                                               |
| `olmo-3:7b`                                                        | Ai2                                 | 4.5 GB                                  | none       | 2.7 GB (32 KV heads, no GQA)       | 8.4 GB       | does not fit                                                                                                                                                                                                                                                            |
| `llama3.1:8b`                                                      | Meta                                | 4.9 GB                                  | none       | 1.1 GB                             | 7.2 GB       | no: July 2024                                                                                                                                                                                                                                                           |
| `lfm2.5:8b`                                                        | Liquid AI                           | 5.2 GB                                  | 0.30.0     | 0.1 GB                             | 6.5 GB       | no: edge model, non-standard licence                                                                                                                                                                                                                                    |
| `medgemma1.5:4b`                                                   | Google                              | 3.3 GB                                  | none       | 0.3 GB                             | 4.8 GB       | no: the family being left                                                                                                                                                                                                                                               |
| `qwen3.5:4b` (previous)                                            |                                     |                                         |            |                                    |              | excluded by the study                                                                                                                                                                                                                                                   |

The three chosen stand-ins mirror the study set family by family, in the same order (Mistral,
Gemma 4, a reasoning model with reasoning off), so a workstation run exercises the same
tokenizers, chat templates and `think` handling; gpt-oss has no size under 20B (13.8 GB), so
Nemotron 3 Nano 4B takes that slot. About 12 GB of disk in all.

### 8.5 Quality evidence and the decision

- Mistral Small 3.2 (released 2025-06-20, a maintenance update of 3.1) is documented by Mistral
  as better at exactly what medgemma got wrong: WildBench v2 55.6 to 65.3, Arena Hard v2 19.6
  to 43.1, internal instruction-following accuracy 82.75 to 84.78, and infinite generations
  (repetition loops) 2.11% to 1.29% on long, repetitive prompts; MMLU-Pro 69.1, MMLU 80.5.
  Apache 2.0, 128K context, vision merged into the Ollama GGUF, 24 languages.
- Ministral 3 (released 2025-12, paper arXiv 2601.08584) is cascade-distilled from Mistral
  Small 3.1; the 14B instruct scores Arena Hard 55.1 and WildBench 68.5 (Gemma 3 12B: 43.6 and
  63.2), its base trails Mistral Small 24B by 1.6 points of MMLU (79.4 against 81.0), and
  Mistral and NVIDIA describe the 14B as comparable to Mistral Small 3.2. It is the lighter
  option (11.6 GB loaded) if the dense 24B proves too slow on the A10 or a 16 GB card is all
  there is; the 24B was preferred for capacity, a year of use, and the measured repetition
  figure.
- A January 2026 medRxiv benchmark of extraction tools reports Mistral 3.1 24B returning
  unparseable JSON 73.7% of the time under plain prompting (Gemma 3 27B: 18.1%). That is the
  risk this pipeline removes with `format=` (the schema is enforced by the server), and Small
  3.2 halved the loop rate; what remains measurable is rule-following, which the retry loop
  counts per model (`Rejected attempts`), so the first full run tells.
- gpt-oss keeps its place: the JMIR Cardio 2026 symptom-extraction study puts gpt-oss-120B at
  F1 0.88 against Llama 3.3 70B's 0.73, and section 4 already cites the npj Precision Oncology
  result for the 20B. Gemma 4 26B A4B keeps its place on the run itself (fewest failures,
  fastest) and on its model card (MMLU-Pro 82.6, GPQA Diamond 82.3).
- Nemotron 3 Nano 30B A3B (MMLU-Pro 78.3, hybrid Mamba MoE, 3.5B active) would have been the
  natural medgemma replacement, Apache-like licence and CPU-friendly, but the only Ollama build
  under 34 GB is 24.3 GB and does not fit the A10; its 4B sibling is the stand-in instead.
- Granite 4.1 (2026-04-29) and 4.2 (2026-08-25) are dense 3B/8B/30B models with IBM-only
  benchmark tables; independent indices rank the 30B well below Gemma 4 26B, and a dense 30B
  brings back medgemma's CPU problem.

Decision: `MODELS` = `mistral-small3.2:24b-instruct-2506-q4_K_M` (`LOCAL_MODEL`, `think=False`),
`gemma4:26b-a4b-it-q4_K_M`, `gpt-oss:20b` (`think="low"`), all at 8192; `LOCAL_MODELS` =
`ministral-3:3b-instruct-2512-q4_K_M`, `gemma4:e4b-it-qat`, `nemotron-3-nano:4b`, all at 8192
with `think=False`. The exact tags are pinned because the library has re-pointed default tags
before (`gemma4:26b`, section 3); `mistral-small3.2:24b` and the pinned tag share one digest
today. With medgemma gone, the three study models would also fit the A10 at a 16K window
(about 19.1, 20.1 and 15.3 GB loaded), which would recover the 2% of notes now failed as too
long; the window stays at 8192 by the study's decision, and that is a separate change.

### 8.6 Not verified

- No new tag has been loaded on an Ollama 0.24.0 build, or run at all: the first load is the
  Argon preflight (study models) and the workstation `load` line (stand-ins). Both report
  placement; `gemma4:e4b-it-qat` at 6.7 GB is the tightest fit on an 8 GB card.
- Quality on the real notes: run the 275-note sample again and compare `Rejected attempts` and
  the failure counts per model with section 8.1; the gold labels of `BACKLOG.md` section 1
  remain the only accuracy check.
- The KV-cache figures are computed from the GGUF headers, not measured; Ollama's own estimate
  on load is the one that counts.

### 8.7 Sources

- Ollama library, newest first (2026-10-16): https://ollama.com/library?sort=newest
- `mistral-small3.2` tags: https://ollama.com/library/mistral-small3.2/tags
- `ministral-3` tags: https://ollama.com/library/ministral-3/tags
- `gemma4` tags: https://ollama.com/library/gemma4/tags
- `nemotron-3-nano` page and tags: https://ollama.com/library/nemotron-3-nano and https://ollama.com/library/nemotron-3-nano/tags
- `granite4.1` tags: https://ollama.com/library/granite4.1/tags
- Registry manifests, config blobs and GGUF headers read from `https://registry.ollama.ai/v2/library/<name>/manifests/<tag>` and `/blobs/<digest>` (the protocol `setup.sh` uses)
- Mistral Small 3.2 model card: https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506
- Mistral Small 3.2 release coverage: https://venturebeat.com/ai/mistral-just-updated-its-open-source-small-model-from-3-1-to-3-2-heres-why
- Ministral 3 paper: https://arxiv.org/abs/2601.08584 ; launch page: https://mistral.ai/news/mistral-3 ; NVIDIA's listing of the 14B: https://docs.api.nvidia.com/nim/reference/mistralai-ministral-14b-instruct-2512
- Nemotron 3 Nano 4B model card: https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16
- Nemotron 3 Nano 30B A3B model card: https://build.nvidia.com/nvidia/nemotron-3-nano-30b-a3b.md
- Gemma 4 E4B model card (E2B/E4B/26B/31B table): https://huggingface.co/google/gemma-4-E4B-it
- Granite 4.1: https://huggingface.co/ibm-granite/granite-4.1-30b-base and https://www.llmreference.com/model-family/granite-4.1 ; Granite 4.2: https://datanorth.ai/news/ibm-releases-granite-4-2
- Benchmarking LLM-based information extraction tools for medical documents, medRxiv 2026.01.19: https://www.medrxiv.org/content/10.64898/2026.01.19.26344287v1.full
- Extracting cardiorespiratory symptoms with open-weight LLMs, JMIR Cardio 2026: https://cardio.jmir.org/2026/1/e89480
- Google's Gemma release log (Gemma 4 sizes, 12B Unified of 2026-06-03): https://ai.google.dev/gemma/docs/releases
