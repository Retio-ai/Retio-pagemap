# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""Login wall detection — form structure extraction with sanitization.

Extracts login form fields (field_type, label, required) without
exposing password field name/id/class attributes.

Also handles age gate detection (extended) and 2FA/OTP detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .. import Interactable

from ..sanitizer import sanitize_text

# ── Module-level pre-compiled patterns ─────────────────────────────

_LOGIN_FORM_RE = re.compile(
    r"<form[^>]*(?:login|signin|sign-in|log-in|auth)[^>]*>",
    re.IGNORECASE,
)

_LOGIN_MODAL_RE = re.compile(
    r'(?:class|id)=["\'][^"\']*(?:login[-_]?(?:modal|dialog|overlay|popup|wall|gate)'
    r"|sign[-_]?in[-_]?(?:modal|dialog|overlay|popup|wall|gate)"
    r"|auth[-_]?(?:modal|dialog|overlay|popup|wall|gate))[^\"']*[\"']",
    re.IGNORECASE,
)

_PASSWORD_FIELD_RE = re.compile(
    r'<input[^>]*type=["\']password["\'][^>]*/?>',
    re.IGNORECASE,
)

_EMAIL_FIELD_RE = re.compile(
    r'<input[^>]*type=["\'](?:email|text)["\'][^>]*(?:email|이메일|メール|e-mail)[^>]*/?>',
    re.IGNORECASE,
)

_USERNAME_FIELD_RE = re.compile(
    r'<input[^>]*(?:name|id|placeholder)=["\'][^"\']*(?:username|user[-_]?name|아이디|ユーザー名|用户名)[^"\']*["\'][^>]*/?>',
    re.IGNORECASE,
)

_REQUIRED_RE = re.compile(r"\brequired\b", re.IGNORECASE)

_LABEL_RE = re.compile(
    r"<label[^>]*>(.*?)</label>",
    re.IGNORECASE | re.DOTALL,
)

_OAUTH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("google", re.compile(r"google[-_]?(?:sign[-_]?in|login|oauth|auth)|accounts\.google\.com", re.IGNORECASE)),
    ("facebook", re.compile(r"facebook[-_]?(?:sign[-_]?in|login|oauth|auth)|facebook\.com/v\d+", re.IGNORECASE)),
    ("apple", re.compile(r"apple[-_]?(?:sign[-_]?in|login|auth)|appleid\.apple\.com", re.IGNORECASE)),
    ("kakao", re.compile(r"kakao[-_]?(?:sign[-_]?in|login|oauth|auth)|kauth\.kakao\.com", re.IGNORECASE)),
    ("naver", re.compile(r"naver[-_]?(?:sign[-_]?in|login|oauth|auth)|nid\.naver\.com", re.IGNORECASE)),
    ("github", re.compile(r"github[-_]?(?:sign[-_]?in|login|oauth|auth)|github\.com/login/oauth", re.IGNORECASE)),
    ("twitter", re.compile(r"twitter[-_]?(?:sign[-_]?in|login|oauth|auth)|api\.twitter\.com", re.IGNORECASE)),
    ("line", re.compile(r"line[-_]?(?:sign[-_]?in|login|oauth|auth)|access\.line\.me", re.IGNORECASE)),
    ("wechat", re.compile(r"wechat[-_]?(?:sign[-_]?in|login|oauth|auth)|open\.weixin\.qq\.com", re.IGNORECASE)),
)

_AGE_GATE_RE = re.compile(
    r"(?:age[-_]?(?:gate|verify|verification|check|confirm)"
    r"|are[-_]?you[-_]?(?:over|at[-_]?least|old[-_]?enough)"
    r"|birth[-_]?(?:date|day|year)"
    r"|나이\s*확인|연령\s*확인|생년월일"
    r"|年齢確認|生年月日"
    r"|v\xe9rification.+\xe2ge)",
    re.IGNORECASE,
)

