"""The remote URL forms a registered repository may store (ADR-0016). No imports, so
the models and the registration service share them without other dependencies."""

# The only remote URL forms stored (plan §3a): no userinfo, query, fragment, local path or
# non-ASCII. The same patterns are the database CHECK on `repositories.remote_url`, and
# they mean the same in Python `re` and in Postgres regular expressions.
_PATH = "[A-Za-z0-9._~%+/-]"
REMOTE_URL_FORM = f"^(https|ssh|git)://[a-z0-9-]+([.][a-z0-9-]+)+(:[0-9]{{1,5}})?(/{_PATH}*)?$"
SCP_FORM = f"^(git@)?[A-Za-z0-9-]+([.][A-Za-z0-9-]+)+:{_PATH}+$"
