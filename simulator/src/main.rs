//! Arknights Electronic Pass Simulator
//!
//! A real device preview emulator for the Arknights Pass Material Editor.
//! Supports standalone execution or IPC communication with the Python editor.

mod animation;
mod app;
mod config;
mod ipc;
mod render;
mod utils;
mod video;

use anyhow::Result;
use clap::Parser;
use std::path::PathBuf;
use tracing::{info, Level};
use tracing_subscriber::FmtSubscriber;

use app::SimulatorApp;
use config::EPConfig;

/// Arknights Electronic Pass Simulator
#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
struct Args {
    /// Path to epconfig.json configuration file
    #[arg(short, long)]
    config: Option<PathBuf>,

    /// Base directory for asset files
    #[arg(short, long)]
    base_dir: Option<PathBuf>,

    /// Application directory (for program resources like modular assets)
    #[arg(long)]
    app_dir: Option<PathBuf>,

    /// Named pipe name for IPC communication (Windows)
    #[arg(long)]
    pipe: Option<String>,

    /// Use stdin/stdout for IPC communication
    #[arg(long)]
    stdio: bool,

    /// Cropbox in format "x,y,w,h" (rotated video coordinates)
    #[arg(long)]
    cropbox: Option<String>,

    /// Loop cropbox in format "x,y,w,h" (rotated video coordinates)
    #[arg(long)]
    loop_cropbox: Option<String>,

    /// Intro cropbox in format "x,y,w,h" (rotated video coordinates)
    #[arg(long)]
    intro_cropbox: Option<String>,

    /// Video rotation in degrees (0, 90, 180, 270)
    #[arg(long, default_value = "0")]
    rotation: i32,

    /// Loop video rotation in degrees (0, 90, 180, 270)
    #[arg(long)]
    loop_rotation: Option<i32>,

    /// Intro video rotation in degrees (0, 90, 180, 270)
    #[arg(long)]
    intro_rotation: Option<i32>,

    /// Loop preview start frame
    #[arg(long)]
    loop_start_frame: Option<u32>,

    /// Loop preview end frame (inclusive)
    #[arg(long)]
    loop_end_frame: Option<u32>,

    /// Intro preview start frame
    #[arg(long)]
    intro_start_frame: Option<u32>,

    /// Intro preview end frame (inclusive)
    #[arg(long)]
    intro_end_frame: Option<u32>,

    /// Enable debug logging
    #[arg(short, long)]
    debug: bool,

    /// Theme mode to match main application ("dark" or "light")
    #[arg(long, default_value = "dark")]
    theme: String,
}

fn parse_cropbox(value: Option<&str>) -> Option<(u32, u32, u32, u32)> {
    value.and_then(|s| {
        let parts: Vec<u32> = s.split(',').filter_map(|p| p.parse().ok()).collect();
        if parts.len() == 4 {
            Some((parts[0], parts[1], parts[2], parts[3]))
        } else {
            None
        }
    })
}

fn main() -> Result<()> {
    let args = Args::parse();

    // Initialize logging
    let level = if args.debug {
        Level::DEBUG
    } else {
        Level::INFO
    };
    let subscriber = FmtSubscriber::builder()
        .with_max_level(level)
        .with_writer(std::io::stderr)
        .finish();
    tracing::subscriber::set_global_default(subscriber)?;

    info!("Arknights Pass Simulator starting...");

    // Load configuration if provided
    let (initial_config, config_error) = if let Some(config_path) = &args.config {
        info!("Loading config from: {:?}", config_path);
        match EPConfig::load_from_file(config_path) {
            Ok(config) => {
                info!("Config loaded successfully:");
                info!("  - name: {:?}", config.name);
                info!("  - loop.file: {:?}", config.loop_config.file);
                info!("  - intro: {:?}", config.intro.as_ref().map(|i| &i.file));
                (Some(config), None)
            }
            Err(e) => {
                tracing::error!("Failed to load config: {:?}", e);
                (
                    None,
                    Some(format!("配置加载失败: {:?}\n路径: {:?}", e, config_path)),
                )
            }
        }
    } else {
        (None, None)
    };

    let base_dir = args.base_dir.unwrap_or_else(|| {
        args.config
            .as_ref()
            .and_then(|p| p.parent())
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("."))
    });
    info!("Base directory: {:?}", base_dir);

    // Determine app_dir for program resources (modular assets, etc.)
    let app_dir = args.app_dir.unwrap_or_else(|| {
        // Default to the directory containing the executable
        std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."))
    });
    info!("App directory: {:?}", app_dir);

    // Create native options for eframe
    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([420.0, 860.0])
            .with_min_inner_size([380.0, 720.0])
            .with_resizable(true)
            .with_title("Arknights Pass Simulator"),
        ..Default::default()
    };

    let loop_cropbox = parse_cropbox(args.loop_cropbox.as_deref().or(args.cropbox.as_deref()));
    let intro_cropbox = parse_cropbox(args.intro_cropbox.as_deref());
    let loop_rotation = args.loop_rotation.unwrap_or(args.rotation);
    let intro_rotation = args.intro_rotation.unwrap_or(0);
    let is_dark_theme = args.theme != "light";

    // Run the application
    eframe::run_native(
        "Arknights Pass Simulator",
        native_options,
        Box::new(move |cc| {
            Ok(Box::new(SimulatorApp::new(
                cc,
                initial_config,
                base_dir,
                app_dir,
                args.pipe,
                args.stdio,
                loop_cropbox,
                loop_rotation,
                args.loop_start_frame,
                args.loop_end_frame,
                intro_cropbox,
                intro_rotation,
                args.intro_start_frame,
                args.intro_end_frame,
                is_dark_theme,
                config_error,
            )))
        }),
    )
    .map_err(|e| anyhow::anyhow!("eframe error: {}", e))?;

    Ok(())
}
