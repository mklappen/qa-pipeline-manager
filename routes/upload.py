import io
from fastapi import APIRouter, UploadFile, File, HTTPException

router = APIRouter()

SUPPORTED = {".txt", ".md", ".pdf", ".docx"}


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in SUPPORTED:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Supported: .txt, .md, .pdf, .docx")

    content = await file.read()

    if ext in (".txt", ".md"):
        return {"text": content.decode("utf-8", errors="replace")}

    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            return {"text": "\n\n".join(p for p in pages if p.strip())}
        except ImportError:
            raise HTTPException(500, "pypdf not installed.")
        except Exception as exc:
            raise HTTPException(400, f"Failed to parse PDF: {exc}")

    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return {"text": "\n\n".join(paragraphs)}
        except ImportError:
            raise HTTPException(500, "python-docx not installed.")
        except Exception as exc:
            raise HTTPException(400, f"Failed to parse DOCX: {exc}")
