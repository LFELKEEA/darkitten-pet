from __future__ import annotations

"""本地知识库：SQLite + FTS5，支持中英文片段检索和带引用的召回。

设计取舍：
- 单文件数据库，零外部依赖，复制 ``data/dev/knowledge`` 就能带走；
- 文档按段落切块，每块保留标题、来源和序号，检索结果能指回原文；
- FTS5 不可用或中文短词查不到时自动退回 LIKE，不让功能整体失效。
"""

import hashlib
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .resource_util import ResourceError, atomic_write, read_text, stamp

MAX_DOCUMENT_CHARS = 2_000_000
CHUNK_CHARS = 900
CHUNK_OVERLAP = 120


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class KnowledgeBase:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "knowledge.db"
        self._lock = threading.Lock()
        self._fts_ready = True

    # ------------------------------------------------------------ 数据库

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'note',
                content_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                char_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_document
                ON chunks(document_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_documents_source
                ON documents(source, content_hash);
            """
        )
        if not self._fts_ready:
            return
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                "USING fts5(text, title, source, tokenize='trigram')"
            )
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                    "USING fts5(text, title, source, tokenize='unicode61')"
                )
            except sqlite3.OperationalError:
                self._fts_ready = False
        conn.commit()

    @staticmethod
    def _close(conn: sqlite3.Connection) -> None:
        try:
            conn.close()
        except sqlite3.Error:
            pass

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
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE source = ? AND content_hash = ?",
                    (source_text, digest),
                ).fetchone()
                if existing is not None:
                    doc_id = int(existing["id"])
                    count = conn.execute(
                        "SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?",
                        (doc_id,),
                    ).fetchone()["n"]
                    return {
                        "id": doc_id,
                        "title": clean_title,
                        "chunks": int(count),
                        "existing": True,
                    }
                now = _now()
                cursor = conn.execute(
                    "INSERT INTO documents "
                    "(title, source, kind, content_hash, created_at, updated_at, char_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (clean_title, source_text, kind_text, digest, now, now, len(body)),
                )
                doc_id = int(cursor.lastrowid)
                for ordinal, piece in enumerate(chunks, 1):
                    row = conn.execute(
                        "INSERT INTO chunks (document_id, ordinal, text) VALUES (?, ?, ?)",
                        (doc_id, ordinal, piece),
                    )
                    if self._fts_ready:
                        try:
                            conn.execute(
                                "INSERT INTO chunks_fts (rowid, text, title, source) VALUES (?, ?, ?, ?)",
                                (int(row.lastrowid), piece, clean_title, source_text),
                            )
                        except sqlite3.OperationalError:
                            self._fts_ready = False
                conn.commit()
                return {
                    "id": doc_id,
                    "title": clean_title,
                    "chunks": len(chunks),
                    "existing": False,
                }
            finally:
                self._close(conn)

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
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT d.id, d.title, d.source, d.kind, d.created_at, d.updated_at,
                           d.char_count,
                           (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunks
                    FROM documents d
                    ORDER BY d.id DESC
                    LIMIT ?
                    """,
                    (value,),
                ).fetchall()
                return [dict(row) for row in rows]
            finally:
                self._close(conn)

    def get_document(self, document_id: int) -> dict[str, Any]:
        try:
            doc_id = int(document_id)
        except (TypeError, ValueError) as exc:
            raise ResourceError("document_id 必须是整数。") from exc
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, title, source, kind, created_at, updated_at, char_count "
                    "FROM documents WHERE id = ?",
                    (doc_id,),
                ).fetchone()
                if row is None:
                    raise ResourceError(f"知识库文档不存在：{doc_id}")
                chunks = conn.execute(
                    "SELECT ordinal, text FROM chunks WHERE document_id = ? ORDER BY ordinal",
                    (doc_id,),
                ).fetchall()
                data = dict(row)
                data["text"] = "\n\n".join(str(item["text"]) for item in chunks)
                return data
            finally:
                self._close(conn)

    def stats(self) -> dict[str, int]:
        with self._lock:
            conn = self._connect()
            try:
                docs = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
                chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
                chars = conn.execute(
                    "SELECT COALESCE(SUM(char_count), 0) AS n FROM documents"
                ).fetchone()["n"]
                return {
                    "documents": int(docs),
                    "chunks": int(chunks),
                    "chars": int(chars),
                }
            finally:
                self._close(conn)

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        text = " ".join(str(query or "").split())
        if not text:
            raise ResourceError("query 不能为空。")
        count = max(1, min(20, int(limit or 6)))
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text)
        tokens = [token for token in tokens if len(token) >= 2][:8]
        if not tokens:
            tokens = [text]

        rows: list[sqlite3.Row] = []
        with self._lock:
            conn = self._connect()
            try:
                if self._fts_ready:
                    rows = self._fts_search(conn, tokens, count)
                if not rows:
                    rows = self._like_search(conn, tokens, count)
            finally:
                self._close(conn)

        results: list[dict[str, Any]] = []
        for row in rows:
            body = str(row["text"] or "")
            results.append(
                {
                    "citation": f"[K{int(row['document_id'])}#{int(row['ordinal'])}]",
                    "document_id": int(row["document_id"]),
                    "ordinal": int(row["ordinal"]),
                    "title": str(row["title"] or ""),
                    "source": str(row["source"] or ""),
                    "kind": str(row["kind"] or ""),
                    "snippet": self._snippet(body, tokens),
                }
            )
        return results

    def _fts_search(
        self, conn: sqlite3.Connection, tokens: list[str], limit: int
    ) -> list[sqlite3.Row]:
        usable = [token for token in tokens if len(token) >= 3]
        if not usable:
            return []
        expression = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in usable)
        try:
            return conn.execute(
                """
                SELECT c.document_id, c.ordinal, c.text,
                       d.title, d.source, d.kind, bm25(chunks_fts) AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                JOIN documents d ON d.id = c.document_id
                WHERE chunks_fts MATCH ?
                ORDER BY score ASC, c.document_id DESC
                LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def _like_search(
        self, conn: sqlite3.Connection, tokens: list[str], limit: int
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[Any] = []
        for token in tokens[:5]:
            like = f"%{token}%"
            clauses.append("(c.text LIKE ? OR d.title LIKE ? OR d.source LIKE ?)")
            params.extend([like, like, like])
        if not clauses:
            return []
        params.append(limit)
        try:
            return conn.execute(
                f"""
                SELECT c.document_id, c.ordinal, c.text,
                       d.title, d.source, d.kind, 0.0 AS score
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE {' AND '.join(clauses)}
                ORDER BY c.document_id DESC, c.ordinal ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return []

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
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT title FROM documents WHERE id = ?", (doc_id,)
                ).fetchone()
                if row is None:
                    raise ResourceError(f"知识库文档不存在：{doc_id}")
                title = str(row["title"] or "")
                if self._fts_ready:
                    try:
                        conn.execute(
                            "DELETE FROM chunks_fts WHERE rowid IN "
                            "(SELECT id FROM chunks WHERE document_id = ?)",
                            (doc_id,),
                        )
                    except sqlite3.OperationalError:
                        self._fts_ready = False
                conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                conn.commit()
                return title
            finally:
                self._close(conn)

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
