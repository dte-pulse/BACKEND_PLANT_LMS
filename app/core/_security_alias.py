"""
Phase 1 — Department CRUD: Add hash_password to security.py
"""
from app.core.security import get_password_hash

# Alias for use in auth_service
hash_password = get_password_hash
