# 16 — 脚本来源与本机信任

## 范围与边界

`.vpy` 是 Python 程序，不是受限的滤镜描述文件。它可读写当前用户有权限访问的文件、
导入模块并启动子进程。worker 隔离的是 VS/Qt/DLL 生命周期，不是安全沙箱。

便携版 VapourSynth 还会依赖 DLL 同目录的 `portable.vs` 来发现 `vs-plugins/`；它保证
运行时能找到已随应用分发的原生插件，但不会约束用户 `.vpy` 可以执行哪些 Python 代码。

因此项目把“可运行”与“已由此电脑用户确认”分开：脚本必须通过头部和输出契约，项目
脚本还必须有匹配的本机信任记录。

## 来源

| 来源 | 路径规则 | 信任规则 |
|---|---|---|
| builtin | 应用资源内固定模板 | 随应用发布 |
| global | 仅本机运行时覆盖中的绝对路径 | 本机选择，不写入项目 |
| project | 项目根目录内的规范相对 POSIX 路径 | 首次或变化后确认 |

项目脚本路径会 canonicalize 并检查最终位置仍在项目根内，因而符号链接/reparse 点不能
逃逸到项目外。全局绝对路径不进入 `epconfig.json`，保证共享项目不会泄露个人磁盘路径。

## bundle hash

对于 project 脚本，系统递归计算脚本根目录 bundle hash，并把
`(canonical script root, bundle hash)` 写入当前用户的 `%APPDATA%` trust store。任何
文件新增、删除或修改都会改变 bundle hash，触发再次确认。信任记录不随项目导出，也
不会把一个项目的确认扩散给另一目录。

这比只 hash 主 `.vpy` 有效：用户脚本可以 `import modules/foo.py`，仅改模块而不改主
脚本同样会改变运行行为。相关实现见 `core/vs_runtime/trust.py` 与
`core/vs_runtime/session.py`。

## 不要做的事

- 不要把不可信下载脚本直接标为可信；先审查其 Python 代码与依赖。
- 不要依赖 trust 记录实现权限隔离；它只是本机确认 UX。
- 不要向 stdout 打印调试文本。VSPipe 的 stdout 是 Y4M 视频流；调试写 stderr，或由
  worker 协议发送 `log` 消息。

最后一项也出现在知识库 12 的真实运行时陷阱中：污染 stdout 会让 x264 收到损坏的流，
脚本本身可能没有 Python 异常却仍导出失败。

## 验证

```powershell
uv run python -m unittest tests.test_vs_script_trust tests.test_vs_project_compatibility tests.test_vs_script_panel -v
```
