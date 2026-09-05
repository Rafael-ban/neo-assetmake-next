# 13 — 用户 `.vpy` 脚本接口

## 范围与边界

本文说明本项目如何执行用户 `.vpy`。它不教授所有滤镜，也不承诺第三方 Python
包或 AI 模型可在便携运行时使用。滤镜链由脚本作者定义；宿主只提供 job、脚本
头校验、预览 worker 和编码输出约束。

官方 VapourSynth 的核心模型是 Python 构图后以 `clip.set_output()` 注册输出；参见
[Python Reference](https://www.vapoursynth.com/doc/pythonreference.html)。本项目在此
基础上增加了明确的宿主 ABI，避免把播放器专用隐式变量误当成标准 VS API。

## 先从内置模板开始

最稳妥的方式是复制 `resources/vapoursynth/default_pipeline.vpy`，再修改滤镜部分。
该模板已有源加载、裁剪、颜色、设备补边和输出契约。一个可读的 `.vpy` 通常就是：

```python
# 1. 导入模块
import vapoursynth as vs

# 2. 得到 clip，再按顺序覆写 clip
clip = ...
clip = vs.core.resize.Bicubic(clip, width=360, height=640)

# 3. 显式注册输出
clip.set_output(0)
```

这与 [mpv_PlayKit 的自定义脚本教程](https://github.com/hooke007/mpv_PlayKit/wiki/3_K7sfunc#%E5%88%9B%E5%BB%BA%E8%87%AA%E5%AE%9A%E4%B9%89vpy%E8%84%9A%E6%9C%AC) 所强调的“导入 →
处理 → 输出”结构相同。该教程也建议使用带模块前缀的导入以避免名称冲突；在本项目中
同样建议写 `import package as alias`，不要使用 `from package import *`。

## 本项目注入的变量

执行脚本前，`assetmaker_vs.executor.execute_user_script()` 注入下列字符串变量：

| 变量 | 含义 |
|---|---|
| `assetmaker_job` | 本次渲染冻结的 UTF-8 JSON job 文件绝对路径 |
| `assetmaker_api` | 当前脚本 API 版本，如 `"1"` |
| `assetmaker_script` | 当前主 `.vpy` 的绝对路径 |
| `assetmaker_mode` | `compatible` 或 `raw` |

通过 `from assetmaker_vs.job_api import load_job` 读取 job；不要把 JSON 值拼进 Python
源码。job 中的帧号是索引，时间字段统一为微秒。脚本可把同目录 `modules/` 作为私有
Python 模块目录，但应避免把模块名伪装成标准库或 `assetmaker_vs`。

**不要照搬 mpv 专用变量。** PlayKit 文档中的 `video_in`、`container_fps`、
`display_fps` 和 `vf=vapoursynth=...` 是 mpv 过滤器的宿主接口，不是 VapourSynth
标准 API，也不会在本项目注入。其关于性能的经验只能用于选择你的滤镜顺序，不能改变
本项目 job 或输出格式。

## 脚本头与两种模式

每份脚本前五行应声明接口：

```python
# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1
```

- `compatible`：脚本愿意配合编辑器的 source/trim/crop/rotation/resolution/image_loop
  工作流；若声明了编辑能力，必须把旋转后、trim/crop 前的完整画布注册为
  `output 1`。内置模板演示了该顺序。
- `raw`：脚本完全自行管理图形，只允许 `assetmaker-editor-output: 0`，编辑器只消费
  `output 0`。这适合高度定制的滤镜图，但不会提供可交互裁剪画布。

脚本头在脚本 Python 代码执行前由
`resources/vapoursynth/python/assetmaker_vs/script_header.py` 解析和校验。旧的“随便
运行，然后从异常推断能力”做法无效：它会让预览与导出在不同阶段才失败；显式模式和
能力声明能在加载前给出确定错误。

## 插件、放大与性能

可以在用户脚本中调用已安装插件。例如超分或补帧通常应先控制输入尺寸、再执行昂贵的
滤镜，最后按设备输出尺寸收敛；PlayKit 的
[讨论 #313](https://github.com/hooke007/mpv_PlayKit/discussions/313) 给出了这一类性能
取舍示例。它是第三方经验，不是本项目的默认链，也不保证适合所有视频。

本项目不会自动安装 `k7sfunc`、AI 模型或 GPU 插件。使用它们前应在脚本头
`assetmaker-requires` 声明实际 namespace，并把 DLL/模块安装到运行时允许的目录；
缺依赖必须在 worker 加载时失败，而不是静默退回另一种滤镜。

## 权威来源与仓库验证

- 官方： [VapourSynth Python Reference](https://www.vapoursynth.com/doc/pythonreference.html)。
- 框架直接源码：`assetmaker_vs/executor.py` 的注入环境、`script_header.py` 的模式校验、
  `default_pipeline.vpy` 的 compatible 模板。
- 第三方教程：PlayKit 的 [自定义 VPY](https://github.com/hooke007/mpv_PlayKit/wiki/3_K7sfunc#%E5%88%9B%E5%BB%BA%E8%87%AA%E5%AE%9A%E4%B9%89vpy%E8%84%9A%E6%9C%AC) 与 [讨论 #313](https://github.com/hooke007/mpv_PlayKit/discussions/313)。

```powershell
uv run python -m unittest tests.test_default_vpy_pipeline tests.test_vs_runner tests.test_vs_output_contract -v
```
