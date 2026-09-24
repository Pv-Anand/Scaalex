"""Who this deployment is: name, logos, wording and legal links.

One codebase runs several branded apps (Scaalex, ShiftX Partners). Set
BRAND_KEY on a service to pick a preset; leaving it unset gives Scaalex, so an
existing deployment looks exactly as it did before. Any single value can still
be overridden with its own BRAND_* environment variable.
"""
import os
from types import SimpleNamespace

_PRESETS = {
    "scaalex": {
        "name": "Scaalex",
        "legal_name": "Scaalex Consulting",
        "product": "Project Intelligence",
        "website": "scaalex.com",
        "email_domain": "scaalex.com",
        "descriptor": "premium M&A and capital advisory firm",
        "advisor_role": "banker",
        "mark_light": "images/favicon-white-mark.png",
        "mark_dark": "images/favicon-navy-mark.png",
        "favicon": "images/favicon.png",
    },
    "shiftx": {
        "name": "ShiftX",
        "legal_name": "ShiftX Partners",
        "product": "Project Intelligence",
        "website": "shiftx.partners",
        "email_domain": "shiftx.partners",
        "descriptor": "premium consulting practice spanning growth, revenue, process and critical risk",
        "advisor_role": "consultant",
        "mark_light": "brands/shiftx/mark-white.png",
        "mark_dark": "brands/shiftx/mark-dark.png",
        "favicon": "brands/shiftx/favicon.png",
    },
}


def _load():
    key = os.environ.get("BRAND_KEY", "scaalex").strip().lower()
    base = dict(_PRESETS.get(key, _PRESETS["scaalex"]))

    def pick(env, field):
        return os.environ.get(env, "").strip() or base[field]

    website = pick("BRAND_WEBSITE", "website")
    terms = os.environ.get("BRAND_TERMS_URL", "").strip() or f"https://{website}/terms-and-conditions"
    privacy = os.environ.get("BRAND_PRIVACY_URL", "").strip() or f"https://{website}/privacy-policy"
    return SimpleNamespace(
        key=key if key in _PRESETS else "scaalex",
        name=pick("BRAND_NAME", "name"),
        legal_name=pick("BRAND_LEGAL_NAME", "legal_name"),
        product=pick("BRAND_PRODUCT", "product"),
        website=website,
        email_domain=pick("BRAND_EMAIL_DOMAIN", "email_domain"),
        descriptor=pick("BRAND_DESCRIPTOR", "descriptor"),
        advisor_role=base["advisor_role"],
        terms_url=terms,
        privacy_url=privacy,
        assets=SimpleNamespace(mark_light=base["mark_light"], mark_dark=base["mark_dark"], favicon=base["favicon"]),
    )


BRAND = _load()


def brandify_prompt(text):
    """Swap the firm's name and description into an AI system prompt written
    for Scaalex. A no-op for the default brand."""
    return (
        text.replace("Scaalex Consulting", BRAND.legal_name)
        .replace('"Scaalex"', f'"{BRAND.name}"')
        .replace("premium M&A and capital advisory firm", BRAND.descriptor)
        .replace("senior banker", f"senior {BRAND.advisor_role}")
    )
