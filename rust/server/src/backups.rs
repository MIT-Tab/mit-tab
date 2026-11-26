use std::{
    env, fs,
    io::ErrorKind,
    os::unix::fs::{DirBuilderExt, OpenOptionsExt},
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use axum::body::Bytes;
use chrono::Utc;
use tokio::{process::Command, time::timeout};

use crate::{
    backup_objects::{ObjectStore, Put},
    passwords,
};

pub struct Backups {
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
    pub ca: Option<PathBuf>,
    pub prefix: PathBuf,
    pub s3: bool,
}

#[derive(Clone, Copy)]
pub enum Trigger {
    Pair,
    RePair,
    Rooms,
    Judges,
}

const BACKUP_ERROR: &str = "Could not save the database backup. The pairings are unchanged.";
pub const SUFFIX: &str = ".dump.sql";
pub const MAX_BACKUP_BYTES: usize = 128 * 1024 * 1024;

#[derive(Debug)]
pub enum BackupError {
    InvalidName,
    Missing,
    TooLarge,
    Storage,
}

pub fn valid_key(key: &str) -> bool {
    !key.is_empty()
        && key.len() + SUFFIX.len() <= 255
        && !matches!(key, "." | "..")
        && !key.contains(['/', '\\'])
        && !key.chars().any(char::is_control)
}

impl Backups {
    pub async fn pre_pairing(&self, round: i32, trigger: Trigger) -> Result<(), &'static str> {
        let (name, kind) = match trigger {
            Trigger::Pair => (format!("Before Pairing Round {round}"), 4),
            Trigger::RePair => (format!("Before Re-pairing Round {round}"), 8),
            Trigger::Rooms => (format!("Before Room Assign Round {round}"), 5),
            Trigger::Judges => (format!("Before Judge Assign Round {round}"), 3),
        };
        self.save_named(&name, kind, &round.to_string()).await
    }

    pub async fn save_named(&self, name: &str, kind: u8, round: &str) -> Result<(), &'static str> {
        self.create(name, kind, round, true)
            .await
            .map(|_| ())
            .map_err(|_| BACKUP_ERROR)
    }

    pub async fn create(
        &self,
        name: &str,
        kind: u8,
        round: &str,
        scratches: bool,
    ) -> Result<String, BackupError> {
        let flag = if scratches { "1" } else { "0" };
        let time = Utc::now().timestamp();
        let folder = if self.s3 {
            env::temp_dir()
        } else {
            self.prefix.clone()
        };
        let name_check = format!("{name}_{kind}_{round}_{time}_{flag}");
        if !valid_key(&name_check) {
            return Err(BackupError::InvalidName);
        }
        let tmp = folder.join(format!(
            ".mittab-backup-{}",
            passwords::random_secret().map_err(|_| BackupError::Storage)?
        ));
        let tmp = self
            .snapshot(&tmp, scratches)
            .await
            .map_err(|_| BackupError::Storage)?;
        if self.s3 {
            let content = file_content(&tmp.0).await?;
            self.store_named(name, kind, round, flag, content).await
        } else {
            if fs::metadata(&tmp.0)
                .map_err(|_| BackupError::Storage)?
                .len()
                > MAX_BACKUP_BYTES as u64
            {
                return Err(BackupError::TooLarge);
            }
            self.publish_local(name, kind, round, flag, time, &tmp)
                .await
        }
    }

    async fn publish_local(
        &self,
        name: &str,
        kind: u8,
        round: &str,
        flag: &str,
        time: i64,
        temporary: &TemporaryFile,
    ) -> Result<String, BackupError> {
        for attempt in 0..128 {
            let key = make_key(name, kind, round, flag, time, attempt);
            if !valid_key(&key) {
                return Err(BackupError::InvalidName);
            }
            let path = self.prefix.join(format!("{key}{SUFFIX}"));
            match tokio::fs::hard_link(&temporary.0, path).await {
                Ok(()) => return Ok(key),
                Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
                Err(_) => return Err(BackupError::Storage),
            }
        }
        Err(BackupError::Storage)
    }

    fn objects(&self) -> Result<ObjectStore, BackupError> {
        ObjectStore::from_env(self.prefix.to_str().ok_or(BackupError::Storage)?)
    }

    async fn store_named(
        &self,
        name: &str,
        kind: u8,
        round: &str,
        flag: &str,
        bytes: Vec<u8>,
    ) -> Result<String, BackupError> {
        let objects = self.objects()?;
        let bytes = Bytes::from(bytes);
        let time = Utc::now().timestamp();
        for attempt in 0..16 {
            let key = make_key(name, kind, round, flag, time, attempt);
            match objects.put(&key, bytes.clone()).await? {
                Put::Created => return Ok(key),
                Put::Exists => {}
            }
        }
        Err(BackupError::Storage)
    }

    pub async fn list(&self) -> Result<Vec<String>, BackupError> {
        if self.s3 {
            return self.objects()?.list().await;
        }
        let mut entries = match tokio::fs::read_dir(&self.prefix).await {
            Ok(entries) => entries,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Vec::new()),
            Err(_) => return Err(BackupError::Storage),
        };
        let mut keys = Vec::new();
        while let Some(file) = entries
            .next_entry()
            .await
            .map_err(|_| BackupError::Storage)?
        {
            if !file
                .file_type()
                .await
                .map_err(|_| BackupError::Storage)?
                .is_file()
            {
                continue;
            }
            if let Some(key) = file
                .file_name()
                .to_str()
                .and_then(|name| name.strip_suffix(SUFFIX))
                .filter(|key| valid_key(key))
            {
                keys.push(key.to_string());
            }
        }
        Ok(keys)
    }

    pub async fn content(&self, key: &str) -> Result<Vec<u8>, BackupError> {
        if !valid_key(key) {
            return Err(BackupError::InvalidName);
        }
        if self.s3 {
            return self.objects()?.get(key).await;
        }
        file_content(&self.prefix.join(format!("{key}{SUFFIX}"))).await
    }

    pub async fn upload(
        &self,
        name: &str,
        round: &str,
        bytes: Vec<u8>,
    ) -> Result<String, BackupError> {
        if bytes.len() > MAX_BACKUP_BYTES {
            return Err(BackupError::TooLarge);
        }
        if self.s3 {
            return self.store_named(name, 7, round, "U", bytes).await;
        }
        tokio::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&self.prefix)
            .await
            .map_err(|_| BackupError::Storage)?;
        let path = self.prefix.join(format!(
            ".mittab-backup-{}",
            passwords::random_secret().map_err(|_| BackupError::Storage)?
        ));
        let file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(|_| BackupError::Storage)?;
        let temporary = TemporaryFile(path);
        let mut file = tokio::fs::File::from_std(file);
        use tokio::io::AsyncWriteExt;
        file.write_all(&bytes)
            .await
            .map_err(|_| BackupError::Storage)?;
        file.sync_all().await.map_err(|_| BackupError::Storage)?;
        self.publish_local(name, 7, round, "U", Utc::now().timestamp(), &temporary)
            .await
    }

    async fn snapshot(&self, path: &Path, scratches: bool) -> Result<TemporaryFile, ()> {
        fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(path.parent().ok_or(())?)
            .map_err(|_| ())?;
        let program = env::var_os("MITTAB_MYSQLDUMP").unwrap_or_else(|| "mysqldump".into());
        let help = timeout(
            Duration::from_secs(10),
            Command::new(&program)
                .arg("--help")
                .stderr(Stdio::null())
                .kill_on_drop(true)
                .output(),
        )
        .await
        .map_err(|_| ())?
        .map_err(|_| ())?;
        if !help.status.success() {
            return Err(());
        }

        let file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)
            .map_err(|_| ())?;
        let partial = TemporaryFile(path.to_owned());
        let mut command = Command::new(program);
        command
            .args([
                "--no-defaults",
                "--protocol=TCP",
                "--quick",
                "--complete-insert",
                "--hex-blob",
                "--single-transaction",
                "--skip-lock-tables",
                "--skip-add-locks",
                "--no-tablespaces",
            ])
            .arg(format!("--host={}", self.host))
            .arg(format!("--port={}", self.port))
            .arg(format!("--user={}", self.user))
            .env("MYSQL_PWD", &self.password)
            .stdin(Stdio::null())
            .stdout(Stdio::from(file.try_clone().map_err(|_| ())?))
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let help = String::from_utf8_lossy(&help.stdout);
        if help.contains("set-gtid-purged") {
            command.arg("--set-gtid-purged=OFF");
        }
        if let Some(ca) = &self.ca {
            if help.contains("--ssl-mode") {
                command.arg("--ssl-mode=VERIFY_IDENTITY");
            } else {
                command.args(["--ssl", "--ssl-verify-server-cert"]);
            }
            command.arg("--ssl-ca").arg(ca);
        }
        if !scratches {
            command.arg(format!("--ignore-table={}.tab_scratch", self.database));
        }
        command.arg("--").arg(&self.database);
        let status = timeout(Duration::from_secs(90), command.status())
            .await
            .map_err(|_| ())?
            .map_err(|_| ())?;
        if !status.success() {
            return Err(());
        }
        file.sync_all().map_err(|_| ())?;
        Ok(partial)
    }
}

fn make_key(name: &str, kind: u8, round: &str, flag: &str, time: i64, attempt: usize) -> String {
    if attempt == 0 {
        format!("{name}_{kind}_{round}_{time}_{flag}")
    } else {
        format!("{name} ({})_{kind}_{round}_{time}_{flag}", attempt + 1)
    }
}

async fn file_content(path: &Path) -> Result<Vec<u8>, BackupError> {
    let metadata = tokio::fs::symlink_metadata(path).await.map_err(|error| {
        if error.kind() == ErrorKind::NotFound {
            BackupError::Missing
        } else {
            BackupError::Storage
        }
    })?;
    if !metadata.is_file() {
        return Err(BackupError::Missing);
    }
    if metadata.len() > MAX_BACKUP_BYTES as u64 {
        return Err(BackupError::TooLarge);
    }
    let bytes = tokio::fs::read(path)
        .await
        .map_err(|_| BackupError::Storage)?;
    if bytes.len() > MAX_BACKUP_BYTES {
        return Err(BackupError::TooLarge);
    }
    Ok(bytes)
}

struct TemporaryFile(PathBuf);

impl Drop for TemporaryFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
