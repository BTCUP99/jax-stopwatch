"""X Activity API listener: real-time new-follower and @mention notices.

Credentials and tokens stay in runtime/x.json (0600). Mention text is shown on the
device and kept in memory only; the event log records event kinds, never content.
"""
import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from collections import deque
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout

API = "https://api.x.com/2"
AUTHORIZE = "https://x.com/i/oauth2/authorize"
SCOPES = "tweet.read users.read follows.read offline.access"
# direction=inbound is inferred from the docs ("is followed by another user"); handle() still
# checks that the follow targets this account.
WANTED = {"follow.follow": {"direction": "inbound"}, "post.mention.create": {}, "post.reply.create": {},
          "post.repost.create": {}, "post.quote.create": {}}


class XError(RuntimeError):
    def __init__(self, status, text):
        super().__init__(f"X API HTTP {status}：{text[:300]}")
        self.status = status


def user_of(value):
    """Follow payloads wrap users as {"data": user} in samples and bare users in the schema."""
    value = value or {}
    return value.get("data", value) if isinstance(value.get("data"), dict) else value


def describe(event, me):
    """Turn an Activity event into (kind, notice text), or None when it is not for this account."""
    kind, payload = event.get("event_type"), event.get("payload") or {}
    if kind == "follow.follow":
        source, target = user_of(payload.get("source")), user_of(payload.get("target"))
        if str(target.get("id")) != str(me) or str(source.get("id")) == str(me):
            return None
        return "follow", f"新粉丝：{label(source)}"
    if kind in ("post.mention.create", "post.reply.create", "post.repost.create", "post.quote.create"):
        if str(payload.get("author_id")) == str(me):  # own posts and self-thread replies
            return None
        users = (event.get("includes") or {}).get("users") or []
        author = next((u for u in users if str(u.get("id")) == str(payload.get("author_id"))), {})
        if kind == "post.repost.create":
            return "repost", f"{label(author)} 转发了你的帖子"
        text = re.sub(r"https?://\S+", "", payload.get("text", ""))
        text = re.sub(r"^(?:@\w+\s*)+", "", text).strip()  # leading reply handles
        if len(text) > 80:
            text = text[:80] + "……"
        if kind == "post.quote.create":
            return "quote", f"{label(author)} 引用了你的帖子：{text or '（无评论）'}"
        if kind == "post.reply.create":
            return "reply", f"{label(author)} 回复了你：{text or '（无正文）'}"
        return "mention", f"{label(author)} 提到了你：{text or '（无正文）'}"
    return None


def label(user):
    name, handle = user.get("name"), user.get("username")
    if name and handle:
        return f"{name}（@{handle}）"
    return f"@{handle}" if handle else (name or "有人")