_REGION_BLOCK_RE = re.compile(
    r"(?:not\s+available\s+in\s+your\s+(?:region|country|location)"
    r"|not[-_]?available[-_]?(?:in[-_]?your|this)[-_]?(?:region|country|location)"
    r"|region[-_]?(?:restricted|blocked|unavailable)"
    r"|geo[-_]?(?:blocked|restricted|fence)"
    r"|이\s*지역에서\s*(?:이용|사용)\s*(?:불가|할\s*수\s*없)"
    r"|お住まいの地域ではご利用いただけません"
    r"|此地区不可用)",
    re.IGNORECASE,
)


# ── 2FA / OTP detection ────────────────────────────────────────────

_2FA_RE = re.compile(
    r'type=["\']number["\'][^>]*maxlength=["\'][46]["\']'
    r"|verification\s*code|인증\s*코드|確認コード|code\s*de\s*v\xe9rification"
    r"|2fa|two[-_]?factor|otp|one[-_]?time\s*password",
    re.IGNORECASE,
)

# ── Date picker / age gate extended ────────────────────────────────

_DATE_PICKER_RE = re.compile(
    r"<(?:select|input)[^>]*(?:year|month|day|birth|년|월|일|年|月|日)[^>]*/?>",
    re.IGNORECASE,
)

_AGE_ACCEPT_TERMS: tuple[str, ...] = (
    # en
    "i am over 18",
    "i'm over 18",
    "i am 18 or older",
    "i am of legal age",
    "yes, i am over 18",
    "enter",
    "confirm age",
    "i am over 21",
    # ko
    "18세 이상입니다",
    "성인입니다",
    "나이 확인",
    "연령 확인",
    "19세 이상입니다",
    "예, 성인입니다",
    # ja
    "18歳以上です",
    "はい、18歳以上です",
    "年齢確認",
    # fr
    "j'ai plus de 18 ans",
    "oui, j'ai plus de 18 ans",
    "je suis majeur",
    # de
    "ich bin über 18",
    "ja, ich bin über 18",
    "ich bin volljährig",
    # zh
    "我已满18岁",
    "确认年龄",
    "我已成年",
)


@dataclass(frozen=True, slots=True)
class AgeGateInfo:
    """Extended age gate detection result."""

    confidence: float
    signals: tuple[str, ...]
    has_date_picker: bool = False
    accept_terms: tuple[str, ...] = ()
    gate_type: str = "click_through"  # "click_through" | "date_entry"


@dataclass(frozen=True, slots=True)
class LoginFormInfo:
    """Extracted login form structure."""

    has_password: bool
    has_email: bool
    has_username: bool
    form_fields: tuple[dict[str, Any], ...]
    oauth_providers: tuple[str, ...]
    confidence: float
    signals: tuple[str, ...]
    has_2fa: bool = False
    login_type: str = "password"  # "password" | "social_only" | "mixed"


def _extract_form_fields(raw_html: str, html_lower: str) -> tuple[dict[str, Any], ...]:
    """Extract login form field info (sanitized, no password name/id)."""
    fields: list[dict[str, Any]] = []

    email_match = _EMAIL_FIELD_RE.search(raw_html)
    if email_match:
        email_tag = email_match.group(0)
        fields.append(
            {
                "field_type": "email",
                "name": sanitize_text("email"),
                "required": bool(_REQUIRED_RE.search(email_tag)),
            }
        )

    if _USERNAME_FIELD_RE.search(raw_html):
        fields.append(
            {
                "field_type": "username",
                "name": sanitize_text("username"),
                "required": True,
            }
        )

    if _PASSWORD_FIELD_RE.search(raw_html):
        fields.append(
            {
                "field_type": "password",
                "name": sanitize_text("password"),
                "required": True,
            }
        )

    return tuple(fields)


def _detect_oauth_providers(raw_html: str) -> tuple[str, ...]:
    """Detect OAuth/social login providers."""
    providers: list[str] = []
    for provider_name, pattern in _OAUTH_PATTERNS:
        if pattern.search(raw_html):
            providers.append(provider_name)
    return tuple(providers)


