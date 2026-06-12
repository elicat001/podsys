"""计费扣点服务单测:走 conftest 准备的 MySQL 测试库(*_test),直接用 SessionLocal。"""
from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.models_db import User
from app.services.billing import (
    COST,
    InsufficientCredits,
    charge,
    cost_of,
)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _new_user(db, credits: int = 100) -> User:
    import uuid
    user = User(email=f"u-{uuid.uuid4().hex}@test.local", password_hash="x", credits=credits)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_cost_of_unknown_defaults_to_one():
    assert cost_of("unknown") == 1
    assert cost_of("process") == 2
    assert cost_of("generate") == 5
    assert cost_of("edit") == 4
    assert cost_of("asset") == 1


def test_charge_deducts_cost(db):
    user = _new_user(db, credits=100)
    balance = charge(db, user, "process")
    assert balance == 98
    assert user.credits == 98


def test_charge_until_insufficient_raises(db):
    # 余额 100,process 每次 2 点 -> 50 次后归零,第 51 次余额不足
    user = _new_user(db, credits=100)
    for _ in range(50):
        charge(db, user, "process")
    assert user.credits == 0
    with pytest.raises(InsufficientCredits):
        charge(db, user, "process")


def test_charge_insufficient_does_not_go_negative(db):
    user = _new_user(db, credits=3)
    # generate 需 5 点,余额 3 不足
    with pytest.raises(InsufficientCredits):
        charge(db, user, "generate")
    assert user.credits == 3  # 未被扣减


def test_cost_dict_values():
    # 核心计费项固定;新增项(如 video=3)按子集校验,避免每加一项就改死断言
    for k, v in {"process": 2, "generate": 5, "edit": 4, "asset": 1}.items():
        assert COST[k] == v
