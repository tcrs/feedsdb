import re
import sys
import requests
import requests.exceptions
import bs4

# page size in millimeters
_page_size = (156, 208)

def _mmtopx(mm):
    # 96dpi, 1 inch = 25.4mm
    return int(96 * (mm / 25.4))

_default_opts = dict(
    width = '{}mm'.format(_page_size[0]), height = '{}mm'.format(_page_size[1]),
    viewport = dict(width = _mmtopx(_page_size[0]), height = _mmtopx(_page_size[1])),
    useragent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.97 Safari/537.36')

_anandtech_css = '''
.main_cont {
    width: 100% !important;
}
div.articleContent {
    color: #000 !important;
}
'''

_ars_css = '''
.site-wrapper {
    background-color: white !important;
}
.ad.ad.ad, footer, #article-footer-wrap {
    display: none !important;
}
'''

_acoup_css = '''
.comments-area {
    display: none !important;
}
'''

_torrentfreak_css = '''
footer, aside, .page__sidebar {
    display: none !important;
}
'''

# body is set to display:flex which seems to disable text wrapping?
_cryptography_dispatches_css = '''
body {
    display: block !important;
}
'''

_pipeline_css = '''
#comments {
    display: none !important;
}
'''

def _options(url):
    opts = _default_opts.copy()

    if 'anandtech.com' in url:
        opts.update(css = _anandtech_css)
        opts.update(mediatype = 'screen')
    elif 'arstechnica.com' in url:
        # Ars images are all divs with a background image set!
        opts.update(printBackground = True)
        opts.update(css = _ars_css)
    elif 'acoup.blog' in url:
        opts.update(css = _acoup_css)
    elif 'torrentfreak.com' in url:
        opts.update(css = _torrentfreak_css)
    elif 'buttondown.email/cryptography-dispatches/' in url:
        opts.update(css = _cryptography_dispatches_css)
    elif 'blogs.sciencemag.org/pipeline/' in url:
        opts.update(css = _pipeline_css, kill_sticky = True)
    else:
        # Kill sticky headers etc by default on sites not explictly handled
        opts.update(kill_sticky = True)

    return opts

_headers = {
    'User-Agent': _default_opts['useragent']
}

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
    for i in range(4):
        r = requests.get(link, headers=_headers)
        if r.status_code not in {500, 502, 503, 504}:
            break

    page = bs4.BeautifulSoup(r.text, 'html.parser')
    num_pages = 1
    page_links = None
    # For some reason page.find('nav', class_='page-numbers') doesn't seem to
    # always work. Not sure why...
    for x in page.find_all('nav'):
        if 'page-numbers' in x.attrs.get('class', []):
            page_links = x

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

def process_link(link, comments_link, title, feed_name):
    link = re.sub('anandtech.com/show/', 'anandtech.com/print/', link)

    if re.match(r'https?://arstechnica[.]com/[?]p=[0-9]+', link):
        link = _resolve_redirect(link)

    yield dict(url = link,
        desc = _desc(link, title, feed_name),
        toc_label = _toc_label(link, title, feed_name),
        **_options(link))

    if re.match(r'https?://arstechnica[.]com/', link):
        # First page generated above
        n_pages = get_num_pages_ars(link)
        for i in range(2, n_pages+1):
            page_link = link + str(i) + '/'
            yield dict(url = page_link,
                desc = _desc(page_link, title + ' (page {}/{})'.format(i, n_pages), feed_name),
                **_options(page_link))

    # Only want lobste.rs and hacker news comments
    if comments_link and ('lobste.rs' in comments_link or 'news.ycombinator.com' in comments_link):
        yield dict(url = comments_link,
            desc = _desc(comments_link, title, feed_name + ' (comments)'),
            toc_label = _toc_label(comments_link, title, feed_name + ' (comments)'),
            **_options(comments_link))

if __name__ == '__main__':
    for url in sys.argv[1:]:
        for link in process_link(url, '', '<title>', '<feed>'):
            print(str(link))
