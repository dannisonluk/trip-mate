"""Real-server verification for the P0 security features.

Runs against a live uvicorn instance (not TestClient) because several of the
behaviours under test — cookie handling, reuse detection, response timing —
only manifest over an actual socket.

Usage:  python scripts/verify_runtime.py [base_url]
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8137").rstrip("/")
API = f"{BASE}/api/v1"
COOKIE = "tripmate_refresh"
COOKIE_PATH = "/api/v1/auth"

# The sandbox exports an HTTP proxy; loopback must bypass it or every call 502s.
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

PASSED: list[str] = []
FAILED: list[str] = []


def new_client() -> httpx.Client:
    return httpx.Client(trust_env=False, timeout=20.0)


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def phone() -> str:
    return f"+8529{uuid.uuid4().int % 10**7:07d}"


def register(c: httpx.Client, p: str) -> httpx.Response:
    return c.post(
        f"{API}/auth/register",
        json={
            "phone_number": p,
            "password": "Passw0rd123",
            "nickname": "Verifier",
            "consent_privacy": True,
            "consent_terms": True,
        },
    )


def login(c: httpx.Client, p: str, pw: str) -> httpx.Response:
    return c.post(f"{API}/auth/login", json={"phone_number": p, "password": pw})


def timed_login(c: httpx.Client, p: str, pw: str) -> tuple[int, float]:
    t0 = time.perf_counter()
    r = login(c, p, pw)
    return r.status_code, (time.perf_counter() - t0) * 1000


client = new_client()

print(f"\n=== Trip Mate runtime verification against {BASE} ===\n")

# --- 1. health / headers ----------------------------------------------------
print("1. Service health")
r = client.get(f"{BASE}/health")
check("GET /health -> 200", r.status_code == 200, r.text.strip()[:80])
check("CSP header present", "content-security-policy" in {k.lower() for k in r.headers})
check("X-Frame-Options: DENY", r.headers.get("x-frame-options") == "DENY")
check("Permissions-Policy present", "permissions-policy" in {k.lower() for k in r.headers})

# --- 2. registration --------------------------------------------------------
print("\n2. Registration")
p = phone()
r = register(client, p)
check("register -> 201", r.status_code == 201, f"status={r.status_code}")
check("access token returned in body", bool(r.json().get("access_token")))
set_cookie = " ".join(r.headers.get_list("set-cookie"))
check("refresh cookie is HttpOnly", "httponly" in set_cookie.lower(), set_cookie[:70])
check("refresh cookie is path-scoped to /auth", COOKIE_PATH in set_cookie)
check("refresh cookie is SameSite=Strict", "samesite=strict" in set_cookie.lower())

# --- 3. login timing side-channel ------------------------------------------
print("\n3. Login timing side-channel (unknown account vs wrong password)")
known = phone()
register(client, known)
# Warm up so we measure steady state, not first-call import cost.
timed_login(client, known, "WrongPass123")
timed_login(client, phone(), "WrongPass123")

known_samples: list[float] = []
unknown_samples: list[float] = []
for _ in range(7):
    _, ms = timed_login(client, known, "WrongPass123")
    known_samples.append(ms)
    _, ms = timed_login(client, phone(), "WrongPass123")
    unknown_samples.append(ms)

k_med = statistics.median(known_samples)
u_med = statistics.median(unknown_samples)
ratio = max(k_med, u_med) / min(k_med, u_med)
print(f"     wrong-password  median: {k_med:.1f} ms")
print(f"     unknown-account median: {u_med:.1f} ms")
print(f"     ratio: {ratio:.2f}x")
check("both branches cost comparable time (<1.6x)", ratio < 1.6, f"{k_med:.0f}ms vs {u_med:.0f}ms")
check("unknown account does real work (not a fast 401)", u_med > 15, f"{u_med:.0f}ms")

# --- 4. refresh rotation + reuse detection ---------------------------------
print("\n4. Refresh token rotation & reuse detection")
client.cookies.clear()
login(client, known, "Passw0rd123")
first = client.cookies.get(COOKIE)
check("login sets refresh cookie", bool(first))

r1 = client.post(f"{API}/auth/refresh")
second = client.cookies.get(COOKIE)
check("first refresh -> 200", r1.status_code == 200, f"status={r1.status_code}")
check("refresh token rotated", bool(second) and second != first)

replay = new_client()
replay.cookies.set(COOKIE, first, domain="127.0.0.1", path=COOKIE_PATH)
r2 = replay.post(f"{API}/auth/refresh")
check("replayed (already rotated) token -> 401", r2.status_code == 401, f"status={r2.status_code}")

r3 = client.post(f"{API}/auth/refresh")
check(
    "reuse detection invalidated the whole family",
    r3.status_code == 401,
    f"status={r3.status_code}",
)

# --- 5. recovery ------------------------------------------------------------
print("\n5. Recovery after reuse detection")
client.cookies.clear()
r = login(client, known, "Passw0rd123")
check("fresh login still works -> 200", r.status_code == 200, f"status={r.status_code}")
r = client.post(f"{API}/auth/refresh")
check("fresh session can refresh -> 200", r.status_code == 200, f"status={r.status_code}")

# --- 6. logout revocation ---------------------------------------------------
print("\n6. Logout revocation")
client.cookies.clear()
login(client, known, "Passw0rd123")
before = client.cookies.get(COOKIE)
r = client.post(f"{API}/auth/logout")
check("logout -> 200/204", r.status_code in (200, 204), f"status={r.status_code}")

replay2 = new_client()
replay2.cookies.set(COOKIE, before, domain="127.0.0.1", path=COOKIE_PATH)
r = replay2.post(f"{API}/auth/refresh")
check("replay of logged-out token -> 401", r.status_code == 401, f"status={r.status_code}")

# --- 7. OTP delivery --------------------------------------------------------
print("\n7. OTP request (console provider)")
client.cookies.clear()
r = client.post(f"{API}/auth/otp/request", json={"phone_number": known})
check("otp/request -> 200", r.status_code == 200, f"status={r.status_code}")
body = r.json() if r.status_code == 200 else {}
check("response reports delivery status", "sent" in body, str(body)[:80])

# --- 8. Event loop responsiveness under password-hash load ------------------
# Argon2 costs ~35 ms per call. If it runs inline in `async def`, N concurrent
# logins serialise and every other request waits behind them. Because it is
# offloaded to a worker thread, an unrelated endpoint should stay fast.
print("\n8. Event loop responsiveness while hashing")


async def _probe_loop_responsiveness() -> tuple[float, float, float]:
    import asyncio

    async with httpx.AsyncClient(trust_env=False, timeout=30.0) as ac:
        async def one_login() -> int:
            resp = await ac.post(
                f"{API}/auth/login",
                json={"phone_number": known, "password": "WrongPass123"},
            )
            return resp.status_code

        async def sample_health(latencies: list[float]) -> None:
            while True:
                t0 = time.perf_counter()
                await ac.get(f"{BASE}/health")
                latencies.append((time.perf_counter() - t0) * 1000)
                await asyncio.sleep(0.005)

        health_latencies: list[float] = []
        sampler = asyncio.create_task(sample_health(health_latencies))
        started = time.perf_counter()
        statuses = await asyncio.gather(*(one_login() for _ in range(12)))
        burst = (time.perf_counter() - started) * 1000
        sampler.cancel()
        try:
            await sampler
        except asyncio.CancelledError:
            pass

    assert all(s == 401 for s in statuses), statuses
    health_latencies.sort()
    p95 = health_latencies[int(len(health_latencies) * 0.95) - 1]
    return burst, health_latencies[0], p95


burst_ms, health_min, health_p95 = asyncio.run(_probe_loop_responsiveness())
print(f"     12 concurrent logins: {burst_ms:.0f} ms total")
print(f"     /health latency during burst: min {health_min:.0f} ms, p95 {health_p95:.0f} ms")
# 12 logins x ~35 ms = ~420 ms if serialised on the loop; /health would stall.
check(
    "/health stays responsive under hash load (p95 < 250 ms)",
    health_p95 < 250,
    f"p95 {health_p95:.0f} ms",
)
check(
    "concurrent logins overlap (burst < 12 x single-call cost)",
    burst_ms < 12 * 35 * 0.8,
    f"{burst_ms:.0f} ms vs ~420 ms serialised",
)

# --- summary ----------------------------------------------------------------
print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
if FAILED:
    for name in FAILED:
        print(f"  FAILED: {name}")
    sys.exit(1)
print("All runtime checks passed.")
