# 0003. ONNX embeddings with `fastembed` instead of PyTorch

- Status: accepted (2026-10-20)
- Related: [0002](0002-lambda-instead-of-ecs.md)

## Context

The notebooks embedded recipe text with MiniLM and recipe images with CLIP through `sentence-transformers`, which pulls in PyTorch. Retrieval is multimodal: a fusion reranker merges text and image similarity per recipe, and the image side lifts search by visual description from MRR 0.52 to 0.90 ([evaluation report](../evals/report.md#retrieval-no-llm)). The production API runs on CPU inside a Lambda container image.

## Decision

Use **`fastembed`** (ONNX Runtime) for both embedding models, and the **quantised ONNX export of Llama Prompt Guard 2** for injection screening. No PyTorch in the runtime image.

## Alternatives considered

- **`sentence-transformers` + CPU-only PyTorch.** Same vectors, but PyTorch alone adds on the order of a gigabyte to the image and slows cold starts, for no gain on CPU inference.
- **A hosted embedding API (e.g. Titan Embeddings on Bedrock).** Adds a network call and a cost to every query, and the index would need to be rebuilt with different vectors, losing the comparison with the notebooks.
- **Pre-computing query embeddings.** Impossible: queries are free text.

## Consequences

- **The image stays at ~1.5 GB** with the index, MiniLM, CLIP and Prompt Guard baked in, which keeps Lambda cold starts in the 10 to 30 second range.
- **Same results as the notebooks.** `fastembed` produces the MiniLM and CLIP vectors the course notebooks used, so the retrieval numbers are comparable across versions.
- **Dependency pins to watch.** `fastembed` 0.9 pins `pillow<13`, and those versions have critical CVEs. The image forces Pillow 13; Pillow only decodes images while the index is built, never in the API. This is documented in [SECURITY.md](../../SECURITY.md#known-limitations) and should be revisited when the pin is lifted.
- **Fewer model choices.** Only models with an ONNX export supported by `fastembed` can be swapped in without code changes.
