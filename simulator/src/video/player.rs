//! Video player module
//!
//! High-level video player that manages loop and intro videos.

use image::RgbImage;
use std::path::{Path, PathBuf};
use tracing::{error, info, warn};

use super::decoder::VideoDecoder;
use crate::config::EPConfig;

#[derive(Clone, Copy, Debug)]
struct TrackPreviewConfig {
    cropbox: Option<(u32, u32, u32, u32)>,
    rotation: i32,
    start_frame: u32,
    end_frame: Option<u32>,
}

impl TrackPreviewConfig {
    fn new(
        cropbox: Option<(u32, u32, u32, u32)>,
        rotation: i32,
        start_frame: Option<u32>,
        end_frame: Option<u32>,
        track_name: &str,
    ) -> Self {
        let start_frame = start_frame.unwrap_or(0);
        let end_frame = match end_frame {
            Some(end_frame) if end_frame < start_frame => {
                warn!(
                    "{} preview end frame {} is before start frame {}; ignoring end frame",
                    track_name, end_frame, start_frame
                );
                None
            }
            other => other,
        };

        Self {
            cropbox,
            rotation,
            start_frame,
            end_frame,
        }
    }

    fn contains_last_frame(&self, frame_index: u32) -> bool {
        self.end_frame
            .map(|end_frame| frame_index >= end_frame)
            .unwrap_or(false)
    }
}

/// Video player that manages playback of loop and intro videos
pub struct VideoPlayer {
    /// Loop video decoder
    loop_video: Option<VideoDecoder>,
    /// Intro video decoder
    intro_video: Option<VideoDecoder>,
    /// Current cached frame from loop video
    loop_current_frame: Option<RgbImage>,
    /// Last frame from intro video (for transition)
    intro_last_frame: Option<RgbImage>,
    /// Target width
    target_width: u32,
    /// Target height
    target_height: u32,
    /// Cropbox for loop video (x, y, w, h) in rotated video coordinates
    loop_preview: TrackPreviewConfig,
    /// Preview state for intro video
    intro_preview: TrackPreviewConfig,
    /// Current cached frame index for loop video
    loop_current_frame_index: Option<u32>,
    /// Current cached frame index for intro video
    intro_current_frame_index: Option<u32>,
}

impl VideoPlayer {
    /// Create a new video player with the given target dimensions
    pub fn new(
        target_width: u32,
        target_height: u32,
        loop_cropbox: Option<(u32, u32, u32, u32)>,
        loop_rotation: i32,
        loop_start_frame: Option<u32>,
        loop_end_frame: Option<u32>,
        intro_cropbox: Option<(u32, u32, u32, u32)>,
        intro_rotation: i32,
        intro_start_frame: Option<u32>,
        intro_end_frame: Option<u32>,
    ) -> Self {
        Self {
            loop_video: None,
            intro_video: None,
            loop_current_frame: None,
            intro_last_frame: None,
            target_width,
            target_height,
            loop_preview: TrackPreviewConfig::new(
                loop_cropbox,
                loop_rotation,
                loop_start_frame,
                loop_end_frame,
                "Loop",
            ),
            intro_preview: TrackPreviewConfig::new(
                intro_cropbox,
                intro_rotation,
                intro_start_frame,
                intro_end_frame,
                "Intro",
            ),
            loop_current_frame_index: None,
            intro_current_frame_index: None,
        }
    }

