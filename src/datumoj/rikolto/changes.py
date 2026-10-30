"""纯函数变化检测。正文由解析器抽取；这里仅忽略明确的导航行、日期元信息行。"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from hashlib import sha256

from datumoj.rikolto.core.models import ChangeEvent, ChangeKind, DocKind, Document

SIMILARITY_THRESHOLD = 0.98
_REVISION_KEYS = ("law_revision_id", "revision_id", "law_revision", "revision")
_NAVIGATION = {"ホーム", "トップページ", "サイトマップ", "お問い合わせ", "前のページ", "次のページ",
               "首页", "上一页", "下一页", "home", "sitemap", "contact", "previous", "next"}
_DATE_LINE = re.compile(
    r"(?:(?:最終更新(?:日|日時)?|更新(?:日|日時)?|掲載日|公開日|发布日期|更新日期|last updated|updated|published)"
    r"\s*[:：]?\s*)?(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    lines = []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        line = " ".join(line.split())
        if not line or line.casefold() in _NAVIGATION or _DATE_LINE.fullmatch(line):
            continue
        # 只忽略显式标记的面包屑，不删正文里提及日期或链接的句子。
        if re.match(r"^(?:ホーム|トップページ|首页|Home)\s*[>›»|]", line, re.IGNORECASE):
            continue
        lines.append(line)
    return " ".join(lines)


def detect(old_doc: Document | None, old_text: str, new_doc: Document, new_text: str, *,
           detected_at: datetime = datetime(1970, 1, 1, tzinfo=UTC)) -> ChangeEvent | None:
    """比较同一 doc_key。调用方传检测时间；默认 epoch 使四参数调用仍是纯函数。

    返回快照哈希引用；流水线补 resource_url 和实际检测时间。正文中的业务日期不会被删除。
    """
    if old_doc is not None and old_doc.doc_key != new_doc.doc_key:
        raise ValueError("只能比较同一 doc_key")
    if old_doc is None:
        kind, summary = ChangeKind.added, f"新增文档：{new_doc.title}"
    else:
        if old_doc.snapshot_sha256 == new_doc.snapshot_sha256:
            return None
        kind = ChangeKind.modified
        if new_doc.kind is DocKind.dataset:
            summary = "统计表快照内容变化"
        elif new_doc.kind is DocKind.law or any(
            k in new_doc.metadata for k in ("law_num", "law_revision_id", "law_revision")
        ):
            old_revision = next((old_doc.metadata[k] for k in _REVISION_KEYS if old_doc.metadata.get(k)), None)
            new_revision = next((new_doc.metadata[k] for k in _REVISION_KEYS if new_doc.metadata.get(k)), None)
            if old_revision is not None and new_revision is not None:
                if old_revision == new_revision:
                    return None
                summary = f"法令修订：{old_revision} → {new_revision}"
            else:
                if sha256(old_text.encode()).digest() == sha256(new_text.encode()).digest():
                    return None
                summary = "法令正文哈希变化"
        else:
            before, after = normalize_text(old_text), normalize_text(new_text)
            if before == after:
                return None
            similarity = SequenceMatcher(None, before, after, autojunk=False).ratio()
            if similarity >= SIMILARITY_THRESHOLD:
                return None
            summary = f"正文文字变化约 {100 * (1 - similarity):.1f}%"
    return ChangeEvent.make(
        source_id=new_doc.source_id, kind=kind, doc_key=new_doc.doc_key,
        old_sha256=old_doc.snapshot_sha256 if old_doc else None, new_sha256=new_doc.snapshot_sha256,
        detected_at=detected_at, summary=" ".join(summary.split()),
    )
