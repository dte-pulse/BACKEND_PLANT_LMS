"""Quick verification of security fixes (run with: python scripts/verify_security_fixes.py)."""
import os

os.environ.setdefault('SECRET_KEY', 'test-verification-key-not-for-production')

# 1. Production + default key must refuse to boot
import importlib
import app.core.config as cfg
os.environ.pop('SECRET_KEY', None)  # simulate a deploy that forgot to set it
os.environ['APP_ENV'] = 'production'
try:
    importlib.reload(cfg)
    print('FAIL: insecure key allowed in production')
except RuntimeError:
    print('PASS: production refuses default SECRET_KEY')
finally:
    os.environ['SECRET_KEY'] = 'test-verification-key-not-for-production'
    os.environ['APP_ENV'] = 'development'
    importlib.reload(cfg)

# 2. CSV formula sanitization
from app.api.v1.endpoints.reports import _csv_safe
assert _csv_safe('=HYPERLINK("http://evil")').startswith("'=")
assert _csv_safe('+1+1').startswith("'+")
assert _csv_safe('-1').startswith("'-")
assert _csv_safe('@cmd').startswith("'@")
assert _csv_safe('normal') == 'normal'
assert _csv_safe(123) == 123
print('PASS: CSV formula sanitization')

# 3. Upload validation
from app.storage.file_storage import validate_upload, UploadValidationError
try:
    validate_upload(b'<html>not a pdf</html>', 'evil.pdf')
    print('FAIL: fake pdf accepted')
except UploadValidationError:
    print('PASS: magic-byte validation rejects fake PDF')
try:
    validate_upload(b'PK\x03\x04 fake', 'evil.exe')
    print('FAIL: bad extension accepted')
except UploadValidationError:
    print('PASS: extension allowlist rejects .exe')
try:
    validate_upload(b'', 'empty.pdf')
    print('FAIL: empty file accepted')
except UploadValidationError:
    print('PASS: empty file rejected')
validate_upload(b'PK\x03\x04rest', 'real.docx')
print('PASS: valid docx accepted')

# 4. Rate limiter logic (with Redis stubbed out)
import app.utils.rate_limit as rl


class FakePipe:
    def __init__(self, store):
        self.store = store

    def incr(self, k):
        self.store[k] = self.store.get(k, 0) + 1
        return self.store[k]

    def expire(self, k, ttl):
        pass

    def execute(self):
        return []


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def ttl(self, k):
        return 900

    def delete(self, k):
        self.store.pop(k, None)

    def setex(self, k, ttl, v):
        self.store[k] = v

    def pipeline(self):
        return FakePipe(self.store)


fake = FakeRedis()
rl._client = lambda: fake

rl.check_login_allowed('emp1', '1.2.3.4')  # no failure counters yet -> allowed
for _ in range(rl.MAX_ATTEMPTS_PER_ACCOUNT):
    rl.record_failed_login('emp1', '10.0.0.1')
try:
    rl.check_login_allowed('emp1', '1.2.3.4')
    print('FAIL: locked account allowed to login')
except rl.LoginRateLimited as e:
    print('PASS: account lockout after', rl.MAX_ATTEMPTS_PER_ACCOUNT, 'failures')

# Different account, same IP over IP budget
for _ in range(rl.MAX_ATTEMPTS_PER_IP):
    rl.record_failed_login(f'user{_}', '9.9.9.9')
try:
    rl.check_login_allowed('fresh_user', '9.9.9.9')
    print('FAIL: IP over budget allowed')
except rl.LoginRateLimited:
    print('PASS: per-IP limit blocks distributed guessing')

# Successful login clears account counter
rl.clear_failed_logins('emp1')
rl.check_login_allowed('emp1', '7.7.7.7')
print('PASS: clear_failed_logins resets lockout')
