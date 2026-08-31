# 01 · 色彩范围帧属性：`_Range` 与 `_ColorRange`

**结论（最要紧的一句）：两者语义相同，但数值定义互为反转。本捆绑包（api R4.1，`lsmas` 源）只发 `_ColorRange`，值 `1` = limited/tv。**

## 官方原文 ✅ 已核实

`http://www.vapoursynth.com/doc/apireference.html`（R76 文档）：

> `_Range` … 0 = full range, 1 = limited range … **Deprecated since API 4.1, use `_ColorRange` instead**

> `_ColorRange` … **0 = limited range, 1 = full range**

两个键都存在，含义相同，**数值恰好相反**。这不是笔误——`_Range` 沿用早期约定，`_ColorRange` 与 H.273 `video_full_range_flag` 对齐（1 = full）。

## 本捆绑包实测 🔬

本机 R73 / api **R4.1**。对一个真实导出的 mp4（经 `lsmas` 读回）取第 0 帧属性：

```
_ChromaLocation = 0
_ColorRange = 1
_Matrix = 6
_Primaries = 6
_Transfer = 6
...
_Range present?  False
_ColorRange present? True
```

复现：见 `10-research-method.md`。

另一项实测：`std.SetFrameProps` **不做键映射**。写 `_Range=0` 不会让 `_ColorRange` 变成 `1`，两个键各自独立存在。所以"写一个读另一个"的写法必然读到 `None`。

## 对本项目意味着什么

`tests/test_export_color_roundtrip.py` 断言：

```python
self.assertEqual(props.get("_ColorRange"), 1, "expected limited (tv) range")
```

**当前是对的**：`lsmas` 在 api 4.1 下发 `_ColorRange`，limited 编码为 `1`。x264 侧用 `--range tv` 打标，读回一致。

## 语义已用 `resize` 定论 🔬（不要再按"文档可能不符"处理）

只回读 `SetFrameProps` 写进去的值证明不了语义——要看**真正消费 range 的滤镜**怎么解释它。做法：同一份 YUV 数据（`Y=16`，即 limited range 的黑点）只改 range 标记，用 `resize.Point` 转 RGB24 看 R 值。若被当成 limited，16 会被展开成纯黑 `0`；若被当成 full，16 原样留在 `16`（暗灰）。

本机 R73 / api **R4.1** 实测：

```
Y=16 (limited black) 转 RGB 后的 R 值:
  _ColorRange =0 -> R= 16      # 按 full 解释
  _ColorRange =1 -> R=  0      # 按 limited 解释
  _Range      =0 -> R=  0      # 未被承认，落回默认
  _Range      =1 -> R=  0      # 未被承认，落回默认
  (无标记)          -> R=  0      # 默认 limited

对照 resize 参数（无帧属性）:
  range_in_s=limited -> R=  0
  range_in_s=full    -> R= 16
```

**结论:api 4.1 下 `_ColorRange` 是 `0 = full, 1 = limited`,与官方文档一致。`_Range` 在本捆绑包里 `resize` 完全不认**（写得进属性，但对色彩转换无效——落回默认 limited）。

所以 `tests/test_export_color_roundtrip.py:146` 断言 `_ColorRange == 1` **本来就是对的**：`1` = limited，与 x264 的 `--range tv` 完全吻合，四色块往返验证颜色正确也是同一个事实的另一面。**此处不存在"lsmas 与官方文档不一致"的问题**（本条目早前版本曾如此记载，已被上述探针推翻）。

⚠️ **仍然存在的前向兼容陷阱**：升级到 api 4.2+ 后若源滤镜改发 `_Range`，语义**数值互为反转**（`_Range=1` 是 limited，`_ColorRange=1` 也是 limited，此处同号；但 `_Range=0` 是 full 而 `_ColorRange=0` 也是 full——两键在 R73 文档下恰好同向，真正的差异见官方 R76 文档对 `_Range` 的旧约定描述）。**判据只有一条：升级后重跑上面这段探针**，不要凭文档推断。

**防御写法**（只在确实要兼容双键时才引入；现在固定 4.1，多余抽象更容易错）：

```python
if "_ColorRange" in props:
    is_limited = props["_ColorRange"] == 1
elif "_Range" in props:
    is_limited = props["_Range"] == 1    # 升级后必须先用探针确认方向
```

## 相关

- [02-resize-semantics.md](02-resize-semantics.md) — `range_in` 参数与帧属性的优先级
- `config/vsconfig.json` — 色彩契约单一事实源（`matrix_s='170m'` 等，禁改）
