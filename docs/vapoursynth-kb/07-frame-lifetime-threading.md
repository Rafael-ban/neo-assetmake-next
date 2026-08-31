# 07 · 帧生命周期与线程规则

**结论：`frame[plane]` 是 VS 自有内存的 memoryview，`frame.close()` 之后失效——必须先 copy。异步回调跑在 VS worker 线程，回调里绝不能碰 Qt widget。**

## 依据

**帧内存**（binding 源 `vapoursynth.pyx`）：`VideoFrame` 支持缓冲协议，`frame[plane]` 暴露的是**底层帧缓冲的视图**，不是副本。`close()`（或退出 `with`）释放帧后视图悬空。

**平面有 stride 填充** 🔬：本机实测 `width=360` 的 RGB24 帧，plane stride 为 **384**。所以不能按 `width` 直接 reshape，必须按 stride 逐行取或用 `np.asarray(frame[p])` 让 buffer 协议给出正确形状再 copy。

**RGB24 是平面格式且 plane 顺序为 R/G/B** 🔬：用 `BlankClip(format=vs.RGB24, color=[255,0,0])` 验证——plane0 是 R。本仓 `current_frame` 全程是 **BGR**（`cv2` 系），所以必须按 `[2,1,0]` 堆叠。

**异步回调线程**（binding 源 `vapoursynth.pyx`）：完成回调声明为 `noexcept nogil`，紧接着 `with gil:` 重获 GIL。**回调运行在 VS worker 线程**，不是 GUI 线程。

**顺序迭代器不适合跳帧**：`clip.frames()` 是顺序迭代器；随机跳帧要用 `get_frame` / `get_frame_async`。

## 本项目的做法为何正确

`core/vs_frame.py` + `core/vs_player.py`：

1. 回调内只做 `VideoFrame` → numpy（含 copy）并 `close()` —— **不触碰任何 Qt 对象**。
2. 然后 `emit pyqtSignal(int, int, object)`（epoch, index, array）。PyQt6 的 `AutoConnection` 在跨线程时自动走**队列投递**，接收端在 GUI 线程执行。
3. 播放用 `QTimer` 作时钟 + 单帧请求，不用 `clip.frames()`。

**性能余量** 🔬：真实 mp4 经 `lsmas` + 完整编辑链（Transpose+FlipHorizontal+CropAbs+resize→360x640）：首帧 **2.2ms**、随机跳帧 **0.9ms**、顺序 **0.3ms/帧**。30fps 预算 33ms → 两个数量级余量。重复随机跳帧降到 **0.03ms**（core 内部缓存生效，`max_cache_size` 默认 4096MB）。

## prewarm 顺序（本项目特有的硬约束）🔬

**VS core 必须在 PyQt6 加载之前初始化**。`main.py` 与 `tests/qt_harness.py` 在 import 期调 `vs_engine.prewarm()`。Qt 已加载后再建 core 会**段错误（exit 139）**；反序无事。

因此 `_use_vs_preview()` 永不惰性建 core —— `vs_engine._core is None` 时直接返回 False。prewarm 失败则媒体根本无法加载（在加载时**响亮失败**，而不是"预览正常、导出才炸"）。

## 曾经踩过的坑

帧请求在途时关窗，会向已删除的 QObject 发信号 → 偶发段错误。修法是 epoch 令牌 + 关窗时清理在途请求（提交 `14bbcca`）。**`_load_epoch` 仍然吃重**：`lsmas` 建索引慢且异步，被取代的加载必须丢弃迟到帧。

## 相关

- [05-plugin-autoload-portable.md](05-plugin-autoload-portable.md) — 加载策略
- `core/vs_player.py`（`FrameRequester`，含在途预算 `MAX_INFLIGHT` 合并拖动）
