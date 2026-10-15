//! Render module
//!
//! Contains transition effects and overlay rendering.

pub mod bezier;
pub mod image_loader;
mod overlay;
pub mod text_renderer;
mod transition;

pub use bezier::*;
pub use image_loader::{
    generate_barcode, generate_vertical_barcode, generate_vertical_barcode_gradient, ImageLoader,
};
pub use overlay::OverlayRenderer;
pub use text_renderer::{render_text_rotated_90, render_top_right_bar_text_rotated};
pub use transition::TransitionRenderer;
