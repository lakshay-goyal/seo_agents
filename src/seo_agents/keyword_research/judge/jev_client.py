"""Thin wrapper over the TypeSafe Python SDK (typesafe-sdk, sync client) for Jev System One calls.

* Questions arrive as plain dicts {type, instructions, criteria} (built in jev_questions.py) and are converted to SDK
  Noul / Choice / Score objects here, so the logged payload is exactly what was sent.
* Every request is written to <evidence_dir>/jev/<payload_id>.json (state, questions, model, then answers or error).
  payload_id = sha256 of the canonical request, so a rerun of the same request maps to the same file.
* The API key comes from TYPESAFE_API_KEY and is never logged or written.
* Any SDK / network / validation failure raises JevError. Callers must treat it as an error, never as a "no".

Env: TYPESAFE_API_KEY (required for live mode), TYPESAFE_MODEL (default jev-latest; pin e.g. jev-1.14.0 once the
thresholds are tuned), TYPESAFE_BASE_URL (optional, read by the SDK).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'jev-latest'
MAX_RETRIES = 3
REQUEST_TIMEOUT_SECONDS = 30.0


class JevError(RuntimeError):
    """A Jev call failed (network, auth, rate limit after retries, invalid response). Never a 'no'."""

    def __init__(self, message: str, payload_ref: str | None = None):
        super().__init__(message)
        self.payload_ref = payload_ref


@dataclass
class JevResult:
    answers: dict[str, dict]          # {question_id: {'type': 'noul', 'noul': p} | {'type': 'choice', 'choice', ...}}
    model: str | None
    payload_ref: str | None
    usage: dict = field(default_factory=dict)


class JevBackend(Protocol):
    def ask(self, state: dict, questions: dict[str, dict]) -> JevResult: ...


def payload_id(model: str, state, questions) -> str:
    canonical = json.dumps({'model': model, 'state': state, 'questions': questions}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class PayloadLog:
    """Writes the exact request (and its outcome) to <evidence_dir>/jev/<id>.json. No secrets are ever passed in."""

    def __init__(self, evidence_dir: str | Path | None):
        self.dir = Path(evidence_dir) / 'jev' if evidence_dir else None

    def write(self, pid: str, record: dict) -> str:
        """Returns the payload ref 'jev/<id>.json' (relative to the evidence dir)."""
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / f'{pid}.json').write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str))
        return f'jev/{pid}.json'


def _normalize_answer(answer) -> dict:
    data = answer.model_dump() if hasattr(answer, 'model_dump') else dict(answer)
    kind = data.get('type')
    if kind == 'noul':
        return {'type': 'noul', 'noul': float(data['noul'])}
    if kind == 'choice':
        return {'type': 'choice', 'choice': data['choice'], 'probabilities': data.get('probabilities'),
                'confidence': data.get('confidence')}
    if kind == 'score':
        return {'type': 'score', 'score': float(data['score']), 'probabilities': data.get('probabilities'),
                'legend': data.get('legend'), 'confidence': data.get('confidence')}
    raise JevError(f'unexpected answer type {kind!r}')


def _to_sdk_questions(questions: dict[str, dict]):
    from typesafe_sdk import Choice, Noul, Score
    out = {}
    for qid, q in questions.items():
        if q['type'] == 'noul':
            out[qid] = Noul(instructions=q['instructions'], criteria=q.get('criteria'))
        elif q['type'] == 'choice':
            out[qid] = Choice(instructions=q['instructions'], criteria=q['criteria'])
        elif q['type'] == 'score':
            out[qid] = Score(instructions=q['instructions'], criteria=q['criteria'])
        else:
            raise ValueError(f'unknown question type {q["type"]!r} for {qid}')
    return out


class JevClient:
    """Live client. Construct with `from_env()`; returns None there when TYPESAFE_API_KEY is not set."""

    def __init__(self, *, api_key: str, model: str = DEFAULT_MODEL, evidence_dir: str | Path | None = None,
                 max_retries: int = MAX_RETRIES, timeout: float = REQUEST_TIMEOUT_SECONDS, sdk_client=None):
        self.model = model
        self.log = PayloadLog(evidence_dir)
        if sdk_client is None:
            from typesafe_sdk import RetryPolicy, TypeSafeClient
            sdk_client = TypeSafeClient(api_key=api_key, model=model, timeout=timeout,
                                        retry=RetryPolicy(max_retries=max_retries, timeout=timeout * (max_retries + 1)))
        self._client = sdk_client

    @classmethod
    def from_env(cls, *, evidence_dir: str | Path | None = None) -> 'JevClient | None':
        api_key = os.getenv('TYPESAFE_API_KEY', '').strip()
        if not api_key:
            return None
        return cls(api_key=api_key, model=os.getenv('TYPESAFE_MODEL', '').strip() or DEFAULT_MODEL,
                   evidence_dir=evidence_dir)

    def ask(self, state: dict, questions: dict[str, dict]) -> JevResult:
        pid = payload_id(self.model, state, questions)
        record = {'payload_id': pid, 'requested_at': datetime.now(timezone.utc).isoformat(), 'model': self.model,
                  'state': state, 'questions': questions}
        started = time.monotonic()
        try:
            response = self._client.system_one(state=state, questions=_to_sdk_questions(questions), model=self.model)
            answers = {qid: _normalize_answer(a) for qid, a in response.answers.items()}
        except Exception as exc:   # SDK errors, network, pydantic validation: all are errors, never a "no"
            record.update(error=f'{type(exc).__name__}: {exc}', elapsed_s=round(time.monotonic() - started, 3))
            ref = self.log.write(pid, record)
            logger.warning('Jev call %s failed: %s', pid, record['error'])
            raise JevError(record['error'], payload_ref=ref) from exc
        usage = getattr(response, 'usage', None)
        usage = usage.model_dump() if hasattr(usage, 'model_dump') else {}
        record.update(response_model=getattr(response, 'model', None), usage=usage, answers=answers,
                      request_id=getattr(response, 'request_id', None), elapsed_s=round(time.monotonic() - started, 3))
        ref = self.log.write(pid, record)
        return JevResult(answers=answers, model=getattr(response, 'model', None) or self.model, payload_ref=ref,
                         usage=usage)
