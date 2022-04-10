# -*- python -*-
#
"""
$Id$

Copyright (C) 2009 Alexios Chouchoulas

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2, or (at your option)
any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software Foundation,
Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
"""

import os
import sys

try:
    import re
except ImportError:
    sys.stderr.write ('You need the regular expression (re) package to run this. Sorry.\n')
    sys.exit(1)

from setuptools import setup


###############################################################################
#
# BASICS
#
###############################################################################

# These are obtained automatically from the files in debian/
# PACKAGENAME = 'renderconsole'
# VERSION = '0.5'
# AUTHOR = 'Alexios Chouchoulas'
# AUTHOREMAIL = 'alexios@bedroomlan.org'
# URL = 'http://www.bedroomlan.org/projects/renderconsole'
# DESCRIPTION = "Convert BBS 'ANSI' files to image formats."


###############################################################################
#
# EXTRA METADATA
#
###############################################################################

LONG_DESCRIPTION = """
Translates a TTY connection from one character encoding to
another. This is useful when, for instance, you have a UTF-8 based
desktop with national language and keyboard support and you still want
to connect to systems that are unaware of this, or use a different or
unusual encoding.
""".strip().rstrip()

# Found at: http://pypi.python.org/pypi?%3Aaction=list_classifiers
CLASSIFIERS = [
    "Development Status :: 5 - Mature",
    "Environment :: Console",
    "License :: OSI Approved :: GNU General Public License (GPL)",
    "Natural Language :: English",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 2.4",
    "Programming Language :: Python :: 2.5",
    "Programming Language :: Python :: 2.6",
    "Topic :: Utilities",
    ]

###############################################################################
#
# UTILITIES
#
###############################################################################

def dataFilesIn (dirname):
    """
    Return all non-python-file filenames in dir
    """
    result = []
    allResults = []
    for name in os.listdir (dirname):
        path = os.path.join (dirname, name)
        if os.path.isfile (path) and \
           os.path.splitext (name)[1] not in ['.py','.pyc','.pyo']:
            result.append (path)
        elif os.path.isdir (path) and name.lower() not in ['cvs', '.svn', '.lib']:
            allResults.extend (dataFilesIn (path))

    if result:
        allResults.append ((dirname, result))

    return allResults


###############################################################################
#
# AUTOMAGIC STUFF
#
###############################################################################

# Obtain the version from the Debian changelog.
changelog = open('debian/changelog').read()
control = open('debian/control').read()

try:
    PACKAGENAME = re.findall ('^(\S+)', changelog).pop()
    DEB_PACKAGE = PACKAGENAME
    assert PACKAGENAME
    # Debian makes Python packages start with 'python-'
    if PACKAGENAME.startswith ('python-'):
        PACKAGENAME = PACKAGENAME[len ('python-'):]
except (IndexError, AssertionError):
    raise RuntimeError ('could not find package name information in debian/changelog! Is it valid?')

try:
    VERSION = re.findall ('^\S+\s+\([^)]+\)', changelog).pop()
    DEB_VERSION = VERSION
    assert VERSION
    # Debian may use a version in the format
    # x:major.minor[.patch]-debian_version. Extract the
    # major.minor.patch version.
    m = re.search ('\(((\d+):)?([0-9A-Za-z]+(\.[0-9A-Za-z]+(\.[0-9A-Za-z]+)?)?)(-.+)?', VERSION)
    if not m:
        raise RuntimeError ("version string '%s' seems malformed." % VERSION)
    VERSION = m.groups()[2]
except (IndexError, AssertionError):
    raise RuntimeError ('could not find version information in debian/changelog! Is it valid?')

m = re.search ('\n -- ([^<]+)\s+<([^>]+)>', changelog)
if not m:
    raise RuntimeError ('could not find author and their email in debian/changelog! Is it valid?')
AUTHOR, AUTHOREMAIL = m.groups()[:2]

try:
    DESCRIPTION = re.findall('Description:\s*(.+)\n', control)[0]
    assert DESCRIPTION
except (IndexError, AssertionError):
    raise RuntimeError ('could not find Description: field in debian/control! Is it valid?')

try:
    URL = re.findall ('Homepage:\s*(.+)\n', control).pop()
    assert URL
except (IndexError, AssertionError):
    raise RuntimeError ('could not find Homepage: field in debian/control! Is it valid?')


if 'build' in sys.argv:
    print("""
    Package:        %(PACKAGENAME)s version %(VERSION)s
    Debian package: %(DEB_PACKAGE)s %(DEB_VERSION)s
    Description:    %(DESCRIPTION)s
    Author:         %(AUTHOR)s <%(AUTHOREMAIL)s>
    Homepage:       %(URL)s
    """ % locals())

    print("stamping version.")
    filename = 'libttyconv/ttyconv.py'
    init = open (filename).read()
    init = re.sub ('@Version(:[ A-Za-z0-9._-]+)?@', '@Version: %s @' % VERSION, init)
    open (filename, 'w').write (init)
    

###############################################################################
#
# LAUNCH THE SETUP
#
###############################################################################

setup (
    name=PACKAGENAME,
    version=VERSION,
    author=AUTHOR, author_email=AUTHOREMAIL,
    url=URL,
    description=DESCRIPTION,
    long_description=LONG_DESCRIPTION,
    license='GNU GPL v2',
    platforms='any',

    classifiers=CLASSIFIERS,
    
    packages=['libttyconv'],
    entry_points={
        "console_scripts": ['ttyconv = libttyconv.ttyconv:run']
        },
    #data_files=dataFilesIn ('fonts'),
    )

# End of file.
