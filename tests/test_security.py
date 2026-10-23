import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from brsy.security import AuthError, parse_token, validate_init_data

BOT_TOKEN = '123456789:' + 'x' * 35


def signed(uid=42, *, now=1000, **extra):
    fields = {'auth_date': str(now), 'user': json.dumps({'id': uid, 'first_name': 'Александр'}), **extra}
    check = '\n'.join(f'{k}={v}' for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid_signature_and_unicode_user():
    assert validate_init_data(signed(), BOT_TOKEN, now=1100)['id'] == 42


@pytest.mark.parametrize('raw,clock', [(signed() + '&user=spoof', 1000), (signed().replace('auth_date=1000', 'auth_date=1001'), 1000), (signed(), 5000), (signed(), 900), ('', 1000)])
def test_reject_tampered_duplicate_expired_future_or_missing(raw, clock):
    with pytest.raises(AuthError):
        validate_init_data(raw, BOT_TOKEN, now=clock)


@pytest.mark.parametrize('uid', [0, -1, '42', True])
def test_reject_bad_user_ids(uid):
    with pytest.raises(AuthError):
        validate_init_data(signed(uid), BOT_TOKEN, now=1000)


def test_token_parsing_does_not_accept_page_or_urls():
    assert parse_token(' "' + 'a' * 40 + '"\n') == 'a' * 40
    for bad in ['password', 'x' * 39, 'https://example.com/' + 'a' * 40, '<p>' + 'a' * 40 + '</p>', 'a' * 40 + '\n' + 'b' * 40]:
        with pytest.raises(ValueError):
            parse_token(bad)
