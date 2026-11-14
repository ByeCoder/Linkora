import asyncio
import httpx


class TelegramError(Exception):
    def __init__(self, code, description, parameters=None):
        self.code, self.description = code, description
        self.parameters = parameters or {}
        super().__init__(f"Telegram {code}: {description}")


class Telegram:
    def __init__(self, token, client=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(65, connect=15))
        self.presentation_fallbacks = 0

    async def call(self, method, **payload):
        # Retry rate limits, but do not blindly retry network-ambiguous sends.
        for attempt in range(4):
            response = await self.client.post(self.base+method, json=payload)
            try:
                data = response.json()
            except ValueError:
                raise TelegramError(response.status_code, "Invalid API response") from None
            if data.get("ok"):
                return data["result"]
            error = TelegramError(data.get("error_code", response.status_code), data.get("description", "API error"), data.get("parameters"))
            if error.code == 429 and attempt < 3:
                await asyncio.sleep(max(1, min(60, error.parameters.get("retry_after", 1))))
                continue
            raise error

    async def send(self, uid, text, rows=None, html=True):
        payload = {"chat_id": uid, "text": text, "link_preview_options": {"is_disabled": True}}
        if html:
            payload["parse_mode"] = "HTML"
        if rows is not None:
            payload["reply_markup"] = {"inline_keyboard": rows}
        try:
            return await self.call("sendMessage", **payload)
        except TelegramError as exc:
            # Preserve buttons when custom emoji or styling is rejected by Telegram.
            description=exc.description.lower()
            decorated=any('style' in b or 'icon_custom_emoji_id' in b for row in (rows or []) for b in row)
            if exc.code!=400 or not decorated or not any(word in description for word in ('emoji','style','button_type_invalid')):
                raise
            self.presentation_fallbacks+=1
            payload['reply_markup']={'inline_keyboard':[[{k:v for k,v in b.items() if k not in {'style','icon_custom_emoji_id'}} for b in row] for row in rows]}
            return await self.call('sendMessage',**payload)

    async def document(self, uid, data, filename, caption=""):
        r = await self.client.post(self.base+"sendDocument", data={"chat_id": str(uid), "caption": caption},
                                   files={"document": (filename, data, "application/octet-stream")})
        try:
            result = r.json()
        except ValueError:
            raise TelegramError(r.status_code, "Invalid document response") from None
        if not result.get("ok"):
            raise TelegramError(result.get("error_code", r.status_code), result.get("description", "Upload failed"))
        return result["result"]

    async def close(self):
        await self.client.aclose()
