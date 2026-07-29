# OR-Bench 项目运行与维护说明

## 1. 项目目标

本项目评估同一个 OR-Bench harmless prompt 在不同语言输入下的
over-refusal。canonical v2 测试面板固定为：

| resource level | language code | language |
|---|---|---|
| high-resource | `en` | English |
| high-resource | `zh` | Chinese |
| high-resource | `ja` | Japanese |
| medium-resource | `ko` | Korean |
| medium-resource | `sv` | Swedish |
| medium-resource | `da` | Danish |
| low-resource | `ta` | Tamil |
| low-resource | `mn` | Mongolian |
| low-resource | `sw` | Swahili |

资源等级沿用 Wang et al. 的 PNAS 论文分类。它是数据资源分类，不等同于
使用人口分类。

## 2. 目录约定

```text
configs/       canonical 配置
data/source/   固定的英文 OR-Bench 源 CSV
data/frozen/   通过质量门槛后可进入主分析的冻结数据
scripts/       唯一的当前运行入口
tests/         本地单元和配置验证
experiments/v2/运行时 manifest、judge、summary 和外部 artifact 记录
archive/       v0/v1 历史说明和摘要，不作为运行入口
```

raw generations、response judgments、checkpoint 和 repair/archive JSONL 不
进入 Git。它们如需保留，应放在外部 artifact 目录，并写入路径、大小、行数
和 SHA256 清单。

## 3. 环境要求

- macOS/Linux；Python 3.9 或更高版本。
- 安装依赖：`python3 -m pip install -r requirements.txt`。
- TRAPI/Azure 身份认证必须由本机环境提供；不要把 token、凭证或 `.env` 提交到仓库。
- 所有 deployment 必须先通过 TRAPI 查询确认，配置中的逻辑名称不能被静默替换。

运行前建议检查：

```bash
python3 scripts/orbench_pipeline.py preflight \
  --config configs/orbench_multilingual_v2.yaml
```

`preflight` 只检查服务状态、实例和可见模型，不会生成数据。

## 4. canonical 数据流程

完整顺序固定为：

```text
prepare
→ translate
→ judge-translations
→ select-subset
→ export-final
→ model generation
→ response judge
→ summarize
```

### 4.1 本地数据准备

```bash
python3 scripts/orbench_pipeline.py prepare \
  --config configs/orbench_multilingual_v2.yaml
```

`prepare` 下载或读取固定源 CSV，验证 1,319 行、10 个 category，生成稳定
`prompt_id` 和 manifest。若源文件已经存在，不重复下载。

### 4.2 翻译和翻译质量审核

以下两个阶段会产生模型调用，必须分开运行：

```bash
python3 scripts/orbench_pipeline.py translate \
  --config configs/orbench_multilingual_v2.yaml

python3 scripts/orbench_pipeline.py judge-translations \
  --config configs/orbench_multilingual_v2.yaml
```

翻译必须保留用户意图、实体、范围、情态、category 和 harmless intent。
失败任务不写占位翻译。翻译 judge 的严格保留条件是：

- `semantic_equivalence == equivalent`；
- task intent、referents、scope、benign intent、category 全部为 true；
- `harmfulness_introduced == false`；
- `naturalness >= 4`；
- 无 `translation_judge_error`。

### 4.3 共同子集和最终数据

```bash
python3 scripts/orbench_pipeline.py select-subset \
  --config configs/orbench_multilingual_v2.yaml

python3 scripts/orbench_pipeline.py export-final \
  --config configs/orbench_multilingual_v2.yaml
```

最终数据只保留所有目标语言均通过严格质量门槛的 prompt。导出前必须验证：

- 语言集合恰好是 v2 的 9 种语言；
- `es` 和 `ar` 不出现；
- `(prompt_id, language)` 唯一；
- 每条非英文记录都能追溯到英文 source prompt 和翻译 judge。

### 4.4 三模型评估

多模型入口读取 `data/frozen/final_test_dataset.jsonl`，模型固定为：

- Qwen3.5-27B；
- GPT-4o；
- Gemma-3-27B-it。

先生成 smoke manifest：

```bash
python3 scripts/orbench_multimodel.py prepare \
  --config configs/orbench_multilingual_v2.yaml
```

模型调用阶段：

```bash
python3 scripts/orbench_multimodel.py generate-smoke \
  --config configs/orbench_multilingual_v2.yaml
python3 scripts/orbench_multimodel.py judge-smoke \
  --config configs/orbench_multilingual_v2.yaml

python3 scripts/orbench_multimodel.py generate-full \
  --config configs/orbench_multilingual_v2.yaml
python3 scripts/orbench_multimodel.py judge-full \
  --config configs/orbench_multilingual_v2.yaml
python3 scripts/orbench_multimodel.py summarize-full \
  --config configs/orbench_multilingual_v2.yaml
```

也可以使用 `--models qwen3.5-27b,gpt-4o` 选择模型子集。成功结果按
`model_key`、`generation_id` 断点续跑，不覆盖已有成功记录。generation error
不会进入有效生成分母；response judge error 不进入有效 judge 分母。

## 5. 指标和分母

summary 至少报告：

- refusal rate；
- strict substantive-task failure rate；
- disclaimer rate；
- partial refusal rate；
- substantive completion rate；
- category breakdown；
- Wilson confidence interval；
- generation error 和 judge error 数量。

任何错误行都必须保留错误原因，但不能混入对应指标的有效分母。不得把
refusal、disclaimer、belief 或其他不同语义的指标合并为一个 ASR 数值。

## 6. 断点续跑和失败处理

- JSONL 结果采用 append-only 写入，并按稳定 key 去重/跳过已完成任务。
- 网络错误和服务错误按配置重试；重试次数耗尽后记录 error，不伪造结果。
- 不要在未刷新当前状态时重复提交同一个大批量任务。
- 翻译、翻译 judge、目标模型 generation、response judge 必须分别检查完成度。
- 任何数据修复都必须生成新的 artifact，不直接覆盖历史原始文件。

## 7. Git 和冻结规则

Git 只跟踪代码、配置、文档、源 CSV、冻结小型数据包和摘要。禁止提交：

- token、credential、`.env`；
- raw model generations；
- response judgments；
- checkpoint、repair、archive JSONL；
- 临时日志和本地缓存。

建议使用以下 tag：

```text
pipeline-v1   代码、配置、测试和文档冻结
dataset-v2    9 语言翻译、judge 和 final dataset 冻结
results-v2    三模型结果和 summary 冻结
```

每次数据冻结都要生成 artifact manifest，记录外部大文件的相对路径、字节数、
行数和 SHA256。

## 8. 新增语言、模型或数据源

必须同时更新：

1. canonical YAML 配置及 resource group；
2. 翻译语言名称映射；
3. final dataset schema/语言集合测试；
4. smoke manifest 测试；
5. 本文档和实验说明。

不得只改配置后直接跑全量。先运行配置解析、source preparation 和小规模
smoke，确认质量门槛与输出 schema 后才能扩展。

## 9. 常用验收命令

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/orbench_pipeline.py scripts/orbench_multimodel.py
python3 scripts/orbench_pipeline.py --help
python3 scripts/orbench_multimodel.py --help
```

整理后的仓库必须能够在干净工作区通过以上检查，并且不依赖绝对路径。
