-- 0002_api_key_rotation: rotation grace window (plan phase-3 task 6, security.md 3.3).
-- Rotating a key keeps the previous digest valid for 5 minutes: the old hash and prefix
-- move to prev_hash/prev_prefix with an absolute prev_expires_at deadline that
-- engine/keys.verify_key checks in constant time; revoke clears both immediately.
ALTER TABLE api_keys ADD COLUMN prev_hash TEXT;
ALTER TABLE api_keys ADD COLUMN prev_prefix TEXT;
ALTER TABLE api_keys ADD COLUMN prev_expires_at REAL;
