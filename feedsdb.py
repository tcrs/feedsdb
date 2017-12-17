#!/usr/bin/env python3

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

def do_update(conn):
    now = int(time.time())
    conn.execute('UPDATE feeds SET updated = 0')
    for name, url, etag, modified in conn.cursor().execute('SELECT name, url, etag, modified FROM feeds WHERE last_update + poll_period < ?', (now,)):
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
            conn.execute('INSERT OR REPLACE INTO items (feed, id, title, link, pub_date, pub_day) VALUES(?, ?, ?, ?, ?, ?)',
                (name, entry.id, entry.title, entry.link, timestamp, day))
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
            conn.execute('''CREATE TABLE IF NOT EXISTS items (id text, feed text, title text, link text, pub_date INT, pub_day TEXT, PRIMARY KEY (feed, id))''')
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
            ET.SubElement(item, 'img', attrib={'class': feed_name, 'src': icon})
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
    for name, url in conn.execute('SELECT name, url FROM feeds'):
        print('{}: {}'.format(name, url))

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
    add_parser.add_argument('--prune-period', type=parse_period, default='7d', help='Prune feed items older than this (seconds)')
    add_parser.add_argument('--icon-url', help='URL of favicon to show next to feed items')
    add_parser.add_argument('name')
    add_parser.add_argument('url')
    add_parser.set_defaults(func=add_feed)

    list_parser = subparsers.add_parser('list', help='List feeds')
    list_parser.set_defaults(func=list_feeds)

    del_parser = subparsers.add_parser('del', help='Delete a feed')
    del_parser.add_argument('name')
    del_parser.set_defaults(func=with_db(lambda conn, args: do_delete(conn, args.name)))

    update_parser = subparsers.add_parser('update', help='Update feeds')
    update_parser.set_defaults(func=with_db(lambda conn, args: do_update(conn)))

    args = parser.parse_args()
    args.func(args)
