#!/usr/bin/python
#
# $Id$

import os
import re
import urllib
import pprint

# Get a list of all encodings and format it accordingly. Hits docs.python.org.

fname = 'docs.python.org_library_codecs.html'
outfname = 'ttyconv/encodings.py'

if not os.path.exists(fname):

    text = urllib.urlopen('http://docs.python.org/library/codecs.html').read()
    open (fname, 'w').write(text)

page = open(fname).read()

idx = page.find ('<th class="head">Languages</th>')
page = page[idx:]
page = page[:page.find('</table>')]

codecs = []

out = open(outfname, 'w')
out.write ("""# -*- python -*-
# Coding:utf-8
#
# $Id$
#
# GENERATED AUTOMATICALLY, DO NOT EDIT.

encodings = [\n""")

for name, aliases, lang in re.findall ('<tr><td>([^>]+)</td>\n<td>([^>]+)</td>\n<td>([^>]+)</td>\n</tr>', page):
    aliases = aliases.replace ('\n', ' ')
    if aliases == '&nbsp;':
        aliases = None
    lang = lang.replace ('\n', ' ')
    out.write ("    ('%(name)s', '%(aliases)s', '%(lang)s'),\n" % locals())
out.write ("]\n\n# End of file.\n")

print "%(outfname)s written successfully." % locals()

# End of file.
