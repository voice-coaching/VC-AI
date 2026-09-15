"""Bounded PDF text extraction in a disposable subprocess."""
from __future__ import annotations

import io
import multiprocessing


def _extract(connection, content: bytes):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            connection.send({"error": "ENCRYPTED_PDF"})
            return
        if len(reader.pages) > 50:
            connection.send({"error": "TOO_MANY_PAGES"})
            return
        pages, chars = [], 0
        for page in reader.pages:
            text = page.extract_text() or ""
            chars += len(text)
            if chars > 190000:
                connection.send({"error": "EXTRACTED_TEXT_TOO_LARGE"})
                return
            pages.append(text)
        if not any(x.strip() for x in pages):
            connection.send({"error": "OCR_REQUIRED"})
            return
        connection.send({"pages": pages})
    except Exception:
        connection.send({"error": "PDF_EXTRACTION_FAILED"})
    finally:
        connection.close()


def extract_pdf(content: bytes) -> dict:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_extract, args=(child, content), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(20):
            return {"error": "PDF_EXTRACTION_TIMEOUT"}
        return parent.recv()
    except EOFError:
        return {"error": "PDF_EXTRACTION_FAILED"}
    finally:
        parent.close()
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        process.close()
