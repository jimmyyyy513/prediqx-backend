from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
import httpx
import json
import re

app = FastAPI(title="PREDIQX Polymarket API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later replace with your Base44 domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GAMMA_BASE = "https://gamma-api.polymarket.com"


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "prediqx-polymarket-api",
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def parse_outcome_prices(raw):
    if raw is None:
        return []

    if isinstance(raw, list):
        return [safe_float(x) for x in raw]

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [safe_float(x) for x in parsed]
        except Exception:
            return []

    return []


def normalize_market(market):
    title = market.get("question") or market.get("title") or market.get("name") or "Untitled Market"
    slug = market.get("slug")
    market_id = str(market.get("id") or slug or title)

    prices = parse_outcome_prices(market.get("outcomePrices"))

    yes_price = prices[0] if len(prices) > 0 else safe_float(market.get("yesPrice"), 0.5)
    no_price = prices[1] if len(prices) > 1 else round(1 - yes_price, 4)

    volume = safe_float(
        market.get("volume") or market.get("volumeNum") or market.get("volume24hr") or 0,
        0
    )

    liquidity = safe_float(
        market.get("liquidity") or market.get("liquidityNum") or 0,
        0
    )

    end_date = market.get("endDate") or market.get("end_date") or market.get("closedTime")

    market_url = f"https://polymarket.com/event/{slug}" if slug else None

    return {
        "id": market_id,
        "slug": slug,
        "title": title,
        "category": "Other",
        "yes_price": yes_price,
        "no_price": no_price,
        "market_probability": round(yes_price * 100, 2) if yes_price is not None else None,
        "volume": volume,
        "liquidity": liquidity,
        "spread": None,
        "daily_change": None,
        "end_date": end_date,
        "status": "active",
        "market_url": market_url,
    }


async def fetch_gamma_markets(limit=100):
    url = f"{GAMMA_BASE}/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return data.get("markets") or data.get("data") or []

    return []


@app.get("/polymarket/markets/active")
async def active_markets(limit: int = 100):
    markets = await fetch_gamma_markets(limit)
    normalized = [normalize_market(m) for m in markets]

    return {
        "markets": normalized,
        "count": len(normalized),
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.get("/polymarket/markets/trending")
async def trending_markets(limit: int = 20):
    markets = await fetch_gamma_markets(100)
    normalized = [normalize_market(m) for m in markets]

    normalized.sort(
        key=lambda x: (x.get("volume") or 0) + (x.get("liquidity") or 0),
        reverse=True
    )

    return {
        "markets": normalized[:limit],
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.get("/polymarket/markets/top-volume")
async def top_volume(limit: int = 20):
    markets = await fetch_gamma_markets(100)
    normalized = [normalize_market(m) for m in markets]
    normalized.sort(key=lambda x: x.get("volume") or 0, reverse=True)

    return {
        "markets": normalized[:limit],
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.get("/polymarket/search")
async def search_markets(q: str = Query(...), limit: int = 20):
    markets = await fetch_gamma_markets(200)
    q_lower = q.lower()

    results = []

    for market in markets:
        normalized = normalize_market(market)
        title = normalized["title"].lower()
        slug = str(normalized.get("slug") or "").lower()

        if q_lower in title or q_lower in slug:
            results.append(normalized)

    return {
        "markets": results[:limit],
        "query": q,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.get("/scanner/summary")
async def scanner_summary():
    markets = await fetch_gamma_markets(100)
    normalized = [normalize_market(m) for m in markets]

    highest_volume_market = None
    if normalized:
        highest_volume_market = max(
            normalized,
            key=lambda x: x.get("volume") or 0
        ).get("title")

    return {
        "active_markets_scanned": len(normalized),
        "potential_arbitrage_count": 0,
        "best_theoretical_edge": 0,
        "top_mover": None,
        "highest_volume_market": highest_volume_market,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.get("/scanner/arbitrage")
async def arbitrage_scanner(limit: int = 100):
    markets = await fetch_gamma_markets(limit)
    opportunities = []

    for market in markets:
        normalized = normalize_market(market)

        yes = normalized.get("yes_price")
        no = normalized.get("no_price")

        if yes is None or no is None:
            continue

        total_cost = yes + no
        theoretical_edge = 1 - total_cost

        if total_cost < 0.995:
            opportunities.append({
                "market_title": normalized["title"],
                "market_url": normalized["market_url"],
                "yes_price": round(yes, 4),
                "no_price": round(no, 4),
                "total_cost": round(total_cost, 4),
                "theoretical_edge": round(theoretical_edge, 4),
                "available_size": None,
                "spread": normalized.get("spread"),
                "liquidity": normalized.get("liquidity"),
                "volume": normalized.get("volume"),
                "risk_level": "High",
                "actionable": False,
                "reason": "Theoretical pricing gap exists, but execution, fees, spread, and liquidity may remove edge."
            })

    opportunities.sort(key=lambda x: x["theoretical_edge"], reverse=True)

    return {
        "opportunities": opportunities,
        "count": len(opportunities),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "warning": "Scanner results are theoretical and do not guarantee profit."
    }


@app.get("/scanner/mispriced")
async def mispriced_scanner(limit: int = 50):
    markets = await fetch_gamma_markets(limit)
    results = []

    for market in markets:
        normalized = normalize_market(market)

        market_probability = normalized.get("market_probability")
        if market_probability is None:
            continue

        volume = normalized.get("volume") or 0
        liquidity = normalized.get("liquidity") or 0

        volume_adjustment = 2 if volume > 500000 else 0
        liquidity_adjustment = 2 if liquidity > 100000 else 0

        ai_probability = market_probability + volume_adjustment + liquidity_adjustment
        ai_probability = max(1, min(99, ai_probability))

        edge = ai_probability - market_probability

        risk_level = "Low" if liquidity > 250000 else "Medium" if liquidity > 50000 else "High"

        results.append({
            "market_title": normalized["title"],
            "market_url": normalized["market_url"],
            "market_probability": round(market_probability, 2),
            "ai_probability": round(ai_probability, 2),
            "edge": round(edge, 2),
            "confidence": "Medium" if risk_level != "High" else "Low",
            "risk_level": risk_level,
            "volume": volume,
            "liquidity": liquidity,
            "spread": normalized.get("spread"),
            "decision": "WATCH",
            "reason": "Based on market probability, volume, and liquidity. External evidence not connected yet."
        })

    results.sort(key=lambda x: abs(x["edge"]), reverse=True)

    return {
        "markets": results[:20],
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.get("/scanner/crypto-lag")
async def crypto_lag():
    return {
        "signals": [],
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "message": "Crypto lag scanner endpoint connected. Exchange price comparison not added yet."
    }


def extract_slug_from_url(text: str):
    match = re.search(r"polymarket\.com/event/([^/?#]+)", text)
    if match:
        return match.group(1)
    return None


@app.post("/analyze")
async def analyze(payload: dict):
    user_input = payload.get("input", "")
    slug = extract_slug_from_url(user_input)

    markets = await fetch_gamma_markets(200)
    normalized_markets = [normalize_market(m) for m in markets]

    selected = None

    if slug:
        selected = next((m for m in normalized_markets if m.get("slug") == slug), None)

    if selected is None:
        query = user_input.lower()
        selected = next((m for m in normalized_markets if query in m["title"].lower()), None)

    if selected is None:
        return {
            "error": "market_not_found",
            "message": "Market not found. Try another Polymarket link or search term."
        }

    market_probability = selected.get("market_probability") or 50
    volume = selected.get("volume") or 0
    liquidity = selected.get("liquidity") or 0

    volume_adjustment = 3 if volume > 1000000 else 1 if volume > 100000 else 0
    liquidity_adjustment = 3 if liquidity > 250000 else 1 if liquidity > 50000 else 0

    ai_probability = market_probability + volume_adjustment + liquidity_adjustment
    ai_probability = max(1, min(99, ai_probability))

    edge = ai_probability - market_probability

    risk_level = "Low" if liquidity > 250000 else "Medium" if liquidity > 50000 else "High"

    if edge >= 8 and risk_level != "High":
        decision = "BUY"
    elif edge <= -8:
        decision = "AVOID"
    else:
        decision = "WATCH"

    return {
        "market_title": selected["title"],
        "market_url": selected["market_url"],
        "decision": decision,
        "market_probability": round(market_probability, 2),
        "ai_probability": round(ai_probability, 2),
        "edge": round(edge, 2),
        "confidence": "Medium" if risk_level != "High" else "Low",
        "risk_level": risk_level,
        "suggested_position_size": "2%" if decision == "BUY" else "1%",
        "market_data": {
            "yes_price": selected.get("yes_price"),
            "no_price": selected.get("no_price"),
            "volume": volume,
            "liquidity": liquidity,
            "spread": selected.get("spread"),
            "daily_change": selected.get("daily_change"),
            "end_date": selected.get("end_date"),
        },
        "model_breakdown": {
            "base_market_probability": round(market_probability, 2),
            "volume_adjustment": volume_adjustment,
            "liquidity_adjustment": liquidity_adjustment,
            "spread_penalty": 0,
            "time_risk_penalty": 0,
            "volatility_penalty": 0
        },
        "reasoning": [
            f"The market currently prices YES at {round(market_probability, 2)}%.",
            f"Volume is {volume}, liquidity is {liquidity}.",
            "This first model uses live market data only. News/sentiment are not connected yet."
        ],
        "warnings": [
            "This is analysis only, not financial advice.",
            "Prediction markets are risky.",
            "This model can be wrong."
        ],
        "last_updated": datetime.now(timezone.utc).isoformat()
    }

async def copytrading_leaderboard(
    period: str = "MONTH",
    category: str = "OVERALL",
    sort: str = "PNL",
    limit: int = 25
):
    return {
        "traders": [],
        "period": period,
        "category": category,
        "sort": sort,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "message": "Copy Trading leaderboard endpoint connected. Live trader data integration coming next."
    }
DATA_API_BASE = "https://data-api.polymarket.com"


@app.get("/copytrading/leaderboard")
async def copytrading_leaderboard(
    period: str = "MONTH",
    category: str = "OVERALL",
    sort: str = "PNL",
    limit: int = 25
):
    url = f"{DATA_API_BASE}/v1/leaderboard"

    params = {
        "limit": limit
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    traders = []

    items = data if isinstance(data, list) else data.get("leaderboard", []) if isinstance(data, dict) else []

    for item in items[:limit]:
        username = item.get("userName")
        wallet = item.get("proxyWallet")

        traders.append({
            "rank": item.get("rank"),
            "wallet": wallet,
            "name": username or wallet,
            "pnl": item.get("pnl"),
            "volume": item.get("vol"),
            "win_rate": None,
            "trade_count": None,
            "category": category,
            "profile_url": f"https://polymarket.com/@{username}" if username else None,
            "profile_image": item.get("profileImage"),
            "x_username": item.get("xUsername"),
            "verified": item.get("verifiedBadge", False)
        })

    if sort.upper() == "VOLUME":
        traders.sort(key=lambda x: x.get("volume") or 0, reverse=True)
    else:
        traders.sort(key=lambda x: x.get("pnl") or 0, reverse=True)

    return {
        "traders": traders,
        "period": period,
        "category": category,
        "sort": sort,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }

DATA_API_BASE = "https://data-api.polymarket.com"


@app.get("/copytrading/leaderboard")
async def copytrading_leaderboard(
    period: str = "MONTH",
    category: str = "OVERALL",
    sort: str = "PNL",
    limit: int = 25
):
    url = f"{DATA_API_BASE}/v1/leaderboard"

    params = {
        "limit": limit
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    traders = []

    for item in data[:limit] if isinstance(data, list) else []:
        traders.append({
            "rank": item.get("rank"),
            "wallet": item.get("proxyWallet"),
            "name": item.get("userName") or item.get("proxyWallet"),
            "pnl": item.get("pnl"),
            "volume": item.get("vol"),
            "win_rate": None,
            "trade_count": None,
            "category": category,
            "profile_url": f"https://polymarket.com/@{item.get('userName')}" if item.get("userName") else None,
            "profile_image": item.get("profileImage"),
            "x_username": item.get("xUsername"),
            "verified": item.get("verifiedBadge", False)
        })

    if sort.upper() == "VOLUME":
        traders.sort(key=lambda x: x.get("volume") or 0, reverse=True)
    else:
        traders.sort(key=lambda x: x.get("pnl") or 0, reverse=True)

    return {
        "traders": traders,
        "period": period,
        "category": category,
        "sort": sort,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
@app.get("/copytrading/trader/{wallet}/trades")
async def trader_trades(wallet: str, limit: int = 50):
    wallet = wallet.strip()

    attempts = [
        {
            "endpoint": f"{DATA_API_BASE}/activity",
            "params": {
                "proxy_wallet": wallet,
                "type": "TRADE",
                "limit": limit,
            },
        },
        {
            "endpoint": f"{DATA_API_BASE}/activity",
            "params": {
                "user": wallet,
                "type": "TRADE",
                "limit": limit,
            },
        },
        {
            "endpoint": f"{DATA_API_BASE}/activity",
            "params": {
                "address": wallet,
                "type": "TRADE",
                "limit": limit,
            },
        },
        {
            "endpoint": f"{DATA_API_BASE}/trades",
            "params": {
                "proxy_wallet": wallet,
                "limit": limit,
            },
        },
        {
            "endpoint": f"{DATA_API_BASE}/trades",
            "params": {
                "user": wallet,
                "limit": limit,
            },
        },
        {
            "endpoint": f"{DATA_API_BASE}/trades",
            "params": {
                "address": wallet,
                "limit": limit,
            },
        },
    ]

    raw_items = []
    successful_attempt = None

    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in attempts:
            try:
                response = await client.get(
                    attempt["endpoint"],
                    params=attempt["params"]
                )
                response.raise_for_status()
                data = response.json()

                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = (
                        data.get("activity")
                        or data.get("trades")
                        or data.get("data")
                        or []
                    )
                else:
                    items = []

                if items:
                    raw_items = items
                    successful_attempt = attempt
                    break

            except Exception:
                continue

    trades = []

    for item in raw_items[:limit]:
        slug = item.get("slug") or item.get("eventSlug")
        market_url = f"https://polymarket.com/event/{slug}" if slug else None

        trades.append({
            "market_title": item.get("title") or item.get("marketTitle") or "Unknown Market",
            "market_url": market_url,
            "side": item.get("side"),
            "action": item.get("type") or item.get("action"),
            "price": item.get("price"),
            "size": item.get("size"),
            "usdc_size": item.get("usdcSize"),
            "timestamp": item.get("timestamp") or item.get("createdAt"),
            "outcome": item.get("outcome"),
            "condition_id": item.get("conditionId"),
            "asset_id": item.get("asset") or item.get("assetId"),
            "transaction_hash": item.get("transactionHash"),
        })

    return {
        "wallet": wallet,
        "trades": trades,
        "count": len(trades),
        "successful_attempt": successful_attempt,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "debug_note": "If count is 0, this wallet may have no recent public trades available from this endpoint."
    }