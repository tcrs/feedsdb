The `feedsdb.py` script without any arguments operates as a CGI application. The
provided `feeds.css` provides an example simple styling of the HTML output.
Feeds can be added/removed using the form at the bottom of the generated
webpage, or via the CLI.

Feeds and the items pulled from them are stored in an sqlite database, by
default `feeds.db` in the current directory.

# Dependencies

 * Requires the `feedparser` python library.
 * Assumes the user under which the CGI script runs has read/write access to the
   sqlite database file.
 * Assumes that the `feeds.css` CSS file is served from the same folder as the
   script.

# CLI

There are `add`, `del`, and `list` commands available (see `feedsdb.py --help`)
for managing feeds from the command line.
