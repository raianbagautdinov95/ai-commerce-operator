"""Six digits is only safe with a ceiling on guesses.

A million possibilities sounds like a lot until you can try them. Every test
here is about one of the three limits that make the number defensible: the code
expires, wrong answers run out, and fresh codes cannot be farmed to guess
against. The last test is the one that would be a breach: a code must never be
readable from the database it is stored in.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import datetime as dt

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounts import InvalidEmailError
from app import auth_email
from app.auth_email import (MAX_ATTEMPTS, MAX_CODES_PER_HOUR, LoginCodeError,
                            LoginCodesUnavailable, TooManyLoginCodes,
                            request_login_code, verify_login_code)
from app.db import models
from app.db.models import Base

EMAIL = "seller@example.com"


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s" * 48)
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def _rows(db):
    return list(db.scalars(select(models.LoginCode)))


def test_a_fresh_code_signs_the_address_in(db):
    address, code = request_login_code(db, email=" Seller@Example.com ")
    assert address == EMAIL
    assert code.isdigit() and len(code) == 6
    assert verify_login_code(db, email=EMAIL, code=code) == EMAIL


def test_a_code_works_once(db):
    _, code = request_login_code(db, email=EMAIL)
    verify_login_code(db, email=EMAIL, code=code)
    with pytest.raises(LoginCodeError):
        verify_login_code(db, email=EMAIL, code=code)


def test_the_plain_code_is_never_stored(db):
    """A copy of the database must not be a way to sign in as anyone."""
    _, code = request_login_code(db, email=EMAIL)
    stored = _rows(db)[0]
    assert code not in stored.code_hash
    assert len(stored.code_hash) == 64


def test_rotating_the_signing_secret_invalidates_pending_codes(db, monkeypatch):
    _, code = request_login_code(db, email=EMAIL)
    monkeypatch.setenv("JWT_SECRET", "r" * 48)
    with pytest.raises(LoginCodeError):
        verify_login_code(db, email=EMAIL, code=code)


def test_an_expired_code_is_refused(db):
    _, code = request_login_code(db, email=EMAIL)
    row = _rows(db)[0]
    row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    db.add(row); db.commit()
    with pytest.raises(LoginCodeError):
        verify_login_code(db, email=EMAIL, code=code)


def test_wrong_answers_run_out_and_burn_the_code(db):
    _, code = request_login_code(db, email=EMAIL)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(LoginCodeError):
            verify_login_code(db, email=EMAIL, code=wrong)
    # Even the right code is dead once the attempts are spent.
    with pytest.raises(LoginCodeError):
        verify_login_code(db, email=EMAIL, code=code)


def test_asking_for_a_new_code_retires_the_old_one(db):
    _, first = request_login_code(db, email=EMAIL)
    _, second = request_login_code(db, email=EMAIL)
    with pytest.raises(LoginCodeError):
        verify_login_code(db, email=EMAIL, code=first)
    assert verify_login_code(db, email=EMAIL, code=second) == EMAIL


def test_codes_cannot_be_farmed_to_guess_against(db):
    for _ in range(MAX_CODES_PER_HOUR):
        request_login_code(db, email=EMAIL)
    with pytest.raises(TooManyLoginCodes) as exc:
        request_login_code(db, email=EMAIL)
    assert exc.value.status_code == 429


def test_the_hourly_limit_lets_go_of_old_requests(db):
    for _ in range(MAX_CODES_PER_HOUR):
        request_login_code(db, email=EMAIL)
    long_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    for row in _rows(db):
        row.created_at = long_ago
        db.add(row)
    db.commit()
    address, code = request_login_code(db, email=EMAIL)
    assert verify_login_code(db, email=address, code=code) == EMAIL


def test_the_limit_is_per_address(db):
    for _ in range(MAX_CODES_PER_HOUR):
        request_login_code(db, email=EMAIL)
    _, code = request_login_code(db, email="someone.else@example.com")
    assert verify_login_code(db, email="someone.else@example.com", code=code)


def test_every_refusal_says_the_same_thing(db):
    """Telling 'wrong code' apart from 'no such request' halves the work."""
    _, code = request_login_code(db, email=EMAIL)
    wrong = "000000" if code != "000000" else "111111"
    with pytest.raises(LoginCodeError) as bad_code:
        verify_login_code(db, email=EMAIL, code=wrong)
    with pytest.raises(LoginCodeError) as no_request:
        verify_login_code(db, email="nobody@example.com", code=wrong)
    assert str(bad_code.value) == str(no_request.value)


def test_a_malformed_address_is_refused_before_a_code_exists(db):
    for value in ("", "   ", "not-an-address", "a@b", "two@@example.com",
                  "sp ace@example.com"):
        with pytest.raises(InvalidEmailError):
            request_login_code(db, email=value)
    assert _rows(db) == []


def test_a_server_without_a_signing_secret_refuses_to_issue_codes(db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "short")
    with pytest.raises(LoginCodeError) as exc:
        request_login_code(db, email=EMAIL)
    assert "JWT_SECRET" in str(exc.value)


def test_a_server_without_a_secret_says_so_rather_than_blaming_the_caller(db, monkeypatch):
    """A deployment missing JWT_SECRET cannot issue a code to anybody.

    This answered 400 for a while, which tells the person their request was
    wrong and sends them to check their own email address. The request was
    fine; the server is not ready. 503 is the difference between "you made a
    mistake" and "come back when this is fixed".
    """
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(LoginCodesUnavailable) as raised:
        request_login_code(db, email="seller@example.com")
    assert raised.value.status_code == 503
    assert "JWT_SECRET" in str(raised.value)


def test_a_short_secret_is_refused_the_same_way(db, monkeypatch):
    """Short is as unusable as absent: the HMAC would be trivially forgeable."""
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(LoginCodesUnavailable) as raised:
        request_login_code(db, email="seller@example.com")
    assert raised.value.status_code == 503


def test_it_is_still_a_login_code_error():
    """The router maps any LoginCodeError to its status_code, so the new class
    must stay inside that family or it stops being handled at all."""
    assert issubclass(LoginCodesUnavailable, LoginCodeError)
