"""Article-owned thumbnails from publisher metadata; no generated images."""
from urllib.parse import urljoin, urlsplit


def image_url(value, article_url):
    if not isinstance(value, str) or not value.strip():
        return ''
    try:
        url = urljoin(article_url, value.strip())
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return ''
        return url
    except ValueError:
        return ''


def article_image(soup, article_url):
    # Google's article relay can expose its own logo, not the publisher image.
    if urlsplit(article_url).hostname == 'news.google.com':
        return ''
    selectors = [('meta[property="og:image:secure_url"]', 'content'),
                 ('meta[property="og:image"]', 'content'),
                 ('meta[name="twitter:image"]', 'content'),
                 ('#dic_area img', 'data-src'), ('#dic_area img', 'src'),
                 ('#newsct_article img', 'src'), ('article img', 'src')]
    for selector, attribute in selectors:
        node = soup.select_one(selector)
        if node:
            candidate = image_url(node.get(attribute), article_url)
            if candidate:
                return candidate
    return ''
