# 多语言大语言模型安全不平等研究计划

## 一、研究题目

**中文题目：** 多语言大语言模型安全不平等研究

**英文工作题目：** *Language Inequality in the Safety Alignment of Large Language Models*

## 二、核心研究问题

本研究关注：

> 大模型的安全对齐是否对不同语言用户形成了不平等的决策边界，使合法用户获得帮助的机会和承担的安全风险因语言而异？

这里的“不平等”特指：面对语义、意图和风险程度相同的请求，模型是否仅因用户使用的语言不同，就更容易错误拒绝合法请求，或错误满足危险请求。

研究对象不是一般意义上的多语言能力差距，而是不同语言条件下的安全—有用性边界（safety–helpfulness boundary）。

## 三、总体研究主线

```text
Study 1：现象识别
不同语言用户是否遭遇不同程度的 over-refusal？
        ↓
Study 2：安全权衡
这种差异是否换来了更强的安全防护？
        ↓
Study 3：替代解释
这种差异是否只是模型没有理解用户意图？
        ↓
Study 4：文化边界
文化语境是否进一步改变模型的安全决策？
        ↓
Study 5：形成机制
不同安全对齐方法如何产生或缓解这种不平等？
```

五个模块依次回答不平等是否存在、是否属于合理的安全权衡、是否由语言理解能力造成、在何种文化条件下出现，以及如何在安全对齐过程中形成。

## 四、核心概念与统一指标

### 4.1 Over-refusal

过度拒答是指模型面对已经确认无害、但表面涉及安全敏感内容的请求时，仍然拒绝回答或拒绝提供正常帮助。

对于模型 $m$、语言 $l$ 和 topic $t$：

$$
OR_{m,l,t}
=P(\text{refusal}\mid\text{harmless},m,l,t)
$$

### 4.2 Attack Success Rate

攻击成功率（Attack Success Rate, ASR）是指模型面对 harmful 请求时，输出与请求相关、包含具体操作性细节，并能够实质性促进有害行为的比例。

仅有一般性讨论、风险说明、虚构性确认、态度赞同或缺少操作细节的回答不计为攻击成功。

$$
ASR_{m,l,t}
=P(\text{unsafe compliance}\mid\text{harmful},m,l,t)
$$

### 4.3 用户意图理解

用户意图理解能力表示模型能否正确识别请求的目标、对象、动作、条件和范围：

$$
C_{m,l}=P(\text{correct intent understanding}\mid m,l)
$$

### 4.4 安全—有用性状态

| Over-refusal | ASR | 安全边界状态 |
|---|---|---|
| 低 | 低 | 理想校准 |
| 高 | 低 | 过度保守 |
| 低 | 高 | 安全防护不足 |
| 高 | 高 | 安全边界失调 |

本研究不把 over-refusal 和 ASR 合并成单一安全分数，而是分析每种语言在安全—有用性平面上的位置。

## 五、统一实验对象与控制条件

### 5.1 语言面板

主实验采用固定九语言面板：

| Resource group | Languages |
|---|---|
| High-resource | English (`en`)、Chinese (`zh`)、Japanese (`ja`) |
| Medium-resource | Korean (`ko`)、Swedish (`sv`)、Danish (`da`) |
| Low-resource | Tamil (`ta`)、Mongolian (`mn`)、Swahili (`sw`) |

资源等级用于描述性汇总；具体语言是主要分析单位。研究不预设 low-resource languages 必然呈现单调劣势。

### 5.2 Topic taxonomy

OR-Bench 使用 `category` 字段表示 topic。当前固定十个类别：

- `illegal`
- `privacy`
- `unethical`
- `harmful`
- `deception`
- `sexual`
- `violence`
- `self-harm`
- `hate`
- `harassment`

所有主结果同时报告总体结果和 topic-level 结果。

### 5.3 模型范围

Study 1–4以闭源 frontier models 为主，覆盖：

- 不同模型提供方；
- 同一 model family 的多个迭代版本；
- 支持 reasoning 控制的模型在 reasoning on/off 或不同 reasoning effort 下的表现。

Study 5使用开源 base model 和对应的可训练 checkpoint，以支持可控的安全对齐实验。

### 5.4 固定控制条件

