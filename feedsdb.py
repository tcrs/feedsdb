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
import urllib.error

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
        try:
            feed = feedparser.parse(url, etag=etag, modified=modified)
        except urllib.error.URLError as e:
            if verbose:
                print('error updating {} ({}): {}'.format(name, url, e))
            continue

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
            conn.execute('INSERT INTO items (feed, id, title, link, comments_link, pub_date, pub_day, seen) VALUES(?, ?, ?, ?, ?, ?, ?, 0) ON CONFLICT(feed, id) DO UPDATE SET title = excluded.title, link = excluded.link, comments_link = excluded.comments_link',
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
            conn.execute('''CREATE TABLE IF NOT EXISTS items (id text, feed text, title text, link text, comments_link text,
                    pub_date INT, pub_day TEXT, seen BOOLEAN DEFAULT 0, PRIMARY KEY (feed, id))''')

            # Old version didn't have comments_link, check and add it if required
            try:
                conn.execute('''SELECT comments_link FROM items LIMIT 1''')
            except sqlite3.OperationalError:
                conn.execute('''ALTER TABLE items ADD COLUMN comments_link text''')
            conn.commit()

            # Old versions didn't have seen, add if required
            try:
                conn.execute('''SELECT seen FROM items LIMIT 1''')
            except sqlite3.OperationalError:
                conn.execute('''ALTER TABLE items ADD COLUMN seen BOOLEAN''')
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
    import mimetypes

    try:
        import pikepdf
    except ModuleNotFoundError:
        print('Please install the pikepdf module')
        raise

    try:
        from playwright.async_api import async_playwright
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
        script = spec.pop('script', None)
        if script is not None:
            await page.evaluate('() => {' + script + '}')

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
            await page.emulate_media(media = mediatype)

        await page.wait_for_timeout(500)

        await page.pdf(**spec)

    async def get_pdf(browser, spec):
        context_opts = dict(
            accept_downloads = True,
            java_script_enabled = False,
            color_scheme = 'light'
        )

        if 'useragent' in spec:
            context_opts['user_agent'] = spec.pop('useragent')
        if 'viewport' in spec:
            context_opts['viewport'] = spec.pop('viewport')

        context = await browser.new_context(**context_opts)

        page = await context.new_page()

        # Default 2 minute nav timeout
        page.set_default_navigation_timeout(2 * 60 * 1000)

        url = spec.pop('url')
        print("Starting: " + url)

        for i in range(3):
            success = False

            download_task = asyncio.create_task(page.wait_for_event('download'))
            goto_task = asyncio.create_task(page.goto(url, wait_until='networkidle'))
            try:
                await goto_task
                await pdf_from_page(page, spec)
                print('Done goto: ' + url)
                await page.close()
                success = True
            except Exception as e:
                pass

            try:
                download = await download_task
                mt, _ = mimetypes.guess_type(download.suggested_filename)
                if mt != 'application/pdf':
                    await download.cancel()
                    print('Fail download: {}: does not look like a PDF: {}'.format(
                        url, download.suggested_filename))
                    # Do not retry, just going to hit this case again
                else:
                    await download.save_as(spec['path'])
                    print('Done download: ' + url)
                await page.close()
                success = True
            except Exception as e:
                # TODO: still get warnings out of the runtime saying that the
                # underlying Future exception was not retrieved. Can't work out
                # why and how to stop it happening - this is the exception here!
                if not success:
                    print('Fail download: {}: {}'.format(url, e))

            if success:
                return
            elif goto_task.done() and not goto_task.cancelled() and goto_task.exception() is not None:
                # If download is done then goto always fails with an aborted
                # error, only print the failure if download doesn't succeed
                print('Fail goto: {}: {}'.format(url, goto_task.exception()))

        await page.close()
        raise Exception('Abandoning: {}'.format(url))

    async def get_all_pdfs(specs, max_concurrent, debug_browser):
        async with async_playwright() as p:
            if debug_browser:
                browser = await p.chromium.launch(slow_mo=1000, headless=False)
            else:
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

    def mark_seen(to_mark):
        for article in to_mark:
            conn.execute('UPDATE items SET seen = 1 WHERE feed = ? AND id = ?',
                (article['feed'], article['id']))
        conn.commit()

    if args.update:
        print('Updating feeds...')
        do_update(conn, force=False, verbose=True)

    if args.url:
        article_iter = [('none', url, 'command line', 'none', None) for url in args.url]
    elif args.period:
        article_iter = conn.execute('SELECT id, link, title, items.feed, comments_link FROM items INNER JOIN feeds on items.feed = feeds.name WHERE pub_date >= ? ORDER BY feeds.priority ASC, feeds.name ASC, pub_date ASC', (int(time.time()) - args.period,))
    else:
        article_iter = conn.execute('SELECT id, link, title, items.feed, comments_link FROM items INNER JOIN feeds on items.feed = feeds.name WHERE seen != 1 OR seen IS NULL ORDER BY feeds.priority ASC, feeds.name ASC, pub_date ASC')

    orig_articles = []
    # Drop duplicate articles, I see quite a few duplicates from new aggregators
    # so this is useful. Can't see any downside?
    seen_links = set()
    for item_id, link, title, feed_name, comments_link in article_iter:
        print('Processing ' + link)
        for new_link in process_link(link, comments_link, title, feed_name):
            if new_link['url'] not in seen_links:
                new_link.update(id = item_id, feed = feed_name)
                orig_articles.append(new_link)
                seen_links.add(new_link['url'])

    if not args.non_interactive:
        with tempfile.NamedTemporaryFile(delete=False, mode='w') as f:
            filename = f.name
            f.write('# pick articles will be added to the PDF and marked as seen\n')
            f.write('# deleted articles will not be added to the PDF but will be marked as seen\n')
            f.write('# change pick to keep to not add the article to the PDF but not to mark it as seen\n')
            f.write('# lines starting with # will be ignored\n')
            for i, article in enumerate(orig_articles):
                f.write('{:04d} pick {}\n'.format(i, article['desc']))
        ret = subprocess.run([os.getenv('EDITOR', 'vi'), filename])
        cmd_map = {}
        if ret.returncode == 0:
            with open(filename, 'r') as f:
                for line in f:
                    if line.startswith('#'):
                        continue
                    s, cmd, _ = line.split(' ', 2)
                    try:
                        cmd_map[int(s)] = cmd
                    except ValueError:
                        pass
        os.unlink(filename)
        if ret.returncode != 0:
            print('Editor failed. Aborting.')
            sys.exit(ret.returncode)

        articles = [x for i, x in enumerate(orig_articles)
            if cmd_map.get(i, None) in {'pick', 'p'}]

        seen_articles = [x for i, x in enumerate(orig_articles)
            if cmd_map.get(i, None) not in {'k', 'keep'}]
    else:
        articles = orig_articles
        seen_articles = orig_articles

    if not articles:
        mark_seen(seen_articles)
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
        opts.pop('feed', None)
        opts.pop('id', None)
        all_opts.append(opts)

    asyncio.get_event_loop().run_until_complete(get_all_pdfs(all_opts, args.parallel, args.debug_browser))

    print('Merging...')
    if os.path.isfile(args.output) and not args.no_append:
        joined = pikepdf.Pdf.open(args.output, allow_overwriting_input=True)
    else:
        joined = pikepdf.Pdf.new()

    page_count = len(joined.pages)
    with joined.open_outline() as outline:
        for i, article in enumerate(articles):
            pdf = temp_path(i)
            if os.path.isfile(pdf):
                with pikepdf.Pdf.open(pdf) as inp_doc:
                    if article.get('toc_label', None):
                        outline.root.append(pikepdf.OutlineItem(article['toc_label'], page_count))
                    page_count += len(inp_doc.pages)
                    joined.pages.extend(inp_doc.pages)
            else:
                print('WARNING: PDF not found for {}'.format(article['url']))

    if page_count == 0:
        print('WARNING: final PDF empty, not creating it')
    else:
        joined.save(args.output, linearize=True)

    mark_seen(seen_articles)

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
    pdf_parser.add_argument('--url', action='append', default=[], help='For testing. Create a PDF using just the given URLs as if they were feed entries.')
    pdf_parser.add_argument('--debug-browser', action='store_true', help='For testing. Run playwright-controlled browser in non-headless mode with slow_mo enabled. Note that PDFs can not be generated in this mode')
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
