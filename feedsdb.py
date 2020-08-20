#!/usr/bin/env python3

import os
import json
import tempfile
import subprocess
import shutil
import sys
import time
import calendar
import sqlite3
import argparse
import functools
import feedparser
import datetime
import xml.etree.ElementTree as ET

import cgi
import cgitb
cgitb.enable()

def do_delete(conn, name):
    conn.execute('DELETE FROM feeds WHERE name = ?', (name,))
    conn.execute('DELETE FROM items WHERE feed = ?', (name,))

def do_update(conn, force=False, verbose=False):
    # Some feeds don't have IDs on the entries, so just fall back to using the
    # link :s
    def entry_id(e):
        return getattr(e, 'id', e.link)

    now = int(time.time())
    conn.execute('UPDATE feeds SET updated = 0')
    cursor = conn.cursor()
    if force:
        cursor.execute('SELECT name, url, etag, modified FROM feeds')
    else:
        cursor.execute('SELECT name, url, etag, modified FROM feeds WHERE last_update + poll_period < ?', (now,))
    for name, url, etag, modified in cursor:
        if verbose:
            print('{} ({})'.format(name, url))
        feed = feedparser.parse(url, etag=etag, modified=modified)
        conn.execute('UPDATE feeds SET last_update = ? WHERE name = ?', (now, name))
        if not feed.feed:
            # OK, just nothing new (via etag or modified time)
            continue

        conn.execute('UPDATE feeds SET etag = ?, modified = ?, updated = 1 WHERE name = ?',
            (getattr(feed, 'etag', None), getattr(feed, 'modified', None), name))
        for entry in feed.entries:
            dt = getattr(entry, 'published_parsed', getattr(entry, 'updated_parsed'))
            day = time.strftime('%Y-%m-%d', dt)
            timestamp = calendar.timegm(dt)
            conn.execute('INSERT OR REPLACE INTO items (feed, id, title, link, comments_link, pub_date, pub_day) VALUES(?, ?, ?, ?, ?, ?, ?)',
                (name, entry_id(entry), entry.title, entry.link, getattr(entry, 'comments', ''), timestamp, day))
        conn.commit()
    conn.execute('''DELETE FROM items WHERE rowid IN (
        SELECT items.rowid FROM items INNER JOIN feeds ON items.feed = feeds.name
        WHERE items.pub_date + feeds.prune_period < ?)''', (now,))
    conn.commit()

