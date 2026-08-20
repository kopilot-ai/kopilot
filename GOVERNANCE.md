# Governance

Kopilot uses maintainer governance. It is deliberately small and will grow
with the contributor base.

- **Decisions** happen in public: GitHub issues and pull requests. Routine
  changes need one maintainer approval; changes to the safety model,
  autonomy semantics, or CRD schemas need consensus of all maintainers.
- **Maintainers** are listed in MAINTAINERS.md. New maintainers are added by
  consensus of the existing maintainers after sustained, quality
  contributions. Maintainers inactive for six months are moved to emeritus.
- **Releases** are cut by any maintainer: tag, changelog entry, GitHub
  Release. The chart and image publish from CI on the tag.
- **Conduct** is governed by CODE_OF_CONDUCT.md.
- **Vendor neutrality**: Kopilot has no hosted service and no commercial
  edition. Features that only work with one vendor's LLM are rejected; every
  agent feature must work with the self-hosted Ollama path.
