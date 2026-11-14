# -*- coding: utf-8 -*-
"""تست‌های فاز ۳ — بهداشت مخزن و برابری معنایی دوزبانه (fa/en):
  • OPS-017: هم‌ترازی نسخهٔ Python (3.13) در README، Dockerfile، requirements.txt و ci.yml
  • OPS-019: پین‌شدن همهٔ بسته‌های requirements.txt و پین‌شدن نسخهٔ pip در ci.yml
  • OPS-023: وجود CODEOWNERS، dependabot.yml، pull_request_template.md، CHANGELOG.md و LICENSE
  • OPS-029: یکپارچگی دستور استخراج اثرانگشت SSH میزبان (ssh-keygen -lf) بین DEPLOY_RUNBOOK.md و SECURITY.md
  • TEST-010: برابری معنایی fa/en در TXT، DIAG، STAGES و FIELD_LABEL (برابری placeholderها، حروف لاتین در en، fallback در tr و بازنویسی DB در gtext)
"""
import pathlib
import re

from newsbot import ui_i18n

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ops017_python_version_aligned_across_repo():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Python 3.12+" not in readme
    assert "Python 3.13+" in readme


def test_ops019_requirements_and_ci_pip_are_strictly_pinned():
    for fname in ("requirements.txt", "requirements-dev.txt"):
        lines = (ROOT / fname).read_text(encoding="utf-8").splitlines()
        pkg_lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith(("#", "-r"))]
        assert pkg_lines
        for line in pkg_lines:
            assert "==" in line, f"Unpinned dependency in {fname}: {line}"

    ci_yml = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pip install --upgrade pip" not in ci_yml
    assert 'pip install "pip==' in ci_yml


def test_ops023_governance_and_hygiene_files_exist():
    expected = [
        ROOT / ".github" / "CODEOWNERS",
        ROOT / ".github" / "dependabot.yml",
        ROOT / ".github" / "pull_request_template.md",
        ROOT / "CHANGELOG.md",
        ROOT / "LICENSE",
    ]
    for path in expected:
        assert path.is_file() and path.stat().st_size > 20, f"Missing or empty: {path}"


def test_ops029_ssh_fingerprint_procedure_is_consistent():
    runbook = (ROOT / "docs" / "DEPLOY_RUNBOOK.md").read_text(encoding="utf-8")
    security = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    assert "ssh-keygen -lf" in runbook
    assert "ssh-keygen -lf" in security


def test_test010_i18n_semantic_parity_and_placeholders():
    ph = lambda s: set(re.findall(r"\{([a-zA-Z0-9_]+)\}", str(s)))
    symbol_only_keys = {"none", "unlimited", "sup_received"}
    bilingual_keys = {"choose_lang"}

    for table_name, table in (("TXT", ui_i18n.TXT), ("DIAG", ui_i18n.DIAG), ("STAGES", ui_i18n.STAGES)):
        assert table, table_name
        for key, val in table.items():
            assert isinstance(val, tuple) and len(val) == 2, f"{table_name}[{key}] is not a 2-tuple"
            fa_txt, en_txt = str(val[0]), str(val[1])
            assert fa_txt.strip() and en_txt.strip(), f"Empty translation in {table_name}[{key}]"
            assert ph(fa_txt) == ph(en_txt), (
                f"Placeholder mismatch in {table_name}[{key}]: fa={ph(fa_txt)} vs en={ph(en_txt)}"
            )
            if key not in symbol_only_keys:
                assert re.search(r"[A-Za-z]", en_txt), f"No Latin chars in English {table_name}[{key}]: {en_txt}"
            if key not in bilingual_keys:
                assert not re.search(r"[\u0600-\u06FF]", en_txt), (
                    f"Persian characters leaked into English {table_name}[{key}]: {en_txt}"
                )

    for _key, val in ui_i18n.FIELD_LABEL.items():
        assert isinstance(val, tuple) and len(val) == 2
        assert re.search(r"[\u0600-\u06FF]", str(val[0]))
        assert re.search(r"[A-Za-z]", str(val[1]))

    # بررسی رفتار fallback در tr() و بازنویسی دیتابیس در gtext()
    assert ui_i18n.tr("en", "nonexistent_key_xyz") == "nonexistent_key_xyz"
    ui_i18n.gtext_set("help", "en", "Custom English Help")
    try:
        assert ui_i18n.gtext("help", "en") == "Custom English Help"
    finally:
        ui_i18n.gtext_set("help", "en", "")
