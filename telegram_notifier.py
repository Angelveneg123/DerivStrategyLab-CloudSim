import json
import os
import urllib.error
import urllib.request

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def is_configured():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_message(text, timeout=8):
    """Envía Telegram sin permitir que un fallo detenga el worker."""
    if not is_configured():
        print(
            "[Telegram] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados. "
            "Notificacion omitida.",
            flush=True,
        )
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))

        if result.get("ok"):
            print("[Telegram] Alerta enviada correctamente.", flush=True)
            return True

        print(f"[Telegram] Telegram respondio sin OK: {result}", flush=True)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"[Telegram] No se pudo enviar la alerta: {exc}", flush=True)
    except Exception as exc:
        print(f"[Telegram] Error inesperado: {exc}", flush=True)

    return False


def send_pre_entry(
    *,
    trade_number,
    symbol,
    direction,
    entry,
    stop,
    take,
    reward_ratio,
    signal_time,
):
    """
    Usa Entry/SL/TP ya calculados por worker_simulation.
    No calcula señales y no ejecuta operaciones.
    """
    direction_icon = "🟢" if str(direction).upper() == "BUY" else "🔴"

    message = (
        f"🚨 PRE-ENTRADA #{trade_number}\n"
        f"{symbol}\n"
        f"{direction_icon} {direction}\n\n"
        f"Entrada: {float(entry):.3f}\n"
        f"Stop Loss: {float(stop):.3f}\n"
        f"Take Profit: {float(take):.3f}\n"
        f"RR: 1:{float(reward_ratio):.2f}\n"
        f"Hora señal (UTC): {signal_time}"
    )

    return send_message(message)
