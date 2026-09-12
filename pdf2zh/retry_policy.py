"""D2 typed retry budgets; does not validate, route, or select outcomes."""
from __future__ import annotations

from typing import Any

from pdf2zh.integrity import TranslationIntegrityError
from pdf2zh.invariants import TechnicalInvariantError, VerifiedProperNameError
from pdf2zh.terminology import TerminologyConsistencyError
from pdf2zh.translator import FormulaPlaceholderError, SegmentTooLongError

CONTENT_QUALITY_MAX_ATTEMPTS = 2
PRE_PROVIDER_MAX_ATTEMPTS = 1
TRANSPORT_MAX_ATTEMPTS = 8

# The only current SegmentTooLongError raise site is Google.do_translate's
# payload-length guard, before session.get. No post-provider variant exists.
PRE_PROVIDER_DETERMINISTIC_ERRORS = (SegmentTooLongError,)
CONTENT_QUALITY_ERRORS = (
    VerifiedProperNameError,
    TechnicalInvariantError,
    FormulaPlaceholderError,
    TranslationIntegrityError,
    TerminologyConsistencyError,
)


def _is_content_quality_error(error: BaseException) -> bool:
    return isinstance(error, CONTENT_QUALITY_ERRORS)


def _stop_by_failure_class(retry_state: Any) -> bool:
    outcome = retry_state.outcome
    error = outcome.exception() if outcome is not None and outcome.failed else None
    if isinstance(error, PRE_PROVIDER_DETERMINISTIC_ERRORS):
        budget = PRE_PROVIDER_MAX_ATTEMPTS
    elif error is not None and _is_content_quality_error(error):
        budget = CONTENT_QUALITY_MAX_ATTEMPTS
    else:
        budget = TRANSPORT_MAX_ATTEMPTS
    return retry_state.attempt_number >= budget


def _estimated_content_retry_backoff_seconds(max_attempts: int) -> float:
    return float(sum(min(60, 2 ** index) for index in range(max(0, max_attempts - 1))))


def _reason_from_exception(error: BaseException) -> str:
    """Keep the existing worker's exception-class reason, not message text."""
    return type(error).__name__
