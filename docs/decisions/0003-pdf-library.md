# ADR-0003: PDF library is pypdfium2, not PyMuPDF

Status: **proposed, awaiting owner approval** (2026-10-28)

BUILD_PROMPT §3 names PyMuPDF. PyMuPDF is licensed under **AGPL-3.0** (or a paid commercial
licence from Artifex). The kit is distributed as an offline bundle under Apache-2.0, so bundling
PyMuPDF would make it, at minimum, an AGPL network-service component. Customers would then owe
AGPL source-offer obligations, and the kit's own licensing would become harder to explain. That
conflicts with BUILD_PROMPT rule 1, which requires permissive reuse that is safe to redistribute.

**Proposal:** use **pypdfium2** (Google's PDFium, BSD-3-Clause / Apache-2.0) for text extraction
and for rendering pages to images for OCR. All PDF access goes through
`airgap/ingestion/parsers/pdf.py`, so changing back costs about one file plus its tests.

Trade-off: PDFium gives less font and layout information than PyMuPDF. Heading detection uses a
text heuristic (`ingestion/headings.py`: numbered or all-caps short lines), not font size. If
retrieval evaluation in Phase 3 shows heading quality matters, revisit this with a
permissively licensed layout analyser.
