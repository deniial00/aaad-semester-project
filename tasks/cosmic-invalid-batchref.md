Bug: sending a `ChangeBatchQuantity` command for a batch reference that does not exist crashes with `AttributeError: 'NoneType' object has no attribute 'change_batch_quantity'` in `src/allocation/service_layer/handlers.py`. The message bus re-raises it, so one bad message also kills the Redis consumer in `src/allocation/entrypoints/redis_eventconsumer.py`.

Fix requirements:
1. Add a separate top-level `InvalidBatchRef` next to `InvalidSku`; preserve `InvalidSku`. Raise `InvalidBatchRef(f"Invalid batch ref {cmd.ref}")` for a missing product, without committing.
2. Import and catch `InvalidBatchRef` in the consumer, log it with `logger.error`, and return without raising. Preserve the message format and valid-message behavior.
3. Add an invalid-reference unit test to the existing `TestChangeBatchQuantity` class. Preserve every existing test and fake collaborator.

Only change those three files. Inspect all three with `read_file`, and use `search` to locate the relevant symbols. Then make all five minimal replacements below with the native `edit_file` tool. The JSON objects below are argument examples for native tool calls, not a format for your final reply. Copy their string values exactly: no extra indentation, no numbered-line prefixes. Do not rewrite entire functions or overwrite the test module. If a replacement fails, re-read that small section and correct it; do not repeat the same failed arguments.

## Add the exception without removing InvalidSku

```json
{"path":"src/allocation/service_layer/handlers.py","old_text":"class InvalidSku(Exception):\n    pass","new_text":"class InvalidSku(Exception):\n    pass\n\n\nclass InvalidBatchRef(Exception):\n    pass"}
```

## Guard the missing product inside the unit of work

```json
{"path":"src/allocation/service_layer/handlers.py","old_text":"        product = uow.products.get_by_batchref(batchref=cmd.ref)","new_text":"        product = uow.products.get_by_batchref(batchref=cmd.ref)\n        if product is None:\n            raise InvalidBatchRef(f\"Invalid batch ref {cmd.ref}\")"}
```

## Import the exception in the consumer

```json
{"path":"src/allocation/entrypoints/redis_eventconsumer.py","old_text":"from allocation.domain import commands","new_text":"from allocation.domain import commands\nfrom allocation.service_layer.handlers import InvalidBatchRef"}
```

## Catch the rejected command, without duplicating the function body

```json
{"path":"src/allocation/entrypoints/redis_eventconsumer.py","old_text":"    bus.handle(cmd)","new_text":"    try:\n        bus.handle(cmd)\n    except InvalidBatchRef as e:\n        logger.error(\"Rejected change_batch_quantity: %s\", e)"}
```

## Add a unit test while preserving the existing class and tests

```json
{"path":"tests/unit/test_handlers.py","old_text":"class TestChangeBatchQuantity:","new_text":"class TestChangeBatchQuantity:\n    def test_unknown_batch_ref_does_not_commit(self):\n        bus = bootstrap_test_app()\n        bus.handle(commands.CreateBatch(\"b1\", \"SMALL-TABLE\", 100, None))\n        bus.uow.committed = False\n        with pytest.raises(handlers.InvalidBatchRef, match=\"Invalid batch ref NONEXISTENT\"):\n            bus.handle(commands.ChangeBatchQuantity(\"NONEXISTENT\", 10))\n        assert not bus.uow.committed\n"}
```

After ALL five edits succeed, run `pytest tests/unit -q` with `run_command`. The user must approve the command. Read the actual test result; if it fails, inspect and fix the cause. Finish with a short summary only after tests pass.
