# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1

"""AssetMaker 内置 compatible pipeline。

output 1 是旋转后、trim/crop 前的完整编辑画布；output 0 严格按 RenderJob
生成处理帧；编码格式由导出模块决定。脚本有意保持自包含和可读，便于用户复制后定制。
"""

from pathlib import Path

import vapoursynth as vs

from assetmaker_vs.job_api import load_job


core = vs.core

# 可复制的默认处理参数。修改这些值或下方 process_source() 即可定制画质；
# RenderJob 只描述编辑状态和设备输出，不承载滤镜参数。
VS_CACHE_SIZE_MB = 4096
VIDEO_SOURCE_CACHE = 1  # LWLibavSource 的 .lwi 索引缓存
UNTAGGED_HD_MIN_HEIGHT = 720  # 未标记 YUV 源按原始高度推断矩阵
FINAL_RESIZER = core.resize.Lanczos
FINAL_RESIZE_OPTIONS = {}  # 尺寸缩放保留源格式和位深

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

if source["kind"] == "image":
    virtual_count = source["virtual_frame_count"]
    clip = core.std.Loop(clip, times=virtual_count)[:virtual_count]

# output 1 保留完整编辑时间轴，坐标空间已应用 rotation。
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
