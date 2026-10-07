import json
import pytest
from allocation import bootstrap
from allocation.adapters import notifications, repository
from allocation.domain import commands
from allocation.entrypoints import redis_eventconsumer
from allocation.service_layer import handlers, unit_of_work


class _Repo(repository.AbstractRepository):
    def __init__(self, products):
        super().__init__()
        self._products = set(products)

    def _add(self, product):
        self._products.add(product)

    def _get(self, sku):
        return next((p for p in self._products if p.sku == sku), None)

    def _get_by_batchref(self, batchref):
        return next((p for p in self._products for b in p.batches if b.reference == batchref), None)


class _UoW(unit_of_work.AbstractUnitOfWork):
    def __init__(self):
        self.products = _Repo([])
        self.commits = 0

    def _commit(self):
        self.commits += 1

    def rollback(self):
        pass


class _Notifications(notifications.AbstractNotifications):
    def send(self, destination, message):
        pass


def _bus():
    bus = bootstrap.bootstrap(start_orm=False, uow=_UoW(), notifications=_Notifications(), publish=lambda *a: None)
    bus.handle(commands.CreateBatch('b1', 'SMALL-TABLE', 100, None))
    return bus


def test_unknown_batchref_raises_invalid_batch_ref():
    bus = _bus()
    commits_before = bus.uow.commits
    with pytest.raises(handlers.InvalidBatchRef, match='Invalid batch ref NONEXISTENT'):
        bus.handle(commands.ChangeBatchQuantity('NONEXISTENT', 10))
    assert bus.uow.commits == commits_before


def test_known_batchref_still_changes_quantity():
    bus = _bus()
    bus.handle(commands.ChangeBatchQuantity('b1', 50))
    assert bus.uow.products.get_by_batchref('b1').batches[0].available_quantity == 50


def test_redis_consumer_survives_unknown_batchref(caplog):
    bus = _bus()
    redis_eventconsumer.handle_change_batch_quantity({'data': json.dumps({'batchref': 'NONEXISTENT', 'qty': 10})}, bus)
    assert 'Invalid batch ref NONEXISTENT' in caplog.text
    redis_eventconsumer.handle_change_batch_quantity({'data': json.dumps({'batchref': 'b1', 'qty': 50})}, bus)
    assert bus.uow.products.get_by_batchref('b1').batches[0].available_quantity == 50
