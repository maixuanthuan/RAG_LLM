from pathlib import Path

from assistant.ingestion.models import ParsedDocument, ParseError

SUPPORTED_SUFFIXES = {".txt", ".md", ".docx", ".pdf", ".xlsx"}


def _title_from(path: Path) -> str:
    return path.stem.replace("_", " ").strip()


def parse_file(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    title = _title_from(path)
    if suffix in (".txt", ".md"):
        from assistant.ingestion.parsers.txt import parse_txt

        return ParsedDocument(title=title, blocks=parse_txt(path))
    if suffix == ".docx":
        from assistant.ingestion.parsers.docx import parse_docx

        return ParsedDocument(title=title, blocks=parse_docx(path))
    if suffix == ".xlsx":
        from assistant.ingestion.parsers.xlsx import parse_xlsx

        blocks, warnings = parse_xlsx(path)
        return ParsedDocument(title=title, blocks=blocks, warnings=warnings)
    if suffix == ".pdf":
        from assistant.ingestion.parsers.pdf import parse_pdf

        blocks, pages, warnings = parse_pdf(path)
        return ParsedDocument(title=title, blocks=blocks, pages=pages, warnings=warnings)
    raise ParseError(f"Định dạng '{suffix}' chưa được hỗ trợ")
