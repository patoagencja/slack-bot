from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from anthropic import Anthropic

# Inicjalizacja Slack App
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# Inicjalizacja Claude
anthropic = Anthropic(api_key=os.environ.get("CLAUDE_API_KEY"))

# Reaguj na wzmianki (@bot)
@app.event("app_mention")
def handle_mention(event, say):
    # Pobierz tekst wiadomości (usuń wzmianke bota)
    user_message = event['text']
    # Usuń <@BOTID> z początku
    user_message = ' '.join(user_message.split()[1:])
    
    # Wyślij "pisze..." indicator
    channel = event['channel']
    thread_ts = event.get('thread_ts', event['ts'])
    
    try:
        # Zapytaj Claude
        message = anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
        
        # Wyślij odpowiedź w tym samym wątku
        response_text = message.content[0].text
        say(text=response_text, thread_ts=thread_ts)
        
    except Exception as e:
        say(text=f"Przepraszam, wystąpił błąd: {str(e)}", thread_ts=thread_ts)

# Uruchom bota
handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
print("⚡️ Bot działa!")
handler.start()