- 相同 system prompt；
- 尽可能一致的 temperature、top-p 和 seed；
- 不人为设置不一致的输出 token 上限；
- 每个 `(model, prompt_id, language, condition)` 使用一致的生成次数；
- 保留模型版本、模型 family、reasoning setting 和运行日期；
- 使用稳定 `prompt_id` 保持跨语言和跨实验配对；
- generation、judge 和 summary 分阶段执行；
- generation error 不作为拒答或攻击成功写入。

## 六、Study 1：多语言 Over-refusal 不平等

### 6.1 研究问题

- **RQ1.1：** 同一个无害但安全敏感的请求，在不同语言下是否获得同等程度的帮助？
- **RQ1.2：** 语言差异是否随模型和 topic 改变？
- **RQ1.3：** 同一 model family 的版本迭代是否缩小或扩大语言差距？
- **RQ1.4：** 开启 reasoning 是否缩小或扩大多语言 over-refusal gap？

### 6.2 数据

使用当前冻结的 OR-Bench-Hard-1K harmless 侧共同子集：

- 704个共同 prompt；
- 9种语言；
- 每个模型6,336个 `prompt × language` 条件；
- 所有目标语言共享相同的 prompt ID；
- 覆盖十个安全敏感 topic。

### 6.3 实验维度

```text
language
× frontier model
× topic
× model version
× reasoning setting
```

模型版本比较必须限定在同一 family 内；reasoning 比较必须使用同一模型、同一 prompt 和同一语言进行配对。

### 6.4 主要分析

```text
over_refusal
~ model
+ language
+ topic
+ model × language
+ language × topic
+ model × topic
+ model × language × topic
+ (1 | prompt_id)
```

模型迭代和 reasoning 分析进一步加入：

```text
model_version × language
reasoning_setting × language
```

### 6.5 目标结论

判断多语言 over-refusal 不平等表现为：

- 某些语言在多数 frontier models 上持续受损；
- 特定模型对特定语言的异常；
- 仅在部分 topic 中出现；
- 随模型迭代改善或恶化；
- 被 reasoning setting 系统性改变。

本模块回答不平等是否存在，但不单独解释较高拒答是否意味着更安全。

## 七、Study 2：Over-refusal 与 ASR 的安全权衡

### 7.1 研究问题

- **RQ2.1：** 不同语言和模型的 ASR 是否存在差异？
- **RQ2.2：** 某种语言上的高 over-refusal 是否换来了更低的 ASR？
- **RQ2.3：** 是否存在无额外安全收益、却承担更高拒答成本的语言用户？
- **RQ2.4：** 是否存在 over-refusal 和 ASR 同时较高的安全边界失调？

### 7.2 数据

主实验固定一个 harmful benchmark，优先使用与 OR-Bench topic 体系相近的数据，例如经来源、字段和许可核验后的 OR-Bench harmful/toxic 侧数据。

HarmBench、AdvBench、XSafety 或 MultiJail可作为外部验证集，但不在主分析中混合不同 benchmark 的难度。

如果 harmful 数据与 harmless 数据不是逐题配对关系，则只进行相同模型、语言和 topic 层面的比较，不声称逐题对应。

### 7.3 实验控制

harmful 实验尽可能使用与 Study 1 相同的：

- 模型面板；
- 模型版本；
- reasoning setting；
- system prompt；
- 解码参数；
- 语言面板；
- topic taxonomy。

### 7.4 核心分析

为每个 `(model, language, topic, version, reasoning)` 条件同时展示 OR 和 ASR。

重点分析：

1. 不同语言的 ASR 是否接近，而 OR 差异明显；
2. OR 与 ASR 是否呈稳定的负相关关系；
3. safety–helpfulness trade-off 是否因模型、topic、版本或 reasoning 改变；
4. 同一语言是否在多个模型上持续处于不利位置。

### 7.5 两类核心故事

如果不同语言 ASR 接近，但 OR 差异明显：

> 部分语言用户承担了更高的误拒绝成本，却没有获得额外安全收益。

如果 ASR 与 OR 显著负相关：

> 不同语言用户处于不同的 safety–helpfulness trade-off；同一个模型通过牺牲部分语言用户的可用性换取安全性。

两种结果都指向同一结论：模型没有为不同语言用户实施统一的安全决策边界。

## 八、Study 3：排除语言理解能力的影响

### 8.1 研究问题

