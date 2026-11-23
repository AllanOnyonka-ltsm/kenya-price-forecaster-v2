"""One interface for the optional narrative LLM: ``complete_json(prompt, schema) -> dict``.

Backend is chosen by the environment variable CROP_SWARM_LLM:

    offline        (default) deterministic stub, no network. Used by tests and backtests.
    ollama         OpenAI-compatible server, default http://localhost:11434/v1.
                   Needs CROP_SWARM_OLLAMA_MODEL (and optionally CROP_SWARM_OLLAMA_URL).
    claude_cli     Shells out to `claude -p --output-format json`. Needs CROP_SWARM_ALLOW_CLAUDE_CLI=1.
                   Optional CROP_SWARM_CLAUDE_BIN, CROP_SWARM_CLAUDE_MODEL, CROP_SWARM_TIMEOUT_S.
    anthropic_api  Anthropic Messages API with ANTHROPIC_API_KEY. Model from
                   CROP_SWARM_ANTHROPIC_MODEL (default claude-haiku-5-5). Falls back to offline
                   when the key is not set.

Every backend validates the reply against the schema and retries once on invalid output. Any other
failure (missing CLI, timeout, limit or rate-limit error, HTTP error, no key) falls back to the
offline stub, and the reason is kept in ``provider.last_fallback_reason``. Nothing is swallowed
silently: the reason is recorded on the provider and echoed in the narrative agent's detail.

The claude_cli backend is for PERSONAL LOCAL DEVELOPMENT ONLY: running experiments on your own
machine with your own subscription login. It is NOT for serving other users (for example the
WhatsApp bot or any public or shared service), which Anthropic's terms do not permit for
subscription logins. Terms change; see monorepo/swarm/README.md for the caveats.

API keys are read from the environment and are never printed, logged or stored.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request

ENV_BACKEND = "CROP_SWARM_LLM"
BACKENDS = ("offline", "ollama", "claude_cli", "anthropic_api")
MAX_ATTEMPTS = 2                       # one retry after an invalid reply
RETRY_SUFFIX = "\n\nYour previous reply did not match the required JSON schema. Reply with JSON only."
LIMIT_RE = re.compile(r"rate limit|usage limit|limit reached|too many requests|quota", re.IGNORECASE)
NEUTRAL_WORDS = ("flat", "none", "neutral")
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_OLLAMA_URL = "http://localhost:11434/v1"


class SchemaError(ValueError):
    pass


class LimitError(RuntimeError):
    pass


# ----------------------------------------------------------------- schema check (stdlib subset)
def validate_json(value, schema, path="$"):
    """Return a list of error strings (empty = valid). Supports the subset used by crop_swarm."""
    errs = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}.{k}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errs.extend(validate_json(value[k], sub, f"{path}.{k}"))
    elif t == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            errs.append(f"{path}: length {len(value)} outside bounds")
        if "items" in schema:
            for i, item in enumerate(value):
                errs.extend(validate_json(item, schema["items"], f"{path}[{i}]"))
    elif t == "string":
        if not isinstance(value, str):
            return [f"{path}: expected string"]
    elif t in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return [f"{path}: expected finite number"]
        if t == "integer" and not float(value).is_integer():
            errs.append(f"{path}: expected integer")
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above maximum {schema['maximum']}")
    elif t == "boolean":
        if not isinstance(value, bool):
            errs.append(f"{path}: expected boolean")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    return errs


def neutral_for(schema):
    """Deterministic neutral value for a schema (used by the offline stub)."""
    if "enum" in schema:
        prefer = [v for v in schema["enum"] if v in NEUTRAL_WORDS]
        return prefer[0] if prefer else schema["enum"][0]
    t = schema.get("type")
    if t == "object":
        return {k: neutral_for(schema.get("properties", {}).get(k, {})) for k in schema.get("required", [])}
    if t == "array":
        return []
    if t in ("number", "integer"):
        v = 0.0
        if "minimum" in schema:
            v = max(v, schema["minimum"])
        if "maximum" in schema:
            v = min(v, schema["maximum"])
        return int(v) if t == "integer" else float(v)
    if t == "boolean":
        return False
    return ""


def parse_json_text(text: str) -> dict:
    """Parse a JSON object from model text, tolerating code fences and surrounding prose."""
    if not isinstance(text, str):
        raise SchemaError("reply is not text")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SchemaError("no JSON object in reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise SchemaError("JSON reply is not an object")
    return obj


def _schema_hint(schema) -> str:
    return "Respond with a single JSON object matching this JSON schema: " + json.dumps(schema, sort_keys=True)


# ----------------------------------------------------------------- providers
class _Base:
    name = "base"

    def __init__(self):
        self.last_fallback_reason = None
        self.calls = 0
        self.fallbacks = 0

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.last_fallback_reason = None
        try:
            return self._call_validated(prompt, schema)
        except Exception as exc:  # noqa: BLE001 - recorded, then the offline stub answers
            self.fallbacks += 1
            self.last_fallback_reason = f"{self.name} unavailable ({type(exc).__name__}: {exc}); offline stub used"
            return OfflineProvider().complete_json(prompt, schema)

    def _call_validated(self, prompt, schema):
        last = None
        for attempt in range(MAX_ATTEMPTS):
            self.calls += 1
            text = self._raw(prompt if attempt == 0 else prompt + RETRY_SUFFIX, schema)
            try:
                obj = parse_json_text(text)
            except (ValueError, SchemaError) as exc:
                last = f"unparseable reply: {exc}"
                continue
            errs = validate_json(obj, schema)
            if not errs:
                return obj
            last = "; ".join(errs)
        raise SchemaError(f"reply failed schema after {MAX_ATTEMPTS} attempts: {last}")

    def _raw(self, prompt, schema) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


class OfflineProvider:
    """Deterministic stub: the neutral answer for the schema. No network, no randomness."""

    name = "offline"

    def __init__(self):
        self.last_fallback_reason = None
        self.calls = 0
        self.fallbacks = 0

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.calls += 1
        obj = neutral_for(schema)
        errs = validate_json(obj, schema)
        if errs:  # the schema has no neutral value; this is a programming error, not a runtime one
            raise SchemaError(f"offline stub cannot satisfy schema: {errs}")
        return obj


class OllamaProvider(_Base):
    name = "ollama"

    def _raw(self, prompt, schema):
        model = os.environ.get("CROP_SWARM_OLLAMA_MODEL", "").strip()
        if not model:
            raise RuntimeError("CROP_SWARM_OLLAMA_MODEL is not set")
        base = os.environ.get("CROP_SWARM_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
        body = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Reply with JSON only. " + _schema_hint(schema)},
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=float(os.environ.get("CROP_SWARM_TIMEOUT_S", "60"))) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


class ClaudeCLIProvider(_Base):
    """Subprocess provider: `claude -p <prompt> --output-format json`.

    PERSONAL LOCAL DEVELOPMENT ONLY. This uses the user's own Claude subscription login on the
    user's own machine. It must NOT be used to serve other users (for example the WhatsApp bot,
    a public demo or a shared server): Anthropic's terms do not allow subscription logins to be
    routed through third-party products. This class strips ANTHROPIC_API_KEY from the child
    environment so the CLI uses the subscription login rather than per-token billing, and it never
    passes --bare, which ignores subscription credentials.
    """

    name = "claude_cli"

    def _resolve_binary(self):
        exe = os.environ.get("CROP_SWARM_CLAUDE_BIN") or shutil.which("claude")
        if not exe or not os.path.exists(exe):
            raise FileNotFoundError("claude CLI not found (set CROP_SWARM_CLAUDE_BIN or put claude on PATH)")
        return exe

    def _raw(self, prompt, schema):
        if os.environ.get("CROP_SWARM_ALLOW_CLAUDE_CLI") != "1":
            raise PermissionError("set CROP_SWARM_ALLOW_CLAUDE_CLI=1 to allow the claude CLI backend")
        exe = self._resolve_binary()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        cmd = [exe, "-p", prompt + "\n\n" + _schema_hint(schema), "--output-format", "json"]
        model = os.environ.get("CROP_SWARM_CLAUDE_MODEL", "").strip()
        if model:
            cmd += ["--model", model]
        timeout = float(os.environ.get("CROP_SWARM_TIMEOUT_S", "120"))
        workdir = tempfile.mkdtemp(prefix="crop_swarm_cli_")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                                  cwd=workdir, stdin=subprocess.DEVNULL)
        finally:
            try:
                os.rmdir(workdir)
            except OSError:
                pass
        out = proc.stdout or ""
        err = proc.stderr or ""
        if proc.returncode != 0:
            if LIMIT_RE.search(out + err):
                raise LimitError("claude CLI reported a usage or rate limit")
            raise RuntimeError(f"claude CLI exited with code {proc.returncode}")
        try:
            envelope = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude CLI output is not JSON: {exc}") from exc
        if isinstance(envelope, dict):
            text = envelope.get("result", "")
            if envelope.get("is_error") or str(envelope.get("subtype", "")).startswith("error"):
                if LIMIT_RE.search(str(text)):
                    raise LimitError("claude CLI reported a usage or rate limit")
                raise RuntimeError(f"claude CLI returned an error ({envelope.get('subtype', 'error')})")
            if LIMIT_RE.search(str(text)):
                raise LimitError("claude CLI reported a usage or rate limit")
            return str(text)
        return out


class AnthropicAPIProvider(_Base):
    name = "anthropic_api"

    def _raw(self, prompt, schema):
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        model = os.environ.get("CROP_SWARM_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
        body = {
            "model": model,
            "max_tokens": 400,
            "system": "Reply with JSON only. " + _schema_hint(schema),
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=float(os.environ.get("CROP_SWARM_TIMEOUT_S", "60"))) as r:
            data = json.loads(r.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


_PROVIDERS = {"ollama": OllamaProvider, "claude_cli": ClaudeCLIProvider, "anthropic_api": AnthropicAPIProvider}


def get_provider(name: str | None = None):
    """Provider for ``name`` or for CROP_SWARM_LLM. Unknown names fall back to offline with a reason."""
    chosen = (name if name is not None else os.environ.get(ENV_BACKEND, "offline")).strip().lower() or "offline"
    if chosen == "offline":
        return OfflineProvider()
    if chosen in _PROVIDERS:
        return _PROVIDERS[chosen]()
    provider = OfflineProvider()
    provider.last_fallback_reason = f"unknown CROP_SWARM_LLM {chosen!r}; offline stub used (valid: {', '.join(BACKENDS)})"
    return provider