def detect_login_wall(
    raw_html: str,
    html_lower: str,
    url: str,
    interactables: list[Interactable],
    page_type: str,
) -> LoginFormInfo | None:
    """Detect login wall from HTML and interactables.

    Never raises — returns None if no login wall detected.
    """
    try:
        signals: list[str] = []
        confidence = 0.0

        # Check for login form
        has_login_form = bool(_LOGIN_FORM_RE.search(raw_html))
        if has_login_form:
            signals.append("login_form_tag")
            confidence += 0.4

        # Check for login modal/overlay
        has_login_modal = bool(_LOGIN_MODAL_RE.search(raw_html))
        if has_login_modal:
            signals.append("login_modal")
            confidence += 0.3

        # Check for password field (strong signal)
        has_password = bool(_PASSWORD_FIELD_RE.search(raw_html))
        if has_password:
            signals.append("password_field")
            confidence += 0.3

        has_email = bool(_EMAIL_FIELD_RE.search(raw_html))
        has_username = bool(_USERNAME_FIELD_RE.search(raw_html))

        # Check interactables for login-related elements
        login_interactable_count = 0
        for item in interactables:
            name_lower = item.name.lower()
            if any(term in name_lower for term in ("sign in", "log in", "login", "로그인", "ログイン", "登录")):
                login_interactable_count += 1
        if login_interactable_count > 0:
            signals.append(f"login_interactables:{login_interactable_count}")
            confidence += 0.2

        # Not enough evidence for login wall
        if confidence < 0.5:
            return None

        confidence = min(confidence, 1.0)

        form_fields = _extract_form_fields(raw_html, html_lower)
        oauth_providers = _detect_oauth_providers(raw_html)

        # 2FA detection
        has_2fa = bool(_2FA_RE.search(raw_html))
        if has_2fa:
            signals.append("2fa_detected")

        # Login type classification
        login_type = "password"
        if has_password and oauth_providers:
            login_type = "mixed"
        elif not has_password and oauth_providers:
            login_type = "social_only"
        elif has_password:
            login_type = "password"

        return LoginFormInfo(
            has_password=has_password,
            has_email=has_email,
            has_username=has_username,
            form_fields=form_fields,
            oauth_providers=oauth_providers,
            confidence=confidence,
            signals=tuple(signals),
            has_2fa=has_2fa,
            login_type=login_type,
        )

    except Exception:
        return None


def detect_age_gate_extended(
    html_lower: str,
    interactables: list[Interactable] | None = None,
) -> AgeGateInfo | None:
    """Extended age gate detection with date picker and accept terms.

    Returns AgeGateInfo with gate_type and accept_terms, or None.
    Never raises.
    """
    try:
        match = _AGE_GATE_RE.search(html_lower)
        if not match:
            return None

        signals = [f"age_gate:{match.group()[:50]}"]
        has_date_picker = bool(_DATE_PICKER_RE.search(html_lower))

        if has_date_picker:
            signals.append("date_picker_detected")
            gate_type = "date_entry"
        else:
            gate_type = "click_through"

        return AgeGateInfo(
            confidence=0.85,
            signals=tuple(signals),
            has_date_picker=has_date_picker,
            accept_terms=_AGE_ACCEPT_TERMS,
            gate_type=gate_type,
        )
    except Exception:
        return None


def detect_age_gate(html_lower: str) -> tuple[float, tuple[str, ...]]:
    """Detect age verification gate. Returns (confidence, signals).

    Backward-compatible wrapper around detect_age_gate_extended().
    """
    try:
        info = detect_age_gate_extended(html_lower)
        if info is not None:
            return info.confidence, info.signals
        return 0.0, ()
    except Exception:
        return 0.0, ()


def detect_region_block(html_lower: str) -> tuple[float, tuple[str, ...]]:
    """Detect region restriction. Returns (confidence, signals)."""
    try:
        match = _REGION_BLOCK_RE.search(html_lower)
        if match:
            return 0.80, (f"region_block:{match.group()[:50]}",)
        return 0.0, ()
    except Exception:
        return 0.0, ()
