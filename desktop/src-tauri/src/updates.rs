//! Updater control stays in the native shell; remote/plugin pages receive no IPC privileges.
use std::{
    process::{Command, Stdio},
    sync::atomic::Ordering,
    time::Duration,
};
use tauri::{
    menu::{MenuBuilder, SubmenuBuilder},
    Manager,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::UpdaterExt;

fn backup_command(app: &tauri::AppHandle, mode: &str) -> Result<std::process::Output, String> {
    let root = super::payload(app).map_err(|e| e.to_string())?;
    let python = root.join(if cfg!(windows) {
        "python/python.exe"
    } else {
        "python/bin/python3"
    });
    let mut command = Command::new(python);
    command
        .current_dir(&root)
        .args(["-I", "-B", "launch.py", mode])
        .env_remove("OPEN_AGENT_WORLD_DATA_ROOT")
        .env_remove("OPEN_AGENT_WORLD_PLUGIN_DIRS")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let output = command.output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_owned());
    }
    Ok(output)
}

pub fn setup(app: &tauri::AppHandle) -> tauri::Result<()> {
    let menu = MenuBuilder::new(app)
        .item(
            &SubmenuBuilder::new(app, "Open Agent World")
                .text("check-updates", "Check for updates / 检查更新")
                .separator()
                .quit()
                .build()?,
        )
        .item(
            &SubmenuBuilder::new(app, "Edit / 编辑")
                .undo()
                .redo()
                .separator()
                .cut()
                .copy()
                .paste()
                .select_all()
                .build()?,
        )
        .build()?;
    app.set_menu(menu)?;
    app.on_menu_event(|app, event| {
        if event.id().as_ref() == "check-updates" {
            check(app.clone(), false);
        }
    });
    Ok(())
}

pub fn check(app: tauri::AppHandle, quiet: bool) {
    if app.get_webview_window("main").is_none() {
        return;
    }
    if app
        .state::<super::Runtime>()
        .updating
        .swap(true, Ordering::SeqCst)
    {
        return;
    }
    std::thread::spawn(move || {
        let result = tauri::async_runtime::block_on(update(&app, quiet));
        if let Err(error) = result {
            app.dialog().message(format!("{error}\n\nYour workspace remains available. Resolve this issue, then try the update again.\n工作区仍可使用。请解决上述问题后重试更新。"))
                .title("Update could not complete / 更新未完成").blocking_show();
        }
        app.state::<super::Runtime>()
            .updating
            .store(false, Ordering::SeqCst);
    });
}

async fn update(app: &tauri::AppHandle, quiet: bool) -> Result<(), String> {
    if option_env!("OAW_UPDATER_ENABLED") != Some("1") {
        if !quiet {
            app.dialog().message("Automatic updates are not configured for this build. Open the official release page?\n此版本未配置自动更新，是否打开官方发布页？")
                .title("Open Agent World").buttons(MessageDialogButtons::OkCancel).show(|yes| {
                    if yes { super::open_external(&"https://github.com/theAfish/open-agent-world/releases".parse().unwrap()); }
                });
        }
        return Ok(());
    }
    let updater = app
        .updater_builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let checked = match updater.check().await {
        Ok(value) => value,
        Err(_) if quiet => return Ok(()),
        Err(error) => return Err(error.to_string()),
    };
    let Some(mut update) = checked else {
        if !quiet {
            app.dialog()
                .message("You are up to date.\n当前已是最新版本。")
                .title("Open Agent World")
                .blocking_show();
        }
        return Ok(());
    };
    if !app.dialog().message(format!("Version {} is available. Download it now? You can keep working until installation.\n发现新版本 {}，是否下载？下载期间可以继续工作。", update.version, update.version))
        .title("Open Agent World").buttons(MessageDialogButtons::OkCancel).blocking_show() { return Ok(()); }
    update.timeout = Some(Duration::from_secs(1800));
    let window = app.get_webview_window("main");
    let mut downloaded = 0usize;
    let bytes = update
        .download(
            |size, total| {
                downloaded += size;
                if let Some(ref window) = window {
                    let progress = total
                        .map(|total| format!("{}%", downloaded as u64 * 100 / total.max(1)))
                        .unwrap_or_else(|| format!("{} MB", downloaded / 1_000_000));
                    let _ = window.set_title(&format!(
                        "Open Agent World · Downloading update / 下载更新 {progress}"
                    ));
                }
            },
            || {},
        )
        .await;
    if let Some(ref window) = window {
        let _ = window.set_title("Open Agent World");
    }
    let bytes = bytes.map_err(|e| e.to_string())?; // Signature verified before stopping anything.
    if let Some(ref window) = window {
        let _ = window.set_title("Open Agent World · Checking backup space / 检查备份空间");
    }
    let preflight = backup_command(app, "--backup-preflight");
    if let Some(ref window) = window {
        let _ = window.set_title("Open Agent World");
    }
    preflight?; // Leave the running workspace available when space is insufficient.
    if !app.dialog().message("The update is verified. Save your drafts and finish active tasks before continuing. OAW will close, back up saved data, then install and restart.\n更新已验证。请先保存草稿并完成运行中的任务；继续后将关闭工作区、备份数据并安装重启。")
        .title("Open Agent World").buttons(MessageDialogButtons::OkCancel).blocking_show() { return Ok(()); }
    // Stop editing while the backend drains. Normal exit handling remains available.
    if let Some(ref window) = window {
        let _ = window.hide();
    }
    app.state::<super::Runtime>()
        .exiting
        .store(true, Ordering::SeqCst);
    let result = (|| -> Result<(), String> {
        if !super::stop(app) {
            return Err("Backend did not stop cleanly".into());
        }
        let backup = backup_command(app, "--backup-for-update")?;
        // Tell the user where the retained, verified backup lives before replacing the app.
        app.dialog()
            .message(format!(
                "Backup saved / 备份已保存:\n{}",
                String::from_utf8_lossy(&backup.stdout).trim()
            ))
            .title("Open Agent World")
            .blocking_show();
        update.install(bytes).map_err(|e| e.to_string())?;
        Ok(())
    })();
    if let Err(error) = result {
        app.dialog().message(format!("{error}\n\nInstallation was cancelled. Reopen OAW; your data directory was kept.\n安装已取消。请重新打开 OAW；原数据目录仍然保留。"))
            .title("Update could not complete / 更新未完成").blocking_show();
        app.exit(1);
        return Ok(());
    }
    app.restart();
}
