"""Shared host-side helpers for ops: Postgres, the Gemini judge and Phoenix.

The judge is trusted host-side code, so it calls Gemini directly with the key file; the agent
never can (it only reaches the LiteLLM gateway with its budget-capped virtual key).
"""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import certifi
import psycopg

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / ".secrets"
GEMINI = "https://generativelanguage.googleapis.com"
GEMINI_KEY_FILE = Path(os.getenv("GEMINI_API_KEY_FILE", DEPLOY / "secrets" / "gemini_api_key"))
PHOENIX = os.getenv("PHOENIX_URL", "http://127.0.0.1:6006")
PHOENIX_PUBLIC = os.getenv("PHOENIX_PUBLIC_URL", PHOENIX)  # for links opened in the browser
PHOENIX_KEY_FILE = Path(os.getenv("PHOENIX_API_KEY_FILE", DEPLOY / "phoenix-api-key"))
PHOENIX_PROJECT = os.getenv("PHOENIX_PROJECT", "northwind-support")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gemini-3.1-pro-preview")
OPENER = build_opener(ProxyHandler({}))
TLS = ssl.create_default_context(cafile=certifi.where())

SCHEMA = """
CREATE TABLE IF NOT EXISTS online_evals (
  turn_id TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, score DOUBLE PRECISION,
  label TEXT, explanation TEXT, judge_model TEXT, created TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (turn_id, name));
CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY, started TIMESTAMPTZ NOT NULL DEFAULT now(), finished TIMESTAMPTZ,
  endpoint TEXT NOT NULL, dataset TEXT NOT NULL, judge_model TEXT NOT NULL, label TEXT,
  cases INTEGER, passed INTEGER, pass_rate DOUBLE PRECISION, mean_correctness DOUBLE PRECISION,
  p50_latency_ms INTEGER);
CREATE TABLE IF NOT EXISTS eval_results (
  run_id TEXT NOT NULL REFERENCES eval_runs(id), case_id TEXT NOT NULL, category TEXT,
  passed BOOLEAN NOT NULL, checks JSONB NOT NULL, correctness DOUBLE PRECISION,
  explanation TEXT, reply TEXT, latency_ms INTEGER, trace_id TEXT, tools JSONB,
  PRIMARY KEY (run_id, case_id));
"""


def connect() -> psycopg.Connection:
    password = Path(os.getenv("DB_PASSWORD_FILE", DEPLOY / "db-password")).read_text().strip()
    host, port = os.getenv("DB_HOST", "127.0.0.1"), os.getenv("DB_PORT", "5433")
    db = psycopg.connect(
        host=host,
        port=port,
        dbname="support",
        user="support",
        password=password,
        autocommit=True,
        connect_timeout=5,
    )
    db.execute(SCHEMA)
    return db


def post_json(url: str, body: dict, headers: dict | None = None, timeout: int = 120) -> dict:
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    opener = urlopen_tls if url.startswith("https://") else OPENER.open
    with opener(request, timeout=timeout) as response:
        return json.load(response)


def urlopen_tls(request: Request, timeout: int):
    from urllib.request import urlopen

    return urlopen(request, timeout=timeout, context=TLS)


def judge(prompt: str, schema: dict) -> dict:
    """Ask the judge model for JSON matching `schema` (Gemini structured output)."""
    data = post_json(
        f"{GEMINI}/v1beta/models/{JUDGE_MODEL}:generateContent",
        {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        },
        {"x-goog-api-key": GEMINI_KEY_FILE.read_text().strip()},
    )
    parts = data["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))


def phoenix_key() -> str:
    try:
        return PHOENIX_KEY_FILE.read_text().strip()
    except OSError:
        return ""


def annotate_span(
    span_id: str, name: str, kind: str, score=None, label=None, explanation=None
) -> bool:
    """Attach an evaluation result to a span in Phoenix (best effort)."""
    if not span_id or not phoenix_key():
        return False
    try:
        post_json(
            f"{PHOENIX}/v1/span_annotations?sync=false",
            {
                "data": [
                    {
                        "span_id": span_id,
                        "name": name,
                        "annotator_kind": kind,
                        "identifier": "",
                        "result": {"score": score, "label": label, "explanation": explanation},
                    }
                ]
            },
            headers={"Authorization": "Bearer " + phoenix_key()},
            timeout=10,
        )
        return True
    except (HTTPError, URLError, TimeoutError):
        return False


def agent_endpoint() -> str:
    return json.loads((DEPLOY / "last-deploy.json").read_text())["endpoint"]
