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

# PDF generation

The CLI command `pdf` creates a PDF of (a subset of) the articles from a given
amount of time before now. It uses
[playwright](https://github.com/microsoft/playwright-python) and
[pymupdf](https://github.com/pymupdf/PyMuPDF). Unless you specify
`--non-interactive` an editor (change by setting the `EDITOR` env var) will pop
up and you can delete any articles you don't want in the PDF.

To get `python-playwright` you can do:

   pip install playwright # install playwright-python
   python -m playwright install # download browsers

Each article is downloaded using playwright to a temporary file and then they
are all stitched together using `pymupdf`. A ToC/Outline is added to the final
PDF with an entry for each article to make it easy to jump to articles.

By default (disable with `--no-append`) if the output PDF file already exists
the selected articles will be appended to it rather than overwriting the whole
thing.

The PDF generation calls into `link_processor.py` to get one or more dict of
settings for each article. I've included my link processor as an example to work
from, you can do pretty much anything here. `feedsdb.py` expects the dict to
contain 'url' and 'desc' keys (desc is what's shown in the editor interface),
and 'toc\_label' if you want the article to appear in the PDF ToC.
