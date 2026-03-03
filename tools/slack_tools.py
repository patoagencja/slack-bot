"""Slack read/search tools — use _ctx.app for API calls."""
import logging
from datetime import datetime

import _ctx

logger = logging.getLogger(__name__)


def slack_read_channel_tool(channel_id, limit=50, oldest=None, latest=None):
    """Czyta historię wiadomości z kanału"""
    try:
        params = {'channel': channel_id, 'limit': min(limit, 100)}

        if oldest:
            if len(oldest) == 10:
                dt = datetime.strptime(oldest, '%Y-%m-%d')
                params['oldest'] = str(int(dt.timestamp()))
            else:
                params['oldest'] = oldest

        if latest:
            if len(latest) == 10:
                dt = datetime.strptime(latest, '%Y-%m-%d')
                params['latest'] = str(int(dt.timestamp()))
            else:
                params['latest'] = latest

        result = _ctx.app.client.conversations_history(**params)
        messages = result.get('messages', [])

        formatted = []
        for msg in messages:
            ts = msg.get('ts', '')
            date_str = datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S') if ts else 'Unknown'
            formatted.append({
                'user':       msg.get('user', 'Unknown'),
                'text':       msg.get('text', ''),
                'timestamp':  ts,
                'date':       date_str,
                'has_thread': msg.get('reply_count', 0) > 0,
                'thread_ts':  msg.get('thread_ts'),
            })

        return {'channel_id': channel_id, 'message_count': len(formatted), 'messages': formatted}

    except Exception as e:
        logger.error(f"Błąd czytania kanału: {e}")
        return {"error": str(e)}


def slack_search_tool(query, sort='timestamp', limit=20):
    """Wyszukuje wiadomości na Slacku"""
    try:
        result = _ctx.app.client.search_messages(query=query, sort=sort, count=min(limit, 100))
        matches = result.get('messages', {}).get('matches', [])
        formatted = [{
            'user':      m.get('username', 'Unknown'),
            'text':      m.get('text', ''),
            'channel':   m.get('channel', {}).get('name', 'Unknown'),
            'timestamp': m.get('ts', ''),
            'permalink': m.get('permalink', ''),
        } for m in matches]
        return {'query': query, 'result_count': len(formatted), 'results': formatted}

    except Exception as e:
        logger.error(f"Błąd wyszukiwania: {e}")
        return {"error": str(e)}


def slack_read_thread_tool(channel_id, thread_ts):
    """Czyta wątek (thread) z kanału"""
    try:
        result = _ctx.app.client.conversations_replies(channel=channel_id, ts=thread_ts)
        messages = result.get('messages', [])
        formatted = []
        for msg in messages:
            ts = msg.get('ts', '')
            date_str = datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S') if ts else 'Unknown'
            formatted.append({
                'user':      msg.get('user', 'Unknown'),
                'text':      msg.get('text', ''),
                'timestamp': ts,
                'date':      date_str,
            })
        return {
            'channel_id':  channel_id,
            'thread_ts':   thread_ts,
            'reply_count': len(formatted) - 1,
            'messages':    formatted,
        }

    except Exception as e:
        logger.error(f"Błąd czytania wątku: {e}")
        return {"error": str(e)}
