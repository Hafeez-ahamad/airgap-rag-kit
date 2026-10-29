# ADR-0011: Offline bundle integrity and signing

Status: accepted (2026-10-29).

- `MANIFEST.json` lists every payload file with size and SHA-256.
- `MANIFEST.json.sig` is a raw **Ed25519** signature (Python `cryptography`, Apache-2.0/BSD)
  over the exact manifest bytes. Chosen over GPG/minisign/cosign because it needs no extra
  binary on the air-gapped target: the verifier is part of the app (`airgap bundle verify`).
- Verification order: signature -> schema and safe paths (no absolute, `..` or backslash
  paths) -> no unlisted files -> no missing files -> size and SHA-256 of every file. Any failure
  rejects the whole bundle before anything is loaded.
- The private key is created on the build machine (`airgap bundle keygen`) and never enters the
  bundle or the repository. The public key ships with the installer.
- Tests: modified file, added file, removed file, edited manifest, wrong key, missing signature,
  traversal path — all rejected.
- Not done: key rotation/revocation, and bundle encryption.
