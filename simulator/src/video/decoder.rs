//! Video decoder module
//!
//! Provides a lightweight placeholder frame source for video previews.

use anyhow::{bail, Result};
use image::{Rgb, RgbImage};
use std::path::Path;

const PLACEHOLDER_FPS: f64 = 30.0;
const BLACK_PIXEL: Rgb<u8> = Rgb([0, 0, 0]);

/// Placeholder video decoder that emits target-sized preview frames.
pub struct VideoDecoder {
    /// Target width for generated frames.
    target_width: u32,
    /// Target height for generated frames.
    target_height: u32,
    /// Synthetic preview FPS.
    fps: f64,
    /// Number of placeholder frames emitted since the last seek/reset.
    frame_index: u64,
}

impl VideoDecoder {
    /// Open a video path for placeholder preview generation.
    ///
    /// The current implementation intentionally validates only file presence and
    /// does not parse media contents. A real decoder can be connected behind this
    /// API later without changing `VideoPlayer`.
    ///
    /// # Arguments
    /// * `path` - Path to the video file.
    /// * `target_width` - Target width for generated frames.
    /// * `target_height` - Target height for generated frames.
    /// * `_cropbox` - Reserved for the future decoder implementation.
    /// * `_rotation` - Reserved for the future decoder implementation.
    pub fn open(
        path: &str,
        target_width: u32,
        target_height: u32,
        _cropbox: Option<(u32, u32, u32, u32)>,
        _rotation: i32,
    ) -> Result<Self> {
        let path_obj = Path::new(path);

        if !path_obj.exists() {
            bail!("Video file not found: {}", path);
        }

        if !path_obj.is_file() {
            bail!("Video path is not a file: {}", path);
        }

        Ok(Self {
            target_width,
            target_height,
            fps: PLACEHOLDER_FPS,
            frame_index: 0,
        })
    }

    /// Read the next placeholder frame.
    pub fn read_frame(&mut self) -> Option<RgbImage> {
        self.frame_index = self.frame_index.saturating_add(1);
        Some(RgbImage::from_pixel(
            self.target_width,
            self.target_height,
            BLACK_PIXEL,
        ))
    }

    /// Seek to the beginning of the placeholder stream.
    pub fn seek_to_start(&mut self) {
        self.frame_index = 0;
    }

    /// Get the synthetic preview FPS.
    pub fn fps(&self) -> f64 {
        self.fps
    }

    /// Get the target (output) width.
    pub fn target_width(&self) -> u32 {
        self.target_width
    }

    /// Get the target (output) height.
    pub fn target_height(&self) -> u32 {
        self.target_height
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_path(file_name: &str) -> std::path::PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock should be after Unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!(
            "neo_assetmaker_decoder_test_{}_{}_{}",
            std::process::id(),
            unique,
            file_name
        ))
    }

    #[test]
    fn test_decoder_nonexistent() {
        let path = temp_path("nonexistent.mp4");
        let result = VideoDecoder::open(&path.to_string_lossy(), 360, 640, None, 0);
        assert!(result.is_err());
    }

    #[test]
    fn test_decoder_existing_file_returns_placeholder_frame_with_target_size() {
        let path = temp_path("placeholder.bin");
        fs::write(&path, b"placeholder video contents").expect("test file should be writable");

        let mut decoder =
            VideoDecoder::open(&path.to_string_lossy(), 123, 45, Some((1, 2, 3, 4)), 90)
                .expect("existing file should open with placeholder decoder");
        let frame = decoder
            .read_frame()
            .expect("placeholder decoder should return a frame");

        assert_eq!(frame.width(), 123);
        assert_eq!(frame.height(), 45);
        assert!(frame.pixels().all(|pixel| pixel.0 == [0, 0, 0]));

        let _ = fs::remove_file(path);
    }
}
