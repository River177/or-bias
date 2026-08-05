# Frozen datasets

此目录只包含可直接用于分析的冻结数据与发布元数据。

- `orbench-v2/`：Git 跟踪的 canonical OR-Bench v2 数据（1,319 source、704 common prompts、6,336 language conditions）。
- `external-overrefusal-v1/`：Git 只跟踪 README、manifest、validation 和 release metadata；五个大型 JSONL 由 GitHub Release 安装，目录受 `.gitignore` 保护。

安装并验证外部 frozen 数据：

```bash
orbias data fetch external-overrefusal-v1
orbias data verify external-overrefusal-v1
```

raw translation、完整 judgment、错误和日志只能位于 `ORBIAS_ARTIFACT_ROOT`，不得复制进 Git。
