"""Entry point for GitHub Action mode.

Reads the webhook payload from $GITHUB_EVENT_PATH (placed there by the
Actions runner) and posts a review using $GITHUB_TOKEN. Unlike the Flask
webhook, there is no HTTP listener and no GitHub App JWT — Actions has
already authenticated us.
"""

import json
import logging
import os
import sys

from .config import Config
from .github_client import GitHubClient
from .llm_client import LLMResponseError
from .reviewer import run_followup, run_review
from .triggers import build_review_request


def _format_llm_response_error(exc: LLMResponseError) -> str:
    excerpt = exc.body_preview.strip()
    if len(excerpt) > 600:
        excerpt = excerpt[:600] + "..."
    reason_part = f" {exc.reason}" if exc.reason else ""
    if excerpt:
        return f"LLM endpoint returned {exc.status_code}{reason_part}: {excerpt}"
    return f"LLM endpoint returned {exc.status_code}{reason_part}"


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("ai-reviewer.action")

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_name or not event_path or not os.path.exists(event_path):
        log.error(
            "GITHUB_EVENT_NAME/GITHUB_EVENT_PATH missing — not running in Actions?"
        )
        return 1

    with open(event_path, "r") as f:
        payload = json.load(f)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error(
            "GITHUB_TOKEN missing (forgot to pass it via env or inputs.github_token?)"
        )
        return 1

    cfg = Config.from_env(require_app=False)
    cfg.llm_api_key = cfg.llm_api_key.strip()
    if not cfg.llm_api_key:
        log.error(
            "LLM_API_KEY missing (forgot to pass it via env or inputs.llm_api_key?)"
        )
        return 1

    req = build_review_request(event_name, payload, cfg.mention_trigger)
    if req is None:
        log.info(
            "Trigger conditions not met for %s (action=%s); nothing to do.",
            event_name,
            payload.get("action"),
        )
        return 0

    gh = GitHubClient(token)
    try:
        if req.inline is not None:
            run_followup(cfg, gh, req)
        else:
            run_review(cfg, gh, req)
    except LLMResponseError as exc:
        message = _format_llm_response_error(exc)
        log.warning("review failed: %s", message)
        body = f"⚠️ Review failed: `{message}`"
        if cfg.persona_header:
            body = f"{cfg.persona_header}\n\n{body}"
        try:
            # On inline-comment failures, post the failure as a reply
            # on the same thread so the commenter sees it in-context.
            if req.inline is not None:
                gh.reply_to_review_comment(
                    req.owner, req.repo, req.number, req.inline.comment_id, body
                )
            else:
                gh.post_issue_comment(req.owner, req.repo, req.number, body)
        except Exception as post_exc:
            log.warning("failed to post failure comment to PR: %s", post_exc)
        return 1
    except Exception as exc:
        log.exception("review failed")
        body = f"⚠️ Review failed: `{type(exc).__name__}: {exc}`"
        if cfg.persona_header:
            body = f"{cfg.persona_header}\n\n{body}"
        try:
            # On inline-comment failures, post the failure as a reply
            # on the same thread so the commenter sees it in-context.
            if req.inline is not None:
                gh.reply_to_review_comment(
                    req.owner, req.repo, req.number, req.inline.comment_id, body
                )
            else:
                gh.post_issue_comment(req.owner, req.repo, req.number, body)
        except Exception as post_exc:
            log.warning("failed to post failure comment to PR: %s", post_exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