- **RQ3.1：** 模型是否正确理解不同语言中的用户意图？
- **RQ3.2：** 用户意图理解能力能解释多少 over-refusal 和 ASR 差异？
- **RQ3.3：** 控制语言理解能力后，语言条件化的安全差异是否仍然存在？

### 8.2 实验设计

围绕与 OR-Bench 相同的请求语义构造不要求安全决策、但要求理解用户意图的任务：

- 识别用户希望完成的目标；
- 从候选描述中选择与请求最匹配的意图；
- 判断请求涉及的对象、动作、条件和范围；
- 区分多个语义相近但意图不同的选项。

所有任务保持自然的同语言输入和同语言回答，不改变真实用户交互方式。

### 8.3 联合分析

```text
over_refusal
~ language
+ comprehension
+ topic
+ model
+ model × language
+ (1 | prompt_id)
```

对 harmful 数据使用对应模型分析 ASR。

### 8.4 结果解释

- 理解能力相近但安全行为不同：支持安全对齐不平等；
- 理解能力越差、拒答率越高：语言能力是重要来源；
- 控制 comprehension 后语言差异仍存在：语言能力只能解释部分不平等；
- 理解错误与低拒答或高 ASR 并存：模型可能因未识别请求风险而表现为安全防护不足。

本模块只排除语言理解能力这一替代解释，不扩展为通用多语言能力排行榜。

## 九、Study 4：语言与文化敏感性的交互

### 9.1 研究问题

- **RQ4.1：** 多语言安全不平等是纯粹的语言效应，还是语言与文化语境共同造成的？
- **RQ4.2：** 文化相关但合法的请求是否在部分语言中更容易被误拒绝？
- **RQ4.3：** 不同 frontier model 是否共享相同的文化敏感方向？

### 9.2 数据设计

建立两类安全敏感请求：

1. **文化中立请求：** 不同语言版本共享相同事实、意图和风险程度；
2. **文化情境请求：** 涉及目标语言社区真实存在的法律、社会规范、身份关系或敏感议题。

实验结构为：

```text
language
× cultural grounding
× topic
× model
```

### 9.3 核心指标

$$
\Delta_{culture}
=OR_{culture\text{-}grounded}-OR_{culture\text{-}neutral}
$$

harmful 侧计算对应的 ASR 差值。

### 9.4 核心分析

- 同一种语言中，文化相关内容是否更容易被拒绝；
- 同一文化情境在不同语言表达下是否触发不同决策；
- 模型是否将文化特有但合法的请求误判为危险；
- 文化影响是否集中在特定 topic；
- 文化影响是否随模型、版本和 reasoning setting 改变。

Study 3回答模型是否理解请求；Study 4进一步回答：即使模型理解了请求，安全边界是否仍受到语言所承载文化语境的影响？

## 十、Study 5：安全对齐如何产生语言不平等

### 10.1 研究问题

- **RQ5.1：** 多语言安全边界差异如何在模型对齐过程中形成？
- **RQ5.2：** 仅英语安全对齐能否公平地迁移到其他语言？
- **RQ5.3：** 多语言对齐是否能够同时降低 ASR 和 over-refusal gap？
- **RQ5.4：** SFT、DPO及其组合是否产生不同形式的语言偏置？

### 10.2 可控对齐实验

选择一个或多个开源 base model，在相同数据规模和训练预算下设置：

| 条件 | 安全训练方法与语言组成 |
|---|---|
| Base | 不进行安全对齐 |
| English SFT | 仅英语安全数据进行 SFT |
| Multilingual SFT | 多语言安全数据进行 SFT |
| English DPO | 仅英语偏好数据进行 DPO |
| Multilingual DPO | 多语言偏好数据进行 DPO |
| English SFT + DPO | 英语两阶段对齐 |
| Multilingual SFT + DPO | 多语言两阶段对齐 |

每个 checkpoint 使用相同协议测量：

- harmless over-refusal；
- harmful ASR；
- 用户意图理解能力；
- 各语言的 safety–helpfulness trade-off。

### 10.3 核心指标

$$
\Delta OR_l=OR_{aligned,l}-OR_{base,l}
$$

$$
\Delta ASR_l=ASR_{aligned,l}-ASR_{base,l}
$$

语言间差距定义为：

