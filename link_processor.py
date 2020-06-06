import re
import sys
import requests
import requests.exceptions
import collections
import bs4

_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; rv:68.0) Gecko/20100101 Firefox/68.0'
}

_default_opts = [
    '--grayscale', '--disable-javascript',
    '--page-width', '6.2in', '--page-height', '8.26in', '--dpi', '226',
    '--minimum-font-size', '16',
    '--load-error-handling', 'ignore',
    '--custom-header', 'User-Agent', _headers['User-Agent'],
    '--margin-top', '2mm', '--margin-bottom', '2mm', '--margin-left', '2mm', '--margin-right', '2mm']

def _options(background=False):
    return _default_opts \
        + ['--{}background'.format('' if background else 'no-')]

def _resolve_redirect(link):
    try:
        r = requests.head(link, headers=_headers, allow_redirects=False)
    except requests.exceptions.ConnectionError:
        print('Error: resolve_redirect("{}") connection error'.format(link), file=sys.stderr)
        return link

    if r.status_code in {301, 302, 307, 308}:
        return r.headers['Location']
    else:
        return link

def get_num_pages_ars(link):
    r = requests.get(link, headers=_headers)
    page = bs4.BeautifulSoup(r.text, 'html.parser')
    num_pages = 1
    page_links = page.body.find('nav', class_ = 'page-numbers')
    if page_links:
        for page_link in page_links.find_all('a'):
            if page_link.string is not None:
                try:
                    n = int(page_link.string)
                    num_pages = max(num_pages, n)
                except ValueError:
                    # There is also a 'Next' link, ignore that
                    pass

    return num_pages

def _desc(link, title, feed_name):
    return '{} [{}] ({})'.format(title, feed_name, link)

def _toc_label(link, title, feed_name):
    return '{}: {}'.format(feed_name, title)

_anandtech_full_width = '''
.main_cont {
	width: 100% !important;
	font-size: 150% !important;
}
'''

_ars_white_background = '''
.site-wrapper {
    background-color: white !important;
}
'''

def process_link(link, comments_link, title, feed_name):
    Link = collections.namedtuple('Link', 'url desc toc_label options custom_css')

    bg = False
    css = None

    if 'anandtech' in link:
        css = _anandtech_full_width
    link = re.sub('anandtech.com/show/', 'anandtech.com/print/', link)

    if 'arstechnica' in link:
        # Ars images are all divs with a background image set!
        bg = True
        css = _ars_white_background

    if re.match(r'https?://arstechnica[.]com/[?]p=[0-9]+', link):
        link = _resolve_redirect(link)

    yield Link(link,
        _desc(link, title, feed_name),
        _toc_label(link, title, feed_name),
        _options(background=bg), css)

    if re.match(r'https?://arstechnica[.]com/', link):
        # First page generated above
        n = get_num_pages_ars(link)
        for i in range(2, n+1):
            page_link = link + str(n) + '/'
            yield Link(page_link,
                _desc(page_link, title + ' (page {}/{})'.format(i, n), feed_name),
                None,
                _options(background=bg), css)

    if comments_link:
        yield Link(comments_link,
            _desc(comments_link, title, feed_name + ' (comments)'),
            _toc_label(comments_link, title, feed_name + ' (comments)'),
            _options(), None)
