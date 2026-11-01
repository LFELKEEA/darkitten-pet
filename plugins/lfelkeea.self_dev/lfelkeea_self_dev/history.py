from __future__ import annotations

"""改动账本：记录“第一次碰之前长什么样”，用于一键还原。

只记录第一次触碰时的基线，而不是每次写入都快照：
- 同一文件改十次，还原时回到十次之前的那一版；
- 先新建、再修改的文件，还原时直接删除（因为最初不存在）；
- 插件目录按整包记录，更新过的插件可以整体退回。

还原前会把“当前状态”先搬到 `.backups/restore/<时间戳>/`，
所以一键还原本身也是可回退的。
"""

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

JOURNAL_VERSION = 1
EXCLUDE_DIRS = {"__pycache__", ".state", ".git", ".svn", ".hg"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


class JournalError(Exception):
    pass


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{int(datetime.now().microsecond / 1000):03d}"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in EXCLUDE_DIRS or Path(name).suffix.lower() in EXCLUDE_SUFFIXES
    }


class ChangeJournal:
    """按文件 / 插件记录首次修改前的基线。"""

    def __init__(
        self,
        dev_dir: Path | str,
        plugins_dir: Path | str,
        workspace: Path | str | None = None,
        logger: Any = None,
    ) -> None:
        self.dev_dir = Path(dev_dir).resolve()
        self.plugins_dir = Path(plugins_dir).resolve()
        self.workspace = Path(workspace or (self.dev_dir / "workspace")).resolve()
        self.root = (self.dev_dir / ".changes").resolve()
        self.path = self.root / "journal.json"
        self.baseline_root = self.root / "baseline"
        self.history_root = self.root / "history"
        self.log = logger or (lambda _message: None)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 读写

    def _empty(self) -> dict[str, Any]:
        return {
            "version": JOURNAL_VERSION,
            "id": _stamp(),
            "created_at": _now(),
            "updated_at": _now(),
            "files": {},
            "plugins": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.log(f"改动账本无法读取，已另存并重新开始：{exc}")
            self._quarantine()
            return self._empty()
        if not isinstance(data, dict) or int(data.get("version", 0) or 0) != JOURNAL_VERSION:
            self._quarantine()
            return self._empty()
        data.setdefault("id", _stamp())
        data.setdefault("files", {})
        data.setdefault("plugins", {})
        if not isinstance(data["files"], dict) or not isinstance(data["plugins"], dict):
            self._quarantine()
            return self._empty()
        return data

    def _quarantine(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / f"journal-broken-{_stamp()}.json"
            if self.path.is_file():
                os.replace(self.path, target)
        except OSError:
            pass

    def _save(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = _now()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _root_of(self, name: str) -> Path:
        if name == "drafts":
            return (self.dev_dir / "drafts").resolve()
        return self.workspace

    def _baseline_dir(self, journal_id: str) -> Path:
        return self.baseline_root / journal_id

    # ------------------------------------------------------------ 记录

    def record_file(self, root: str, path: Path | str) -> None:
        target = Path(path).resolve()
        base = self._root_of(root)
        try:
            rel = target.relative_to(base).as_posix()
        except ValueError as exc:
            raise JournalError("改动账本只记录工作区和草稿目录里的文件。") from exc
        if rel in ("", "."):
            raise JournalError("不能把目录本身记成文件改动。")
        key = f"{root}::{rel}"
        with self._lock:
            data = self._load()
            if key in data["files"]:
                return
            existed = target.is_file()
            baseline = ""
            if existed:
                destination = self._baseline_dir(data["id"]) / root / Path(rel)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, destination)
                baseline = str(destination)
            elif target.exists():
                raise JournalError(f"{rel} 不是普通文件，无法记录基线。")
            data["files"][key] = {
                "root": root,
                "path": rel,
                "existed": existed,
                "baseline": baseline,
                "at": _now(),
            }
            try:
                self._save(data)
            except OSError as exc:
                raise JournalError(f"改动账本写入失败：{exc}") from exc

    def record_plugin(self, plugin_id: str, folder: Path | str) -> None:
        pid = str(plugin_id or "").strip()
        if not pid:
            raise JournalError("插件 id 为空。")
        target = Path(folder).resolve()
        if target != (self.plugins_dir / pid).resolve():
            raise JournalError("只记录插件目录里的插件。")
        with self._lock:
            data = self._load()
            if pid in data["plugins"]:
                return
            existed = target.is_dir()
            baseline = ""
            if existed:
                destination = self._baseline_dir(data["id"]) / "plugins" / pid
                if destination.exists():
                    shutil.rmtree(destination, ignore_errors=True)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(target, destination, ignore=_ignore)
                baseline = str(destination)
            data["plugins"][pid] = {
                "plugin_id": pid,
                "existed": existed,
                "baseline": baseline,
                "at": _now(),
            }
            try:
                self._save(data)
            except OSError as exc:
                raise JournalError(f"改动账本写入失败：{exc}") from exc

    # ------------------------------------------------------------ 查询

    def summary(self) -> dict[str, Any]:
        with self._lock:
            data = self._load()
        files = [
            f"{item['root']}/{item['path']}"
            for item in data["files"].values()
            if isinstance(item, dict)
        ]
        plugins = [str(item.get("plugin_id", "")) for item in data["plugins"].values() if isinstance(item, dict)]
        return {
            "files": sorted(files),
            "plugins": sorted(name for name in plugins if name),
            "count": len(files) + len(plugins),
            "updated_at": data.get("updated_at", ""),
        }

    # ------------------------------------------------------------ 还原

    def restore_all(
        self,
        backup_root: Path | str,
        skip_plugins: Iterable[str] = (),
    ) -> dict[str, Any]:
        stamp = _stamp()
        destination = Path(backup_root).resolve() / stamp
        skip = {str(item) for item in skip_plugins}
        result: dict[str, Any] = {
            "stamp": stamp,
            "backup_dir": str(destination),
            "restored_files": [],
            "removed_files": [],
            "restored_plugins": [],
            "removed_plugins": [],
            "skipped_plugins": [],
            "errors": [],
        }
        with self._lock:
            data = self._load()
            if not data["files"] and not data["plugins"]:
                return result

            # 第一步：先把当前状态整体搬到备份区，还原失败也还有退路。
            for entry in list(data["files"].values()):
                if not isinstance(entry, dict):
                    continue
                target = self._root_of(str(entry.get("root", "workspace"))) / Path(str(entry.get("path", "")))
                if not target.is_file():
                    continue
                try:
                    backup = destination / "files" / str(entry.get("root", "workspace")) / Path(str(entry.get("path", "")))
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                except OSError as exc:
                    result["errors"].append(f"备份当前文件失败：{entry.get('path')}：{exc}")

            for pid, entry in list(data["plugins"].items()):
                if not isinstance(entry, dict):
                    continue
                target = (self.plugins_dir / pid).resolve()
                if not target.is_dir() or pid in skip:
                    continue
                try:
                    backup = destination / "plugins" / pid
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), str(backup))
                except OSError as exc:
                    result["errors"].append(f"备份当前插件失败：{pid}：{exc}")

            # 第二步：文件回到基线，或删掉由代理新建的文件。
            for entry in list(data["files"].values()):
                if not isinstance(entry, dict):
                    continue
                root = str(entry.get("root", "workspace"))
                rel = str(entry.get("path", ""))
                base = self._root_of(root)
                target = (base / Path(rel)).resolve()
                if base not in target.parents:
                    result["errors"].append(f"跳过越界路径：{root}/{rel}")
                    continue
                try:
                    if entry.get("existed"):
                        baseline = Path(str(entry.get("baseline", "")))
                        if not baseline.is_file():
                            result["errors"].append(f"基线丢失：{root}/{rel}")
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(baseline, target)
                        result["restored_files"].append(f"{root}/{rel}")
                    else:
                        if target.is_file():
                            target.unlink()
                            self._prune_empty_dirs(target.parent, base)
                        result["removed_files"].append(f"{root}/{rel}")
                except OSError as exc:
                    result["errors"].append(f"还原文件失败：{root}/{rel}：{exc}")

            # 第三步：插件整包退回。
            for pid, entry in list(data["plugins"].items()):
                if not isinstance(entry, dict):
                    continue
                if pid in skip:
                    result["skipped_plugins"].append(pid)
                    continue
                target = (self.plugins_dir / pid).resolve()
                try:
                    if entry.get("existed"):
                        baseline = Path(str(entry.get("baseline", "")))
                        if not baseline.is_dir():
                            result["errors"].append(f"插件基线丢失：{pid}")
                            continue
                        if target.exists():
                            fallback = destination / "plugins" / f"{pid}-late"
                            fallback.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(target), str(fallback))
                        shutil.copytree(baseline, target)
                        result["restored_plugins"].append(pid)
                    else:
                        if target.exists():
                            fallback = destination / "plugins" / f"{pid}-late"
                            fallback.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(target), str(fallback))
                        result["removed_plugins"].append(pid)
                except OSError as exc:
                    result["errors"].append(f"还原插件失败：{pid}：{exc}")

            # 第四步：归档账本；有错误时保留账本，方便主人排查后重试。
            try:
                self.history_root.mkdir(parents=True, exist_ok=True)
                archive = self.history_root / f"restore-{stamp}.json"
                archive.write_text(
                    json.dumps(
                        {"journal": data, "result": result},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                if not result["errors"]:
                    self._save(self._empty())
            except OSError as exc:
                result["errors"].append(f"归档改动账本失败：{exc}")

        return result

    @staticmethod
    def _prune_empty_dirs(start: Path, stop: Path) -> None:
        current = start
        while current != stop and stop in current.parents:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent
