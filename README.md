dh – dirhash
============

https://github.com/felf/dh
Copyright © 2013–2021 Frank Steinmetzger

Synopsis
--------
Dh creates, verifies and updates file hashes recursively and per directory or
single file. It is written in Python 3.

Configuration and installation
------------------------------
Nothing fancy to do, dh is a simple one-file program. You can copy it whever
you want, such as ~/bin, /usr/local/bin etc.

How to use dh
-------------
dh has a built-in help:

    $ dh.py -h

Dh has three modes:
* check mode, this is the default mode
* write mode
* update mode

It will always work recursively.

In check mode (the default), it looks for a file called Checksums.md5 by
default. It will verify the hash of every file listed in this file, and it will
check whether there exist any files that are not listed in the checksum file.

Using the --paths option, it skips the hashing and only checks the file names.

In write mode, dh will hash all files in a directory and write the hashes to
the checksum file. It ignores the content of pre-existing checksum files, but
warns when it is about to overwrite one.

In update mode, dh first does a quick paths-mode check, but it will also fix
any errors it finds:
* if a file is listed in the checksum file, but does not exist, the entry is
  removed from the checksum file
* if it is listed, but its modification time is newer, then it is rehashed
* if a file is not listed in the checksum file, its hash gets added

Dh has a range of options to alter its behaviour:
* the expected file name of checksum files can be modified (the default is
  Checksums.md5)
* it is possible to write one checksum file per input file
* the number of directories to be processed when dealing with large file
  hierarchies
* output verbosity
* and more

How came dh into being?
-----------------------
I like to hash my media files for long-time storage or when I suspect that a
storage medium has seen better days. Often in such cases I find myself in the
need for hashing recursively, for example when dealing with a collection of
music albums. But unlike md5deep, which uses a single md5 file for everything, I
want a separate checksum file for every directory. Thus dh was born.

Over time, I might re-tag some of my files, which makes the existing checksums
obsolete. Thus, dh’s second task is to clean up checksum files from cruft and
add yet missing files to it. At the same time it does not re-hash every single
file, which saves a lot of time if one is dealing with gigabytes of files.

Dh also sorts the checksum files’ entries by file name.

Over time, new use cases emerged. One of those is a single directory of big,
independent files (such as ISOs or movies). I wanted to hash those, too, but
keep their checksum files separate so it is easier to copy a single file out of
the directory without having to edit checksum files. Thus dh gained a mode to
write one checksum file per input file.

Hacking
-------
Feel free to add new stuff or clean up the code mess that I created. :o) Dh is
written following the standard Python formatting (pep8). Although flake8
and pylint do contradict each other in certain areas, such as hanging indents.

Reporting bugs
--------------
You can use github’s facilities, drop me a mail or submit a pull request with
your own fix. ;-)

TODOs
-----
Here are some notable ToDos:
* Currently, dh only supports md5. (The original name of the tool was
  album-md5). This (c|sh)hould be extended to other hash algorithms.
* manpage
* installation procedure (setup.py)
* tests ;-)
* handle terminal resizes
* -x option to stop at file system boundaries
* The class that handles terminal output seems a mess to me. It is the
  product of a refactoring that separated hashing and printing, but became
  rather convoluted in the process with lots of state variables.
* Localisation
