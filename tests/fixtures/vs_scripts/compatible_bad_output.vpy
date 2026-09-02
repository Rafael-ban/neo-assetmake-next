# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires:
# assetmaker-editor-output: 1

import vapoursynth as vs

core = vs.core
editor = core.std.BlankClip(
    width=384,
    height=640,
    length=3,
    fpsnum=30000,
    fpsden=1001,
    format=vs.RGB24,
)
editor.set_output(1)
editor.set_output(0)
