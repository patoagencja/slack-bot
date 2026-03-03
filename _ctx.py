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
