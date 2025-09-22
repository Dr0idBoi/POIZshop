# app/services/links.py
def carrier_link(carrier: str, track: str) -> str:
    carrier = (carrier or "").lower()
    if carrier == "boxberry":
        return f"https://boxberry.ru/tracking/?code={track}"
    if carrier == "cdek":
        return f"https://www.cdek.ru/ru/tracking?order_id={track}"
    if carrier in ("pochta","почта","почта россии","russianpost"):
        return f"https://www.pochta.ru/tracking#{track}"
    return ""