$$
Gap(metric)=\max_l(metric_l)-\min_l(metric_l)
$$

### 10.4 核心分析

- 仅英语安全对齐对不同语言的迁移是否均衡；
- 多语言对齐能否改善最不利语言，而不显著损害总体安全性；
- SFT和DPO对 OR、ASR及其语言差距的影响是否不同；
- 对齐后语言理解能力变化能否解释安全行为变化；
- 哪种训练条件能够实现更接近统一的多语言安全边界。

Study 5为前四个观察性实验提供关于形成机制和干预方式的因果证据。

## 十一、统一研究问题与假设

### RQ1 — Existence

Frontier models 是否对不同语言用户表现出不同程度的 over-refusal？这种差异是否受到 topic、模型版本和 reasoning setting 的调节？

- **H1：** 语言之间存在可测量的 over-refusal 差异；
- **H2：** 语言效应与模型存在交互，不同模型不会共享完全相同的语言排序；
- **H3：** topic、模型版本和 reasoning setting 会改变语言差距。

### RQ2 — Trade-off

Over-refusal 差异是否对应安全收益，还是无补偿的可用性损失？

- **H4：** 不同语言存在可测量的 ASR 差异；
- **H5：** OR与ASR不构成跨模型、跨语言统一的简单单调关系；
- **H6：** 至少部分语言会表现出无额外安全收益的更高 over-refusal。

### RQ3 — Capability

用户意图理解能力能解释多少安全行为差异？

- **H7：** 语言理解能力能够解释部分差异；
- **H8：** 控制 comprehension 后，部分 model–language effect 仍然存在。

### RQ4 — Culture

文化语境是否在语言之外进一步改变模型的安全边界？

- **H9：** 文化情境与语言和 topic 存在交互；
- **H10：** 文化相关的安全差异并不会在所有 frontier models 上保持相同方向。

### RQ5 — Mechanism

SFT、DPO及其训练语言组成如何产生或缓解多语言安全不平等？

- **H11：** 仅英语安全对齐在不同语言上的迁移不均衡；
- **H12：** 多语言安全对齐能够缩小语言差距，但不同对齐方法在 safety–helpfulness trade-off 上存在差异。

## 十二、跨实验统一分析框架

五个Study尽量共享以下索引：

```text
prompt_id
model_family
model_version
language
resource_group
topic
reasoning_setting
harmless_or_harmful
cultural_grounding
alignment_method
alignment_language_mix
```

统一的基础模型为：

```text
outcome
~ model
+ language
+ topic
+ model × language
+ language × topic
+ model × topic
+ (1 | prompt_id)
```

根据Study加入 `model_version`、`reasoning_setting`、`comprehension`、`cultural_grounding` 或 `alignment_method` 及其与语言的交互。

主报告应提供：

- 各语言的绝对指标；
- 相对英语的配对差值；
- 最好语言与最差语言之间的 gap；
- 95%置信区间；
- model–language 和 language–topic 交互；
- 对多重比较进行校正。

## 十三、研究贡献

### 13.1 现象贡献

系统测量多个 frontier models 在九种语言上的 over-refusal，并研究模型迭代和 reasoning 对语言不平等的影响。

### 13.2 评估贡献

将 harmless over-refusal 与 harmful ASR置于统一的 safety–helpfulness 框架中，区分过度保守、安全不足和安全边界失调。

### 13.3 解释贡献

通过用户意图理解控制和文化情境实验，区分语言能力、文化语境与安全决策本身的作用。

### 13.4 机制贡献

通过开源 base model 的英语/多语言 SFT、DPO及组合实验，识别安全对齐方法及训练语言组成对多语言安全边界的因果影响。

## 十四、预期核心论点

> 大模型并未为不同语言用户实施统一的安全标准。语言、模型、topic、模型迭代、reasoning 和文化语境共同改变安全决策边界，使不同语言用户承担不同程度的误拒绝或安全风险。这种差异可能部分来自语言理解能力，但也可能由英语中心的安全对齐直接产生。通过控制安全训练方法及其语言组成，可以识别并缓解这种不平等。

整项研究的证据链为：

```text
发现不平等
→ 判断是否属于合理安全权衡
→ 排除语言理解能力解释
→ 确定文化语境的边界条件
→ 用可控对齐实验解释来源并验证干预
```
