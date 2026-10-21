import os
import sqlite3
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest


WORKTREE_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = WORKTREE_ROOT / "scripts" / "backup.ps1"


def _run_backup(project_root: Path, destination: Path, database: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the backup contract")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(BACKUP_SCRIPT),
            "-ProjectPath",
            str(project_root),
            "-Destination",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=WORKTREE_ROOT,
        check=False,
    )


def test_backup_archive_contains_project_files_and_database_but_excludes_credentials(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "project.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    (project_root / "plan.json").write_text('{"nodes":[]}\n', encoding="utf-8")
    for directory, filename in (
        ("notes", "note.md"),
        ("reports", "report.md"),
        ("source", "source.txt"),
        ("attachments", "attachment.bin"),
    ):
        path = project_root / directory / filename
        path.parent.mkdir()
        path.write_text(filename, encoding="utf-8")
    (project_root / ".env").write_text("API_KEY=do-not-archive\n", encoding="utf-8")
    (project_root / "notes" / "credentials.json").write_text("secret", encoding="utf-8")
    (project_root / "notes" / "credential.json").write_text("secret", encoding="utf-8")
    (project_root / "notes" / "api_key.txt").write_text("secret", encoding="utf-8")
    secret_dir = project_root / "source" / "secrets"
    secret_dir.mkdir()
    (secret_dir / "config.json").write_text("secret", encoding="utf-8")

    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('snapshot')")
        connection.commit()
    destination = tmp_path / "backups"
    result = _run_backup(project_root, destination, database)

    assert result.returncode == 0, result.stderr or result.stdout
    archives = sorted(destination.glob("*.zip"))
    assert len(archives) == 1
    with ZipFile(archives[0]) as archive:
        names = set(archive.namelist())
    assert "project/project.json" in names
    assert "project/plan.json" in names
    assert "project/notes/note.md" in names
    assert "project/reports/report.md" in names
    assert "project/source/source.txt" in names
    assert "project/attachments/attachment.bin" in names
    assert "_app/app.db" in names
    assert all(Path(name).name.lower() not in {".env", "credentials.json", "credential.json", "api_key.txt", "config.json"} for name in names)
    with ZipFile(archives[0]) as archive:
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(archive.read("_app/app.db"))
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "snapshot"


def test_backup_rejects_destination_inside_project(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "project.json").write_text("{}", encoding="utf-8")
    (project_root / "plan.json").write_text("{}", encoding="utf-8")
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.commit()

    result = _run_backup(project_root, project_root / "backups", database)

    assert result.returncode != 0
    assert "destination" in ((result.stderr or "") + (result.stdout or "")).lower()
