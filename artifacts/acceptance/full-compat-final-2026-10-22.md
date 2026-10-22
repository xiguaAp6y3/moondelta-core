# 完全兼容标准验收 —— 终态报告（M5，2026-10-22）

> 验收人：Team Lead　｜　验收时点：2026-10-22　｜　基线：[acceptance-2026-10-22-full-compat.md](full-compat-2026-10-22.md)（15 FAIL / 9 PASS）
> 标准：[docs/ROADMAP.md](../docs/ROADMAP.md) §1/§2 —— Delta Transaction Log Protocol `minReaderVersion=1, minWriterVersion=2` 完整实现（含嵌套 schema 读写与 checkpoint 恢复），双外部引擎同协议双向对拍。
> 本版结论对应 commit `9b6bff4` 之后的 0.3.0 工作区；全量复验原始输出见 [m5-sweep.txt](m5-sweep.txt)。

## 1. 结论

**完全兼容标准：通过（0 FAIL / 显式例外均已申报）**。基线报告的 15 项 FAIL 全部关闭；七项门槛（范围文档 §3）7/7；案例 A（flat）与案例 B（nested）双向通过。

| 指标 | 值 |
|---|---|
| `moon check --target native` | 0 errors（约 50 条带定位 warning：绝大多数为 derive 触发的 `implicit_impl_as_method` 风格类 + 测试内 unused `err`，已登记为风格债，不阻塞发布） |
| `moon test --target native` | **37/37**（M1 前为 14；新增 23 个负向/回环/互证用例） |
| 五个校验脚本 | d2 17/17 · d3 40/40 · d4_structure 27/27 · d4_v3 65/65 · links 15/15（共 **164 项**） |
| fixture 全量 sweep | 负向 9 项全部 exit=1 + 结构化原因；正向 10 项全部 exit=0（[m5-sweep.txt](m5-sweep.txt)） |
| 外部引擎 | delta-rs 1.6.6 + DuckDB（delta 扩展）；flat 双向（D2）+ nested 读取（M2 oracle 逐值）+ nested 写出回读（M4，10/10） |

## 2. 基线 15 项 FAIL 的关闭对照

| 基线项 | 关闭于 | 证据 |
|---|---|---|
| A1 reader>1 / features 拒绝 | M1 | protocol-reader-v2/v3 exit=1，错误含版本与原因 |
| A2 writer 门控特性（删除向量） | M1 | deletion-vector-add：`add.deletionVector` 拒绝，exit=1 |
| B2 checkpoint 恢复 | M3 | checkpoint-truncated：v10/100 文件/100 行（修复前静默 v9/90）；checkpoint-only（flat/nested）可读 |
| B3 日志名 20 位校验 | M1 | nonstandard-log-name：`invalid _delta_log file name '0.json'`，exit=1 |
| C1/C2 未知 action | M1 | unknown-actions：`unknown action 'sidecar'`；txn 识别；cdc/domainMetadata 显式忽略并提示 |
| D1 byte/short | M1 | fixtures/delta/byte-short（delta-rs 写出）回读一致 |
| D2 嵌套读取 | M2 | 5 个嵌套 fixture 与 pyarrow/delta-rs oracle 逐值一致（含 NULL/空集合/null 元素） |
| D3 嵌套写入 | M4 | moondelta-nested-written 被 delta-rs + DuckDB 回读 10/10 逐字段一致 |
| E1 def/rep level | M2 | shredded 读取 + 写入；修复 vendored def-level 递增规则（REPEATED 也递增） |
| E2 add.path 解码 | M1 | percent-decoding（测试覆盖）；parquet I/O 错误带文件名 |
| F2 stats 静默返 0 | M1 | stats 损坏 → 结构化 ParseError |
| G1 写路径防护 | M1/M4 | create 防重；append 列名/类型/NOT NULL 校验；put-if-absent；writer>2 拒绝 |
| H2 案例 B 双向 | M2+M4 | 外部写→本项目读（M2）；本项目写→外部读（M4 10/10） |
| H3 CLI 退出码/stderr/参数校验 | M1 | 全部错误路径 exit=1、stderr 输出、未知参数报错 |

七项门槛（范围文档 §3）：① def/rep levels ✅（M2，含规范修复）② schema 双向映射 ✅（M2 读 + M4 写，nullable/顺序/嵌套路径保留）③ nested stats 不误用 ✅（嵌套列不写 min/max，放弃剪枝保证正确读取）④ checkpoint 嵌套解析 ✅（M3，checkpoint 含嵌套 add/metaData 的表可恢复）⑤ 双外部引擎 flat+nested ✅（D2 + M2 + M4）⑥ 每类 nested fixture ✅（D3 fixtures 正向化）⑦ 嵌套写读双向 ✅（M4）。

## 3. 显式例外清单（申报材料同步）

- **类型**：decimal、uuid（结构化拒绝，错误含类型名与字段路径）
- **压缩**：ZSTD / GZIP / BROTLI / LZ4（结构化拒绝）
- **协议**：reader ≥ 2 的全部特性（column mapping、deletion vectors、v2 checkpoint）——按协议拒绝并说明原因；多部分 checkpoint（`<v>.checkpoint.<p>.parquet`）显式拒绝
- **写入**：分区表 append、MERGE/UPDATE/DELETE、并发写仲裁、对象存储
- **已知风格债**：约 50 条 `implicit_impl_as_method` 等 warning（非 error，清理不阻塞发布）
- **流程项**：mooncakes.io 0.3.0 发布与 GitHub Actions 运行链接需在可联网发布时补齐（moon.mod/CLI/文档版本已统一为 0.3.0）

## 4. 复验命令

见 [m5-sweep.txt](m5-sweep.txt) 头部与 [acceptance-2026-10-21.md](acceptance-2026-10-21.md) §5（校验脚本部分）；M4 外部回读脚本逻辑见 [m4/README.md](m4/README.md)。

## 5. 完整安全审计（M5 收尾）

Mimosa 深度扫描（deep, static_only）：**findingCount = 0**，已封印。
scanId `scan-2026-10-22T05-27-29.613Z-d02540482fa1`；
seal `sha256:03864376dfed80ee30bc570f66ea0d12486d32ed531dde9bf9b75b900ee39a06`；
产物目录 `~/.mimosa/security-scans/project-9e68e694bbc04eae77235a5a/scan-2026-10-22T05-27-29.613Z-d02540482fa1`。
（M0 阶段互操作脚本的 SQL 拼接/路径问题已改为参数绑定与路径包含校验，本次深度扫描未再发现。）
