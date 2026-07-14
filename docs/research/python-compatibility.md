# Python compatibility research

**Decision:** current official YASB installation guidance requires Python 3.14 or newer. For Limitora, recommend Python 3.10 as the practical Windows/Linux support floor until compatibility is validated in a future implementation phase. This document does not change `requires-python`.

**Research access date:** 2026-07-14. Public documentation and primary project evidence were used; no project configuration was changed.

## Current YASB support

| Question | Finding | Evidence | Confidence |
|---|---|---|---|
| What Python version does current YASB support? | The stable YASB installation documentation states “Install Python >= 3.14.” | [YASB installation documentation](https://docs.yasb.dev/latest/installation) (accessed 2026-07-14). | High for the documented current installation requirement. |
| Is this primary project evidence? | Yes. The documentation links to the official `amnweb/yasb` repository, whose public description identifies YASB as a Python status bar. | [YASB GitHub repository](https://github.com/amnweb/yasb) (accessed 2026-07-14). | High. |
| Does that requirement define Limitora's floor? | No. Limitora is provider-agnostic and intentionally UI-free; YASB is a separate potential consumer. | [Limitora architecture](../architecture/README.md) (accessed 2026-07-14). | High. |

If the official installation page changes, this finding must be revalidated rather than inferred from a historical release or an unrelated dependency.

## Recommended Limitora floor

| Platform | Recommended floor | Rationale | Scope |
|---|---:|---|---|
| Windows | Python 3.10 | A practical, widely available baseline for a plain library while leaving room for modern typing and supported packaging tools. | Recommendation only; requires validation before declaration. |
| Linux | Python 3.10 | Provides a shared cross-platform floor and reduces platform-specific support behavior. | Recommendation only; requires validation before declaration. |

This is a product recommendation, not evidence that the current code supports Python 3.10. The repository's `pyproject.toml` intentionally leaves `requires-python` unset pending validation, and it remains unchanged.

## Validation required before adopting the floor

1. Define the actual dependency set and supported operating-system distributions.
2. Run the future test suite on Python 3.10 and the newest supported Python version on Windows and Linux.
3. Verify packaging metadata and type-checking behavior against that matrix.
4. Document any dependency-driven exception before adding a `requires-python` declaration.

## Non-goals

No YASB integration, compatibility configuration, dependency change, test execution, CI update, or `requires-python` edit is included in this research task.
