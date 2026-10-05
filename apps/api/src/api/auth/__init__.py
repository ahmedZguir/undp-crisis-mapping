"""Coordinator auth: Supabase Auth, RS256 JWT verification, Redis refresh-token rotation.

No re-exports here: dependency and routes import app_state, which imports the other
submodules, so re-exporting would cycle. Import from api.auth.<submodule>.
"""

from __future__ import annotations
