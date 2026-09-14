"""Feed definitions — keyless RSS + Google News RSS only.

GDELT and Moneycontrol/Chittorgarh are deliberately absent (excluded by
instruction until explicitly enabled).
"""
from __future__ import annotations

# (name, url, category)
RSS_FEEDS: list[tuple[str, str, str]] = [
    # ---- Crypto
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto_news"),
    ("cointelegraph", "https://cointelegraph.com/rss", "crypto_news"),
    ("decrypt", "https://decrypt.co/feed", "crypto_news"),
    # ---- US markets
    ("cnbc_markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069", "us_stock_market"),
    ("cnbc_finance", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664", "us_stock_market"),
    ("marketwatch_top", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "us_stock_market"),
    ("yahoo_finance", "https://finance.yahoo.com/news/rssindex", "us_stock_market"),
    # ---- US politics
    ("npr_politics", "https://feeds.npr.org/1014/rss.xml", "us_politics"),
    ("thehill", "https://thehill.com/homenews/feed/", "us_politics"),
    # ---- India
    ("et_markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "india_market_ipo"),
    ("et_stocks", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", "india_market_ipo"),
    ("et_ipo", "https://economictimes.indiatimes.com/markets/ipos/fpos/rssfeeds/14655708.cms", "india_market_ipo"),
    ("livemint_markets", "https://www.livemint.com/rss/markets", "india_market_ipo"),
    ("bs_markets", "https://www.business-standard.com/rss/markets-106.rss", "india_market_ipo"),
    ("thehindu_business", "https://www.thehindu.com/business/feeder/default.rss", "india_market_ipo"),
    ("ndtv_india", "https://feeds.feedburner.com/ndtvnews-india-news", "india_politics"),
    ("toi_india", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms", "india_politics"),
    # ---- Geopolitics (wars, diplomacy, sanctions, alliances)
    ("bbc_mideast", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "geopolitics"),
    ("dw_world", "https://rss.dw.com/rdf/rss-en-world", "geopolitics"),
    ("france24_world", "https://www.france24.com/en/rss", "geopolitics"),
    ("thediplomat", "https://thediplomat.com/feed/", "geopolitics"),
    ("reuters_world_gnews", "https://news.google.com/rss/search?q=when:1d+site:reuters.com+world&hl=en-US&gl=US&ceid=US:en", "geopolitics"),
    # ---- Daily / world
    ("bbc_world", "https://feeds.bbci.co.uk/news/world/rss.xml", "daily_news"),
    ("aljazeera", "https://www.aljazeera.com/xml/rss/all.xml", "daily_news"),
    ("reuters_via_gnews", "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-US&gl=US&ceid=US:en", "daily_news"),
]

# Google News RSS keyword searches (keyless, unofficial)
GOOGLE_NEWS_QUERIES: list[tuple[str, str, str]] = [
    ("gnews_gold", "gold price OR bullion OR XAU when:1d", "gold_news"),
    ("gnews_crypto", "bitcoin OR ethereum OR crypto regulation when:1d", "crypto_news"),
    ("gnews_us_ipo", "IPO pricing OR IPO debut OR S-1 filing when:1d", "us_market_ipo"),
    ("gnews_india_ipo", "India IPO OR SME IPO OR NSE listing when:1d", "india_market_ipo"),
    ("gnews_fed", "Federal Reserve OR FOMC OR US inflation CPI when:1d", "us_econ_calendar"),
    ("gnews_rbi", "RBI policy OR India CPI OR India GDP when:1d", "india_econ_calendar"),
    ("gnews_us_pol", "US Congress OR White House policy when:1d", "us_politics"),
    ("gnews_in_pol", "India government policy OR Parliament session when:1d", "india_politics"),
    ("gnews_geo_conflict", "ceasefire OR airstrike OR military offensive OR border clash when:1d", "geopolitics"),
    ("gnews_geo_diplomacy", "sanctions OR peace talks OR NATO OR UN Security Council when:1d", "geopolitics"),
    ("gnews_geo_asia", "Taiwan OR South China Sea OR North Korea tensions when:1d", "geopolitics"),
    ("gnews_geo_india", "India Pakistan OR India China border OR India foreign policy when:1d", "geopolitics"),
]


def google_news_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
