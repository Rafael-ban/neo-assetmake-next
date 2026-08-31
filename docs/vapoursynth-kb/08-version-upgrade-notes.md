# 08 · 版本与升级注意事项

**结论：本项目锁定 R73（api R4.1），Python 下限 3.12 是被 wheel 的 ABI 强制的，不是偏好。升级 VapourSynth 的主要风险不在滤镜 API，而在分发形态与色彩帧属性语义。**

## 为什么锁 3.12 🔬 本机实测

捆绑的 wheel 只有两个：

- `vapoursynth-73-cp312-abi3-win_amd64.whl` — Tag `cp312-abi3`，载荷 `vapoursynth.pyd`
- `vapoursynth-73-cp38-cp38-win_amd64.whl` — Tag `cp38-cp38`，载荷硬链 `python38.dll`，无用

**没有 3.11 可用的 wheel。** 在 3.11 上 `import vapoursynth` 报：

```
ImportError: DLL load failed while importing vapoursynth: 找不到指定的程序
```
（ERROR_PROC_NOT_FOUND）

根因：`.pyd` 引用 5 个 3.11 的 `python3.dll` **不导出**、3.12 才加入 limited API 的符号 —— `PyObject_Vectorcall`、`PyObject_VectorcallMethod`、`PyType_FromMetaclass`、`PyVectorcall_Call`、`PyVectorcall_NARGS`。

即 `cp312-abi3` = 稳定 ABI **下限 3.12**，向前兼容 3.13+，**永不**向后兼容 3.11。这就是 Stage 0 迁移 Python 3.12 是硬前提的原因。

## 本机运行时事实 🔬

core **R73** / api **4**（4.1）；插件自动加载 `['avs','imwri','lsmas','resize','std','text']`；`num_threads=32`；`max_cache_size=4096MB`；`AudioNode` 存在。

官方文档站当前是 **R76** —— 读文档时注意版本差。

## 升级风险清单（按影响排序）

1. ⚠️ **色彩帧属性语义**：api 4.2+ 若源滤镜改发 `_Range` 而非 `_ColorRange`，数值定义反转。见 [01](01-colour-range-props.md)。**升级后必须重跑 `test_export_color_roundtrip.py` 并核对读到的是哪个键。**
2. ⚠️ **分发形态转向 pip**：较新版本官方主推 pip 安装（`<site-packages>/vapoursynth/plugins`）。本项目依赖的 `portable.vs` + `vs-plugins/` 便携布局是 DLL 层机制，文档里已经不提了。升级时**必须重新验证** `portable.vs` 是否仍被识别，否则插件静默消失。见 [05](05-plugin-autoload-portable.md)。
3. **resize 默认值漂移**：Bicubic 的 b/c 或 dither 默认若变动，所有输出像素改变 → `tests/test_vpy_golden.py` 与 parity 测试全线漂移。此时 golden 漂移是**真实的行为变更**，不能当噪声重抓。
4. **几何滤镜稳定**：`Transpose`/`Turn180`/`CropAbs`/`AddBorders`/`Trim`/`Loop` 语义多年未变，风险最低。
5. ❌ **未核实**：R74/R78 的完整变更日志本次未拿到逐字原文（`vapoursynth.com/2026/08/` 页面无对应条目）。升级前应直接读官方 changelog，不要依赖本条。

## 升级检查清单

```bash
# 1. 语法/导入
uv run python -m compileall main.py config core gui utils _mext build.py tests
# 2. 全量测试（含 golden 与 parity）
uv run python -m unittest discover -s tests -p "test_*.py"
# 3. 便携加载与插件表（关键：确认 lsmas/imwri 仍在）
uv run python -c "from core import vs_engine; vs_engine.prewarm(); import sys; print(vs_engine.get_core().version_number if hasattr(vs_engine.get_core(),'version_number') else '', sorted(p.namespace for p in vs_engine.get_core().plugins())); print('tools/media in sys.path?', any('tools' in p and 'media' in p for p in sys.path))"
# 4. 冻结产物打包契约
uv run python build.py --no-installer --skip-flasher
```

## 相关

- [01](01-colour-range-props.md)、[05](05-plugin-autoload-portable.md)、[09](09-plugin-ecosystem.md)
