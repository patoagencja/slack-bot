import os
import pathlib

_SKILLS_DIR = pathlib.Path(__file__).parent.parent / "skills"

SKILLS_INDEX = {
    "ab-test-setup": "Plan, design, or implement A/B tests and growth experiments",
    "ad-creative": "Generate, iterate, or scale ad creative — headlines, descriptions, full ad variants",
    "ai-seo": "Optimize content for AI search engines, get cited by LLMs, appear in AI-generated answers",
    "analytics-tracking": "Set up, improve, or audit analytics tracking and measurement",
    "aso-audit": "Audit or optimize App Store or Google Play listings",
    "churn-prevention": "Reduce churn, build cancellation flows, save offers, recover failed payments",
    "co-marketing": "Find co-marketing partners, plan joint campaigns, brainstorm partnership opportunities",
    "cold-email": "Write B2B cold emails and follow-up sequences that get replies",
    "community-marketing": "Build and leverage online communities to drive product growth and brand loyalty",
    "competitor-alternatives": "Create competitor comparison or alternative pages for SEO and sales enablement",
    "competitor-profiling": "Research, profile, or analyze competitors from their URLs",
    "content-strategy": "Plan a content strategy, decide what content to create, figure out topics to cover",
    "copy-editing": "Edit, review, or improve existing marketing copy, or refresh outdated content",
    "copywriting": "Write, rewrite, or improve marketing copy for any page (homepage, landing, pricing, feature, about)",
    "customer-research": "Conduct, analyze, or synthesize customer research",
    "directory-submissions": "Submit product to startup, SaaS, AI, or review directories for backlinks and exposure",
    "email-sequence": "Create or optimize email sequences, drip campaigns, automated flows, lifecycle emails",
    "form-cro": "Optimize lead capture forms, contact forms, and non-signup forms for conversions",
    "free-tool-strategy": "Plan, evaluate, or build a free tool for marketing — lead gen, SEO, or brand awareness",
    "image": "Create, generate, edit, or optimize images for marketing",
    "launch-strategy": "Plan a product launch, feature announcement, or release strategy",
    "lead-magnets": "Create, plan, or optimize lead magnets for email capture or lead generation",
    "marketing-ideas": "Generate marketing ideas, inspiration, or strategies for SaaS/software products",
    "marketing-psychology": "Apply psychological principles and behavioral science to marketing",
    "onboarding-cro": "Optimize post-signup onboarding, user activation, first-run experience, time-to-value",
    "page-cro": "Optimize or increase conversions on any marketing page",
    "paid-ads": "Help with paid advertising on Google Ads, Meta, LinkedIn, Twitter/X or other platforms",
    "paywall-upgrade-cro": "Create or optimize in-app paywalls, upgrade screens, upsell modals, feature gates",
    "popup-cro": "Create or optimize popups, modals, overlays, slide-ins, or banners for conversion",
    "pricing-strategy": "Help with pricing decisions, packaging, or monetization strategy",
    "product-marketing-context": "Create or update product marketing context document",
    "programmatic-seo": "Create SEO-driven pages at scale using templates and data",
    "referral-program": "Create, optimize, or analyze referral programs, affiliate programs, word-of-mouth",
    "revops": "Revenue operations, lead lifecycle management, or marketing-to-sales handoff",
    "sales-enablement": "Create sales collateral, pitch decks, one-pagers, objection handling docs, demo scripts",
    "schema-markup": "Add, fix, or optimize schema markup and structured data on a site",
    "seo-audit": "Audit, review, or diagnose SEO issues on a site",
    "signup-flow-cro": "Optimize signup, registration, account creation, or trial activation flows",
    "site-architecture": "Plan, map, or restructure website page hierarchy, navigation, URL structure",
    "social-content": "Create, schedule, or optimize social media content for LinkedIn, Twitter/X, Instagram, TikTok",
    "video": "Create, generate, or produce video content using AI tools or programmatic frameworks",
}


def load_marketing_skill(skill_name: str) -> dict:
    skill_name = skill_name.strip().lower()
    if skill_name not in SKILLS_INDEX:
        available = ", ".join(sorted(SKILLS_INDEX.keys()))
        return {"error": f"Skill '{skill_name}' not found. Available: {available}"}

    skill_path = _SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return {"error": f"Skill file not found: {skill_path}"}

    content = skill_path.read_text(encoding="utf-8")
    return {"skill": skill_name, "instructions": content}