def with_db(fn):
    @functools.wraps(fn)
    def wrapper(cmd_args, *args, **kwargs):
        with sqlite3.connect(cmd_args.db_path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS feeds (
                    name text PRIMARY KEY, url text, priority INT,
                    last_update INT, poll_period INT, prune_period INT, updated INT,
                    icon TEXT,
                    etag TEXT, modified TEXT)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS items (id text, feed text, title text, link text, comments_link text, pub_date INT, pub_day TEXT, PRIMARY KEY (feed, id))''')

            conn.execute('''CREATE TABLE IF NOT EXISTS meta (key text PRIMARY KEY, value text)''')

            # Old version didn't have comments_link, check and add it if required
            try:
                conn.execute('''SELECT comments_link FROM items LIMIT 1''')
            except sqlite3.OperationalError:
                conn.execute('''ALTER TABLE items ADD COLUMN comments_link text''')
            conn.commit()

            fn(conn, cmd_args, *args, **kwargs)
            conn.commit()
    return wrapper

@with_db
def serve_cgi(conn, args):
    form = cgi.FieldStorage()

    # Handle form submits (delete/add feed)
    if form.getfirst('delete'):
        do_delete(conn, form.getfirst('delete'))
        conn.commit()

    if form.getfirst('url') and form.getfirst('name'):
        conn.execute('INSERT INTO feeds VALUES(?, ?, ?, 0, ?, ?, 0, ?, NULL, NULL)',
            (form.getfirst('name'), form.getfirst('url'), form.getfirst('priority', 0),
                form.getfirst('poll_period', 60*60), form.getfirst('prune_period', 7*24*60*60),
                form.getfirst('icon_url')))
        conn.commit()

    do_update(conn)

    print('Content-Type: text/html')
    print()
    print('<!DOCTYPE html>')

    sys.stdout.flush()
    sys.stdout.buffer.flush()

    root = ET.Element('html')
    head = ET.SubElement(root, 'head')
    ET.SubElement(head, 'meta', attrib={'http-equiv': 'Content-Type', 'content': 'text/html; charset=utf8'})
    ET.SubElement(head, 'title').text = 'Feeds'
    ET.SubElement(head, 'link', rel='stylesheet', href='feeds.css', type='text/css')
    body = ET.SubElement(root, 'body')

    # Show list of updated feeds favicons
    updates = ET.SubElement(body, 'div', attrib={'class': 'updates'})
    for name, updated, icon in conn.execute('SELECT name, updated, icon FROM feeds'):
        if icon and updated:
            ET.SubElement(updates, 'img', attrib={'class': name, 'src': icon})

    # All feed items, grouped by day & sorted by priority then date/time
    for day_date, in conn.cursor().execute('SELECT DISTINCT pub_day FROM items ORDER BY pub_day DESC'):
        day = ET.SubElement(body, 'div', attrib={'class': 'day'})
        ET.SubElement(day, 'div', attrib={'class': 'day-date'}).text = day_date
        items = ET.SubElement(day, 'ul')
        for link, title, feed_name, icon in conn.cursor().execute('SELECT link, title, items.feed, feeds.icon FROM items INNER JOIN feeds on items.feed = feeds.name WHERE pub_day = ? ORDER BY priority, pub_date', (day_date,)):
            item = ET.SubElement(items, 'li')
            ET.SubElement(item, 'img', attrib={'class': feed_name, 'src': icon or ''})
            ET.SubElement(item, 'a', href=link).text = title

    # Simple form to add a feed
    add_form = ET.SubElement(body, 'form', method='post')
    d = ET.SubElement(add_form, 'span')
    ET.SubElement(d, 'label', attrib={'for': 'name'}).text = 'name: '
    ET.SubElement(d, 'input', attrib={'type': 'text', 'id': 'name', 'name': 'name'})
    d = ET.SubElement(add_form, 'span')
    ET.SubElement(d, 'label', attrib={'for': 'url'}).text = 'url: '
    ET.SubElement(d, 'input', attrib={'type': 'text', 'id': 'url', 'name': 'url'})
    d = ET.SubElement(add_form, 'span')
    ET.SubElement(d, 'label', attrib={'for': 'icon_url'}).text = 'Icon URL'
    ET.SubElement(d, 'input', attrib={'type': 'text', 'id': 'icon_url', 'name': 'icon_url'})
    d = ET.SubElement(add_form, 'span')
    ET.SubElement(d, 'label', attrib={'for': 'priority'}).text = 'Priority'
    ET.SubElement(d, 'input', attrib={'type': 'number', 'id': 'priority', 'name': 'priority'})
    d = ET.SubElement(add_form, 'span')
    ET.SubElement(d, 'button', attrib={'type': 'submit'}).text = 'Add feed'

    # List each feed with a delete button
    d = ET.SubElement(body, 'div', attrib={'class': 'deletes'})
    for name, url in conn.execute('SELECT name, url FROM feeds'):
        t = ET.SubElement(d, 'form', method='post')
        ET.SubElement(t, 'input', type='hidden', id='delete', name='delete', value=name)
        ET.SubElement(t, 'span', attrib={'class': 'name'}).text = name
        ET.SubElement(t, 'span', attrib={'class': 'url'}).text = url
        ET.SubElement(t, 'button', type='submit').text = 'Delete'

    sys.stdout.buffer.write(ET.tostring(root, encoding='utf-8'))

@with_db
def add_feed(conn, args):
    conn.execute('INSERT INTO feeds VALUES(?, ?, ?, 0, ?, ?, 0, ?, NULL, NULL)', (args.name, args.url, args.priority, args.poll_period, args.prune_period, args.icon_url))

@with_db
def list_feeds(conn, args):
    for name, url, prio in conn.execute('SELECT name, url, priority FROM feeds'):
        print('{}: {} ({})'.format(name, url, prio))

@with_db
def make_pdf(conn, args):
    import asyncio

    try:
        import fitz
    except ModuleNotFoundError:
        print('Please install the pymupdf (fitz) module')
        raise

    try:
        from playwright import async_playwright
    except ModuleNotFoundError:
        print('Please install python-playwright')
        raise

    try:
        import link_processor
        process_link = link_processor.process_link
    except ModuleNotFoundError:
        print('**** No link_processor, using default')
        def process_link(link, comments_link, title, feed_name):
            yield dict(url = link,
                desc = '{}: {}'.format(feed_name, title),
                toc_label = title)

            if comments_link:
                yield dict(url = comments_link,
                    desc = '{} (comments): {}'.format(feed_name, title),
                    toc_label = title)

    async def pdf_from_page(page, spec):
        css = spec.pop('css', None)
        if css is not None:
            # page.addStyleTag will hang if the page has javascript disabled
            # It works by injecting a script similar to that below, but with a
            # promise that resolves on the style.onload callback - this never gets
            # called if javascript is disabled.
            # Here I'm assuming that the act of printing the PDF will make chromium
            # do a full relayout and so the injected style will take effect :s
            await page.evaluate('''content => {
                const style = document.createElement('style');
                style.type = 'text/css';
                style.appendChild(document.createTextNode(content));
                document.head.appendChild(style);
            }''', css)

        if spec.pop('kill_sticky', False):
            # Kill Sticky headers code from here:
            # https://alisdair.mcdiarmid.org/kill-sticky-headers/
            await page.evaluate('''() => {
                var i, elements = document.querySelectorAll('body *');

                for (i = 0; i < elements.length; i++) {
                    if (getComputedStyle(elements[i]).position === 'fixed') {
                        elements[i].parentNode.removeChild(elements[i]);
                    }
                }
            }''')

        mediatype = spec.pop('mediatype', None)
        if mediatype is not None:
            await page.emulateMedia(media = mediatype)

        await page.pdf(**spec)

    async def get_pdf(browser, spec):
        context_opts = dict(
            acceptDownloads = True,
            javaScriptEnabled = False,
            colorScheme = 'light'
        )

        if 'useragent' in spec:
            context_opts['userAgent'] = spec.pop('useragent')
        if 'viewport' in spec:
            context_opts['viewport'] = spec.pop('viewport')

        context = await browser.newContext(**context_opts)

        page = await context.newPage()

        # Default 2 minute nav timeout
        page.setDefaultNavigationTimeout(2 * 60 * 1000)

        url = spec.pop('url')
        print("Starting: " + url)

        for i in range(3):
            try:
                await page.goto(url, waitUntil='networkidle')
                break
            except Exception as e:
                print('Fail: {}: {}'.format(url, e))

        await pdf_from_page(page, spec)
        print('Done: ' + url)

    async def get_all_pdfs(specs, max_concurrent):
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            running = set()
            for spec in specs:
                if len(running) >= max_concurrent:
                    done, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                    for t in done:
                        if t.exception():
                            print('Error: {}'.format(t.exception()))
                running.add(asyncio.create_task(get_pdf(browser, spec)))

            await asyncio.wait(running)
            await browser.close()

    if args.update:
        print('Updating feeds...')
        do_update(conn, force=False, verbose=True)

    start_time = int(time.time())
    if args.period is not None:
        first_time = start_time - args.period
    else:
        first_time = conn.execute('SELECT value FROM meta WHERE key = "last_pdf_time"').fetchone()
        if first_time is None:
            print('No PDF generated before - defaulting to last day of articles')
            first_time = start_time - parse_period('1d')
        else:
            first_time = int(first_time[0])

    articles = []

    # Drop duplicate articles, I see quite a few duplicates from new aggregators
    # so this is useful. Can't see any downside?
    seen_links = set()
    for link, title, feed_name, comments_link in conn.execute('SELECT link, title, items.feed, comments_link FROM items INNER JOIN feeds on items.feed = feeds.name WHERE pub_date >= ? ORDER BY feeds.priority ASC, feeds.name ASC, pub_date ASC', (first_time,)):
        print('Processing ' + link)
        for new_link in process_link(link, comments_link, title, feed_name):
            if new_link['url'] not in seen_links:
                articles.append(new_link)
                seen_links.add(new_link['url'])

    if not args.non_interactive:
        with tempfile.NamedTemporaryFile(delete=False, mode='w') as f:
            filename = f.name
            for i, article in enumerate(articles):
                f.write('{:04d} {}\n'.format(i, article['desc']))
        ret = subprocess.run([os.getenv('EDITOR', 'vi'), filename])
        keep_set = set()
        if ret.returncode == 0:
            with open(filename, 'r') as f:
                for line in f:
                    s = line.split(' ', 1)
                    try:
                        keep_set.add(int(s[0]))
                    except ValueError:
                        pass
        os.unlink(filename)

        articles = [x for i, x in enumerate(articles)
            if i in keep_set]

    if not articles:
        print('No articles to download')
        sys.exit(2)

    temp_dir = tempfile.mkdtemp()
    print('Using temp folder: ' + temp_dir)
    def temp_path(n, ext='pdf'):
        return os.path.join(temp_dir, '{:04d}.{}'.format(n, ext))

    all_opts = []
    for i, article in enumerate(articles):
        opts = article.copy()
        opts.update(path = temp_path(i))
        opts.pop('desc', None)
        opts.pop('toc_label', None)
        all_opts.append(opts)

    asyncio.get_event_loop().run_until_complete(get_all_pdfs(all_opts, args.parallel))

    if args.period is None:
        conn.execute('INSERT OR REPLACE INTO meta (key, value) VALUES("last_pdf_time", ?)', (start_time,))
        conn.commit()

    print('Merging...')
    joined = fitz.open()
    joined_toc = joined.getToC()
    if os.path.isfile(args.output) and not args.no_append:
        orig = fitz.open(args.output)
        joined.insertPDF(orig)
        joined_toc.extend(orig.getToC())
        orig.close()

    for i, article in enumerate(articles):
        pdf = temp_path(i)
        if os.path.isfile(pdf):
            inp_doc = fitz.open(pdf)
            if article.get('toc_label', None):
                joined_toc.append((1, article['toc_label'], joined.pageCount + 1))
            joined.insertPDF(inp_doc, annots=False)
            inp_doc.close()
        else:
            print('WARNING: PDF not found for {}'.format(article['url']))
    joined.setToC(joined_toc)
    joined.save(args.output, garbage=4, clean=True, deflate=True, incremental=False)

    if not args.keep:
        print('Deleting temp folder')
        shutil.rmtree(temp_dir)

def parse_period(s):
    num = int(s[:-1])
    span = {'s': 'seconds', 'm': 'minutes', 'h': 'hours', 'd': 'days'}[s[-1]]
    return datetime.timedelta(**{span: num}).total_seconds()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db-path', default='feeds.db')
    parser.set_defaults(func=serve_cgi)

    subparsers = parser.add_subparsers()
    add_parser = subparsers.add_parser('add', help='Add a feed')
    add_parser.add_argument('--priority', type=int, default=0, help='Priority of the items from this feed')
    add_parser.add_argument('--poll-period', type=parse_period, default='1h', help='Maximum poll frequency (seconds)')
    add_parser.add_argument('--prune-period', type=parse_period, default='30d', help='Prune feed items older than this')
    add_parser.add_argument('--icon-url', help='URL of favicon to show next to feed items')
    add_parser.add_argument('name')
    add_parser.add_argument('url')
    add_parser.set_defaults(func=add_feed)

    list_parser = subparsers.add_parser('list', help='List feeds')
    list_parser.set_defaults(func=list_feeds)

    pdf_parser = subparsers.add_parser('pdf', help='Make a pdf file of articles')
    pdf_parser.add_argument('--no-append', action='store_true', help='By default if the output PDF exists new articles will be appended to the end. This forces a new document to be created and overwrite the exiting one')
    pdf_parser.add_argument('--no-comments', action='store_true', help='Do not include comment links')
    pdf_parser.add_argument('--period', '-p', type=parse_period, help='How long in the past to start listing articles from (default since last pdf generation)')
    pdf_parser.add_argument('--keep', action='store_true', help='Do not delete temp folder')
    pdf_parser.add_argument('--update', action='store_true', help='Update feeds before generating PDF')
    pdf_parser.add_argument('-n', '--non-interactive', action='store_true', help='Do not launch an editor to interactively select which articles to download')
    pdf_parser.add_argument('-j', '--parallel', type=int, default=5, help='Maximum number of pages to load in parallel')
    pdf_parser.add_argument('output', help='Output PDF file')
    pdf_parser.set_defaults(func=make_pdf)

    del_parser = subparsers.add_parser('del', help='Delete a feed')
    del_parser.add_argument('name')
    del_parser.set_defaults(func=with_db(lambda conn, args: do_delete(conn, args.name)))

    update_parser = subparsers.add_parser('update', help='Update feeds')
    update_parser.add_argument('--force', action='store_true', help='Ignore poll period and update all feeds')
    update_parser.set_defaults(func=with_db(lambda conn, args: do_update(conn, args.force, verbose=True)))

    args = parser.parse_args()
    args.func(args)
