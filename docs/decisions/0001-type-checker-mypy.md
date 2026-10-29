# ADR-0001: mypy in strict mode

Status: accepted (2026-10-28)

REQUIREMENTS §3 asks for mypy or pyright. **Decision: mypy with `strict = true`**, configured in
`backend/pyproject.toml`.

- It runs from the uv lockfile with no Node runtime in the Python CI job.
- Its SQLAlchemy 2 `Mapped[...]` and Pydantic v2 support works without plugins.
- Third-party modules without type stubs (python-docx, pypdfium2, sentence-transformers, torch)
  are listed under `ignore_missing_imports`. Our wrappers around them are fully typed.
