#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEVOPS-06/07 — راستی‌آزماییِ حفاظتِ مخزن در GitHub.

دو چیز باید *واقعاً* در تنظیماتِ GitHub برقرار باشد وگرنه استقرارِ خودکار یعنی
«هرکسی که به مخزن دسترسی push دارد می‌تواند نسخهٔ دلخواهش را روی سرور بگذارد»:

  1) شاخهٔ اصلی (`main`) محافظت شده باشد:
       * حداقلِ یک بازبینیِ PR لازم باشد (`required_pull_request_reviews`)،
       * گیتِ وضعیتِ CI لازم باشد (`required_status_checks`)،
       * قوانین روی ادمین‌ها هم اعمال شود (`enforce_admins.enabled`)،
       * و pushِ مستقیم ممنوع باشد (قاعدهٔ `restrictions` یا نبودِ pushِ آزاد).
  2) محیطِ `production` (محیطِ استقرار) «Required reviewers» داشته باشد تا هر
     استقرارِ VPS یک تأییدِ دستیِ انسانی بگیرد.

این ابزار فقط *می‌خواند* و هیچ تغییری در مخزن نمی‌دهد. اگر API در دسترس نباشد
(توکنِ ناقص، مجوزِ ناکافی، شبکه‌ی قطع، یا حفاظتِ فعال‌نبودن)، با پیامِ روشن
شکست می‌خورد — «نامعلوم» هرگز «سالم» حساب نمی‌شود (fail-closed).

نمونه:
    python tools/verify_github_protection.py --repo Aryasamadi/Bot-Official-Telegram-
    python tools/verify_github_protection.py --repo owner/name --json
    GITHUB_TOKEN=… python tools/verify_github_protection.py --repo owner/name
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "Aryasamadi/Bot-Official-Telegram-"
DEFAULT_BRANCH = "main"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_API = "https://api.github.com"


class ApiError(RuntimeError):
    """خطای دسترسی به API (یا خودِ API در دسترس نیست)."""


