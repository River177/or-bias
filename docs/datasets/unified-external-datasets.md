# Over-Refusal 数据统一格式

## 1. 处理范围

统一处理包含 OR-Bench 和七套已选外部数据：XSTest safe、PHTest harmless、FalseReject all、OverBench Hard、OKTest all、Health-ORSC benign full 和 Bio Over-Refusal all。EVOREFUSE 不在输入中，也不会进入任何输出视图。

第三方下载文件和 `data/external/selected/` 中的来源保留文件均不被修改。统一数据是独立派生物，位于 `data/external/unified/`。

## 2. Canonical schema

每行是一个 JSON object，字段固定如下：

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | string | 当前固定为 `external-overrefusal-v1` |
| `record_id` | string | 沿用稳定的 `selection_id`，包含来源数据集、split 和 source ID |
| `task` | string | 固定为 `over_refusal` |
| `dataset` | string | 统一后的数据集键 |
| `dataset_variant` | string | `safe`、`harmless`、`hard`、`benign_full` 或 `all` |
| `benchmark_group` | string | `general_fixed`、`adaptive_stress`、`domain_health` 或 `domain_biology` |
| `usage` | string | `training` 或 `evaluation` |
| `language` | string | 当前原始数据统一为 `en` |
| `prompt` | string | 模型输入文本 |
| `category` | string/null | 来源数据提供的类别；没有类别时为 null |
| `safety_label` | string | `benign` 或 `ambiguous` |
| `strict_benign` | boolean | 是否进入严格 benign 分母 |
| `include_in_canonical` | boolean | 是否保留在去重 canonical 视图 |
| `duplicate_of` | string/null | 被排除重复行所对应的首次记录 ID |
| `source_dataset_key` | string | 统一前的 selected dataset 键 |
| `source_split` | string | 原始 split |
| `source_id` | string | 原始 ID 或稳定行号 ID |
| `source_file` | string | 第三方来源文件相对路径 |
| `source_label` | string/null | 来源标签 |
| `prompt_sha256` | string | 未修改 prompt 的 SHA256 |
| `metadata` | object | 数据集特有字段，不平铺到公共 schema |

不同数据集自己的 category 和 metadata 语义仍然不同。统一字段不意味着可以把七套数据合并成同一个 refusal-rate 分母。

## 3. 输出视图

| 文件 | 行数 | 用途 |
|---|---:|---|
| `all_rows.jsonl` | 80,609 | 完整来源保留；重复行仍存在但有排除标记 |
| `canonical_unique.jsonl` | 80,563 | 删除 46 个已审核内部重复后的主数据 |
| `strict_benign.jsonl` | 80,543 | canonical 数据中排除 Bio Tier 5 ambiguous |
| `evaluation_strict_benign.jsonl` | 65,919 | 再排除 FalseReject train，只保留严格评测数据 |

此外，`datasets/` 下为八套数据各自的 canonical 文件：

| Dataset | Canonical rows | Strict benign rows |
|---|---:|---:|
| `orbench` | 1,319 | 1,319 |
| `xstest` | 250 | 250 |
| `phtest` | 2,072 | 2,072 |
| `falsereject` | 15,811 | 15,811 |
| `overbench` | 29,969 | 29,969 |
| `oktest` | 340 | 340 |
| `health_orsc` | 31,920 | 31,920 |
| `bio_overrefusal` | 201 | 181 |

FalseReject 中 14,624 条 train 的 `usage` 为 `training`，1,187 条 test 的 `usage` 为 `evaluation`。其他六套当前全部标记为 evaluation。OKTest 的 10 条 held-out/main 泄漏记录已经从 canonical 视图去重。

OR-Bench 单独输出为 `datasets/orbench.jsonl`。它来自当前冻结的 1,319 行完整快照，保留 10 个原始 category 和稳定 `prompt_id`。虽然来源文件名仍为 `or-bench-hard-1k.csv`，处理时不会截断为 1,000 行。OR-Bench manifest 位于 `datasets/orbench_manifest.json`。

## 4. 推荐使用方式

- 需要保留完整 provenance 或检查排除原因：读取 `all_rows.jsonl`。
- 后续翻译数据准备：读取 `canonical_unique.jsonl`，并根据研究设计决定是否包含 ambiguous。
- benign over-refusal 主评测：读取 `evaluation_strict_benign.jsonl`。
- 训练 over-refusal 缓解模型：只选择 `usage=training`，不得把同一来源的 test 用于训练。
- 汇总 refusal rate：始终按 `dataset` 分开计算；Health、Bio 和 adaptive OverBench 不能与固定通用测试直接合并为一个百分比。

## 5. 重新生成

先重建选取和重复审计，再统一格式：

```bash
python3 scripts/prepare_external_overrefusal.py
python3 scripts/audit_external_duplicates.py
python3 scripts/unify_external_overrefusal.py
python3 scripts/unify_orbench.py
```

统一脚本不会产生任何模型调用。`manifest.json` 记录每个输出的行数、字节数和 SHA256，可用于冻结和传输校验。

验收：

```bash
python3 -m unittest tests.test_unify_external_overrefusal -v
python3 -m unittest tests.test_unify_orbench -v
jq '.counts, .canonical_by_dataset' data/external/unified/manifest.json
jq '.rows, .categories' data/external/unified/datasets/orbench_manifest.json
```
