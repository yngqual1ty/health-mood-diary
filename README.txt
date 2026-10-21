Mood Diary Telegram Mini App — исправленный комплект

1. Скопируйте файлы в папку проекта, сохранив существующий .env и mood.sqlite3. Не заменяйте базу данных.
2. Установите зависимости: py -m pip install -r requirements.txt
3. В .env должны быть BOT_TOKEN и актуальный APP_URL (HTTPS URL работающего Cloudflare Tunnel). BOT_USERNAME необязателен.
4. В одном терминале запустите сервер: py -m uvicorn app:app --host 0.0.0.0 --port 8000
5. В другом терминале запустите Cloudflare Tunnel: cloudflared tunnel --url http://localhost:8000
6. Укажите выданный trycloudflare.com адрес в .env как APP_URL, затем перезапустите bot.py. Если адрес изменился — обновите .env и перезапустите бота.
7. Запустите бота: py bot.py

Роль doctor не назначается автоматически. Для теста назначьте роль doctor в существующей базе SQLite вручную; новые пользователи по умолчанию patient. Не публикуйте .env и BOT_TOKEN.

Проверки, выполненные при сборке: синтаксис Python и JavaScript. Реальную авторизацию Telegram и доступность вашего tunnel нужно проверить в Telegram.
