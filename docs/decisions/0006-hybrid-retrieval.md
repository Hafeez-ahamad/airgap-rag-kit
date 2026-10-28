# ADR-0006: Hybrid retrieval with app-side BM25 and RRF

Status: accepted (2026-10-28)

- **Sparse side:** classic BM25, as the spec says, rather than bge-m3's learned sparse vectors.
  bge-m3 sparse vectors become a Phase 4 ablation. Documents store the BM25 term-frequency
  component; Qdrant applies IDF at query time (`modifier=IDF`). `avg_doc_len` is a fixed constant
  (180 tokens), so indexing one file never changes another file's vectors. The cost is a
  slightly imprecise length normalisation.
- **Tokenizer:** lowercase word tokens. The pattern includes the Indic Unicode blocks explicitly,
  because `\w` alone splits Devanagari words at vowel signs. There is a small English stopword
  list and no stemming yet.
- **Fusion:** reciprocal rank fusion (k = 60), done in application code rather than by Qdrant's
  built-in fusion. This keeps fusion testable and independent of the store.
- **ACL:** both searches carry the same `acl_groups` `MatchAny` filter. An empty group list
  returns nothing; it never means "no filter".
- **Rerank and gate:** the top 20 fused candidates are reranked, and the top 5 are used as context.
  The confidence gate compares the best reranker score (0 to 1) with
  `threshold + tier_offset`. The default threshold of 0.30 is **uncalibrated** until the Phase 3
  eval set exists.