    /// Load videos from EPConfig, returns error description if loop video failed
    ///
    /// # Arguments
    /// * `config` - The EP configuration
    /// * `base_dir` - Base directory for resolving relative paths
    pub fn load_from_config(&mut self, config: &EPConfig, base_dir: &Path) -> Option<String> {
        info!("Loading videos from config, base_dir: {:?}", base_dir);
        self.loop_video = None;
        self.intro_video = None;
        self.loop_current_frame = None;
        self.intro_last_frame = None;
        self.loop_current_frame_index = None;
        self.intro_current_frame_index = None;

        // Load loop video
        if !config.loop_config.file.is_empty() {
            let loop_path = Self::resolve_path(&config.loop_config.file, base_dir);
            info!(
                "Loop video path: {:?} (exists: {})",
                loop_path,
                loop_path.exists()
            );
            info!(
                "Loop video preview: cropbox={:?}, rotation={}, start_frame={}, end_frame={:?}",
                self.loop_preview.cropbox,
                self.loop_preview.rotation,
                self.loop_preview.start_frame,
                self.loop_preview.end_frame
            );
            match VideoDecoder::open(
                &loop_path.to_string_lossy(),
                self.target_width,
                self.target_height,
                self.loop_preview.cropbox,
                self.loop_preview.rotation,
            ) {
                Ok(decoder) => {
                    info!("Loaded loop video successfully: {}", loop_path.display());
                    self.loop_video = Some(decoder);
                }
                Err(e) => {
                    let msg = format!(
                        "循环视频加载失败\n路径: {}\n原因: {}",
                        loop_path.display(),
                        e
                    );
                    error!("{}", msg);
                    return Some(msg);
                }
            }
        } else {
            return Some("未配置循环视频文件路径".to_string());
        }

        // Load intro video if enabled.
        if let Some(ref intro) = config.intro {
            if intro.enabled && !intro.file.is_empty() {
                let intro_path = Self::resolve_path(&intro.file, base_dir);
                info!(
                    "Intro video preview: cropbox={:?}, rotation={}, start_frame={}, end_frame={:?}",
                    self.intro_preview.cropbox,
                    self.intro_preview.rotation,
                    self.intro_preview.start_frame,
                    self.intro_preview.end_frame
                );
                match VideoDecoder::open(
                    &intro_path.to_string_lossy(),
                    self.target_width,
                    self.target_height,
                    self.intro_preview.cropbox,
                    self.intro_preview.rotation,
                ) {
                    Ok(decoder) => {
                        info!("Loaded intro video: {}", intro_path.display());
                        self.intro_video = Some(decoder);
                    }
                    Err(e) => {
                        warn!("Failed to load intro video: {}", e);
                    }
                }
            }
        }

        // Read first frame of loop video for initial display
        self.prime_loop_video();
        None
    }

