# OR-Bias

OR-Bias 是一个可复现的多语言 over-refusal 数据与评测仓库。canonical OR-Bench v2 面板固定为：

`en, zh, ja, ko, sv, da, ta, mn, sw`

安装并查看统一入口：

```bash
python3 -m pip install -e .
orbias --help
```

完整生命周期、模型调用边界、断点恢复、artifact 与 Release 规则见 [INSTRUCT.md](INSTRUCT.md)。

进一步资料：

- [数据集说明](docs/datasets/overrefusal-datasets.md)
- [统一数据 schema](docs/datasets/unified-external-datasets.md)
- [多语言翻译实验记录](docs/experiments/multilingual-translation-experiment.md)
- [GCR 运行与恢复](docs/operations/gcr-multilingual-translation.md)

导入模块、运行测试和 `--help` 都不会产生模型调用。
