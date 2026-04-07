from __future__ import annotations

import re

from cryptobot.core.ids import new_order_id, new_run_id


def test_new_run_id_shape():
    rid = new_run_id()
    assert re.match(r"^run-\d{8}T\d{6}Z-[0-9a-f]{8}$", rid), rid


def test_new_run_id_unique():
    assert new_run_id() != new_run_id()


def test_new_order_id_shape_and_unique():
    a, b = new_order_id(), new_order_id()
    assert a.startswith("ord-")
    assert b.startswith("ord-")
    assert a != b