    /// Resolve a potentially relative path against the base directory
    fn resolve_path(file_path: &str, base_dir: &Path) -> PathBuf {
        let path = Path::new(file_path);
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            base_dir.join(path)
        }
    }

    fn next_frame_index(current_frame_index: Option<u32>, start_frame: u32) -> u32 {
        current_frame_index
            .map(|frame_index| frame_index.saturating_add(1))
            .unwrap_or(start_frame)
    }

    fn prime_decoder(
        decoder: &mut VideoDecoder,
        preview: TrackPreviewConfig,
    ) -> Option<(RgbImage, u32)> {
        decoder.seek_to_start();

        for _ in 0..preview.start_frame {
            decoder.read_frame()?;
        }

        decoder
            .read_frame()
            .map(|frame| (frame, preview.start_frame))
    }

    fn prime_loop_video(&mut self) -> bool {
        if let Some(ref mut decoder) = self.loop_video {
            if let Some((frame, frame_index)) = Self::prime_decoder(decoder, self.loop_preview) {
                self.loop_current_frame = Some(frame);
                self.loop_current_frame_index = Some(frame_index);
                return true;
            }
        }

        self.loop_current_frame = None;
        self.loop_current_frame_index = None;
        false
    }

    fn prime_intro_video(&mut self) -> bool {
        if let Some(ref mut decoder) = self.intro_video {
            if let Some((frame, frame_index)) = Self::prime_decoder(decoder, self.intro_preview) {
                self.intro_last_frame = Some(frame);
                self.intro_current_frame_index = Some(frame_index);
                return true;
            }
        }

        self.intro_last_frame = None;
        self.intro_current_frame_index = None;
        false
    }

    /// Check if intro video is available
    pub fn has_intro(&self) -> bool {
        self.intro_video.is_some()
    }

    /// Check if loop video is available
    pub fn has_loop(&self) -> bool {
        self.loop_video.is_some()
    }

    /// Advance to the next frame in the loop video
    ///
    /// Updates the internal cache without returning a clone.
    /// Loops automatically when reaching the end.
    /// Returns true if a frame was successfully read.
    pub fn advance_loop_frame(&mut self) -> bool {
        if let Some(frame_index) = self.loop_current_frame_index {
            if self.loop_preview.contains_last_frame(frame_index) {
                return self.prime_loop_video();
            }
        }

        let next_frame = if let Some(ref mut decoder) = self.loop_video {
            decoder.read_frame()
        } else {
            return false;
        };

        match next_frame {
            Some(frame) => {
                self.loop_current_frame = Some(frame);
                self.loop_current_frame_index = Some(Self::next_frame_index(
                    self.loop_current_frame_index,
                    self.loop_preview.start_frame,
                ));
                true
            }
            None => self.prime_loop_video(),
        }
    }

    /// Advance to the next frame in the intro video
    ///
    /// Updates the internal cache without returning a clone.
    /// Returns true if a frame was read, false when the intro video ends (no looping).
    pub fn advance_intro_frame(&mut self) -> bool {
        if let Some(frame_index) = self.intro_current_frame_index {
            if self.intro_preview.contains_last_frame(frame_index) {
                return false;
            }
        }

        if let Some(ref mut decoder) = self.intro_video {
            match decoder.read_frame() {
                Some(frame) => {
                    self.intro_last_frame = Some(frame);
                    self.intro_current_frame_index = Some(Self::next_frame_index(
                        self.intro_current_frame_index,
                        self.intro_preview.start_frame,
                    ));
                    true
                }
                None => false,
            }
        } else {
            false
        }
    }

    /// Get the last frame from the intro video
    ///
    /// Useful for transition effects after intro ends
    pub fn get_intro_last_frame(&self) -> Option<&RgbImage> {
        self.intro_last_frame.as_ref()
    }

    /// Get the current cached loop frame
    pub fn get_loop_current_frame(&self) -> Option<&RgbImage> {
        self.loop_current_frame.as_ref()
    }

    /// Seek intro video to start
    pub fn seek_intro_to_start(&mut self) {
        self.prime_intro_video();
    }

    /// Seek loop video to start
    pub fn seek_loop_to_start(&mut self) {
        self.prime_loop_video();
    }

    /// Reset both videos to start
    pub fn reset(&mut self) {
        self.seek_intro_to_start();
        self.seek_loop_to_start();
    }

    /// Get the FPS of the loop video
    pub fn loop_fps(&self) -> f64 {
        self.loop_video.as_ref().map(|d| d.fps()).unwrap_or(30.0)
    }

    /// Get the FPS of the intro video
    pub fn intro_fps(&self) -> f64 {
        self.intro_video.as_ref().map(|d| d.fps()).unwrap_or(30.0)
    }

    /// Create a black frame with the target dimensions
    pub fn create_black_frame(&self) -> RgbImage {
        image::RgbImage::from_pixel(self.target_width, self.target_height, image::Rgb([0, 0, 0]))
    }
}

impl Default for VideoPlayer {
    fn default() -> Self {
        Self::new(360, 640, None, 0, None, None, None, 0, None, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_video_player_new() {
        let player = VideoPlayer::new(360, 640, None, 0, None, None, None, 0, None, None);
        assert!(!player.has_intro());
        assert!(!player.has_loop());
    }

    #[test]
    fn test_create_black_frame() {
        let player = VideoPlayer::new(360, 640, None, 0, None, None, None, 0, None, None);
        let frame = player.create_black_frame();
        assert_eq!(frame.width(), 360);
        assert_eq!(frame.height(), 640);
    }

    #[test]
    fn test_track_preview_config_ignores_invalid_end_frame() {
        let preview = TrackPreviewConfig::new(None, 0, Some(12), Some(4), "Loop");
        assert_eq!(preview.start_frame, 12);
        assert_eq!(preview.end_frame, None);
    }
}
