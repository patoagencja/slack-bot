"""
Shared fixtures and mocks — patch everything that calls external services
(Slack, Meta API, Anthropic, OpenAI) so tests run offline.
"""
import sys
import types
import pytest
from unittest.mock import MagicMock, patch


# ── Stub heavy external modules before bot.py is imported ─────────────────────

def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# facebook_business stubs
_fb = _stub_module("facebook_business")
_fb_api = _stub_module("facebook_business.api", FacebookAdsApi=MagicMock())
_fb_aa = _stub_module("facebook_business.adobjects.adaccount", AdAccount=MagicMock())
_fb_camp = _stub_module("facebook_business.adobjects.campaign", Campaign=MagicMock())
_fb_adset = _stub_module("facebook_business.adobjects.adset", AdSet=MagicMock())
_fb_ad = _stub_module("facebook_business.adobjects.ad", Ad=MagicMock())
_fb_img = _stub_module("facebook_business.adobjects.adimage", AdImage=MagicMock())
_fb_vid = _stub_module("facebook_business.adobjects.advideo", AdVideo=MagicMock())
_fb_cr = _stub_module("facebook_business.adobjects.adcreative", AdCreative=MagicMock())
_fb_ts = _stub_module("facebook_business.adobjects.targetingsearch", TargetingSearch=MagicMock())
_fb_exc = _stub_module("facebook_business.exceptions", FacebookRequestError=Exception)

# slack_bolt stubs
_bolt = _stub_module("slack_bolt", App=MagicMock())
_bolt_sm = _stub_module("slack_bolt.adapter.socket_mode", SocketModeHandler=MagicMock())

# anthropic stub
_anth = _stub_module("anthropic", Anthropic=MagicMock())

# openai stub
_oai = _stub_module("openai", OpenAI=MagicMock())

# apscheduler stub
_aps = _stub_module("apscheduler")
_aps_bg = _stub_module("apscheduler.schedulers.background", BackgroundScheduler=MagicMock())

# pytz stub
_pytz = _stub_module("pytz")
_pytz.timezone = MagicMock(return_value=MagicMock())
_pytz.utc = MagicMock()

# jobs / tools stubs (only what bot.py imports at top level)
_ALL_MOCK_ATTRS = [
    # jobs.team
    "close_request", "get_pending_requests", "_format_requests_list",
    "_next_workday", "get_availability_for_date", "_format_availability_summary",
    "handle_employee_dm", "send_daily_team_availability",
    "sync_availability_from_slack", "remove_availability_entries", "find_team_member",
    # jobs.standup
    "send_standup_questions", "post_standup_summary",
    "handle_standup_reply", "handle_standup_slash",
    # jobs.onboarding
    "_handle_onboarding_done", "check_stale_onboardings", "handle_onboard_slash",
    # jobs.industry_news
    "weekly_industry_news",
    # tools.meta_ads
    "meta_ads_tool",
    # tools.google_ads
    "google_ads_tool",
    "create_google_campaign_draft", "generate_google_campaign_preview", "_detect_google_client",
    # tools.google_analytics
    "google_analytics_tool",
    # tools.email_tools
    "email_tool", "get_user_email_config",
    # tools.slack_tools
    "slack_read_channel_tool", "slack_read_thread_tool",
    # tools.icloud_calendar
    "icloud_calendar_tool",
    # tools.memory
    "init_memory", "remember", "recall_as_context", "get_history",
    # tools.memory_backfill
    "memory_backfill",
    # jobs.*
    "_dispatch_ads_command", "backfill_campaign_history",
    "generate_daily_digest_dre", "daily_digest_dre", "weekly_learnings_dre",
    "check_budget_alerts", "weekly_report_dre", "send_weekly_reports",
    "weekly_checkin", "send_checkin_reminders", "checkin_summary",
    "daily_email_summary_slack",
    "morning_standup", "standup_summary", "send_standup_reminders",
    "handle_onboarding_start", "handle_onboarding_update",
    "get_onboarding_status", "send_onboarding_digest",
    "handle_team_request", "handle_team_availability",
    "get_team_status", "send_team_digest",
]

for _mod in [
    "jobs.onboarding", "jobs.weekly_reports", "jobs.budget_alerts",
    "jobs.standup", "jobs.daily_digest", "jobs.email_summary", "jobs.checkin",
    "jobs.industry_news", "jobs.performance_analysis", "jobs.team",
    "tools.meta_ads", "tools.google_ads", "tools.google_analytics",
    "tools.email_tools", "tools.slack_tools", "tools.icloud_calendar",
    "tools.memory", "tools.memory_backfill",
]:
    _stub_module(_mod, **{k: MagicMock() for k in _ALL_MOCK_ATTRS})

_stub_module("tools.voice_transcription",
             transcribe_slack_audio=MagicMock(return_value=None),
             SLACK_AUDIO_MIMES=set())
