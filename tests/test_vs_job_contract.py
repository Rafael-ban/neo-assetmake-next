import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from core.vs_runtime import (
    CropSpec,
    OutputSpec,
    PathSpec,
    RationalFPS,
    RenderJob,
    RenderJobError,
    SourceSpec,
    TimelineSpec,
    TransformSpec,
    load_render_job,
    write_render_job,
)


ROOT = Path(__file__).resolve().parents[1]
JOB_SCHEMA = json.loads(
    (ROOT / "schemas" / "vs_job.schema.json").read_text(encoding="utf-8")
)


def make_job(
    *,
    source_path=r"D:\media\loop.mp4",
    source_kind="video",
    virtual_frame_count=None,
    start_frame=0,
    end_frame=120,
    fps=RationalFPS(30_000, 1_001),
    track="loop",
    epoch=7,
    cache_dir=r"D:\cache\assetmaker",
    crop=CropSpec("post_rotation_source_pixels", 0, 0, 0, 0),
    profile="360x640",
):
    return RenderJob(
        api_version=1,
        epoch=epoch,
        track=track,
        project_root=r"D:\素材\黍",
        source=SourceSpec(
            path=source_path,
            kind=source_kind,
            virtual_frame_count=virtual_frame_count,
        ),
        timeline=TimelineSpec(
            start_frame=start_frame,
            end_frame=end_frame,
            fps=fps,
        ),
        transform=TransformSpec(rotation=90, crop=crop),
        output=OutputSpec.from_profile(profile),
        paths=PathSpec(cache_dir=cache_dir),
    )


class RenderJobContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()

    def test_schema_is_valid_and_rejects_unknown_nested_fields(self):
        Draft202012Validator.check_schema(JOB_SCHEMA)
        payload = make_job().to_dict()
        payload["timeline"]["guessed"] = True

        with self.assertRaises(ValidationError):
            Draft202012Validator(JOB_SCHEMA).validate(payload)
        with self.assertRaises(RenderJobError):
            RenderJob.from_dict(payload)

    def test_bootstrap_nulls_are_preview_only(self):
        bootstrap = make_job(end_frame=None, fps=None)

        bootstrap.validate(for_export=False)
        with self.assertRaises(RenderJobError):
            bootstrap.validate(for_export=True)

    def test_fraction_unicode_track_and_crop_space_roundtrip(self):
        job = make_job(source_path=r"D:\素材\黍\loop.mp4", track="intro")

        payload = job.to_dict()
        Draft202012Validator(JOB_SCHEMA).validate(payload)

        self.assertEqual(RenderJob.from_dict(payload), job)
        self.assertEqual(payload["track"], "intro")
        self.assertEqual(
            payload["timeline"]["fps"],
            {"numerator": 30_000, "denominator": 1_001},
        )
        self.assertEqual(
            payload["transform"]["crop"]["coordinate_space"],
            "post_rotation_source_pixels",
        )

    def test_output_is_derived_from_resolution_specs(self):
        compact = OutputSpec.from_profile("360x640")
        large = OutputSpec.from_profile("720x1080")

        self.assertEqual(
            (compact.display_width, compact.display_height), (360, 640)
        )
        self.assertEqual((compact.coded_width, compact.coded_height), (384, 640))
        self.assertEqual(
            (large.display_width, large.display_height), (720, 1080)
        )
        self.assertEqual((large.coded_width, large.coded_height), (720, 1080))
        with self.assertRaises(RenderJobError):
            OutputSpec.from_profile("1080x1920")

    def test_mismatched_profile_dimensions_are_rejected(self):
        job = make_job()
        invalid_output = replace(job.output, coded_width=360)

        with self.assertRaises(RenderJobError):
            replace(job, output=invalid_output).validate()

    def test_python_model_rejects_boolean_rotation_and_float_dimensions(self):
        job = make_job()
        invalid_jobs = (
            replace(job, transform=replace(job.transform, rotation=False)),
            replace(job, output=replace(job.output, display_width=360.0)),
        )

        for invalid in invalid_jobs:
            with self.subTest(job=invalid):
                with self.assertRaises(RenderJobError):
                    invalid.validate()

    def test_video_and_image_virtual_frame_rules_are_enforced(self):
        with self.assertRaises(RenderJobError):
            make_job(virtual_frame_count=120).validate()
        with self.assertRaises(RenderJobError):
            make_job(
                source_path=r"D:\media\still.png",
                source_kind="image",
                virtual_frame_count=None,
            ).validate()

        image = make_job(
            source_path=r"D:\media\still.png",
            source_kind="image",
            virtual_frame_count=240,
            end_frame=120,
        )
        image.validate(for_export=True)
        restored = RenderJob.from_dict(image.to_dict())
        self.assertEqual(restored.source.virtual_frame_count, 240)
        self.assertEqual(restored.timeline.end_frame, 120)

    def test_timeline_crop_rotation_and_paths_are_strict(self):
        invalid_jobs = (
            make_job(start_frame=-1),
            make_job(start_frame=8, end_frame=8),
            make_job(fps=RationalFPS(0, 1)),
            replace(make_job(), transform=replace(make_job().transform, rotation=45)),
            make_job(crop=CropSpec("post_rotation_source_pixels", -1, 0, 0, 0)),
            make_job(crop=CropSpec("post_rotation_source_pixels", 0, 0, 10, 0)),
            make_job(source_path=r"D:\media\..\loop.mp4"),
            replace(make_job(), project_root="relative"),
            replace(make_job(), paths=PathSpec(cache_dir="relative")),
        )
        for job in invalid_jobs:
            with self.subTest(job=job):
                with self.assertRaises(RenderJobError):
                    job.validate()

    def test_write_uses_epoch_filename_and_never_overwrites(self):
        job = make_job(cache_dir=str(self.root), epoch=42)

        path = write_render_job(job)
        before = path.read_bytes()

        self.assertEqual(path, self.root / "job-42.json")
        self.assertEqual(load_render_job(path, for_export=True), job)
        with self.assertRaises(RenderJobError):
            write_render_job(replace(job, track="intro"))
        self.assertEqual(path.read_bytes(), before)

    def test_load_reports_absolute_path_for_invalid_json(self):
        path = self.root / "job-1.json"
        path.write_text("{", encoding="utf-8")

        with self.assertRaises(RenderJobError) as raised:
            load_render_job(path)

        self.assertIn(str(path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
