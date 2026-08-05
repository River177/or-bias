# OR-Bench multilingual over-refusal

This repository contains the reproducible multilingual OR-Bench harmless-side
pipeline. The canonical experiment is the nine-language v2 panel:

`en, zh, ja, ko, sv, da, ta, mn, sw`

Read [INSTRUCT.md](INSTRUCT.md) before running any stage. It defines the data
contract, model-call boundaries, resume rules, quality gates, and Git freeze
procedure.

For a detailed comparison of the downloaded external over-refusal datasets,
including construction methods, schemas, licenses, strengths, limitations, and
local audit findings, read [docs/OVERREFUSAL_DATASETS.md](docs/OVERREFUSAL_DATASETS.md).
The canonical external-dataset schema and generated views are documented in
[docs/UNIFIED_EXTERNAL_DATASETS.md](docs/UNIFIED_EXTERNAL_DATASETS.md).
The staged GCR translation/judge execution and adaptive-concurrency design are
documented in [docs/GCR_MULTILINGUAL_TRANSLATION_PLAN.md](docs/GCR_MULTILINGUAL_TRANSLATION_PLAN.md).
The verified execution record, approved exclusions, strict-common counts, and
final dataset paths are in
[docs/MULTILINGUAL_TRANSLATION_EXPERIMENT.md](docs/MULTILINGUAL_TRANSLATION_EXPERIMENT.md).

The default configuration is
`configs/orbench_multilingual_v2.yaml`. Preparation and model-call stages are
separate commands; no model call is made by importing the scripts or running
the unit tests.
