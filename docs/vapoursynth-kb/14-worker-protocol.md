# 14 — 预览 worker、epoch 与帧传输

## 范围与边界

预览不在 Qt 主进程直接创建 VS core。`vs_worker.exe` 独占 VapourSynth、加载用户脚本
并请求帧；主进程只处理 UI、协议和显示。这不是额外滤镜链，因此不会造成预览/导出
分叉。

## 协议为何这样设计

控制通道是带长度前缀的 JSON 消息，帧像素走 Windows named mmap：

```text
Qt → load(epoch, job, script) → worker
Qt → request_frame(epoch, index, surface) → worker
worker → frame_ready(epoch, slot, BGR24 metadata) → Qt
```

`epoch` 是一次加载会话的世代号。用户切换文件、旋转、裁剪或改脚本时会创建新的
epoch；旧 epoch 的帧即使迟到也会被丢弃，不能覆盖新画面。取消操作以 worker 的
ACK 为线性化点，worker 异常退出则由客户端回收请求并重启，不把旧 mmap 槽当作有效帧。

`shared_frame.py` 说明为何帧不经 JSON：一帧 384×640 BGR24 已接近 720 KiB，复制到
文本协议会增加延迟和内存压力。mmap 元数据带有 epoch、槽名与 generation，客户端会
逐项校验后再显示。

## 与官方 API 的关系

VapourSynth 的异步帧请求和 frame 生命周期属于 VS 运行时；Qt 线程不可直接持有
worker 内的 VS frame。worker 在取得 frame 后复制有效行到连续 BGR24，再把复制件交给
Qt。这符合本机接口源码 `vapoursynth.pyx` 中独立的 `get_read_ptr()`、`get_stride()`
及 frame memoryview 行为；stride 不是图像有效宽度，读取时必须裁掉 padding。

## 排错

- `worker.stale_epoch`：正常的旧请求淘汰，不应弹出媒体损坏提示。
- `contract_error`：脚本执行成功但 output 不合格，查看 [15 输出契约](15-output-contract.md)。
- `script_error` 或 `requirement_error`：检查脚本头、插件和模块路径，见
  [13 用户 VPY ABI](13-user-vpy-abi.md)。
- worker 连续崩溃：停止重试后保留 stderr 尾部；不要在主进程尝试直接加载 VS 绕过它。

## 直接源码与验证

- `core/vs_runtime/protocol.py`：长度前缀编解码。
- `core/vs_runtime/worker_main.py`：load、frame、cancel_epoch 和异常响应。
- `core/vs_runtime/shared_frame.py`：命名 mmap 和 BGR24 帧槽。
- `gui/widgets/video_preview.py`：epoch 所有权与过时消息过滤。

```powershell
uv run python -m unittest tests.test_vs_worker_process tests.test_preview_worker_integration tests.test_vs_frame_probe -v
```