def _request(url: str, token: str, timeout: float = 20.0) -> dict:
    """یک درخواستِ GET به API و برگرداندنِ JSON (یا خطای روشن)."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "newsbot-verify-github-protection")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:  # pragma: no cover — بدنه‌ی خطا خوانده نشد
            pass
        if exc.code == 404:
            raise ApiError(
                f"404 for {url} — the resource does not exist, the token has no access, "
                f"or branch protection is not enabled. ({detail})"
            ) from exc
        if exc.code in (401, 403):
            raise ApiError(
                f"{exc.code} for {url} — the token is missing/expired or lacks the "
                f"'administration' permission. ({detail})"
            ) from exc
        raise ApiError(f"HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"the GitHub API is unreachable ({url}): {exc.reason}") from exc
    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ApiError(f"the GitHub API returned a non-JSON body for {url}") from exc


def check_branch_protection(api: str, repo: str, branch: str, token: str) -> dict:
    """وضعیتِ حفاظتِ شاخه را می‌خواند و به یک گزارشِ ساختاریافته تبدیل می‌کند."""
    url = f"{api}/repos/{repo}/branches/{branch}/protection"
    data = _request(url, token)

    reviews = data.get("required_pull_request_reviews") or {}
    checks = data.get("required_status_checks") or {}
    admins = data.get("enforce_admins") or {}
    restrictions = data.get("restrictions") or {}

    review_count = int(reviews.get("required_approving_review_count") or 0)
    contexts = list((checks.get("contexts") or []))
    strict = bool(checks.get("strict"))
    admin_enforced = bool(admins.get("enabled"))
    push_users = list((restrictions.get("users") or []))
    push_teams = list((restrictions.get("teams") or []))
    push_apps = list((restrictions.get("apps") or []))
    # «push_restricted» یعنی هیچ‌کس نمی‌تواند *مستقیم* به شاخه push کند.
    # این یا با restrictions (فهرستِ push-کنندگانِ مجاز) و یا با اجبارِ
    # عبور از PR (بازبینیِ لازم + گیتِ وضعیت + اعمال برای ادمین‌ها) برقرار است.
    push_restricted = bool(restrictions) or (review_count >= 1 and bool(contexts) and admin_enforced)

    problems = []
    if review_count < 1:
        problems.append("at least one approving review is required (required_pull_request_reviews)")
    if not contexts:
        problems.append("at least one required status check is configured (required_status_checks)")
    if not admin_enforced:
        problems.append("the rule is not enforced for administrators (enforce_admins.enabled)")
    if not push_restricted:
        problems.append(
            "direct pushes are not restricted — enable restrictions.users/teams/apps so that every "
            "change goes through a pull request"
        )

    return {
        "kind": "branch",
        "target": f"{repo}@{branch}",
        "ok": not problems,
        "problems": problems,
        "details": {
            "required_approving_review_count": review_count,
            "required_status_checks": contexts,
            "strict_status_checks": strict,
            "enforce_admins": admin_enforced,
            "push_restricted": push_restricted,
            "push_users": push_users,
            "push_teams": push_teams,
            "push_apps": push_apps,
        },
    }


def check_environment_protection(api: str, repo: str, environment: str, token: str) -> dict:
    """قاعدهٔ حفاظتِ محیطِ استقرار (Required reviewers) را می‌خواند.

    ساختارِ پاسخِ API دو شکل دارد:
      * ``protection_rules``: فهرستِ قاعده‌ها
        (``{"type": "required_reviewers", "reviewers": [...]}``، ``wait_timer``، ...)
      * ``deployment_protection_rules``: یک شیءِ واحد با کلیدهای مشابه
    هر دو پشتیبانی می‌شوند.
    """
    url = f"{api}/repos/{repo}/environments/{environment}"
    data = _request(url, token)

    rules = data.get("protection_rules")
    if rules is None:
        rules = data.get("deployment_protection_rules") or {}

    reviewer_names = []
    wait_timer = 0
    branch_policy_enabled = False
    if isinstance(rules, list):
        for rule in rules:
            rtype = rule.get("type")
            if rtype == "required_reviewers":
                for rev in rule.get("reviewers") or []:
                    if rev.get("type") == "User":
                        reviewer_names.append(f"User:{rev.get('reviewer', {}).get('login')}")
                    elif rev.get("type") == "Team":
                        reviewer_names.append(f"Team:{rev.get('reviewer', {}).get('slug')}")
            elif rtype == "wait_timer":
                wait_timer = int(rule.get("wait_timer") or 0)
            elif rtype == "branch_policy":
                branch_policy_enabled = True
    elif isinstance(rules, dict):
        for rev in rules.get("required_reviewers") or []:
            if rev.get("type") == "User":
                reviewer_names.append(f"User:{rev.get('reviewer', {}).get('login')}")
            elif rev.get("type") == "Team":
                reviewer_names.append(f"Team:{rev.get('reviewer', {}).get('slug')}")
        wait_timer = int(rules.get("wait_timer") or 0)
        branch_policy_enabled = bool((rules.get("custom_branch_policies") or {}).get("enabled"))

    problems = []
    if not reviewer_names:
        problems.append(
            "the environment has no required reviewers — every deployment would start without a human approval"
        )

    return {
        "kind": "environment",
        "target": f"{repo}@{environment}",
        "ok": not problems,
        "problems": problems,
        "details": {
            "required_reviewers": reviewer_names,
            "wait_timer_minutes": wait_timer,
            "branch_policy_enabled": branch_policy_enabled,
            "deployment_branch_policy": data.get("deployment_branch_policy"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the GitHub branch/environment protection of the repository.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO,
                        help="owner/name of the repository (default: %(default)s)")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="protected branch (default: %(default)s)")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT,
                        help="deployment environment (default: %(default)s)")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "",
                        help="a token with 'administration: read' (default: $GITHUB_TOKEN / $GH_TOKEN)")
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL") or DEFAULT_API,
                        help="base URL of the GitHub API (default: %(default)s)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--skip-branch", action="store_true", help="skip the branch-protection check")
    parser.add_argument("--skip-environment", action="store_true", help="skip the environment-protection check")
    args = parser.parse_args(argv)

    if not args.token:
        message = ("no token: set GITHUB_TOKEN (or --token). A token with the 'administration: read' "
                   "permission is required to read the protection rules.")
        if args.json:
            print(json.dumps({"ok": False, "error": message, "results": []}, ensure_ascii=False, indent=2))
        else:
            print(f"❌ {message}", file=sys.stderr)
        return 2

    results = []
    try:
        if not args.skip_branch:
            results.append(check_branch_protection(args.api_url, args.repo, args.branch, args.token))
        if not args.skip_environment:
            results.append(check_environment_protection(args.api_url, args.repo, args.environment, args.token))
    except ApiError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc), "results": results},
                             ensure_ascii=False, indent=2))
        else:
            print(f"❌ the GitHub API could not be queried: {exc}", file=sys.stderr)
        return 2

    ok = all(r["ok"] for r in results) and results

    if args.json:
        print(json.dumps({"ok": bool(ok), "repo": args.repo, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        for res in results:
            mark = "✅" if res["ok"] else "❌"
            print(f"{mark} {res['kind']}: {res['target']}")
            for key, value in res["details"].items():
                print(f"     - {key}: {value}")
            for problem in res["problems"]:
                print(f"     ! {problem}")
        if ok:
            print("✅ the repository protection is complete: the branch is protected and the "
                  "deployment environment requires a manual approval.")
        else:
            print("❌ the repository protection is incomplete — see the problems above.", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
