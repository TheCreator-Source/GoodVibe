import json
import threading
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
import requests

DATA_DIR = Path.home() / ".messenger_google"
DATA_DIR.mkdir(exist_ok=True)
TOKEN_FILE = DATA_DIR / "token.json"

SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile'
]

class GoogleAuthManager:
    def __init__(self, client_id, client_secret, callback=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback = callback

    def start_login(self):
        thread = threading.Thread(target=self._perform_login, daemon=True)
        thread.start()

    def _perform_login(self):
        try:
            client_config = {
                "installed": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"]
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
            # Автоматический запуск локального сервера
            credentials = flow.run_local_server(port=0, open_browser=True)
            # Получение информации о пользователе
            response = requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {credentials.token}'}
            )
            response.raise_for_status()
            user_info = response.json()
            # Сохраняем токен
            with open(TOKEN_FILE, 'w') as f:
                json.dump({'token': credentials.token, 'user': user_info}, f)
            if self.callback:
                self.callback(user_info)
        except Exception as e:
            print(f"Ошибка привязки Google: {e}")
            if self.callback:
                self.callback(None)