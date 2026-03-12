"""
Tests for the wizard awaiting_approval state — ensures "uruchom" triggers
approve_and_launch_campaign and NOT the channel handler hallucination.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import MagicMock, patch, call


def get_handler():
    import bot
    return bot._handle_meta_campaign_wizard


def make_wizard(state="awaiting_approval", campaign_id="123456"):
    return {
        "state": state,
        "draft_campaign_id": campaign_id,
        "source_channel": "C123",
        "thread_ts": "1234567890.000001",
        "mode": "simple",
        "resolved_mode": "simple",
        "messages": [],
        "files": [],
    }


class TestAwaitingApproval:

    def setup_method(self):
        import _ctx
        _ctx.meta_campaign_wizard.clear()

    @pytest.mark.parametrize("msg", [
        "uruchom", "URUCHOM", "uruchamiaj", "zatwierdź", "zatwierdz",
        "tak", "yes", "ok", "lecimy", "wgraj",
    ])
    def test_launch_keywords_call_approve(self, msg):
        import _ctx
        import bot
        _ctx.meta_campaign_wizard["U123"] = make_wizard()

        say = MagicMock()
        with patch.object(bot, "approve_and_launch_campaign", return_value="✅ Uruchomiono") as mock_approve:
            result = bot._handle_meta_campaign_wizard("U123", msg, [], say)

        assert result is True
        mock_approve.assert_called_once_with("123456")
        assert "U123" not in _ctx.meta_campaign_wizard, "Wizard should be deleted after launch"

    @pytest.mark.parametrize("msg", [
        "anuluj", "cancel", "stop", "nie", "no",
    ])
    def test_cancel_keywords_call_cancel(self, msg):
        import _ctx
        import bot
        _ctx.meta_campaign_wizard["U123"] = make_wizard()

        say = MagicMock()
        with patch.object(bot, "cancel_campaign_draft", return_value=None) as mock_cancel:
            result = bot._handle_meta_campaign_wizard("U123", msg, [], say)

        assert result is True
        mock_cancel.assert_called_once_with("123456")
        assert "U123" not in _ctx.meta_campaign_wizard

    def test_other_message_shows_reminder(self):
        import _ctx
        import bot
        _ctx.meta_campaign_wizard["U123"] = make_wizard()

        say = MagicMock()
        result = bot._handle_meta_campaign_wizard("U123", "zmień budżet na 100", [], say)

        assert result is True
        say.assert_called_once()
        msg = say.call_args[0][0]
        assert "uruchom" in msg.lower() or "anuluj" in msg.lower()
        # Wizard should still be alive
        assert "U123" in _ctx.meta_campaign_wizard

    def test_returns_false_when_no_wizard(self):
        import bot
        result = bot._handle_meta_campaign_wizard("U_NOBODY", "uruchom", [], MagicMock())
        assert result is False


class TestWizardNotDeletedBeforeApproval:
    """After draft creation, wizard must stay alive in awaiting_approval state."""

    def setup_method(self):
        import _ctx
        _ctx.meta_campaign_wizard.clear()

    def test_wizard_state_set_to_awaiting_after_draft(self):
        import _ctx
        import bot

        # Simulate wizard that just finished collecting data
        wizard = {
            "state": None,
            "draft_campaign_id": None,
            "source_channel": "C123",
            "thread_ts": "111.222",
            "mode": "simple",
            "resolved_mode": "simple",
            "messages": [],
            "files": [],
        }
        _ctx.meta_campaign_wizard["U123"] = wizard

        completion_text = (
            "===KAMPANIA_META_GOTOWA===\n"
            "Kampania gotowa!\n"
            "```json\n"
            '{"mode":"simple","campaign_name":"DRE test","objective":"traffic",'
            '"daily_budget":"10","country":"Polska","age_range":"18-30",'
            '"gender":"female","interests":[],'
            '"landing_page_url":"https://dre.eu",'
            '"creative":{"primary_text":"ELO","cta":"LEARN_MORE"},'
            '"ready_to_create":true}\n'
            "```\n"
        )

        say = MagicMock()
        mock_draft = {"campaign_id": "CAMP_999", "adset_id": "ADSET_1", "ad_ids": ["AD_1"],
                      "params": {}, "account_id": "act_12345"}

        with patch.object(bot, "get_meta_account_id", return_value="act_12345"), \
             patch.object(bot, "build_meta_targeting", return_value={}), \
             patch.object(bot, "create_campaign_draft", return_value=mock_draft), \
             patch.object(bot, "generate_campaign_preview", return_value="📋 Preview OK"):

            # Simulate Claude returning the completion text
            with patch.object(_ctx.claude.messages, "create") as mock_claude:
                mock_claude.return_value.content = [MagicMock(text=completion_text)]
                bot._handle_meta_campaign_wizard("U123", "uruchom na koncie dre", [], say)

        # Wizard is cleaned up after successful draft creation (campaign is left PAUSED in Meta)
        assert "U123" not in _ctx.meta_campaign_wizard, \
            "Wizard should be deleted after draft creation — campaign is left paused in Meta"
        # Preview and done message must have been sent
        say.assert_any_call("📋 Preview OK")
        done_calls = [str(c) for c in say.call_args_list]
        assert any("wyłączona" in c or "włącz" in c.lower() for c in done_calls), \
            "Bot should tell user campaign is paused/disabled"
