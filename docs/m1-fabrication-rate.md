# 编造率的可复算定义（M1 采集）

> 立于 2026-08-27，`m1-intake-quality-fixes` 第 7 章（`intake-field-grounding`）。
> 本文件是**编造率这个数字的唯一定义**。任何地方引用"编造率"都必须指回这里，
> 否则两个人报的数字不可比，而这个数字要被拿去做换不换模型的决定。

## 定义

**编造率（下界）= 未溯源字段数 ÷ 写入字段总数**，逐轮统计、按**响应返回的**模型标识分组。

- **分子** `job_profile.ungrounded_fields`：该轮写进画像、但引用片段过不了确定性子串
  判定的业务字段名。判定规则见 `app/agents/field_grounding.py::verify_field_grounding`
  ——引用（NFKC + 去空白归一化后）必须在它自己声明的那一轮用户原话里原样找到。
- **分母** `job_profile.written_fields`：该轮写进画像的业务字段名，**含**未溯源的那些，
  **不含**系统管理字段（`unspecified_fields` / `derived_unspecified_fields`）。
  *为什么单独存一列而不是从 `profile_json` 数*：`profile_json` 是**累积**画像，
  同一字段被修正重写时键数不变，反推出来的分母恒偏小、编造率恒偏大。
- **分组键** `job_profile.llm_response_model`：API 响应里实际返回的模型标识
  （工程铁律 5），**不是**配置里写的别名（`LLMGateway` 的配置别名）。别名经
  `AuditHook` 单独记录（`app/llm/gateway.py`），两者分开记录、不互相覆盖。

三列全部由 `app/graph/nodes.py` 的 `effect_persist_draft` 在同一条
`INSERT INTO job_profile` 里写入，不新增写入语句、不新增 effect 节点
（tasks 1.5 / 7.5 / 7.9 的口径一致，理由见变更包 Global Constraints）。

## 口径 SQL

```sql
SELECT COALESCE(llm_response_model, '(未记录)')        AS model,
       COUNT(*)                                        AS turns,
       SUM(json_array_length(written_fields))          AS written_fields,
       SUM(json_array_length(ungrounded_fields))       AS ungrounded_fields,
       ROUND(1.0 * SUM(json_array_length(ungrounded_fields))
             / NULLIF(SUM(json_array_length(written_fields)), 0), 4) AS fabrication_rate_lower_bound
FROM job_profile
GROUP BY COALESCE(llm_response_model, '(未记录)')
ORDER BY model;
```

在 `.51` 上跑（口令与路径见 `05-发布运行手册.md`）：

```bash
sqlite3 data/demo.db < docs/sql/fabrication-rate.sql
```

`NULLIF` 不是装饰：2026-08-19 到本批上线之前写下的历史行 `written_fields` /
`ungrounded_fields` 两列都是默认值 `[]`、`llm_response_model` 为 `NULL`，
分母为 0，没有 `NULLIF` 会直接除零报错，整份统计拿不到任何结果。

### 老行（`.51` 上 2026-08-19 之前的 15 个历史 job）的处置口径

这些行 `written_fields = '[]'`、`ungrounded_fields = '[]'`、`llm_response_model = NULL`。

- **不参与任何模型的比例计算**：`NULLIF(...,0)` 把它们的 `rate` 算成 `NULL`（SQL 的
  `NULL` 不会被 `AVG`/加权平均之类的后续处理误当成 0 参与运算），从根上排除。
- **单独归入 `COALESCE(llm_response_model, '(未记录)')` 这一组，不整行剔除**：
  `GROUP BY` 上的 `COALESCE` 让它们落进 `(未记录)` 分组而不是被 `WHERE` 过滤掉，
  这组的 `turns` 数仍然可见（上例中 `('(未记录)', 1, 0, 0, None)`）。
  *为什么不直接 `WHERE llm_response_model IS NOT NULL` 整行剔除*：那样会让
  "这批数据里有多少轮压根没记录模型标识"这个事实从统计结果里消失，而这正是
  铁律 5"锁不住版本时至少记得住版本"这条规则在本批之前没有被兑现的证据。
  保留这一行、但让它的 `rate` 恒为 `NULL`，是唯一既不除零、又不掩盖历史缺口的写法。

