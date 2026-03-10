# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only

"""S9: Pre-compiled i18n regex patterns for page failure state detection.

6-locale coverage: ko/en/ja/fr/de/zh.
All patterns compiled at module level for O(1) re-use.
"""

from __future__ import annotations

import re

# ── Out of stock ──────────────────────────────────────────────────

_OUT_OF_STOCK_TERMS: tuple[str, ...] = (
    # ko
    "품절",
    "매진",
    "일시품절",
    "재입고 알림",
    # en
    "sold out",
    "out of stock",
    "currently unavailable",
    "no longer available",
    # ja
    "在庫切れ",
    "品切れ",
    "完売",
    "売り切れ",
    # fr
    "rupture de stock",
    "épuisé",
    "indisponible",
    # de
    "ausverkauft",
    "nicht verfügbar",
    "nicht auf lager",
    # zh
    "缺货",
    "已售罄",
    "暂时缺货",
    "无货",
)

OUT_OF_STOCK_RE = re.compile("|".join(re.escape(t) for t in _OUT_OF_STOCK_TERMS), re.IGNORECASE)

# ── Empty results ─────────────────────────────────────────────────

_EMPTY_RESULTS_TERMS: tuple[str, ...] = (
    # ko
    "검색 결과 없음",
    "검색 결과가 없습니다",
    "결과가 없습니다",
    "상품이 없습니다",
    # en
    "no results found",
    "no results",
    "no items found",
    "no matches found",
    "your search did not match",
    # ja
    "見つかりませんでした",
    "検索結果はありません",
    "該当する商品がありません",
    # fr
    "aucun résultat",
    "aucun produit trouvé",
    # de
    "keine ergebnisse",
    "keine treffer",
    "keine produkte gefunden",
    # zh
    "没有找到",
    "未找到结果",
    "暂无结果",
    "没有搜索结果",
)

EMPTY_RESULTS_RE = re.compile("|".join(re.escape(t) for t in _EMPTY_RESULTS_TERMS), re.IGNORECASE)

# ── Error page ────────────────────────────────────────────────────

_ERROR_PAGE_TERMS: tuple[str, ...] = (
    # HTTP status patterns
    "404 not found",
    "403 forbidden",
    "500 internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "page not found",
    # ko
    "페이지를 찾을 수 없습니다",
    "존재하지 않는 페이지",
    "오류가 발생",
    "서버 오류",
    # en
    "this page doesn't exist",
    "this page does not exist",
    "something went wrong",
    "an error occurred",
    "server error",
    "oops",
    # ja
    "ページが見つかりません",
    "お探しのページは見つかりませんでした",
    "エラーが発生しました",
    # fr
    "page introuvable",
    "une erreur est survenue",
    "erreur serveur",
    # de
    "seite nicht gefunden",
    "ein fehler ist aufgetreten",
    "serverfehler",
    # zh
    "页面未找到",
    "页面不存在",
    "服务器错误",
    "发生错误",
)

ERROR_PAGE_RE = re.compile("|".join(re.escape(t) for t in _ERROR_PAGE_TERMS), re.IGNORECASE)

# ── Bot blocked ───────────────────────────────────────────────────

_BOT_BLOCKED_TERMS: tuple[str, ...] = (
    # en
    "bot detected",
    "access denied",
    "please verify you are a human",
    "verify you are human",
    "please complete the security check",
    "unusual traffic",
    "automated access",
    "are you a robot",
    "confirm you are not a robot",
    # ko
    "접근 차단",
    "접근이 차단",
    "자동 접속",
    "보안 확인",
    "비정상적인 접근",
    # ja
    "アクセスが拒否",
    "アクセスが制限",
    "ボットの検出",
    "自動アクセス",
    # fr
    "accès refusé",
    "vérifiez que vous êtes humain",
    "trafic inhabituel",
    # de
    "zugriff verweigert",
    "ungewöhnlicher datenverkehr",
    "bestätigen sie, dass sie kein roboter",
    # zh
    "访问被拒绝",
    "请验证您是人类",
    "异常流量",
    "自动访问",
)

BOT_BLOCKED_RE = re.compile("|".join(re.escape(t) for t in _BOT_BLOCKED_TERMS), re.IGNORECASE)

# ── Age verification ──────────────────────────────────────────────

_AGE_VERIFICATION_TERMS: tuple[str, ...] = (
    # en
    "age verification",
    "verify your age",
    "are you over 18",
    "are you 18",
    "you must be 18",
    "you must be 21",
    "adult content",
    "age-restricted",
    # ko
    "나이 확인",
    "성인 인증",
    "성인인증",
    "연령 확인",
    "19세 이상",
    "18세 이상",
    # ja
    "年齢確認",
    "年齢認証",
    "18歳以上",
    "20歳以上",
    # fr
    "vérification de l'âge",
    "êtes-vous majeur",
    "contenu pour adultes",
    # de
    "altersverifikation",
    "altersüberprüfung",
    "sind sie 18",
    # zh
    "年龄验证",
    "请确认您的年龄",
    "18岁以上",
)

AGE_VERIFICATION_RE = re.compile("|".join(re.escape(t) for t in _AGE_VERIFICATION_TERMS), re.IGNORECASE)

# ── Region restricted ────────────────────────────────────────────

_REGION_RESTRICTED_TERMS: tuple[str, ...] = (
    # en
    "not available in your region",
    "not available in your country",
    "this content is not available in your location",
    "geo-restricted",
    "geographically restricted",
    "this service is not available",
    # ko
    "해당 지역에서 이용할 수 없습니다",
    "서비스 지역이 아닙니다",
    "접속 지역 제한",
    # ja
    "お住まいの地域では利用できません",
    "地域制限",
    "このサービスはご利用いただけません",
    # fr
    "non disponible dans votre région",
    "non disponible dans votre pays",
    "contenu géo-restreint",
    # de
    "in ihrer region nicht verfügbar",
    "in ihrem land nicht verfügbar",
    "regional eingeschränkt",
    # zh
    "您所在的地区无法使用",
    "地区限制",
    "该服务在您的地区不可用",
)

REGION_RESTRICTED_RE = re.compile("|".join(re.escape(t) for t in _REGION_RESTRICTED_TERMS), re.IGNORECASE)

# ── Login required (supplement existing i18n.LOGIN_TERMS) ────────

_LOGIN_REQUIRED_TERMS: tuple[str, ...] = (
    # en
    "please log in",
    "please sign in",
    "you must be logged in",
    "login required",
    "sign in to continue",
    "sign in to view",
    # ko
    "로그인이 필요합니다",
    "로그인 후 이용",
    "로그인해 주세요",
    # ja
    "ログインしてください",
    "ログインが必要です",
    "サインインしてください",
    # fr
    "veuillez vous connecter",
    "connexion requise",
    # de
    "bitte melden sie sich an",
    "anmeldung erforderlich",
    # zh
    "请登录",
    "需要登录",
    "登录后查看",
)

LOGIN_REQUIRED_RE = re.compile("|".join(re.escape(t) for t in _LOGIN_REQUIRED_TERMS), re.IGNORECASE)
