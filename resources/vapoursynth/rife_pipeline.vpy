# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read,rife.RIFE
# assetmaker-editor-output: 1

"""AssetMaker 可选 RIFE compatible pipeline。

output 1 是旋转后、trim/crop 前的完整编辑画布；output 0 严格按 RenderJob
生成处理帧；编码格式由导出模块决定。低帧率视频默认插帧到 60 fps；图片、单帧和高帧率视频跳过。
选择此脚本前须将独立 RIFE DLC 解压到应用目录。
脚本有意保持自包含和可读，便于用户复制到项目或外部目录后定制。
"""

import hashlib
from pathlib import Path
from fractions import Fraction

import vapoursynth as vs

from assetmaker_vs.job_api import load_job
from assetmaker_vs.rife_cache import cached_rife


core = vs.core

# 可复制的默认处理参数。修改这些值或下方 process_source() 即可定制画质；
# RenderJob 只描述编辑状态和设备输出，不承载滤镜参数。
VS_CACHE_SIZE_MB = 16000  # 保留用户指定阈值；空闲/重载时通过退出 worker 回收资源
RIFE_DISK_CACHE_BYTES = 8 * 1024**3  # 项目补帧缓存总额上限 8 GiB
VIDEO_SOURCE_CACHE = 1  # LWLibavSource 的 .lwi 索引缓存
UNTAGGED_HD_MIN_HEIGHT = 720  # 未标记 YUV 源按原始高度推断矩阵
FINAL_RESIZER = core.resize.Lanczos
FINAL_RESIZE_OPTIONS = {}  # 尺寸缩放保留源格式和位深
# 插帧会改变编辑时间轴的帧编号；请在开始编辑新素材前决定是否开启。
# 旧项目仅保存 trim 帧编号，修改此开关后须重新检查入出点。
RIFE_ENABLED = True
RIFE_TARGET_FPS = 60  # 只为低于此帧率的视频插帧；更高帧率保持原样
# 默认模型随实际加载的 RIFE DLL 定位，预览/导出和手动插件目录保持一致。
# 模型单独存放时，将整个 RIFE_MODEL_PATH 表达式替换为绝对 Path。
RIFE_MODEL_PATH = (
    Path(core.rife.plugin_path).resolve().parent
    / "models" / "rife-v4.6_ensembleFalse"
)

core.max_cache_size = VS_CACHE_SIZE_MB
job = load_job(assetmaker_job, for_export=False)


def rotate(clip, degrees):
    if degrees == 90:
        return core.std.FlipHorizontal(core.std.Transpose(clip))
    if degrees == 180:
        return core.std.Turn180(clip)
    if degrees == 270:
        return core.std.FlipVertical(core.std.Transpose(clip))
    return clip


def crop_safely(clip, crop, target_width, target_height):
    requested_width = crop["width"]
    requested_height = crop["height"]
    if requested_width <= 0 or requested_height <= 0:
        return clip

    x_step = 1 << clip.format.subsampling_w
    y_step = 1 << clip.format.subsampling_h
    left_limit = max(0, clip.width - x_step)
    top_limit = max(0, clip.height - y_step)
    left = min(max(0, crop["x"]), left_limit)
    top = min(max(0, crop["y"]), top_limit)
    left = left // x_step * x_step
    top = top // y_step * y_step
    width = min(requested_width, clip.width - left)
    height = min(requested_height, clip.height - top)

    target_aspect = target_width / target_height
    scale = min(
        width / float(requested_width),
        height / float(requested_height),
    )
    width = int(min(width, round(requested_width * scale)))
    height = int(min(height, round(width / target_aspect)))
    width = int(min(width, round(height * target_aspect)))
    width = width // x_step * x_step
    height = height // y_step * y_step
    if width < x_step or height < y_step:
        return clip
    return core.std.CropAbs(
        clip,
        width=width,
        height=height,
        left=left,
        top=top,
    )


def process_source(clip):
    """只处理 output 0；output 1 始终保留完整编辑画布。

    按素材需要在这里加入 CPU 降噪或去色带。下面两例只适用于插件支持的
    planar YUV 整数格式；RGB 图片须先显式转换格式，勿直接取消注释。
    # clip = core.dfttest.DFTTest(clip, sigma=2.0, tbsize=1)
    # clip = core.neo_f3kdb.Deband(
    #     clip, range=15, y=24, cb=24, cr=24, grainy=0, grainc=0
    # )
    启用时把对应插件函数加入文件头的 assetmaker-requires。默认不改变源画面。
    """
    return clip


def fill_missing_source_colour(clip, *, kind, original_height):
    """仅为未标记源提供解释，不覆盖解码器已有的颜色属性。"""
    family = clip.format.color_family
    if family == vs.YUV:
        is_hd = original_height >= UNTAGGED_HD_MIN_HEIGHT
        defaults = {
            "_Matrix": 1 if is_hd else 6,
            "_Transfer": 1 if is_hd else 6,
            "_Primaries": 1 if is_hd else 6,
            "_Range": 0,
        }
    elif family == vs.RGB:
        defaults = {
            "_Matrix": 0,
            "_Transfer": 13 if kind == "image" else 1,
            "_Primaries": 1,
            "_Range": 1,
        }
    else:
        # GRAY 没有 YUV 矩阵；亮度默认值只用于未标记的输入。
        defaults = {
            "_Transfer": 13 if kind == "image" else 1,
            "_Primaries": 1,
            "_Range": 1 if kind == "image" else 0,
        }

    frame = clip.get_frame(0)
    try:
        missing = {
            name: value
            for name, value in defaults.items()
            if name not in frame.props or int(frame.props[name]) == 2
        }
    finally:
        frame.close()
    return core.std.SetFrameProps(clip, **missing) if missing else clip


