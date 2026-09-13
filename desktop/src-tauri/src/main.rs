#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

fn open_external(url: &tauri::Url) {
    if !matches!(url.scheme(), "http" | "https") {
        return;
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::Shell::ShellExecuteW;
        let address: Vec<u16> = url.as_str().encode_utf16().chain(Some(0)).collect();
        let operation: Vec<u16> = "open".encode_utf16().chain(Some(0)).collect();
        unsafe {
            ShellExecuteW(
                std::ptr::null_mut(),
                operation.as_ptr(),
                address.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
            );
        }
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new(if cfg!(target_os = "macos") {
            "open"
        } else {
            "xdg-open"
        })
        .arg(url.as_str())
        .spawn();
    }
}

#[cfg(windows)]
mod job {
    use std::{io, mem, os::windows::io::AsRawHandle, process::Child};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE},
        System::JobObjects::*,
    };
    pub struct Job(HANDLE);
    // The handle is owned, never borrowed, and accessed only during construction/drop.
    unsafe impl Send for Job {}
    impl Job {
        pub fn attach(child: &Child) -> io::Result<Self> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let job = Self(handle);
                let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = mem::zeroed();
                limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as _,
                    mem::size_of_val(&limits) as u32,
                ) == 0
                    || AssignProcessToJobObject(handle, child.as_raw_handle() as _) == 0
                {
                    return Err(io::Error::last_os_error());
                }
                Ok(job)
            }
        }
    }
    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::{
            os::windows::process::CommandExt,
            process::Command,
            time::{Duration, Instant},
        };

        #[test]
        fn closing_the_job_terminates_its_owned_process() {
            let mut child = Command::new("powershell.exe")
                .args([
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Start-Sleep -Seconds 60",
                ])
                .creation_flags(0x08000000)
                .spawn()
                .unwrap();
            let job = match Job::attach(&child) {
                Ok(job) => job,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    panic!("{error}");
                }
            };
            drop(job);
            let deadline = Instant::now() + Duration::from_secs(5);
            while child.try_wait().unwrap().is_none() {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    panic!("Owned process survived closing the job");
                }
                std::thread::sleep(Duration::from_millis(25));
            }
        }
    }
}

struct Backend {
    child: Child,
    #[cfg(windows)]
    _job: job::Job,
}
struct Runtime {
    backend: Mutex<Option<Backend>>,
    exiting: AtomicBool,
}

fn status(app: &tauri::AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("splash") {
        let value = serde_json::to_string(message).unwrap();
        let _ = window.eval(&format!(
            "document.getElementById('status').textContent={value}"
        ));
    }
}

fn payload(app: &tauri::AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../.open-agent-world/desktop-payload")
            .canonicalize()?);
    }
    Ok(app.path().resource_dir()?.join("payload"))
}

fn launch(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let root = payload(&app)?;
    let python = root.join(if cfg!(windows) {
        "python/python.exe"
    } else {
        "python/bin/python3"
    });
    if !python.is_file() || !root.join("frontend/dist/index.html").is_file() {
        return Err("Application files are missing. Reinstall Open Agent World.".into());
    }
    let mut command = Command::new(python);
    command
        .current_dir(&root)
        .args([
            "-I",
            "-B",
            "launch.py",
            "--mode",
            "production",
            "--desktop",
            "--frontend",
            "frontend/dist",
        ])
        .env("OPEN_AGENT_WORLD_MODE", "production")
        .env_remove("OPEN_AGENT_WORLD_DATA_ROOT")
        .env_remove("OPEN_AGENT_WORLD_PLUGIN_DIRS")
        .env(
            "PATH",
            format!(
                "{}{}{}",
                root.join("tools").display(),
                if cfg!(windows) { ";" } else { ":" },
                std::env::var("PATH").unwrap_or_default()
            ),
        )
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command.spawn()?;
    #[cfg(windows)]
    let job = match job::Job::attach(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error.into());
        }
    };
    let output = child.stdout.take().unwrap();
    let errors = child.stderr.take().unwrap();
    let backend = Backend {
        child,
        #[cfg(windows)]
        _job: job,
    };
    *app.state::<Runtime>().backend.lock().unwrap() = Some(backend);
    // Drain stderr so a verbose backend cannot block on a full pipe. Python logs to disk.
    std::thread::spawn(
        move || {
            for _ in BufReader::new(errors).lines().map_while(Result::ok) {}
        },
    );
    let mut ready = false;
    for line in BufReader::new(output).lines().map_while(Result::ok) {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else {
            continue;
        };
        if value["event"] == "starting" {
            if let Some(window) = app.get_webview_window("splash") {
                let path = serde_json::to_string(&value["log"]).unwrap();
                let _ = window.eval(&format!(
                    "document.getElementById('log').textContent={path}"
                ));
            }
        }
        if value["event"] == "ready" && !ready {
            let url: tauri::Url = value["url"]
                .as_str()
                .ok_or("Invalid backend address")?
                .parse()?;
            if url.scheme() != "http" || url.host_str() != Some("127.0.0.1") || url.port().is_none()
            {
                return Err("Invalid backend address".into());
            }
            let origin = url.origin();
            WebviewWindowBuilder::new(&app, "main", WebviewUrl::External(url))
                .title("Open Agent World")
                .inner_size(1440.0, 940.0)
                .min_inner_size(860.0, 600.0)
                .devtools(false)
                .on_navigation(move |target| {
                    if target.origin() == origin {
                        true
                    } else {
                        open_external(target);
                        false
                    }
                })
                .on_new_window(|target, _| {
                    open_external(&target);
                    tauri::webview::NewWindowResponse::Deny
                })
                .build()?;
            if let Some(splash) = app.get_webview_window("splash") {
                let _ = splash.close();
            }
            ready = true;
        }
    }
    if !app.state::<Runtime>().exiting.load(Ordering::SeqCst) {
        if ready {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window
                    .set_title("Open Agent World · Backend stopped — restart the application");
            }
        } else {
            return Err("The backend could not start. Check the log shown below, then reopen the application.".into());
        }
    }
    Ok(())
}

fn stop(app: &tauri::AppHandle) {
    if let Some(mut backend) = app.state::<Runtime>().backend.lock().unwrap().take() {
        if let Some(mut input) = backend.child.stdin.take() {
            let _ = input.write_all(b"shutdown\n");
        }
        let deadline = Instant::now() + Duration::from_secs(40);
        loop {
            if matches!(backend.child.try_wait(), Ok(Some(_))) {
                break;
            }
            if Instant::now() >= deadline {
                let _ = backend.child.kill();
                let _ = backend.child.wait();
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        // Dropping the Windows job also cleans up any descendants after forced exit.
    }
}

fn main() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app
                .get_webview_window("main")
                .or_else(|| app.get_webview_window("splash"))
            {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(Runtime {
            backend: Mutex::new(None),
            exiting: AtomicBool::new(false),
        })
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if let Err(error) = launch(handle.clone()) {
                    status(&handle, &error.to_string());
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Cannot initialize the desktop window");
    application.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            if !app.state::<Runtime>().exiting.swap(true, Ordering::SeqCst) {
                api.prevent_exit();
                let handle = app.clone();
                std::thread::spawn(move || {
                    stop(&handle);
                    handle.exit(0);
                });
            }
        }
    });
}
