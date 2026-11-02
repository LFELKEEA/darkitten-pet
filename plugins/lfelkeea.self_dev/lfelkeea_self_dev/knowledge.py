from __future__ import annotations

"""本地知识库：纯 Python 存储，支持中英文片段检索和带引用的召回。

设计取舍：
- 只用一个 JSON 文件，零外部依赖。打包版桌宠没有带 sqlite3，插件不能假设它存在；
- 文档按段落切块，每块保留标题、来源和序号，检索结果能指回原文；
- 检索用词频打分，标题和来源命中加权，规模到几千个片段都够用；
- 写入走临时文件替换，进程中断不会留下半个知识库。
"""

import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .resource_util import ResourceError, atomic_write, read_text, stamp

MAX_DOCUMENT_CHARS = 2_000_000
CHUNK_CHARS = 900
CHUNK_OVERLAP = 120
STORE_VERSION = 1


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_text(value: Any) -> str:
    return str(value or "").strip()


class KnowledgeBase:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "knowledge.json"
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 存储

    def _load(self) -> dict[str, Any]:
        if not self.db_path.is_file():
            return {"version": STORE_VERSION, "next_id": 1, "documents": []}
        try:
            data = json.loads(read_text(self.db_path) or "{}")
        except Exception as exc:
            raise ResourceError(f"知识库文件读取失败：{exc}") from exc
        if not isinstance(data, dict):
            raise ResourceError("知识库文件格式不正确。")
        documents = data.get("documents")
        if not isinstance(documents, list):
            documents = []
        cleaned: list[dict[str, Any]] = []
        used: list[int] = []
        for document in documents:
            if not isinstance(document, dict):
                continue
            try:
                document_id = int(document.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if document_id <= 0:
                continue
            document["id"] = document_id
            used.append(document_id)
            cleaned.append(document)
        data["documents"] = cleaned
        data["version"] = STORE_VERSION
        try:
            next_id = int(data.get("next_id") or 1)
        except (TypeError, ValueError):
            next_id = 1
        data["next_id"] = max(next_id, max(used, default=0) + 1, 1)
        return data

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write(
            self.db_path,
            json.dumps(data, ensure_ascii=False, indent=1) + "\n",
        )

    # ------------------------------------------------------------ 写入

    def add_document(
        self,
        title: str,
        text: str,
        *,
        source: str = "",
        kind: str = "note",
    ) -> dict[str, Any]:
        clean_title = " ".join(str(title or "").split()).strip() or "未命名"
        body = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not body:
            raise ResourceError("内容不能为空。")
        if len(body) > MAX_DOCUMENT_CHARS:
            raise ResourceError(f"内容超过 {MAX_DOCUMENT_CHARS} 字，请先拆分。")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        source_text = str(source or "").strip()
        kind_text = str(kind or "note").strip()[:32] or "note"
        chunks = self._chunk(body)

        with self._lock:
            data = self._load()
            for document in data["documents"]:
                if (
                    _as_text(document.get("source")) == source_text
                    and _as_text(document.get("content_hash")) == digest
                ):
                    return {
                        "id": int(document.get("id") or 0),
                        "title": _as_text(document.get("title")) or clean_title,
                        "chunks": len(document.get("chunks") or []),
                        "existing": True,
                    }
            doc_id = int(data["next_id"])
            data["next_id"] = doc_id + 1
            now = _now()
            data["documents"].append(
                {
                    "id": doc_id,
                    "title": clean_title,
                    "source": source_text,
                    "kind": kind_text,
                    "content_hash": digest,
                    "created_at": now,
                    "updated_at": now,
                    "char_count": len(body),
                    "chunks": chunks,
                }
            )
            self._save(data)
        return {
            "id": doc_id,
            "title": clean_title,
            "chunks": len(chunks),
            "existing": False,
        }

    def import_file(self, path: Path, *, title: str = "", source: str = "") -> dict[str, Any]:
        target = Path(path)
        if not target.is_file():
            raise ResourceError(f"文件不存在：{target}")
        if target.stat().st_size > 4 * 1024 * 1024:
            raise ResourceError("文件超过 4 MB，请先提取需要的部分。")
        try:
            with target.open("rb") as handle:
                if b"\x00" in handle.read(4096):
                    raise ResourceError("文件看起来是二进制，知识库只收文本。")
        except OSError as exc:
            raise ResourceError(f"读不了文件：{exc}") from exc
        text = read_text(target)
        return self.add_document(
            title or target.stem,
            text,
            source=source or target.name,
            kind="file",
        )

    def seed_defaults(self, references: Path, readme: Path | None = None) -> int:
        if self.stats()["documents"]:
            return 0
        created = 0
        candidates = [
            (references / "plugin_api.md", "桌宠插件 API 速查", "reference:plugin_api.md"),
            (references / "agent_notes.md", "自开发代理工作笔记", "reference:agent_notes.md"),
        ]
        if readme is not None:
            candidates.append((readme, "自开发工坊说明", "reference:README.md"))
        for path, title, source in candidates:
            if not path.is_file():
                continue
            try:
                self.import_file(path, title=title, source=source)
                created += 1
            except ResourceError:
                continue
        return created

    # ------------------------------------------------------------ 查询

    def list_documents(self, limit: int = 100) -> list[dict[str, Any]]:
        value = max(1, min(500, int(limit or 100)))
        with self._lock:
            data = self._load()
        documents = sorted(
            data["documents"], key=lambda item: int(item.get("id") or 0), reverse=True
        )
        rows: list[dict[str, Any]] = []
        for document in documents[:value]:
            rows.append(
                {
                    "id": int(document.get("id") or 0),
                    "title": _as_text(document.get("title")),
                    "source": _as_text(document.get("source")),
                    "kind": _as_text(document.get("kind")),
                    "created_at": _as_text(document.get("created_at")),
                    "updated_at": _as_text(document.get("updated_at")),
                    "char_count": int(document.get("char_count") or 0),
                    "chunks": len(document.get("chunks") or []),
                }
            )
        return rows

    def get_document(self, document_id: int) -> dict[str, Any]:
        try:
            doc_id = int(document_id)
        except (TypeError, ValueError) as exc:
            raise ResourceError("document_id 必须是整数。") from exc
        with self._lock:
            data = self._load()
        for document in data["documents"]:
            if int(document.get("id") or 0) != doc_id:
                continue
            chunks = [str(item) for item in (document.get("chunks") or [])]
            result = {
                "id": doc_id,
                "title": _as_text(document.get("title")),
                "source": _as_text(document.get("source")),
                "kind": _as_text(document.get("kind")),
                "created_at": _as_text(document.get("created_at")),
                "updated_at": _as_text(document.get("updated_at")),
                "char_count": int(document.get("char_count") or 0),
            }
            result["text"] = "\n\n".join(chunks)
            return result
        raise ResourceError(f"知识库文档不存在：{doc_id}")

    def stats(self) -> dict[str, int]:
        with self._lock:
            data = self._load()
        documents = data["documents"]
        return {
            "documents": len(documents),
            "chunks": sum(len(item.get("chunks") or []) for item in documents),
            "chars": sum(int(item.get("char_count") or 0) for item in documents),
        }

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        text = " ".join(str(query or "").split())
        if not text:
            raise ResourceError("query 不能为空。")
        count = max(1, min(20, int(limit or 6)))
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text)
        tokens = [token for token in tokens if len(token) >= 2][:8]
        if not tokens:
            tokens = [text]

        with self._lock:
            data = self._load()

        scored: list[tuple[int, int, int, dict[str, Any], str]] = []
        lowered = [token.lower() for token in tokens]
        for document in data["documents"]:
            doc_id = int(document.get("id") or 0)
            title = _as_text(document.get("title"))
            source = _as_text(document.get("source"))
            kind = _as_text(document.get("kind"))
            title_text = title.lower()
            source_text = source.lower()
            title_hits = sum(1 for token in lowered if token in title_text)
            source_hits = sum(1 for token in lowered if token in source_text)
            for ordinal, chunk in enumerate(document.get("chunks") or [], 1):
                body = str(chunk or "")
                body_text = body.lower()
                body_hits = sum(body_text.count(token) for token in lowered)
                if not body_hits and not title_hits and not source_hits:
                    continue
                score = body_hits + title_hits * 5 + source_hits * 3
                scored.append(
                    (
                        score,
                        doc_id,
                        ordinal,
                        {
                            "title": title,
                            "source": source,
                            "kind": kind,
                        },
                        body,
                    )
                )
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))

        results: list[dict[str, Any]] = []
        for _, doc_id, ordinal, meta, body in scored[:count]:
            results.append(
                {
                    "citation": f"[K{doc_id}#{ordinal}]",
                    "document_id": doc_id,
                    "ordinal": ordinal,
                    "title": meta["title"],
                    "source": meta["source"],
                    "kind": meta["kind"],
                    "snippet": self._snippet(body, tokens),
                }
            )
        return results

    @staticmethod
    def _snippet(text: str, tokens: list[str], limit: int = 420) -> str:
        value = " ".join(str(text or "").split())
        if not value:
            return ""
        index = -1
        for token in tokens:
            index = value.lower().find(token.lower())
            if index >= 0:
                break
        if index < 0:
            return value[:limit] + ("…" if len(value) > limit else "")
        start = max(0, index - 120)
        end = min(len(value), index + limit)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(value) else ""
        return prefix + value[start:end] + suffix

    def delete_document(self, document_id: int) -> str:
        try:
            doc_id = int(document_id)
        except (TypeError, ValueError) as exc:
            raise ResourceError("document_id 必须是整数。") from exc
        with self._lock:
            data = self._load()
            for index, document in enumerate(data["documents"]):
                if int(document.get("id") or 0) != doc_id:
                    continue
                title = _as_text(document.get("title"))
                data["documents"].pop(index)
                self._save(data)
                return title
        raise ResourceError(f"知识库文档不存在：{doc_id}")

    def archive_and_delete(self, document_id: int, trash_root: Path) -> tuple[str, Path]:
        document = self.get_document(document_id)
        archive = (
            Path(trash_root)
            / "knowledge"
            / f"doc-{int(document['id']):04d}-{stamp()}.md"
        )
        atomic_write(
            archive,
            f"# {document['title']}\n\n"
            f"- 来源：{document.get('source', '')}\n"
            f"- 类型：{document.get('kind', '')}\n"
            f"- 创建：{document.get('created_at', '')}\n\n"
            f"{document.get('text', '')}\n",
        )
        title = self.delete_document(document_id)
        return title, archive

    # ------------------------------------------------------------ 切块

    @staticmethod
    def _chunk(text: str) -> list[str]:
        body = text.strip()
        if len(body) <= CHUNK_CHARS:
            return [body] if body else []
        paragraphs = re.split(r"\n\s*\n", body)
        pieces: list[str] = []
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) <= CHUNK_CHARS:
                pieces.append(paragraph)
                continue
            pieces.extend(KnowledgeBase._split_long(paragraph))
        chunks: list[str] = []
        current = ""
        for piece in pieces:
            if not piece:
                continue
            if current and len(current) + len(piece) + 2 > CHUNK_CHARS:
                chunks.append(current.strip())
                tail = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
                current = (tail + "\n\n" + piece).strip()
            else:
                current = (current + "\n\n" + piece).strip()
        if current.strip():
            chunks.append(current.strip())
        return chunks

    @staticmethod
    def _split_long(paragraph: str) -> list[str]:
        sentences = re.split(r"(?<=[。！？!?])\s*|\n", paragraph)
        pieces: list[str] = []
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) > CHUNK_CHARS:
                if current:
                    pieces.append(current)
                    current = ""
                for start in range(0, len(sentence), CHUNK_CHARS):
                    pieces.append(sentence[start : start + CHUNK_CHARS])
                continue
            if current and len(current) + len(sentence) > CHUNK_CHARS:
                pieces.append(current)
                current = sentence
            else:
                current = (current + sentence).strip()
        if current:
            pieces.append(current)
        return pieces