## 这个数字是**下界**，不是精确值

`design.md` 决策 11 已经声明这一点，这里补齐已知的三条低估/高估来源。前两条让
真实编造率**大于等于**这里算出的数字（方向：偏低，即让编造被误判为"已溯源"），
第三条让统计结果**大于等于**真正落进画像的编造字段数（方向：偏高，即分母/分子
含有随后被摘除、并未真正留在 `profile_json` 里的字段）。三条都写明是因为读数字
的人需要知道自己在读什么，而不是把这个数字当精确值使用。

1. **【偏低】点选提交会把问题原文一起拼进用户消息**：单元 C 的 `collectSelections()`
   拼的是"问题原文：档位A、档位B"这种形式，因此问题文本自身逐字包含某个档位值时
   （如「ASIL 等级要求（ASIL-B / ASIL-D）？」），该值**即使未被勾选**也出现在
   用户原话里，模型引用它可以过校验——引用是真的、但与字段值是否真被用户选中无关。
   本单元**刻意不收窄搜索范围**：这类作弊只会让未溯源率**偏低**，方向与"下界"
   不变式一致，因此"编造率下降了"这个结论依然可信。收窄需要后端持有上一轮
   `pending_questions` 并做字符串剥离，是一条又脆又只影响下界紧度的路，本批不做。
2. **【偏低】归一化去空白导致英文词边界消失**：`verify_field_grounding` 的
   `normalize_for_grounding` 在 NFKC 之后去掉全部空白，英文词之间的空格因此消失，
   例如 `"C  A"` 能匹配 `"CA"`。同样只会让本该判"未溯源"的引用被判成"已溯源"，
   方向偏低，与下界不变式一致。要收紧需要区分中英文分别处理空白折叠规则，
   等真实数据显示这类误判确实发生了再说。
3. **【偏高】`ungrounded_fields` / `written_fields` 取自候选值摘除之前的 patch**：
   某字段若随后被单元 B"候选档位不得代替用户做决定"这条防线从 patch 里摘掉，
   仍会计入这两个列表——落库后 `ungrounded_fields` 里**可能出现
   `profile_json` 里查不到的字段名**。人工核对数字与画像内容时会显得不一致，
   这里预先说明：这不是数据损坏，是口径的刻意选择。
   **已裁定不改口径**：若改成"摘除之后再统计"，会让"模型编造了、但被摘掉"这个
   事实从统计里彻底消失，而这恰恰是本章要观测的东西——那才是真正的低估。

## 校验的是引用的真实性，不是值与引用的等价性

`design.md` 决策 11：用户说 "MISRA C"，字段值写成规范化后的枚举值，只要
`source_quote` 逐字命中用户原话中的 "MISRA C"，就判**已溯源**。所以"值被模型
归纳/规范化过"本身不计入编造——`verify_field_grounding` 只断言"引用片段确实
出现在用户说过的话里"，不断言"引用片段与字段值语义等价"。这条同样是本口径
的组成部分，不是遗漏。

## 怎么读这个数字

- **和什么比**：`docs/m1-model-comparison.md` 记录的 `deepseek-v4-pro` 实测 1/3
  编造率是人工核对得出的，与本口径**不可直接相减**（一个是人判、一个是引用判）。
  可比的是**同口径的前后两次**：换模型前后、改提示词前后。
- **样本量与后续动作**：`design.md` 决策 12 定死了触发条件——累计 ≥ 20 场真实
  采集会话拿到分布之后，才单独开变更定拦截阈值。**在此之前不要据此改任何拦截
  逻辑**（本章的定义性约束是"只观测不拦截"，见 `docs/tech-debt.md` TD-3）。
- **人工抽查仍然必要**：`design.md` 风险表要求回放真实会话时人工抽查若干
  未溯源字段，区分"模型不会引用"与"模型真编造"。第一次抽查结论由第 8 章 8.7
  填在下方。

## 首次真实测量（第 8 章 8.7 回填）

| 日期 | 模型标识 | 轮数 | 写入字段 | 未溯源 | 未溯源率 | 人工抽查结论 |
|---|---|---|---|---|---|---|
| 待填 | | | | | | |
