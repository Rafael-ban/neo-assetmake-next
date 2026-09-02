"""在全新解释器中运行会初始化真实 VapourSynth core 的测试 case。"""

from __future__ import annotations

import hashlib
import gc
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = ROOT / "resources" / "vapoursynth" / "python"
DEFAULT_PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"


def _emit(payload: dict[str, object], *, exit_code: int = 0) -> None:
    sys.__stdout__.write(json.dumps(payload, ensure_ascii=False) + "\n")
    raise SystemExit(exit_code)


def _load_vs():
    from core import vs_engine

    return vs_engine.load_vapoursynth()


def _executor_deferred_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.executor import (
        execute_user_script,
        install_python_stdout,
    )

    log_lines: list[str] = []
    install_python_stdout(log_lines.append)
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve()
        modules = root / "modules"
        modules.mkdir()
        (modules / "marker.py").write_text(
            'VALUE = "marker-from-script-root"\n', encoding="utf-8"
        )
        (modules / "lazy_marker.py").write_text(
            'print("lazy module imported")\nVALUE = "lazy-marker"\n',
            encoding="utf-8",
        )
        script = root / "pipeline.vpy"
        script.write_bytes(
            (ROOT / "tests" / "fixtures" / "vs_scripts" / "prints_and_imports.vpy")
            .read_bytes()
        )
        job = root / "job.json"
        job.write_text("{}", encoding="utf-8")
        graph = execute_user_script(
            script_path=script,
            job_path=job,
            api_version="1",
            mode="raw",
        )
        active_during_render = str(modules) in sys.path
        output = vs.get_output(0)
        frame = output.clip.get_frame(0)
        frame.close()
        graph.close()
        active_after_close = str(modules) in sys.path
    sys.stdout = sys.__stdout__
    return {
        "logs": log_lines,
        "active_during_render": active_during_render,
        "active_after_close": active_after_close,
    }