class XWatch:
    def __init__(self, runtime, notify, emit, redirect):
        self.file = runtime / "x.json"
        self.notify = notify
        self.emit = emit
        self.redirect = redirect
        self.config = json.loads(self.file.read_text()) if self.file.exists() else {}
        self.pending_auth = {}
        self.streaming = False
        self.backfill = True  # cleared if the app's X plan rejects backfill_minutes
        self.error = ""
        self.last_event = 0.
        self.recent = deque(maxlen=20)  # memory only
        self.seen = deque(maxlen=500)
        self.seen_posts = deque(maxlen=500)
        self.wake = asyncio.Event()
        self.session = None

    def save(self):
        saved = self.file.with_suffix(".tmp")
        saved.write_text(json.dumps(self.config))
        saved.chmod(0o600)
        saved.replace(self.file)

    def status(self):
        c = self.config
        return {"appConfigured": bool(c.get("bearer") and c.get("clientId")),
                "authorized": bool(c.get("refresh") or c.get("access")),
                "username": (c.get("user") or {}).get("username"),
                "enabled": c.get("enabled", True), "streaming": self.streaming,
                "subscriptions": sorted(c.get("subscriptions", {})), "error": self.error,
                "lastEvent": self.last_event, "redirect": self.redirect,
                "recent": list(self.recent)}

    def configure(self, data):
        for field, key in (("bearer", "bearer"), ("clientId", "clientId"), ("clientSecret", "clientSecret")):
            value = str(data.get(field) or "").strip()
            if value:
                if len(value) > 400 or any(ch.isspace() for ch in value):
                    raise ValueError("凭据格式不对，请确认没有多复制空格或换行")
                if key == "clientId" and value != self.config.get("clientId"):
                    for stale in ("access", "refresh", "expires", "user", "subscriptions"):
                        self.config.pop(stale, None)
                self.config[key] = value
        if isinstance(data.get("enabled"), bool):
            self.config["enabled"] = data["enabled"]
        if not self.config.get("bearer") or not self.config.get("clientId"):
            raise ValueError("请填写 Bearer Token 和 OAuth 2.0 Client ID")
        self.save()
        self.error = ""
        self.wake.set()

    def authorize_url(self):
        if not self.config.get("clientId"):
            raise ValueError("请先保存 X 应用凭据")
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        self.pending_auth = {"state": state, "verifier": verifier, "time": time.monotonic()}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return AUTHORIZE + "?" + urlencode({"response_type": "code", "client_id": self.config["clientId"],
            "redirect_uri": self.redirect, "scope": SCOPES, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})

    async def complete(self, code, state):
        pending, self.pending_auth = self.pending_auth, {}
        if not pending or not secrets.compare_digest(state or "", pending["state"]) \
                or time.monotonic() - pending["time"] > 600:
            raise ValueError("授权已过期或不匹配，请从控制台重新点“授权 X 账号”")
        await self.token({"grant_type": "authorization_code", "code": code,
                          "redirect_uri": self.redirect, "code_verifier": pending["verifier"]})
        me = (await self.call("GET", "/users/me", auth="user"))["data"]
        self.config["user"] = {"id": me["id"], "username": me.get("username")}
        self.config.pop("subscriptions", None)
        self.save()
        self.error = ""
        self.wake.set()
        await self.emit("x_authorized", {"username": me.get("username")})

    async def token(self, form):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        form = {**form, "client_id": self.config["clientId"]}
        if self.config.get("clientSecret"):
            pair = f"{self.config['clientId']}:{self.config['clientSecret']}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
        async with self.http().post(API + "/oauth2/token", data=form, headers=headers) as response:
            body = await response.text()
            if response.status != 200:
                raise XError(response.status, body)
        data = json.loads(body)
        self.config.update(access=data["access_token"], expires=time.time() + int(data.get("expires_in", 7200)))
        if data.get("refresh_token"):
            self.config["refresh"] = data["refresh_token"]  # X rotates refresh tokens
        self.save()

    async def user_token(self):
        if not self.config.get("access") or time.time() > self.config.get("expires", 0) - 120:
            if not self.config.get("refresh"):
                raise RuntimeError("X 账号授权已失效，请在控制台重新授权")
            await self.token({"grant_type": "refresh_token", "refresh_token": self.config["refresh"]})
        return self.config["access"]

    def http(self):
        if self.session is None or self.session.closed:
            self.session = ClientSession(timeout=ClientTimeout(total=30), trust_env=True)
        return self.session

    async def call(self, method, path, auth="app", **kwargs):
        token = await self.user_token() if auth == "user" else self.config["bearer"]
        async with self.http().request(method, API + path, headers={"Authorization": "Bearer " + token},
                                       **kwargs) as response:
            body = await response.text()
            if response.status >= 300:
                raise XError(response.status, body)
            return json.loads(body) if body else {}

    async def ensure_subscriptions(self):
        me = self.config["user"]["id"]
        found = {}
        try:
            listing = await self.call("GET", "/activity/subscriptions", auth="user", params={"user_id": me})
        except XError:
            listing = await self.call("GET", "/activity/subscriptions", params={"user_id": me})
        for sub in listing.get("data") or []:
            filt = sub.get("filter") or {}
            direction = filt.get("direction") or (filt.get("qualifiers") or {}).get("direction")
            want = WANTED.get(sub.get("event_type"))
            if want is not None and str(filt.get("user_id")) == str(me) and direction == want.get("direction"):
                found[sub["event_type"]] = sub.get("subscription_id")
        for event_type, extra in WANTED.items():
            if event_type in found:
                continue
            body = {"event_type": event_type, "filter": {"user_id": me, **extra}, "tag": "stopwatch"}
            try:
                result = await self.call("POST", "/activity/subscriptions", auth="user", json=body)
            except XError:
                if event_type != "follow.follow":
                    raise
                result = await self.call("POST", "/activity/subscriptions", json=body)
            data = result.get("data") or {}
            found[event_type] = (data.get("subscription") or data).get("subscription_id")
            await self.emit("x_subscribed", {"eventType": event_type})
        self.config["subscriptions"] = found
        self.save()

    async def run(self):
        delay, disconnected = 5, None
        while True:
            self.wake.clear()
            c = self.config
            if not (c.get("enabled", True) and c.get("bearer") and c.get("user")):
                self.streaming = False
                await self.wake.wait()
                continue
            try:
                if set(c.get("subscriptions") or {}) != set(WANTED):
                    await self.ensure_subscriptions()
                params = {}
                if disconnected and self.backfill:
                    params["backfill_minutes"] = max(1, min(5, int((time.time() - disconnected) // 60) + 1))
                await self.stream(params)
                delay = 5
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if isinstance(error, XError) and error.status == 400 and "backfill" in str(error):
                    self.backfill = False  # reconnect at once without it; events missed while offline are lost
                    continue
                self.error = str(error) or type(error).__name__
                await self.emit("x_error", {"message": self.error[:300]})
                if isinstance(error, XError) and error.status == 429:
                    delay = max(delay, 60)
                if isinstance(error, XError) and error.status in (401, 403):
                    delay = max(delay, 300)
            finally:
                if self.streaming:
                    disconnected = time.time()
                self.streaming = False
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
                delay = 5
            except asyncio.TimeoutError:
                delay = min(delay * 2, 300)

    async def stream(self, params):
        # No documented heartbeat interval: a long read timeout drops dead links, backfill covers the gap.
        timeout = ClientTimeout(total=None, sock_connect=30, sock_read=900)
        async with ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(API + "/activity/stream", params=params,
                                   headers={"Authorization": "Bearer " + self.config["bearer"]}) as response:
                if response.status != 200:
                    raise XError(response.status, await response.text())
                self.streaming, self.error = True, ""
                await self.emit("x_stream", {"connected": True})
                reader = asyncio.create_task(self.read(response))
                changed = asyncio.create_task(self.wake.wait())  # settings changed: reconnect with them
                try:
                    await asyncio.wait((reader, changed), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    changed.cancel()
                    reader.cancel()
                    await asyncio.gather(changed, reader, return_exceptions=True)
                if reader.done() and not reader.cancelled() and reader.exception():
                    raise reader.exception()

    async def read(self, response):
        async for raw in response.content:
            line = raw.strip()
            if line:
                await self.handle(json.loads(line))

    async def handle(self, message):
        if message.get("errors") and not message.get("data"):
            self.error = json.dumps(message["errors"], ensure_ascii=False)[:300]
            return
        event = message.get("data") or {}
        uid = event.get("event_uuid")
        if uid in self.seen:
            return
        self.seen.append(uid)
        result = describe(event, self.config["user"]["id"])
        if not result:
            return
        # A reply that also @-mentions this account arrives as both events; announce the post once.
        post = (event.get("payload") or {}).get("id") if result[0] != "follow" else None
        if post:
            if post in self.seen_posts:
                return
            self.seen_posts.append(post)
        kind, text = result
        self.last_event = time.time()
        self.recent.appendleft({"time": self.last_event, "kind": kind, "text": text})
        await self.emit("x_notice", {"kind": kind})
        await self.notify(kind, text)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
