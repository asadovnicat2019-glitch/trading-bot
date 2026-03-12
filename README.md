# 🤖 Binance Telegram Trading Bot — Quraşdırma Təlimatı

## 📋 Tələblər
- Python 3.10+
- Binance hesabı (API açarı ilə)
- Telegram hesabı

---

## 🔑 ADDIM 1 — API Açarlarını Al

### Binance API:
1. Binance.com → Profil → API Management
2. "Create API" düyməsinə bas
3. Ad ver: `TradingBot`
4. **Yalnız bu icazələri aç:**
   - ✅ Enable Reading
   - ✅ Enable Spot & Margin Trading
   - ❌ Enable Withdrawals — KƏSİNLİKLƏ AÇMA!
5. IP qeydiyyatı: Öz IP-ni əlavə et (əgər statik varsa)
6. API Key və Secret-i kopyala → `.env` faylına yaz

### Telegram Bot:
1. Telegram-da `@BotFather` tapıb yaz
2. `/newbot` komandası
3. Bot adı ver: `MyTradingBot`
4. Username ver: `mytradingbot_az_bot`
5. Sənə TOKEN verəcək → `.env` faylına yaz

### Telegram ID-ni öyrən:
1. `@userinfobot`-a Telegram-da yaz
2. Sənin ID-ni göstərəcək → `.env` faylına yaz

---

## ⚙️ ADDIM 2 — Quraşdır

```bash
# Proyekti yüklə / köçür
cd trading_bot

# Virtual mühit yarat
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# Kitabxanaları yüklə
pip install -r requirements.txt

# .env faylını yarat
cp .env.example .env
nano .env   # Açarları daxil et
```

---

## 🚀 ADDIM 3 — Botu İşə Sal

```bash
python bot.py
```

Terminaldə görəcəksən:
```
🤖 Bot işə düşdü...
```

Telegram-da botuna `/start` yaz!

---

## 📱 Komandalar

| Komanda | İzah |
|---------|------|
| `/start` | Botu başlat, menyu göstər |
| `/status` | Qiymət + RSI + MACD + Siqnal |
| `/balance` | Binance balansın |
| `/buy BTC 10` | 10 USDT dəyərində BTC al |
| `/sell BTC 10` | 10 USDT dəyərində BTC sat |
| `/auto on` | Avtomatik ticarəti aç |
| `/auto off` | Avtomatik ticarəti dayandır |
| `/settings` | Bütün parametrləri göstər |
| `/set stop_loss 3` | Stop-Loss-u 3%-ə dəyiş |

---

## ⚙️ Parametrlər (set ilə dəyiş)

| Parametr | Varsayılan | İzah |
|----------|-----------|------|
| `symbol` | BTCUSDT | Ticarət cütü |
| `trade_amount` | 10 | USDT miqdarı |
| `stop_loss` | 2.0 | Stop-Loss % |
| `take_profit` | 4.0 | Take-Profit % |
| `rsi_period` | 14 | RSI hesabı üçün şam sayı |
| `rsi_oversold` | 30 | Bu RSI altı = AL siqnalı |
| `rsi_overbought` | 70 | Bu RSI üstü = SAT siqnalı |
| `check_interval` | 60 | Avtomatik yoxlama (saniyə) |

---

## 🛡️ Təhlükəsizlik Qaydaları

1. **Heç vaxt** `.env` faylını GitHub-a yükləmə
2. **Heç vaxt** API-də "Withdrawal" icazəsini açma
3. İlk həftə az məbləğlə test et (5-10 USDT)
4. Stop-Loss mütləq aktiv olsun
5. `ALLOWED_CHAT_ID` mütləq doldur — yad adam botunu idarə etməsin

---

## 🔄 Arxa Planda İşləmək (Linux Server)

```bash
# Screen ilə (sadə)
screen -S tradingbot
python bot.py
# Ctrl+A, D ilə çıx

# Systemd ilə (daha etibarlı)
sudo nano /etc/systemd/system/tradingbot.service
```

```ini
[Unit]
Description=Binance Trading Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/trading_bot
ExecStart=/home/ubuntu/trading_bot/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable tradingbot
sudo systemctl start tradingbot
```

---

## ⚠️ Xəbərdarlıq

Bu bot real pul ilə işləyir. İlk olaraq:
1. Binance Testnet-də sına (testnet.binance.vision)
2. Kiçik məbləğlə başla
3. RSI + MACD siqnalları 100% dəqiq deyil — hər zaman risk var