def _executor_cross_root_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs import executor as helper_module
    from assetmaker_vs.executor import execute_user_script

    stdlib_json = sys.modules["json"]
    script_source = """import vapoursynth as vs
from marker import VALUE

marker_value = VALUE
base = vs.core.std.BlankClip(
    width=16,
    height=16,
    length=1,
    format=vs.GRAY8,
)

def deferred(n, f):
    import marker
    assert marker.VALUE == marker_value
    return f

vs.core.std.ModifyFrame(
    clip=base,
    clips=base,
    selector=deferred,
).set_output(0)
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        roots = [Path(temp_dir).resolve() / name for name in ("A", "B")]
        for root, value in zip(roots, ("from-a", "from-b"), strict=True):
            modules = root / "modules"
            modules.mkdir(parents=True)
            (modules / "marker.py").write_text(
                f"VALUE = {value!r}\n", encoding="utf-8"
            )
            (root / "pipeline.vpy").write_text(
                script_source, encoding="utf-8"
            )
            (root / "job.json").write_text("{}", encoding="utf-8")

        first = execute_user_script(
            script_path=roots[0] / "pipeline.vpy",
            job_path=roots[0] / "job.json",
            api_version="1",
            mode="raw",
        )
        first_value = first.namespace["marker_value"]
        first_frame = vs.get_output(0).clip.get_frame(0)
        first_frame.close()
        active_module_path = str(Path(sys.modules["marker"].__file__).resolve())
        first.close()
        retired_after_close = "marker" not in sys.modules

        second = execute_user_script(
            script_path=roots[1] / "pipeline.vpy",
            job_path=roots[1] / "job.json",
            api_version="1",
            mode="raw",
        )
        second_value = second.namespace["marker_value"]
        second_module_path = str(Path(sys.modules["marker"].__file__).resolve())
        second.close()

    return {
        "first_value": first_value,
        "second_value": second_value,
        "active_module_path": active_module_path,
        "second_module_path": second_module_path,
        "retired_after_close": retired_after_close,
        "retired_second_after_close": "marker" not in sys.modules,
        "helper_preserved": sys.modules.get("assetmaker_vs.executor") is helper_module,
        "stdlib_preserved": sys.modules.get("json") is stdlib_json,
    }


def _job_payload(*, frame_count: int = 5) -> dict[str, object]:
    return {
        "api_version": 1,
        "epoch": 1,
        "track": "loop",
        "project_root": r"D:\素材\黍",
        "source": {
            "path": r"D:\素材\黍\source.mp4",
            "kind": "video",
            "virtual_frame_count": None,
        },
        "timeline": {
            "start_frame": 0,
            "end_frame": frame_count,
            "fps": {"numerator": 30000, "denominator": 1001},
        },
        "transform": {
            "rotation": 0,
            "crop": {
                "coordinate_space": "post_rotation_source_pixels",
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
            },
        },
        "output": {
            "profile": "360x640",
            "display_width": 360,
            "display_height": 640,
            "coded_width": 384,
            "coded_height": 640,
            "pixel_format": "YUV420P8",
            "matrix": "170m",
            "transfer": "170m",
            "primaries": "170m",
            "range": "limited",
            "final_rotate_180": False,
        },
        "paths": {"cache_dir": r"D:\素材\黍\cache"},
    }


def _raw_header() -> dict[str, object]:
    return {
        "api_version": 1,
        "mode": "raw",
        "capabilities": ["source"],
        "requires": [],
        "editor_output": 0,
    }


def _tagged_clip(vs, *, length: int = 5):
    clip = vs.core.std.BlankClip(
        width=384,
        height=640,
        length=length,
        fpsnum=30000,
        fpsden=1001,
        format=vs.YUV420P8,
        color=[16, 128, 128],
    )
    return vs.core.std.SetFrameProps(
        clip,
        _Matrix=6,
        _Transfer=6,
        _Primaries=6,
        _ColorRange=1,
    )


def _contract_valid_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import validate_outputs

    vs.clear_outputs()
    _tagged_clip(vs).set_output(0)
    validated = validate_outputs(vs, _job_payload(), _raw_header())
    frame = validated.guarded_clip.get_frame(1)
    props = dict(frame.props)
    frame.close()
    return {
        "vui": validated.vui.to_dict(),
        "matrix": props["_Matrix"],
        "format": validated.guarded_clip.format.name,
    }


def _contract_late_drift_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        decode_output_contract_error,
        validate_outputs,
    )

    base = _tagged_clip(vs)

    def drift(n, f):
        if n != 1:
            return f
        changed = f.copy()
        changed.props["_Matrix"] = 1
        return changed

    clip = vs.core.std.ModifyFrame(clip=base, clips=base, selector=drift)
    vs.clear_outputs()
    clip.set_output(0)
    validated = validate_outputs(vs, _job_payload(), _raw_header())
    try:
        validated.guarded_clip.get_frame(1)
    except BaseException as exc:
        contract_error = decode_output_contract_error(exc)
        if contract_error is not None:
            return {"error": contract_error.to_dict()}
        raise
    raise AssertionError("逐帧 guard 未拒绝第 2 帧的 matrix 漂移")


def _invalid_prop_clip(
    vs,
    *,
    prop: str,
    value: object,
    late: bool = False,
    remove_color_range: bool = False,
):
    base = _tagged_clip(vs)

    def invalid(n, f):
        if late and n != 1:
            return f
        changed = f.copy()
        if remove_color_range:
            del changed.props["_ColorRange"]
        changed.props[prop] = value
        return changed

    return vs.core.std.ModifyFrame(clip=base, clips=base, selector=invalid)


def _contract_strict_types_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        OutputContractError,
        decode_output_contract_error,
        validate_outputs,
    )

    cases = (
        ("_Matrix", 6.5, False),
        ("_Transfer", 6.0, False),
        ("_Primaries", b"6", False),
        ("_Range", 0.0, True),
        ("_ColorRange", 1.0, False),
    )
    results: dict[str, object] = {}
    for prop, value, remove_color_range in cases:
        clip = _invalid_prop_clip(
            vs,
            prop=prop,
            value=value,
            remove_color_range=remove_color_range,
        )
        vs.clear_outputs()
        clip.set_output(0)
        try:
            validate_outputs(vs, _job_payload(), _raw_header())
        except OutputContractError as exc:
            decoded = decode_output_contract_error(exc)
            results[prop] = {
                "error": (decoded or exc).to_dict(),
            }
        else:
            results[prop] = {"accepted": True}
    return {"results": results}


def _contract_bytes_case(*, late: bool) -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        decode_output_contract_error,
        validate_outputs,
    )

    clip = _invalid_prop_clip(
        vs,
        prop="_Matrix",
        value=b"not-an-int",
        late=late,
    )
    vs.clear_outputs()
    clip.set_output(0)
    try:
        validated = validate_outputs(vs, _job_payload(), _raw_header())
        if late:
            validated.guarded_clip.get_frame(1)
    except BaseException as exc:
        decoded = decode_output_contract_error(exc)
        if decoded is None:
            return {
                "exception": type(exc).__name__,
                "message": str(exc),
            }
        return {"error": decoded.to_dict()}
    return {"accepted": True}


def _contract_bytes_sentinel_case() -> dict[str, object]:
    return _contract_bytes_case(late=False)


def _contract_bytes_late_case() -> dict[str, object]:
    return _contract_bytes_case(late=True)


def _range_probe_case() -> dict[str, object]:
    vs = _load_vs()
    rgb = vs.core.std.BlankClip(
        width=16,
        height=16,
        length=1,
        format=vs.RGB24,
        color=[32, 64, 96],
    )
    payload: dict[str, object] = {}
    for name in ("limited", "full"):
        clip = vs.core.resize.Bicubic(
            rgb,
            format=vs.YUV420P8,
            matrix_s="170m",
            range_s=name,
        )
        frame = clip.get_frame(0)
        payload[name] = {
            key: int(frame.props[key])
            for key in ("_Range", "_ColorRange")
            if key in frame.props
        }
        frame.close()
    return payload


def _display_geometry_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.display import to_display_clip

    clip = vs.core.std.BlankClip(
        width=1920,
        height=1080,
        length=1,
        format=vs.YUV420P8,
        color=[16, 128, 128],
    )
    clip = vs.core.std.SetFrameProps(
        clip,
        _Matrix=6,
        _Transfer=6,
        _Primaries=6,
        _ColorRange=1,
    )
    dimensions: dict[str, list[int]] = {}
    for name, zoom in (
        ("one_percent", 0.01),
        ("fit", 1.0),
        ("two_x", 2.0),
        ("hundred_x", 100.0),
    ):
        out = to_display_clip(
            clip,
            viewport=(480, 270),
            zoom_factor=zoom,
            pan=(0.5, 0.5),
        )
        frame = out.get_frame(0)
        frame.close()
        dimensions[name] = [out.width, out.height]
    large = vs.core.std.BlankClip(
        width=3840,
        height=2160,
        length=1,
        format=vs.RGB24,
    )
    capped = to_display_clip(
        large,
        viewport=(321, 181),
        zoom_factor=100.0,
        pan=(1.0, 1.0),
    )
    frame = capped.get_frame(0)
    frame.close()
    dimensions["large_capped"] = [capped.width, capped.height]
    return dimensions


def _display_center_case() -> dict[str, object]:
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.display import to_display_clip

    black = vs.core.std.BlankClip(
        width=1,
        height=1,
        length=1,
        format=vs.RGB24,
        color=[0, 0, 0],
    )
    red = vs.core.std.BlankClip(
        width=1,
        height=1,
        length=1,
        format=vs.RGB24,
        color=[255, 0, 0],
    )
    black_row = vs.core.std.StackHorizontal([black] * 5)
    center_row = vs.core.std.StackHorizontal([black, black, red, black, black])
    source = vs.core.std.StackVertical([black_row, center_row, black_row])
    out = to_display_clip(
        source,
        viewport=(5, 3),
        zoom_factor=100.0,
        pan=(0.5, 0.5),
    )
    frame = out.get_frame(0)
    red_plane = bytes(frame[0])
    green_plane = bytes(frame[1])
    blue_plane = bytes(frame[2])
    frame.close()
    odd = vs.core.std.BlankClip(
        width=7,
        height=5,
        length=1,
        format=vs.RGB24,
    )
    odd_out = to_display_clip(
        odd,
        viewport=(5, 3),
        zoom_factor=2.0,
        pan=(0.5, 0.5),
    )
    one_pixel = to_display_clip(
        odd,
        viewport=(5, 3),
        zoom_factor=100.0,
        pan=(0.5, 0.5),
    )
    one_pixel.get_frame(0).close()
    return {
        "dimensions": [out.width, out.height],
        "all_red": all(value == 255 for value in red_plane),
        "green_zero": all(value == 0 for value in green_plane),
        "blue_zero": all(value == 0 for value in blue_plane),
        "odd_dimensions": [odd_out.width, odd_out.height],
        "one_pixel_dimensions": [one_pixel.width, one_pixel.height],
    }


def _output_payload(profile: str) -> dict[str, object]:
    if profile == "360x640":
        geometry = (360, 640, 384, 640)
    elif profile == "720x1080":
        geometry = (720, 1080, 720, 1080)
    else:
        raise ValueError(profile)
    display_width, display_height, coded_width, coded_height = geometry
    return {
        "profile": profile,
        "display_width": display_width,
        "display_height": display_height,
        "coded_width": coded_width,
        "coded_height": coded_height,
        "pixel_format": "YUV420P8",
        "matrix": "170m",
        "transfer": "170m",
        "primaries": "170m",
        "range": "limited",
        "final_rotate_180": False,
    }


def _write_default_job(
    path: Path,
    *,
    source: Path,
    kind: str,
    virtual_frame_count: int | None,
    start_frame: int,
    end_frame: int | None,
    fps: tuple[int, int] | None,
    rotation: int,
    crop: tuple[int, int, int, int],
    profile: str,
    epoch: int,
) -> None:
    root = path.parent.resolve()
    crop_x, crop_y, crop_width, crop_height = crop
    payload = {
        "api_version": 1,
        "epoch": epoch,
        "track": "loop",
        "project_root": str(root),
        "source": {
            "path": str(source.resolve()),
            "kind": kind,
            "virtual_frame_count": virtual_frame_count,
        },
        "timeline": {
            "start_frame": start_frame,
            "end_frame": end_frame,
            "fps": (
                None
                if fps is None
                else {"numerator": fps[0], "denominator": fps[1]}
            ),
        },
        "transform": {
            "rotation": rotation,
            "crop": {
                "coordinate_space": "post_rotation_source_pixels",
                "x": crop_x,
                "y": crop_y,
                "width": crop_width,
                "height": crop_height,
            },
        },
        "output": _output_payload(profile),
        "paths": {"cache_dir": str((root / "cache").resolve())},
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _make_png(path: Path, *, width: int = 8, height: int = 6) -> None:
    from PIL import Image

    image = Image.new("RGB", (width, height), (220, 40, 20))
    image.putpixel((width // 2, height // 2), (10, 240, 30))
    image.save(path)


def _make_source_mp4(
    path: Path,
    *,
    width: int = 12,
    height: int = 8,
    length: int = 8,
) -> None:
    script = path.with_suffix(".source.vpy")
    script.write_text(
        "\n".join(
            [
                "import vapoursynth as vs",
                "core = vs.core",
                "clip = core.std.BlankClip(",
                f"    width={width}, height={height}, length={length},",
                "    fpsnum=30000, fpsden=1001, format=vs.RGB24,",
                "    color=[220, 40, 20],",
                ")",
                "clip = core.resize.Bicubic(",
                "    clip, format=vs.YUV420P8, matrix_s='170m', range_s='limited'",
                ")",
                "clip.set_output()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _encode_source_script(
        script,
        path,
        colormatrix="smpte170m",
        colorprim="smpte170m",
        transfer="smpte170m",
    )


def _execute_default(job_path: Path, *, for_export: bool):
    sys.path.insert(0, str(HELPER_ROOT))
    vs = _load_vs()
    from assetmaker_vs.contract import (
        validate_outputs,
        verify_required_callables,
    )
    from assetmaker_vs.executor import execute_user_script
    from assetmaker_vs.job_api import load_job
    from assetmaker_vs.script_header import (
        parse_script_header,
        validate_invocation,
    )

    header = parse_script_header(DEFAULT_PIPELINE)
    job = load_job(job_path, for_export=for_export)
    validate_invocation(header, api_version="1", mode="compatible")
    verify_required_callables(vs.core, header["requires"])
    graph = execute_user_script(
        script_path=DEFAULT_PIPELINE,
        job_path=job_path,
        api_version="1",
        mode="compatible",
    )
    validated = validate_outputs(vs, job, header)
    return vs, graph, validated


def _node_metadata(node) -> dict[str, object]:
    frame = node.get_frame(0)
    props = {
        key: int(frame.props[key])
        for key in ("_Matrix", "_Transfer", "_Primaries", "_ColorRange")
        if key in frame.props
    }
    frame.close()
    return {
        "width": node.width,
        "height": node.height,
        "num_frames": node.num_frames,
        "fps": [node.fps_num, node.fps_den],
        "format": node.format.name,
        "props": props,
    }


def _run_default_vspipe(job: Path) -> dict[str, object]:
    from core.media_tools import (
        MediaToolchain,
        build_media_subprocess_env,
    )

    toolchain = MediaToolchain.discover(str(ROOT))
    command = [
        toolchain.vspipe_path,
        "--info",
        "--arg",
        f"assetmaker_job={job}",
        "--arg",
        f"assetmaker_script={DEFAULT_PIPELINE}",
        "--arg",
        "assetmaker_api=1",
        "--arg",
        "assetmaker_mode=compatible",
        str(RUNNER),
        "-",
    ]
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 60,
        "check": False,
        "env": build_media_subprocess_env(toolchain.vspipe_path),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **kwargs)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _default_image_case() -> dict[str, object]:
    _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve() / "素材" / "黍"
        root.mkdir(parents=True)
        image = root / "静态图.png"
        _make_png(image)
        job = root / "image-job.json"
        _write_default_job(
            job,
            source=image,
            kind="image",
            virtual_frame_count=9,
            start_frame=2,
            end_frame=7,
            fps=(30, 1),
            rotation=90,
            crop=(1, 1, 999, 999),
            profile="360x640",
            epoch=11,
        )
        vs, graph, validated = _execute_default(job, for_export=True)
        output0 = _node_metadata(validated.guarded_clip)
        output1 = _node_metadata(vs.get_output(1).clip)
        graph.close()
        runner = _run_default_vspipe(job)
        encoded_path = root / "runner-output.mp4"
        _encode_default_runner(job, encoded_path)
        encoded = {"size": encoded_path.stat().st_size}
        vs.clear_outputs()
        del validated, graph
        gc.collect()
    return {
        "output0": output0,
        "output1": output1,
        "runner": runner,
        "encoded": encoded,
    }


def _default_video_case() -> dict[str, object]:
    _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve() / "素材" / "黍"
        root.mkdir(parents=True)
        source = root / "视频.mp4"
        _make_source_mp4(source)

        bootstrap = root / "bootstrap.json"
        _write_default_job(
            bootstrap,
            source=source,
            kind="video",
            virtual_frame_count=None,
            start_frame=0,
            end_frame=None,
            fps=None,
            rotation=90,
            crop=(0, 0, 0, 0),
            profile="720x1080",
            epoch=12,
        )
        vs, bootstrap_graph, bootstrap_validated = _execute_default(
            bootstrap, for_export=False
        )
        bootstrap0 = _node_metadata(bootstrap_validated.guarded_clip)
        bootstrap1 = _node_metadata(vs.get_output(1).clip)
        bootstrap_graph.close()

        resolved = root / "resolved.json"
        _write_default_job(
            resolved,
            source=source,
            kind="video",
            virtual_frame_count=None,
            start_frame=2,
            end_frame=7,
            fps=(30000, 1001),
            rotation=90,
            crop=(1, 1, 999, 999),
            profile="720x1080",
            epoch=13,
        )
        vs, resolved_graph, resolved_validated = _execute_default(
            resolved, for_export=True
        )
        resolved0 = _node_metadata(resolved_validated.guarded_clip)
        resolved1 = _node_metadata(vs.get_output(1).clip)
        resolved_graph.close()
        vs.clear_outputs()
        del bootstrap_validated, bootstrap_graph
        del resolved_validated, resolved_graph
        gc.collect()
    return {
        "bootstrap0": bootstrap0,
        "bootstrap1": bootstrap1,
        "resolved0": resolved0,
        "resolved1": resolved1,
    }


def _encode_vspipe_output(
    vspipe_arguments: list[str],
    path: Path,
    *,
    fps: float,
    colormatrix: str,
    colorprim: str,
    transfer: str,
) -> None:
    from core.media_pipeline import build_mux_command, build_x264_command
    from core.media_tools import (
        MediaToolchain,
        build_media_subprocess_env,
    )

    toolchain = MediaToolchain.discover(str(ROOT))
    raw = path.with_suffix(".264")
    popen_kwargs: dict[str, object] = {
        "cwd": ROOT,
        "env": build_media_subprocess_env(toolchain.vspipe_path),
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    vspipe = subprocess.Popen(
        [toolchain.vspipe_path, *vspipe_arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    x264 = subprocess.Popen(
        build_x264_command(
            toolchain.x264_path,
            str(raw),
            crf=10,
            preset="ultrafast",
            colormatrix=colormatrix,
            colorprim=colorprim,
            transfer=transfer,
            range_="tv",
        ),
        stdin=vspipe.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    if vspipe.stdout is not None:
        vspipe.stdout.close()
    x264_stderr = x264.communicate(timeout=120)[1]
    vspipe.wait(timeout=30)
    vspipe_stderr = (
        vspipe.stderr.read() if vspipe.stderr is not None else b""
    )
    if vspipe.returncode != 0 or x264.returncode != 0:
        raise RuntimeError(
            (vspipe_stderr + x264_stderr).decode("utf-8", errors="replace")
        )
    mux = subprocess.run(
        build_mux_command(
            toolchain.muxer_path,
            str(raw),
            str(path),
            fps,
        ),
        cwd=ROOT,
        capture_output=True,
        timeout=120,
        check=False,
        env=build_media_subprocess_env(toolchain.muxer_path),
        creationflags=(
            subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        ),
    )
    if mux.returncode != 0:
        raise RuntimeError(mux.stderr.decode("utf-8", errors="replace"))


def _encode_source_script(
    script: Path,
    path: Path,
    *,
    colormatrix: str,
    colorprim: str,
    transfer: str,
) -> None:
    _encode_vspipe_output(
        ["-c", "y4m", str(script), "-"],
        path,
        fps=30000 / 1001,
        colormatrix=colormatrix,
        colorprim=colorprim,
        transfer=transfer,
    )


def _encode_default_runner(job: Path, path: Path) -> None:
    _encode_vspipe_output(
        [
            "-c",
            "y4m",
            "--arg",
            f"assetmaker_job={job}",
            "--arg",
            f"assetmaker_script={DEFAULT_PIPELINE}",
            "--arg",
            "assetmaker_api=1",
            "--arg",
            "assetmaker_mode=compatible",
            str(RUNNER),
            "-",
        ],
        path,
        fps=30.0,
        colormatrix="smpte170m",
        colorprim="smpte170m",
        transfer="smpte170m",
    )


def _encode_untagged_709_source(path: Path) -> None:
    script = path.with_suffix(".vpy")
    script.write_text(
        "\n".join(
            [
                "import vapoursynth as vs",
                "core = vs.core",
                "clip = core.std.BlankClip(",
                "    width=800, height=800, length=3,",
                "    fpsnum=30000, fpsden=1001, format=vs.RGB24,",
                "    color=[220, 40, 20],",
                ")",
                "clip = core.resize.Bicubic(",
                "    clip, format=vs.YUV420P8, matrix_s='709', range_s='limited'",
                ")",
                "clip.set_output()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _encode_source_script(
        script,
        path,
        colormatrix="undef",
        colorprim="undef",
        transfer="undef",
    )


def _frame_digest(frame) -> str:
    digest = hashlib.sha256()
    for plane in range(frame.format.num_planes):
        digest.update(bytes(frame[plane]))
    return digest.hexdigest()


def _default_p7_case() -> dict[str, object]:
    vs = _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir).resolve() / "素材" / "黍"
        root.mkdir(parents=True)
        source = root / "untagged-709.mp4"
        _encode_untagged_709_source(source)
        source_frame = vs.core.lsmas.LWLibavSource(str(source)).get_frame(0)
        source_matrix = int(source_frame.props.get("_Matrix", 2))
        source_frame.close()
        digests: list[str] = []
        for epoch, crop in (
            (21, (0, 0, 450, 800)),
            (22, (0, 0, 404, 718)),
        ):
            job = root / f"p7-{epoch}.json"
            _write_default_job(
                job,
                source=source,
                kind="video",
                virtual_frame_count=None,
                start_frame=0,
                end_frame=3,
                fps=(30000, 1001),
                rotation=0,
                crop=crop,
                profile="360x640",
                epoch=epoch,
            )
            _vs, graph, validated = _execute_default(job, for_export=True)
            frame = validated.guarded_clip.get_frame(0)
            digests.append(_frame_digest(frame))
            frame.close()
            graph.close()
            vs.clear_outputs()
            del validated, graph
            gc.collect()
    return {
        "source_matrix": source_matrix,
        "digests": digests,
        "equal": digests[0] == digests[1],
    }


CASES = {
    "contract_bytes_late": _contract_bytes_late_case,
    "contract_bytes_sentinel": _contract_bytes_sentinel_case,
    "contract_late_drift": _contract_late_drift_case,
    "contract_strict_types": _contract_strict_types_case,
    "contract_valid": _contract_valid_case,
    "display_center": _display_center_case,
    "display_geometry": _display_geometry_case,
    "default_image": _default_image_case,
    "default_p7": _default_p7_case,
    "default_video": _default_video_case,
    "executor_deferred": _executor_deferred_case,
    "executor_cross_root": _executor_cross_root_case,
    "range_probe": _range_probe_case,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in CASES:
        _emit({"error": "unknown case"}, exit_code=2)
    try:
        payload = CASES[sys.argv[1]]()
    except BaseException as exc:
        sys.stdout = sys.__stdout__
        _emit(
            {
                "error": type(exc).__name__,
                "message": str(exc),
            },
            exit_code=1,
        )
    _emit(payload)


if __name__ == "__main__":
    main()
