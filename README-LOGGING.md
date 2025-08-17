# Errors & logging

## Policy
- **No `print()` in library code.** Use opt-in logging only.
- Library functions may accept `logger: Optional[logging.Logger] = None` and/or `log: bool = False`.
- Tests capture logs via `caplog` when needed.

## How to log
```py
from contextforge._logging import resolve_logger

def run_job(job, *, logger=None, log: bool = False):
    log = resolve_logger(logger=logger, enabled=log, name=__name__)
    log.info("starting job %s", job.id)
    ...
```

## Enforcing the rule
- Ruff `T201` is enabled to forbid `print()`.
- A pre-commit hook runs on every commit.

## Migrating legacy `print(...)`
Run the codemod (review diffs before committing):
```
python tools/codemods/replace_prints_with_logging.py contextforge
pre-commit run -a
pytest
```