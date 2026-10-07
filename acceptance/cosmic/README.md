# Cosmic Python acceptance

Repository: https://github.com/cosmicpython/code

Commit: `14c84797ffa77255d53cf1a02fe6aafda2b68aeb`.

An unknown batch reference causes `handlers.change_batch_quantity` to dereference `None`. The message bus re-raises the exception and the Redis consumer dies. The fix raises `InvalidBatchRef` without committing and catches that domain rejection in the consumer. A later valid message must still succeed.

Task-authorized changes:
- `src/allocation/service_layer/handlers.py`
- `src/allocation/entrypoints/redis_eventconsumer.py`
- `tests/unit/test_handlers.py`

These are task instructions, not protected-path policies. Independent acceptance tests remain outside the writable target clone and are mounted read-only only for configured checks.

Run `.venv/bin/harness check --config harness.toml` for baseline verification, or add `--workspace runs/<id>/repo` for an existing run. The baseline must fail acceptance; the reference patch must pass acceptance and regression. No baseline tests are deselected.
