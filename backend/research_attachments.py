"""Temporary evidence owned by the upload capability, independent of shared RAG."""

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from threading import Lock, Timer
from time import monotonic


MAX_UPLOAD_BYTES = 1_000_000
MAX_ATTACHMENTS_PER_RUN = 6
ATTACHMENT_TTL = 2 * 60 * 60
_MAX_DOCUMENT_CHARS = 160_000
_MAX_DOCUMENT_PAGES = 100


class InvalidAttachment(ValueError):
    pass


class AttachmentUnavailable(Exception):
    pass


class AttachmentCapacityReached(Exception):
    pass


def parse_attachment(filename: str, body: bytearray) -> tuple[str, str]:
    title = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not title or any(ord(char) < 32 for char in title):
        raise InvalidAttachment("文件名无效。")
    extension = PurePosixPath(title).suffix.lower()
    if extension not in {".txt", ".md", ".markdown", ".pdf"}:
        raise InvalidAttachment("只支持 TXT、Markdown 和 PDF 文件。")
    if extension == ".pdf":
        import pymupdf

        try:
            with pymupdf.open(stream=body, filetype="pdf") as document:
                if not document.is_pdf or document.needs_pass:
                    raise InvalidAttachment("请选择未加密的 PDF 文件。")
                if document.page_count > _MAX_DOCUMENT_PAGES:
                    raise InvalidAttachment("PDF 超过 100 页。")
                pages = []
                length = 0
                for page in document:
                    page_text = page.get_text()
                    length += len(page_text)
                    if length > _MAX_DOCUMENT_CHARS:
                        raise InvalidAttachment("文件文字过多，请缩小范围后上传。")
                    pages.append(page_text)
                content = "\n".join(pages)
        except (pymupdf.FileDataError, RuntimeError) as exc:
            raise InvalidAttachment("无法读取该 PDF 文件。") from exc
    else:
        try:
            content = body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidAttachment("请使用 UTF-8 编码的文本文件。") from exc
        if "\x00" in content:
            raise InvalidAttachment("无法读取该文本文件。")
    content = content.strip()
    if not content:
        raise InvalidAttachment("文件没有可提取的文字。")
    if len(content) > _MAX_DOCUMENT_CHARS:
        raise InvalidAttachment("文件文字过多，请缩小范围后上传。")
    return title, content


@dataclass(frozen=True)
class ResearchAttachment:
    source_id: str
    title: str
    expires_at: str
    text: str

    def source(self) -> dict:
        return {"title": self.title, "source_type": "upload", "chunk_id": self.source_id,
                "expires_at": self.expires_at}

    def excerpt(self, question: str) -> str:
        windows = [self.text[offset:offset + 2400] for offset in range(0, len(self.text), 2000)]
        query = question.lower()
        words = re.findall(r"[a-z0-9]{2,}", query) + re.findall(r"(?=([\u3400-\u9fff]{2}))", query)
        terms = list(dict.fromkeys(words))[:64]
        scores = [sum(term in window for term in terms) for window in map(str.lower, windows)]
        ranked = sorted(range(len(windows)), key=scores.__getitem__, reverse=True)
        return "\n…\n".join(windows[index] for index in sorted(ranked[:2]))


class AttachmentStore:
    def __init__(self, *, ttl: int = ATTACHMENT_TTL, capacity: int = 64):
        self._ttl = ttl
        self._capacity = capacity
        self._entries: dict[str, tuple[float, ResearchAttachment]] = {}
        self._cleanup: Timer | None = None
        self._lock = Lock()

    def _purge_expired(self) -> None:
        now = monotonic()
        for key, (deadline, _) in list(self._entries.items()):
            if deadline <= now:
                del self._entries[key]

    def _schedule_cleanup(self) -> None:
        if self._cleanup or not self._entries:
            return
        deadline = min(value[0] for value in self._entries.values())
        self._cleanup = Timer(max(0, deadline - monotonic()), self._expire)
        self._cleanup.daemon = True
        self._cleanup.start()

    def _expire(self) -> None:
        with self._lock:
            self._cleanup = None
            self._purge_expired()
            self._schedule_cleanup()

    def put(self, title: str, text: str) -> tuple[str, ResearchAttachment]:
        with self._lock:
            self._purge_expired()
            if len(self._entries) >= self._capacity:
                raise AttachmentCapacityReached
            key = uuid.uuid4().hex
            expires_at = datetime.fromtimestamp(time.time() + self._ttl, timezone.utc).isoformat()
            item = ResearchAttachment(f"upload:{uuid.uuid4().hex}", title, expires_at, text)
            self._entries[key] = (monotonic() + self._ttl, item)
            self._schedule_cleanup()
            return key, item

    def resolve(self, keys: list[str]) -> list[ResearchAttachment]:
        with self._lock:
            self._purge_expired()
            if any(key not in self._entries for key in keys):
                raise AttachmentUnavailable("有附件已过期、被删除或无法访问，请重新上传。")
            return [self._entries[key][1] for key in keys]

    def delete(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
