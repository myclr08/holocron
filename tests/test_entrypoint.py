"""Baslatici: port secimi ve komut satiri secenekleri."""

from __future__ import annotations

import socket

import pytest

from app.__main__ import HOST, PREFERRED_PORT, parse_args, pick_port
from app.lifecycle import BEAT_TIMEOUT_SECONDS


def test_prefers_the_default_port_when_free():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((HOST, PREFERRED_PORT))
    except OSError:
        pytest.skip("Varsayilan port bu makinede kullanimda.")
    finally:
        probe.close()
    assert pick_port() == PREFERRED_PORT


def test_falls_back_to_a_free_port_when_taken():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind((HOST, PREFERRED_PORT))
    holder.listen(1)
    try:
        port = pick_port()
        assert port != PREFERRED_PORT
        assert 1024 < port < 65536
    finally:
        holder.close()


def test_defaults():
    args = parse_args([])
    assert args.port is None
    assert args.no_browser is False
    assert args.timeout == BEAT_TIMEOUT_SECONDS


def test_options_are_parsed():
    args = parse_args(["--port", "9000", "--no-browser", "--timeout", "60"])
    assert args.port == 9000
    assert args.no_browser is True
    assert args.timeout == 60
