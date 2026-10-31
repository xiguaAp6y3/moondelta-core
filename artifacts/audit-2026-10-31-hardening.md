# 2026-10-31 独立审计与协议加固

> 基线：`9ac2669`（0.3.0）。测试：`moon check --target native` 0 errors；`moon test --target native` **44/44 passed**。
> 本文只记录本轮发现与修复；历史验收报告保留原结论，但不再作为当前“完全兼容”的证据。

## 1. 发现并修复

| 严重度 | 问题 | 修复 |
|---|---|---|
| 高 | checkpoint `add.deletionVector` 被固定为 `false`，绕过 JSON 路径的删除向量拒绝 | checkpoint 检测 DV 子树任一非空值并按统一 `apply_action` 拒绝 |
| 高 | checkpoint 顶层未知 action 被兜底 skip 吞掉 | 仅当当前记录真实出现未知 action（def > 0）时拒绝；预声明全空列不误报 |
| 高 | `remove.path` 未 percent-decode，删除编码路径文件失败 | add/remove 统一使用 `decode_action_path`，JSON 与 checkpoint 两条路径一致 |
| 中 | checkpoint 缺失 protocol/metaData 时回退默认值 | 按损坏 checkpoint 拒绝 |
| 中 | 非字符串 `add.stats` 被丢弃并返回 0 行 | 解析期要求 stats 为 JSON 编码字符串 |
| 中 | 非空 `writerFeatures` 仍可 append | writer 门控拒绝，不创建数据文件或 commit |
| 中 | `timestamp_ntz` 被折叠为 UTC timestamp | 在保留时区语义前显式拒绝 |
| 中 | Delta schema 声明 long、Parquet 实际为 string 时仍返回字符串 | flat 读取校验逻辑类型/物理类型；`timestamp` 只接受可识别的 INT96 |

## 2. 回归测试

新增 7 个具名用例（总数 37 → 44）：

- `snapshot: percent-encoded remove.path deletes the added file`
- `stats: non-string add.stats is rejected instead of becoming zero`
- `write: writerFeatures refuse appends without creating a commit`
- `schema: timestamp_ntz is rejected until its semantics are preserved`
- `checkpoint: deletion vector, unknown action and missing metadata are rejected`
- `read: Delta schema and parquet physical type mismatch is rejected`
- `read: INT96 timestamp remains supported after physical type checks`

新增 `fixtures/checkpoint-negative/`（4 个 fixture）与生成脚本 `fixtures/generate_checkpoint_negative.py`。

## 3. 仍存在的已知边界

- flat 列已做 Delta/Parquet 类型一致性校验；nested 叶类型尚未做同等校验。
- `timestamp` 仅支持可识别的 INT96；INT64 TIMESTAMP_MICROS/millis 与 adjusted 标志尚未实现。
- 分区值回填只支持部分类型；date 仅接受整数天数。
- 互操作结论仅覆盖仓库指定 flat/nested fixture，不外推到任意 reader v1 表。
- CI 远端运行链接、mooncakes 新版本发布与安装仍需可联网环境完成。

## 4. 复验命令

```powershell
moon check --target native
moon test --target native
_build\native\debug\build\cmd\delta\delta.exe inspect fixtures\checkpoint-negative\deletion-vector
_build\native\debug\build\cmd\delta\delta.exe inspect fixtures\checkpoint-negative\unknown-action
_build\native\debug\build\cmd\delta\delta.exe inspect artifacts\protocol-audit\fixtures\stats-object
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d2_verify.py
.tools\pyenv\Scripts\python.exe artifacts\acceptance\d3_verify.py
```
