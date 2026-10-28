# ADR-0007: Citation format, verification and the streaming protocol

Status: accepted (2026-10-28)

## Format
The model cites as `[n: "verbatim quote"]`, where `n` is the number of the `<document>` block in
the prompt. The UI shows `[n]` and displays the quote in the citation viewer.

## Verification (code, not model)
A citation is valid when the quote, after NFKC normalisation, casefolding, whitespace collapsing
and folding curly quotes and dashes to ASCII, is an **exact substring** of block `n`, and is at
least 12 characters long. There is deliberately no fuzzy matching: a paraphrase does not count
as a quote. A bare `[n]` without a quote cannot be verified, so it counts as a failure.

- **Grounded mode:** the answer must have at least one citation, and every citation must be
  valid. Otherwise the model gets one retry, with the failures listed. If the retry also fails,
  the answer is returned with `flagged = true` and the UI shows a warning.
- **General mode:** only invalid citations trigger the retry. Sentences without a valid
  citation are labelled "general knowledge, not from your documents".
- **Refusal:** decided only by the confidence gate, before generation. When the gate fails, the
  model is never called.

## Streaming
`POST /chat` returns Server-Sent Events: `meta`, `token`…, then optionally `retry` (the client
discards the tokens shown so far), then `token`… again, and finally `final`, which carries the
verified answer, citations, segments, flags and `message_id`. Tokens stream live; verification
needs the full text, so its verdict arrives in `final`.
