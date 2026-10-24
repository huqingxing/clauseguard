"""Stage 10：审查发现 -> 带批注 Word 的回写层。

公开面：docx_writer（lxml 批注手术，纯函数式核心）、service（源解析/坐标系
自校验/上传编排）。禁止从本包外直接 import 私有模块（_block_maps 等）。
"""

from app.render.docx_writer import (
    AnchorMap,
    BlockText,
    CommentSpec,
    DocxEditor,
    RenderError,
    TextAnchor,
    WriteResult,
    build_anchor_map,
    write_annotated_docx,
)
from app.render.service import (
    PLACEHOLDER_PREFIX,
    RenderOutcome,
    build_comment_specs,
    render_for_state,
    resolve_source,
    resolve_storage,
)

__all__ = [
    "AnchorMap",
    "BlockText",
    "CommentSpec",
    "DocxEditor",
    "PLACEHOLDER_PREFIX",
    "RenderError",
    "RenderOutcome",
    "TextAnchor",
    "WriteResult",
    "build_anchor_map",
    "build_comment_specs",
    "render_for_state",
    "resolve_source",
    "resolve_storage",
    "write_annotated_docx",
]
