# 06 · VSPipe 命令行

**结论：本项目的导出管线形式与官方 Examples 给出的规范形式一致。`--` 是"求所有帧但不输出"的空跑模式，可用于纯性能/正确性验证。**

## 官方原文 ✅ 已核实

`http://www.vapoursynth.com/doc/output.html`（R76）Examples 节：

> Show script info: `vspipe --info script.vpy -`
> Write to stdout: `vspipe [options] script.vpy -`
> Write to a named pipe (Windows only): `vspipe [options] script.vpy "\\\\.\\pipe\\<pipename>"`
> Request all frames but don't output them: `vspipe [options] script.vpy --`
> Write frames 5-100 to file: `vspipe --start 5 --end 100 script.vpy output.raw`
> Pipe to x264 and write timecodes file: `vspipe script.vpy - -c y4m --timecodes timecodes.txt | x264 --demuxer y4m -o script.mkv -`
> Pass values to a script: `vspipe --arg deinterlace=yes --arg "message=fluffy kittens" script.vpy output.raw`

## 本项目的做法为何正确

`core/media_pipeline.py` 用 `VSPipe -c y4m … | x264-7mod --demuxer y4m …`，与官方 "Pipe to x264" 示例同形。**Y4M 管道就是接口** —— x264-7mod 是 exe、无 Python 绑定，所以这一段刻意保留子进程，不进程内化。测试里也用 `vspipe -c y4m -s N -e N` 取单帧做 parity 比对（`tests/test_vs_graph_player.py`）。

`-p`（进度）已用于驱动导出对话框的进度条。

**位置参数 `outfile` 必须给**：`-` 表示 stdout。忘了它 VSPipe 不会写任何东西。

## 未采用但值得知道

- **`--` 空跑**：求值全部帧但不写输出。适合"只想确认这条图能跑通/量一下吞吐"而不想产生文件。可用于将来加一个"导出前预检"步骤。
- **`--arg key=value`**：向 `.vpy` 传参。本项目**不用** —— 参数是直接写死进生成的 `.vpy` 里的，因为 `tests/test_vpy_golden.py` 逐字节钉死脚本内容，改成传参会让 golden 失去意义。
- **具名管道（Windows）**：`vspipe script.vpy "\\.\pipe\name"`。项目用匿名 stdout 管道，够用。
- **`--timecodes`**：本项目输出固定 fps，不需要 timecodes 文件。

## 相关

- `core/media_pipeline.py`（编码/封装命令构造器，**不要改** `X264_PARAMS`）
- [02-resize-semantics.md](02-resize-semantics.md) — 进 x264 之前的像素格式必须是 `YUV420P8`
