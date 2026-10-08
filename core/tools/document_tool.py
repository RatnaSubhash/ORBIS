"""
Document Tool for ORBIS Phase 8.
Controlled filesystem persistence for generated documents within the allowed workspace root.
Enforces workspace boundaries, extension allowlists, traversal prevention,
protected file blocking, overwrite safety, secret redaction, and independent verification.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field, field_validator

from core.config import get_settings
from core.tools.base import BaseTool
from core.tools.secret_redaction import redact_secrets
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

# Windows reserved device names
RESERVED_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}

# Sensitive / protected file patterns that must never be created or overwritten
PROTECTED_PATTERNS = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"^\.git($|[\\/])", re.IGNORECASE),
    re.compile(r"id_rsa|id_ed25519|\.pem$|\.key$", re.IGNORECASE),
    re.compile(r"^credentials(\.json)?$", re.IGNORECASE),
    re.compile(r"system32|windows|sam$", re.IGNORECASE),
]


class DocumentActionParameters(BaseModel):
    """Parameters schema for safe document creation and persistence."""
    file_path: str = Field(
        ...,
        min_length=1,
        description="Target relative or absolute path for the document within the allowed workspace root.",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Text or structured content to write to the document.",
    )
    format: Literal["markdown", "text", "docx", "pptx", "pdf", "xlsx"] = Field(
        default="markdown",
        description="Document format ('markdown', 'text', 'docx', 'pptx', 'pdf', or 'xlsx').",
    )
    overwrite: bool = Field(
        default=False,
        description="Whether to permit overwriting an existing file (requires high-risk approval).",
    )

    @field_validator("file_path")
    @classmethod
    def validate_file_path_syntax(cls, v: str) -> str:
        clean = v.strip()
        if ".." in clean:
            raise ValueError(f"Path traversal ('..') is strictly forbidden in file path: '{v}'")
        if clean.startswith("//") or clean.startswith(r"\\"):
            raise ValueError(f"UNC network paths are prohibited: '{v}'")
        return clean


class DocumentTool(BaseTool):
    """
    Controlled filesystem persistence tool for Markdown and plain text documents.
    Enforces strict path containment, protected file safety, overwrite gating,
    and independent verification.
    """

    name: str = "document"
    description: str = (
        "Safely creates or updates Markdown (.md) and plain text (.txt) documents within the authorized workspace."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = DocumentActionParameters

    def __init__(self, allowed_workspace_root: Optional[str] = None) -> None:
        settings = get_settings()
        root_str = allowed_workspace_root or settings.allowed_workspace_root
        self.allowed_root = Path(root_str).resolve()
        self.max_bytes = settings.document_max_bytes
        self.allowed_extensions = set(ext.lower() for ext in settings.document_allowed_extensions)

    def resolve_safe_path(self, raw_path: str) -> Path:
        """
        Resolves raw_path against the allowed workspace root and strictly enforces
        canonical containment within the root boundary.
        """
        # 1. Syntactic path checks
        if ".." in raw_path:
            raise ValueError(f"Path traversal ('..') is strictly forbidden: '{raw_path}'.")
        if raw_path.startswith("//") or raw_path.startswith(r"\\"):
            raise ValueError(f"UNC paths are strictly prohibited: '{raw_path}'.")

        norm_path = raw_path.strip()
        if norm_path in ("/", "\\", ".", ""):
            resolved = self.allowed_root
        else:
            candidate = Path(norm_path)
            if not candidate.is_absolute():
                rel_parts = norm_path.lstrip("/\\")
                resolved = (self.allowed_root / rel_parts).resolve()
            else:
                resolved = candidate.resolve()

        # 2. Containment boundary check
        if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
            raise ValueError(
                f"Path safety violation: '{raw_path}' resolves outside workspace root '{self.allowed_root}'."
            )

        # 3. Extension allowlist check
        ext = resolved.suffix.lower()
        if ext not in self.allowed_extensions:
            raise ValueError(
                f"File extension '{ext}' is not permitted. Allowed document extensions: {sorted(self.allowed_extensions)}."
            )

        # 4. Reserved device name check (Windows safety)
        base_name = resolved.stem.lower()
        if base_name in RESERVED_DEVICE_NAMES:
            raise ValueError(f"Filename '{resolved.name}' uses a reserved operating system device name.")

        # 5. Protected / sensitive file pattern check
        name_to_check = resolved.name.lower()
        for pat in PROTECTED_PATTERNS:
            if pat.search(name_to_check) or pat.search(str(resolved).lower()):
                raise ValueError(f"Target path '{raw_path}' is a protected or sensitive file and cannot be modified.")

        return resolved

    def get_effective_risk(self, params: Any) -> RiskLevel:
        """
        Dynamically derives the authoritative RiskLevel.
        Writing a new file is LOW risk; overwriting an existing file is HIGH risk
        and triggers the approval gate.
        """
        if isinstance(params, dict):
            raw_path = params.get("file_path", "")
            overwrite = params.get("overwrite", False)
        elif isinstance(params, DocumentActionParameters):
            raw_path = params.file_path
            overwrite = params.overwrite
        else:
            return RiskLevel.HIGH

        try:
            resolved = self.resolve_safe_path(raw_path)
            if resolved.exists():
                # Writing to an existing file requires explicit overwrite approval (HIGH risk)
                return RiskLevel.HIGH
            return RiskLevel.LOW
        except Exception:
            return RiskLevel.HIGH

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Safely persists the document to the authorized workspace."""
        if not isinstance(params, DocumentActionParameters):
            params = self.validate_parameters(params.model_dump())

        # 1. Resolve and validate path
        try:
            target = self.resolve_safe_path(params.file_path)
        except ValueError as err:
            return ToolResult(
                success=False,
                error=f"Document path safety violation: {str(err)}",
                metadata={"file_path": params.file_path},
            )

        # 2. Check overwrite policy
        file_existed = target.exists()
        if file_existed and not params.overwrite:
            return ToolResult(
                success=False,
                error=f"File already exists at '{params.file_path}'. Set overwrite=True to allow overwriting.",
                metadata={"file_path": str(target), "exists": True},
            )

        # 3. Secret redaction before persisting
        clean_content = redact_secrets(params.content)
        encoded_bytes = clean_content.encode("utf-8")

        # 4. Content size bounding
        if len(encoded_bytes) > self.max_bytes:
            return ToolResult(
                success=False,
                error=f"Content size ({len(encoded_bytes)} bytes) exceeds maximum permitted limit ({self.max_bytes} bytes).",
                metadata={"size_bytes": len(encoded_bytes), "max_bytes": self.max_bytes},
            )

        # 5. Write to filesystem according to format
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            doc_format = self._infer_format(target, params.format)

            if doc_format == "docx":
                self._generate_docx(target, clean_content)
            elif doc_format == "pptx":
                self._generate_pptx(target, clean_content)
            elif doc_format == "pdf":
                self._generate_pdf(target, clean_content)
            elif doc_format == "xlsx":
                self._generate_xlsx(target, clean_content)
            else:
                target.write_text(clean_content, encoding="utf-8")

            file_size_on_disk = target.stat().st_size
            output = {
                "file_path": str(target),
                "file_name": target.name,
                "size_bytes": file_size_on_disk,
                "format": doc_format,
                "overwritten": file_existed,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            return ToolResult(
                success=True,
                output=output,
                metadata={
                    "file_path": str(target),
                    "overwritten": file_existed,
                    "size_bytes": file_size_on_disk,
                    "format": doc_format,
                    "risk_level": RiskLevel.HIGH.value if file_existed else RiskLevel.LOW.value,
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to write document to '{target}': {str(e)}",
                metadata={"file_path": str(target)},
            )

    @staticmethod
    def _infer_format(target: Path, explicit_format: str) -> str:
        ext_to_fmt = {
            ".md": "markdown",
            ".txt": "text",
            ".docx": "docx",
            ".pptx": "pptx",
            ".pdf": "pdf",
            ".xlsx": "xlsx",
        }
        ext = target.suffix.lower()
        if ext in ext_to_fmt and explicit_format == "markdown":
            return ext_to_fmt[ext]
        return explicit_format

    def _generate_docx(self, target: Path, content: str) -> None:
        from docx import Document
        doc = Document()
        lines = content.splitlines()
        in_code_block = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                p = doc.add_paragraph(line)
                p.style = "No Spacing"
                continue
            if stripped.startswith("# "):
                doc.add_heading(stripped[2:].strip(), level=1)
            elif stripped.startswith("## "):
                doc.add_heading(stripped[3:].strip(), level=2)
            elif stripped.startswith("### "):
                doc.add_heading(stripped[4:].strip(), level=3)
            elif stripped.startswith("- ") or stripped.startswith("* "):
                doc.add_paragraph(stripped[2:].strip(), style="List Bullet")
            elif re.match(r"^\d+\.\s+", stripped):
                item_text = re.sub(r"^\d+\.\s+", "", stripped)
                doc.add_paragraph(item_text, style="List Number")
            elif stripped:
                doc.add_paragraph(stripped)
        doc.save(str(target))

    def _generate_pptx(self, target: Path, content: str) -> None:
        from pptx import Presentation
        prs = Presentation()
        raw_slides = re.split(r"\n---\n", content)
        if len(raw_slides) <= 1:
            sections = re.split(r"\n(?=##\s+)", content)
            if len(sections) > 1:
                raw_slides = sections

        slide_index = 0
        for s_text in raw_slides:
            s_text = s_text.strip()
            if not s_text:
                continue
            lines = [line.strip() for line in s_text.splitlines() if line.strip()]
            if not lines:
                continue

            if slide_index == 0:
                slide = prs.slides.add_slide(prs.slide_layouts[0])
                title = slide.shapes.title
                subtitle = slide.placeholders[1] if len(slide.placeholders) > 1 else None
                title.text = lines[0].lstrip("#").strip()
                if subtitle and len(lines) > 1:
                    subtitle.text = "\n".join(lines[1:])
            else:
                slide = prs.slides.add_slide(prs.slide_layouts[1])
                title = slide.shapes.title
                content_shape = slide.placeholders[1] if len(slide.placeholders) > 1 else None
                title.text = lines[0].lstrip("#").strip()
                if content_shape and len(lines) > 1:
                    tf = content_shape.text_frame
                    tf.clear()
                    for i, b_line in enumerate(lines[1:]):
                        clean_line = b_line.lstrip("*-#0123456789. ")
                        if i == 0:
                            tf.text = clean_line
                        else:
                            p = tf.add_paragraph()
                            p.text = clean_line
            slide_index += 1

        if slide_index == 0:
            slide = prs.slides.add_slide(prs.slide_layouts[0])
            slide.shapes.title.text = "ORBIS Presentation"

        prs.save(str(target))

    def _generate_pdf(self, target: Path, content: str) -> None:
        import html
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        doc = SimpleDocTemplate(str(target), pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        lines = content.splitlines()
        for line in lines:
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 10))
                continue
            safe_line = html.escape(stripped)
            if stripped.startswith("# "):
                story.append(Paragraph(f"<b>{html.escape(stripped[2:].strip())}</b>", styles["Title"]))
                story.append(Spacer(1, 12))
            elif stripped.startswith("## "):
                story.append(Paragraph(f"<b>{html.escape(stripped[3:].strip())}</b>", styles["Heading2"]))
                story.append(Spacer(1, 8))
            elif stripped.startswith("### "):
                story.append(Paragraph(f"<b>{html.escape(stripped[4:].strip())}</b>", styles["Heading3"]))
                story.append(Spacer(1, 6))
            elif stripped.startswith("- ") or stripped.startswith("* "):
                bullet_text = html.escape(stripped[2:].strip())
                story.append(Paragraph(f"&bull; {bullet_text}", styles["Normal"]))
                story.append(Spacer(1, 4))
            else:
                story.append(Paragraph(safe_line, styles["Normal"]))
                story.append(Spacer(1, 6))

        if not story:
            story.append(Paragraph("ORBIS Document", styles["Title"]))

        doc.build(story)

    def _generate_xlsx(self, target: Path, content: str) -> None:
        import csv
        import io
        import openpyxl
        from openpyxl.styles import Font, PatternFill

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        row_idx = 1
        for line in lines:
            if "|" in line:
                cells = [c.strip() for c in line.split("|")]
                if line.startswith("|"):
                    cells = cells[1:]
                if line.endswith("|"):
                    cells = cells[:-1]
                if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                    continue
            elif "\t" in line:
                cells = [c.strip() for c in line.split("\t")]
            elif "," in line:
                reader = csv.reader(io.StringIO(line))
                cells = next(reader, [line])
            else:
                cells = [line]

            for col_idx, val in enumerate(cells, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                if row_idx == 1:
                    cell.font = Font(bold=True)
                    cell.fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
            row_idx += 1

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(str(target))

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """
        Independently verifies that the real-world file on disk matches expectations:
        file exists, is within allowed root, non-empty, and format-valid.
        """
        if not result.success:
            return VerificationReport(
                verified=False,
                details="Document persistence reported failure.",
                discrepancies=[result.error or "Unknown failure"],
            )

        raw_path = getattr(params, "file_path", None)
        if not raw_path:
            return VerificationReport(
                verified=False,
                details="Missing file_path parameter for verification.",
                discrepancies=["No file_path provided in params."],
            )

        discrepancies = []
        try:
            target = self.resolve_safe_path(raw_path)
        except Exception as err:
            return VerificationReport(
                verified=False,
                details=f"Verification failed path resolution: {err}",
                discrepancies=[str(err)],
            )

        # 1. Existence and type check
        if not target.exists():
            discrepancies.append(f"Target document does not exist on disk: '{target}'.")
        elif not target.is_file():
            discrepancies.append(f"Target path exists but is not a regular file: '{target}'.")

        if discrepancies:
            return VerificationReport(
                verified=False,
                details="Document filesystem existence verification failed.",
                discrepancies=discrepancies,
            )

        # 2. Size bounds check
        file_size = target.stat().st_size
        if file_size == 0:
            discrepancies.append(f"Target document on disk is empty (0 bytes): '{target}'.")
        if file_size > self.max_bytes:
            discrepancies.append(f"Target document size ({file_size} bytes) exceeds maximum limit ({self.max_bytes} bytes).")

        # 3. Format-specific validation
        fmt = self._infer_format(target, getattr(params, "format", "markdown"))
        if fmt in ("markdown", "text"):
            try:
                disk_content = target.read_text(encoding="utf-8")
                expected_content = redact_secrets(getattr(params, "content", ""))
                if disk_content != expected_content:
                    discrepancies.append("Content on disk does not match expected sanitized document text.")
            except Exception as read_err:
                discrepancies.append(f"Failed to read document from disk for verification: {read_err}")
        elif fmt == "docx":
            try:
                from docx import Document
                d = Document(str(target))
                if len(d.paragraphs) == 0 and len(d.tables) == 0:
                    discrepancies.append("DOCX file was created but contains no paragraphs or tables.")
            except Exception as e:
                discrepancies.append(f"Failed to parse created DOCX file: {e}")
        elif fmt == "pptx":
            try:
                from pptx import Presentation
                prs = Presentation(str(target))
                if len(prs.slides) == 0:
                    discrepancies.append("PPTX file was created but contains 0 slides.")
            except Exception as e:
                discrepancies.append(f"Failed to parse created PPTX file: {e}")
        elif fmt == "pdf":
            try:
                with open(target, "rb") as f:
                    header = f.read(5)
                if not header.startswith(b"%PDF-"):
                    discrepancies.append("PDF file does not have a valid %PDF header.")
            except Exception as e:
                discrepancies.append(f"Failed to verify PDF header: {e}")
        elif fmt == "xlsx":
            try:
                import openpyxl
                wb = openpyxl.load_workbook(str(target), read_only=True)
                if not wb.sheetnames:
                    discrepancies.append("XLSX file was created but has no sheets.")
                wb.close()
            except Exception as e:
                discrepancies.append(f"Failed to parse created XLSX file: {e}")

        if discrepancies:
            return VerificationReport(
                verified=False,
                details="Document verification detected discrepancies on disk.",
                discrepancies=discrepancies,
            )

        return VerificationReport(
            verified=True,
            details=f"Document successfully verified on disk ({file_size} bytes, format '{fmt}' at '{target.name}').",
        )
