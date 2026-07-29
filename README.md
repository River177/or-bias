# OR-Bench multilingual over-refusal

This repository contains the reproducible multilingual OR-Bench harmless-side
pipeline. The canonical experiment is the nine-language v2 panel:

`en, zh, ja, ko, sv, da, ta, mn, sw`

Read [INSTRUCT.md](INSTRUCT.md) before running any stage. It defines the data
contract, model-call boundaries, resume rules, quality gates, and Git freeze
procedure.

The default configuration is
`configs/orbench_multilingual_v2.yaml`. Preparation and model-call stages are
separate commands; no model call is made by importing the scripts or running
the unit tests.
