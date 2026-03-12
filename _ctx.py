"""
Shared global state for all Sebol modules.
Never import from project modules here — no circular imports possible.
bot.py sets these at startup before any job module is invoked.
"""

app = None              # Slack Bolt App instance (slack_bolt.App)
claude = None           # Anthropic client (anthropic.Anthropic)
google_ads_client = None  # Google Ads API client

# Weekly check-in state: user_id → {"messages": [...], "done": bool, "name": str}
checkin_responses = {}

# Claude conversation history per user: user_id → [{"role": ..., "content": ...}]
conversation_history = {}

# Campaign drafts pending approval: campaign_id → {campaign_id, adset_id, ad_ids, params, account_id}
campaign_drafts = {}

# Campaign creation pending info: user_id → {params, files, channel, thread_ts, round}
# Używane gdy bot pyta o brakujące pola przed stworzeniem kampanii
campaign_pending = {}

# Threads where bot has participated: set of (channel, thread_ts) tuples
# Allows bot to respond in threads without explicit mention
bot_threads: set = set()

# /kampania wizard state: user_id → {step, answers, files, source_channel}
campaign_wizard: dict = {}

# /kampaniagoogle wizard state: user_id → {messages, source_channel, thread_ts}
google_campaign_wizard: dict = {}
