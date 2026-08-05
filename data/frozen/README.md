# Frozen datasets

`data/frozen/` 只保存已经通过质量门槛、可直接作为实验输入的数据；raw
translation、完整 translation judgment、attempt error 和运行日志保存在
`artifacts/`，不复制到这里。

目录分工：

- 根目录的 `manifest.jsonl`、`final_common_prompts.jsonl` 和
  `final_test_dataset.jsonl`：canonical OR-Bench v2 九语言数据；
- `external-overrefusal-v1/`：外部 over-refusal 数据的严格八目标语言共同集；
- `external-overrefusal-v1/datasets/<dataset>.jsonl`：一个数据集一个文件，
  每行包含 English source、八种翻译、provenance 和精简质量 judgment。

外部数据当前冻结 Bio Over-Refusal、XSTest、OKTest、PHTest 和 FalseReject。
OverBench 与 Health-ORSC 尚未完成，因此不进入当前 frozen manifest。

重新生成：

```bash
python3 scripts/finalize_multilingual_translation.py \
  --dataset bio_overrefusal \
  --dataset xstest \
  --dataset oktest \
  --dataset phtest \
  --dataset falsereject \
  --output-root data/frozen/external-overrefusal-v1 \
  --validation-path data/frozen/external-overrefusal-v1/validation.json
```
