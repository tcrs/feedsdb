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
[wkhtmltopdf](https://github.com/wkhtmltopdf/wkhtmltopdf) and
[pymupdf](https://github.com/pymupdf/PyMuPDF). Unless you specify
`--non-interactive` an editor (change by setting the `EDITOR` env var) will pop
up and you can delete any articles you don't want in the PDF.

Each article is downloaded using `wkhtmltopdf` to a temporary file and then they
are all stitched together using `pymupdf`. A ToC/Outline is added to the final
PDF with an entry for each article to make it easy to jump to articles.

Caveats:

 - The arguments to `wkhtmltopdf` are largely hardcoded at the moment to match
   what I want for reading the result on my reMarkable ereader.
 - User Agent is hardcoded to what the version of Firefox I happened to be using
   reported.
 - It's pretty slow. Could launch multiple `wkhtmltopdf` invocations in
   parallel?
 - The version of `wkhtmltopdf` I am using (default on Arch linux) isn't built
   with a patched version of Qt and seems to handle custom fonts by storing all
   the text as vector images, which produces very large PDFs. On the other hand
   using the static `wkhtmltopdf` build provided by the project I get segfaults
   on some of the sites I like to read, but the fonts generally seem to be
   handled properly. For now I'm just managing with very large PDFs.
