"""cx_Freeze VapourSynth worker 薄入口。"""

import os
import sys


def _main() -> int:
    # TextIOWrapper owns (and closes) its .buffer. Keep the original wrapper
    # alive even after sys.stdout/sys.__stdout__ are replaced by log writers.
    protocol_owner = sys.stdout
    protocol_stream = protocol_owner.buffer
    # 在导入 worker/runtime 模块前永久挪走 Python stdout；fd 1 仅供协议。
    sys.stdout = sys.stderr
    sys.dont_write_bytecode = True
    from core.vs_runtime.worker_main import main

    result = main(protocol_stream=protocol_stream)
    del protocol_owner
    return result


if __name__ == "__main__":
    result = _main()
    if result:
        # drain timeout/retirement failure 不能进入解释器正常清理，因为那会
        # 再次触碰已不可信的旧图或等待卡死的 VS callback。
        os._exit(result)
    raise SystemExit(0)
