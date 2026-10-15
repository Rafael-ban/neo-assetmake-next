"""Lightweight media helpers kept for backward-compatible imports."""

from __future__ import annotations

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable, Optional

import cv2
import numpy as np

from core.video_processor import VideoProcessor

logger = logging.getLogger(__name__)


class OptimizedVideoProcessor:
    """Backward-compatible processor wrapper.

    Frame-level video decoding was retired with the external media toolchain
    migration. This class remains available for callers that only need image
    loading, metadata probing, caching, or graceful unsupported-operation
    callbacks.
    """

    def __init__(self, max_workers: int = 4, cache_size: int = 32):
        self.max_workers = max_workers
        self.cache_size = cache_size
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = Lock()
        self._cache = {}
        self._cache_order = []

    def process_frame(self, frame_path: str, timestamp: float) -> Optional[np.ndarray]:
        try:
            frame = cv2.imread(frame_path)
            if frame is None:
                logger.warning("Unable to read frame: %s", frame_path)
                return None
            return frame
        except Exception as exc:
            logger.error("Frame processing failed: %s", exc)
            return None

    def process_video_async(
        self,
        video_path: str,
        callback: Callable[[list[np.ndarray]], None],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Future:
        def process() -> None:
            logger.warning("Frame extraction is no longer handled by OptimizedVideoProcessor")
            if progress_callback:
                progress_callback(0, 0)
            callback([])

        return self.executor.submit(process)

    def extract_frames(
        self,
        video_path: str,
        frame_indices: list[int],
        callback: Callable[[list[tuple[int, Optional[np.ndarray]]]], None],
    ) -> Future:
        def process() -> None:
            logger.warning("Frame extraction is no longer handled by OptimizedVideoProcessor")
            callback([(index, None) for index in frame_indices])

        return self.executor.submit(process)

    def process_video_stream(
        self,
        video_path: str,
        frame_processor: Callable[[np.ndarray], np.ndarray],
        output_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        raise RuntimeError(
            "Frame-level video processing has moved to the external media pipeline"
        )

    def resize_video(
        self,
        video_path: str,
        output_path: str,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
        scale_factor: Optional[float] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        raise RuntimeError("Video resizing is handled by the export pipeline")

    def get_video_info(self, video_path: str) -> dict:
        cache_key = f"info:{video_path}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        info = VideoProcessor().get_video_info(video_path)
        if info is None:
            return {}

        result = {
            "fps": info.fps,
            "frame_count": info.total_frames,
            "width": info.width,
            "height": info.height,
            "duration": info.duration,
        }
        with self._lock:
            self._cache[cache_key] = result
            self._cache_order.append(cache_key)
            if len(self._cache_order) > self.cache_size:
                oldest_key = self._cache_order.pop(0)
                self._cache.pop(oldest_key, None)
        return result

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_order.clear()
            logger.info("Video processor cache cleared")

    def cleanup(self) -> None:
        self.executor.shutdown(wait=True)
        self.clear_cache()
        logger.info("Video processor cleaned up")


class LargeFileProcessor:
    """Chunked large-file processor."""

    def __init__(self, chunk_size: int = 1024 * 1024):
        self.chunk_size = chunk_size

    def process_large_file(
        self,
        file_path: str,
        processor: Callable[[bytes], None],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        file_size = os.path.getsize(file_path)
        processed_size = 0

        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(self.chunk_size)
                if not chunk:
                    break
                processor(chunk)
                processed_size += len(chunk)
                if progress_callback:
                    progress_callback(processed_size, file_size)


_global_video_processor: Optional[OptimizedVideoProcessor] = None
_global_file_processor: Optional[LargeFileProcessor] = None


def get_video_processor(
    max_workers: int = 4,
    cache_size: int = 32,
) -> OptimizedVideoProcessor:
    """Return the shared backward-compatible processor."""
    global _global_video_processor
    if _global_video_processor is None:
        _global_video_processor = OptimizedVideoProcessor(max_workers, cache_size)
    return _global_video_processor


def cleanup_video_processor() -> None:
    """Clean up the shared processor."""
    global _global_video_processor
    if _global_video_processor is not None:
        _global_video_processor.cleanup()
        _global_video_processor = None


def get_file_processor(chunk_size: int = 1024 * 1024) -> LargeFileProcessor:
    """Return the shared large-file processor."""
    global _global_file_processor
    if _global_file_processor is None:
        _global_file_processor = LargeFileProcessor(chunk_size)
    return _global_file_processor


def cleanup_processors() -> None:
    """Clean up shared processors."""
    global _global_file_processor
    cleanup_video_processor()
    _global_file_processor = None
