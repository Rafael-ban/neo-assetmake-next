"""固定 VSPipe 启动器：加载用户脚本并注册通过契约的 output 0。"""

import sys


# Python 输出永远走 stderr；VSPipe 的 C 层 Y4M stdout 不受此替换影响。
sys.stdout = sys.stderr

from pathlib import Path

import vapoursynth as vs


_root = Path(__file__).resolve().parent
_helper_root = str(_root / "python")
if _helper_root not in sys.path:
    sys.path.insert(0, _helper_root)

from assetmaker_vs.contract import (  # noqa: E402
    validate_outputs,
    verify_required_callables,
)
from assetmaker_vs.executor import execute_user_script  # noqa: E402
from assetmaker_vs.job_api import (  # noqa: E402
    load_job,
    runtime_python_dirs_from_env,
)
from assetmaker_vs.script_header import (  # noqa: E402
    parse_script_header,
    validate_invocation,
)


header = parse_script_header(assetmaker_script)
job = load_job(assetmaker_job, for_export=True)
validate_invocation(
    header,
    api_version=assetmaker_api,
    mode=assetmaker_mode,
)
verify_required_callables(vs.core, header["requires"])
graph = execute_user_script(
    script_path=assetmaker_script,
    job_path=assetmaker_job,
    api_version=assetmaker_api,
    mode=assetmaker_mode,
    python_module_dirs=runtime_python_dirs_from_env(),
)
validated = validate_outputs(vs, job, header)
validated.guarded_clip.set_output(0)
