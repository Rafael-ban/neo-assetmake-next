//! Video module
//!
//! Provides placeholder video frame generation and playback management.
//!
//! # Usage
//!
//! ```rust,ignore
//! use video::VideoPlayer;
//!
//! let mut player = VideoPlayer::default();
//! player.load_from_config(&config, &base_dir);
//!
//! // Read frames
//! if let Some(frame) = player.get_loop_current_frame() {
//!     // Use the frame
//! }
//! ```

mod decoder;
mod player;

pub use decoder::VideoDecoder;
pub use player::VideoPlayer;