def interpolate_timeline(clip, *, kind):
    """在 trim 前建立真实 RIFE 时间轴；尾帧取整最多缩短一个目标帧。"""
    if not RIFE_ENABLED or kind != "video" or clip.num_frames < 2:
        return clip
    if type(RIFE_TARGET_FPS) is not int or RIFE_TARGET_FPS <= 0:
        raise RuntimeError("RIFE_TARGET_FPS 必须是正整数")
    if clip.fps_num <= 0 or clip.fps_den <= 0:
        raise RuntimeError("RIFE 要求源视频具有固定的正帧率")
    source_fps = Fraction(clip.fps_num, clip.fps_den)
    if source_fps >= RIFE_TARGET_FPS:
        return clip
    rife = getattr(getattr(core, "rife", None), "RIFE", None)
    if not callable(rife):
        raise RuntimeError(
            "已启用 RIFE，但缺少 rife.RIFE 插件；请将 RIFE DLC 解压到应用目录，"
            "确认 tools/vs-rife/rife/librife_windows_x86-64.dll 存在"
        )
    model_path = Path(RIFE_MODEL_PATH).resolve()
    if not model_path.is_dir():
        raise RuntimeError(f"已启用 RIFE，但模型目录不存在: {model_path}")
    missing_models = [
        name for name in ("flownet.bin", "flownet.param")
        if not (model_path / name).is_file()
    ]
    if missing_models:
        raise RuntimeError(
            f"已启用 RIFE，但模型文件缺失: {', '.join(missing_models)}；"
            f"请完整解压 RIFE DLC 到应用目录: {model_path}"
        )
    source_count = clip.num_frames
    # RIFE-ncnn-Vulkan 只接受 planar 32-bit float RGB。已补齐的源颜色
    # 属性决定矩阵/transfer；保持源 transfer，避免先转到设备 BT.601。
    rgb = core.resize.Lanczos(
        clip, format=vs.RGBS, range_s="full", dither_type="none"
    )
    expected_count = int(source_count * RIFE_TARGET_FPS / source_fps)

    def compute():
        try:
            interpolated = rife(
                rgb, fps_num=RIFE_TARGET_FPS, fps_den=1,
                model_path=str(model_path),
            )
        except Exception as exc:
            raise RuntimeError(
                "RIFE 插帧失败；请确认 Vulkan GPU/驱动及模型可用: " + str(exc)
            ) from exc
        if (Fraction(interpolated.fps_num, interpolated.fps_den) != RIFE_TARGET_FPS
                or interpolated.num_frames != expected_count):
            raise RuntimeError("RIFE 输出帧率/帧数不符合目标帧率插帧契约")
        return interpolated

    # 磁盘命中时不创建 RIFE 实例；预览/导出跨进程复用同一组完整帧。
    return cached_rife(
        core, rgb, compute, project_root=job["project_root"],
        source_path=job["source"]["path"], rotation=job["transform"]["rotation"],
        target_fps=RIFE_TARGET_FPS, model_path=model_path,
        settings={
            # 仅包含补帧上游策略；trim/crop/输出尺寸变更复用同一组帧。
            "input_colour_policy": 1,
            "untagged_hd_min_height": UNTAGGED_HD_MIN_HEIGHT,
            "rgb_resize": ["Lanczos", "RGBS", "full", "none"],
            "plugin_version": list(core.rife.version),
            "plugin_sha256": hashlib.sha256(
                Path(core.rife.plugin_path).read_bytes()
            ).hexdigest(),
        },
        max_bytes=RIFE_DISK_CACHE_BYTES, num_frames=expected_count,
    )



source = job["source"]
timeline = job["timeline"]
transform = job["transform"]
output = job["output"]

if source["kind"] == "video":
    cache_dir = Path(job["paths"]["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    clip = core.lsmas.LWLibavSource(
        source["path"],
        cache=VIDEO_SOURCE_CACHE,
        cachefile=str(cache_dir / f"source-{job['epoch']}.lwi"),
    )
else:
    clip = core.imwri.Read(source["path"])
    clip = clip[:1]
    fps = timeline["fps"]
    clip = core.std.AssumeFPS(
        clip,
        fpsnum=fps["numerator"],
        fpsden=fps["denominator"],
    )

clip = fill_missing_source_colour(
    clip, kind=source["kind"], original_height=clip.height
)
clip = rotate(clip, transform["rotation"])
clip = interpolate_timeline(clip, kind=source["kind"])
# output 2 暴露补帧缓存节点；output 1 与 output 0 共享此节点，不另建推理图。
clip.set_output(2)

if source["kind"] == "image":
    virtual_count = source["virtual_frame_count"]
    clip = core.std.Loop(clip, times=virtual_count)[:virtual_count]

# output 1 保留完整编辑时间轴（开启时包含 RIFE），坐标空间已应用 rotation。
clip.set_output(1)

if timeline["end_frame"] is not None:
    clip = clip[timeline["start_frame"] : timeline["end_frame"]]

clip = crop_safely(
    clip,
    transform["crop"],
    output["display_width"],
    output["display_height"],
)

clip = process_source(clip)

# 尺寸缩放不指定 format，保留源格式/位深及颜色属性。
clip = FINAL_RESIZER(
    clip,
    width=output["display_width"],
    height=output["display_height"],
    **FINAL_RESIZE_OPTIONS,
)

# 输出处理结果，保留颜色属性和精度；设备格式由导出适配层转换。
clip.set_output(0)
