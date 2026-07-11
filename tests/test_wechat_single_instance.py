# tests/test_wechat_single_instance.py — 微信 Bot 单实例锁：双开会重复轮询、重复回复。
import socket

import wechat_ilink

TEST_PORT = 47999  # 避开运行中 bot 的默认锁端口


def _release():
    if wechat_ilink._LOCK_SOCK is not None:
        wechat_ilink._LOCK_SOCK.close()
        wechat_ilink._LOCK_SOCK = None


def test_first_acquire_succeeds():
    try:
        assert wechat_ilink._acquire_single_instance_lock(TEST_PORT) is True
    finally:
        _release()


def test_second_acquire_fails_while_held():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", TEST_PORT))
    holder.listen(1)
    try:
        assert wechat_ilink._acquire_single_instance_lock(TEST_PORT) is False
        assert wechat_ilink._LOCK_SOCK is None
    finally:
        holder.close()


def test_reacquire_after_release():
    assert wechat_ilink._acquire_single_instance_lock(TEST_PORT) is True
    _release()
    try:
        assert wechat_ilink._acquire_single_instance_lock(TEST_PORT) is True
    finally:
        _release()
