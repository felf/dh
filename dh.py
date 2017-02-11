#!/usr/bin/env python3

"""
dh.py (dirhash) - create and verify checksums for directories recursively.
"""

import argparse
import hashlib
import math
import os
import sys
import time

__prog_name__ = 'dh.py'
__prog_version__ = "1.4.3"

CWD = os.getcwd()


class Output(object):  # {{{1
    """ Handle printing to the screen and the state of the output. """

    # variables {{{2
    # whether the last thing printed was a progress dot
    progress_last = False
    # whether anything was printed yet
    output_shown = False
    # whether each progress message shall appear on an own line
    progress_with_newline = False
    # the text last shown as progress message
    last_progress_text = ""

    @staticmethod
    def colorstring(color):  # {{{2
        """ Return the terminal escape sequence for the given colour. """

        colors = {
            'black':  '30',
            'red':    '31',
            'green':  '32',
            'yellow': '33',
            'blue':   '34',
            'purple': '35',
            'cyan':   '36',
            'white':  '37'
        }
        return "\033[{};{}m".format(
            "0" if color[0].islower() else "1",
            colors.get(color.lower(), "0"))

    @staticmethod
    def ask(msg):  # {{{2
        """ Show the user a question, get input and return it. """

        Output.clear_line()
        return input(msg)

    @staticmethod
    def clear_line():  # {{{2
        """ Print a newline if the last thing printed was a progress msg. """

        if Output.progress_last:
            print("")
            Output.progress_last = False

    @staticmethod
    def clear_progress_text():  # {{{2
        """ Reset the last progress text to empty. """

        Output.last_progress_text = ""

    @staticmethod
    def erase_progress_text():  # {{{2
        """ Overwrite last progress message with spaces. """

        print(" " * len(Output.last_progress_text), end="\r")

    @staticmethod
    def print_separator(width):  # {{{2
        """ Print the stats table headline (only if there is output above). """

        if Output.progress_with_newline:
            if Output.output_shown:
                Output.clear_line()
        else:
            Output.erase_progress_text()
        if Output.output_shown:
            print("-" * width)

    @staticmethod
    def print(*what, file=sys.stdout):  # {{{2
        """ Output the given message. """

        for item in what:
            if isinstance(item, str):
                print(item, end="", file=file)
            else:
                if ARGS.no_color:
                    print(item[1], end="", file=file)
                else:
                    print(Output.colorstring(item[0]) + item[1] + "\033[0;0m",
                          end="", file=file)
        print(file=file)
        file.flush()
        Output.output_shown = True

    @staticmethod
    def print_line(*what, file=sys.stdout):  # {{{2
        """ Output the given message onto a new line. """

        if Output.progress_with_newline:
            Output.clear_line()
        else:
            Output.erase_progress_text()
        Output.print(*what, file=file)
        Output.progress_last = False
        if Output.last_progress_text:
            Output.reprint_progress()

    @staticmethod
    def progress(what, number, total, msg=""):  # {{{2
        """ Print a progress indicator with two numbers and a possible msg. """

        if Output.progress_with_newline and Output.progress_last:
            print()
        else:
            Output.erase_progress_text()
        Output.last_progress_text = "({} {} of {}) {}".format(
            what, number, total, msg)
        terminal_size = os.get_terminal_size()
        print("{text:{length}}\r".format(
            text=Output.last_progress_text,
            length=terminal_size.columns - 1), end="")
        Output.progress_last = True
        Output.output_shown = True
        sys.stdout.flush()

    @staticmethod
    def reprint_progress():  # {{{2
        """ Normal progress output was interrupted by a warning or error
        message. This restores the last progress message. """

        if Output.last_progress_text:
            terminal_size = os.get_terminal_size()
            print("{text:{length}}\r".format(
                text=Output.last_progress_text,
                length=terminal_size.columns - 1), end="")

    @staticmethod
    def error(*arguments, msg=""):  # {{{2
        """ Convenience function: output a given message in error colour. """

        Output.clear_line()
        Output.clear_progress_text()
        if msg != "":
            Output.print_line(("Red", msg), *arguments, file=sys.stderr)
        else:
            Output.print_line(("Red", "".join(arguments)), file=sys.stderr)
        Output.output_shown = True

    @staticmethod
    def warn(*arguments, msg=""):  # {{{2
        """ Convenience function: output a given message in warning colour. """

        Output.clear_line()
        Output.clear_progress_text()
        if msg != "":
            Output.print_line(("Yellow", msg), *arguments, file=sys.stderr)
        else:
            Output.print_line(("Yellow", "".join(arguments)), file=sys.stderr)
        Output.output_shown = True

OUT = Output.print_line
ERR = Output.error
WARN = Output.warn


class ChecksumFiles(object):  # {{{1
    """ Encapsulate access to checksum files while processing a directory. """

    def __init__(self, path, checksum_files):  # {{{2
        # the directory
        self._path = path
        # a dict of all the directory's checksum files to read or write
        # key: full path to file, value: the file's current mtime
        self._csfiles = {
            os.path.join(path, cf): os.path.getmtime(os.path.join(path, cf))
            for cf in checksum_files
        }
        # whether each file has its own checksum file
        self._separate = ARGS.filename == 'all'
        # a dict of all checksums in the current checksum file (filename: hash)
        # key: filename, value: tuple(hash, checksum file)
        self._entries = {}
        # items from _entries that shall be deleted at the end of update run
        # key: checksum file path, value: list of filenames to remove
        self._removed_entries = {}
        # the handle to the currently active checksum file
        self._file = None
        # checksum files that were modified during an update run
        self._updated_csfiles = set()
        # whether the checksum file was modified (needed after an interruption)
        self._modified = False

        # read entries in existing checksum files (but not if creating from
        # scratch, then we don't care what's already there)
        if not ARGS.create:
            for cspath in self._csfiles:
                try:
                    for line in open(cspath):
                        if not line.strip():
                            continue
                        filename, md5 = line[34:-1], line[:32]
                        self._entries[filename] = (md5, cspath)
                except OSError as error:
                    ERR("'" + cspath + "'",
                        msg="Could not read checksum file ({}): ".format(
                            error.args[1]))

    def __enter__(self):  # {{{2
        """ Make the class usable with the "with" statement - entry point. """

        return self

    def __exit__(self, exc_type, exc_value, traceback):  # {{{2
        """ Cleanup at with-block's end - write and close checksum files. """

        if self._file:
            self._file.close()
        # Rewrite updated checksum files b/c they may not be sorted now.
        # But if there are no previous csfiles, there's nothing to sort.
        if self._csfiles:
            for cspath in self._updated_csfiles:
                if ARGS.filename == "all":
                    # first get all entries of the required checksum file
                    filenames = [
                        entry for entry in self._entries
                        if self._entries[entry][1] == cspath]
                else:
                    filenames = list(self._entries.keys())
                if filenames:
                    filenames.sort()
                    try:
                        with open(cspath, "w") as csfile:
                            for entry in filenames:
                                print("{} *{}".format(
                                    self._entries[entry][0], entry),
                                    file=csfile)
                    except KeyboardInterrupt:
                        ERR("\nWARNING! Interrupted while rewriting '{}'\n"
                            "Data loss is possible.".format(cspath))
                        raise
                    except OSError as error:
                        ERR(cspath,
                            msg="Could not write to checksum file ({}): ".
                            format(error.args[1]))
                else:
                    os.unlink(cspath)

    def _get_checksum_file_handle(self):  # {{{2
        """ Encapsulate write access to checksum file. """

        try:
            if ARGS.filename != "all" and self._file is None:
                path = os.path.join(self._path, ARGS.filename)
                self._file = open(
                    path, "a" if ARGS.update else "w")
        except OSError as error:
            ERR("'" + path + "'",
                msg="Could not open checksum file for writing ({}): ".format(
                    error.args[1]))
        return self._file

    def delete_checksum_files(self):  # {{{2
        """ Delete the directory's checksum files. """

        for csfile in self._csfiles:
            os.remove(csfile)

    def entries(self):  # {{{2
        """ Getter to retrieve all listed checksums. """

        return self._entries

    def file_is_not_newer(self, filename):  # {{{2
        """ Compare given file's mtime with mtime of its checksum file.
        Return true if file is not newer than checksum file. """

        return os.path.getmtime(filename) < self._csfiles[
            self._entries[os.path.basename(filename)][1]]

    def is_modified(self):  # {{{2
        """ Getter. """

        return self._modified

    def remove_entry(self, filename):  # {{{2
        """ If update mode detects a dead checksum entry, delete it here. """

        # were there already some entries removed from that file?
        csfile = self._entries[filename][1]
        to_remove = self._removed_entries.get(csfile)
        if to_remove is None:
            # if not, create new list for it
            to_remove = self._removed_entries[csfile] = []
        to_remove.append(filename)

        self._entries.pop(filename)
        self._updated_csfiles.add(csfile)

    def verify_hash(self, filename, checksum):  # {{{2
        """ Look for the given hash/file in existing checksum files. """

        old_sum = self._entries.get(filename)[0]
        if old_sum is None:
            return None
        return old_sum == checksum

    def write_hash(self, filename, checksum):  # {{{2
        """ Add a new hash to the checksum file. """

        try:
            if ARGS.filename == 'all':
                # path is guaranteed to end with "/" (2. stmt in gather_files)
                csfpath = self._path + filename + ".md5"
                # TODO: ask for overwriting here
                with open(csfpath, "a" if ARGS.update else "w") as csfile:
                    print("{} *{}".format(checksum, filename), file=csfile)
                if ARGS.update:
                    # record new checksum item for use in self.__del__
                    self._entries[filename] = (checksum, csfpath)
            else:
                print(
                    "{} *{}".format(checksum, filename),
                    file=self._get_checksum_file_handle())
                csfpath = self._path + ARGS.filename
                if ARGS.update and self._csfiles:
                    self._entries[filename] = (checksum, csfpath)
                    self._updated_csfiles.add(csfpath)
                self._modified = True
        except OSError as error:
            ERR(csfpath, msg="Could not write to checksum file ({}): ".format(
                error.args[1]))

    def check(self, filename, checksum):  # {{{2
        """ Check the given data against existing checksums.

        Return value: None if file is not listed, True/False depending on
        whether checksum is correct. """

        entry = self._entries.get(filename)
        return None if entry is None else entry[0] == checksum


def parse_arguments():  # {{{1
    """ Parse commandline arguments and return the argparse object. """

    parser = argparse.ArgumentParser(
        description='Recursively create and verify md5 checksums in '
                    'directories',
        epilog='By default, only directories without any subdirectories '
               '("leaves") will be processed. This can be overridden with the '
               '-f option. All files in a directory (except the checksum '
               'filename) will be hashed and either stored in the MD5 file or '
               'checked against it, depending on operation mode. If no '
               'operation mode is given, checking mode will be selected.',
        prog=__prog_name__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '-c', '--create', action='store_true',
        help='hash everything and create checksum files')
    group.add_argument(
        '-p', '--paths', action='store_true',
        help='only check paths, don\'t compare checksums (fast)')
    group.add_argument(
        '-u', '--update', action='store_true',
        help='only hash yet unhashed files and remove dead hash entries')
    group.add_argument(
        '-V', '--version', action='store_true',
        help='print version information and exit')

    group = parser.add_argument_group(title="file system options")
    group.add_argument(
        '-a', '--all', action='store_true',
        help='include hidden files and directories')
    group.add_argument(
        '-f', '--force', action='store_true',
        help='force processing of files in dirs with subdirs')
    group.add_argument(
        '-l', '--follow-links', action='store_true',
        help='follow symlinks instead of ignoring them')

    group = parser.add_argument_group(title="checksum file options")
    group.add_argument(
        '-F', '--filename', action='store', default='Checksums.md5',
        metavar='name',
        help='name of checksum files (default: Checksums.md5, use \'all\' to '
             'use any *.md5 file in checking mode and to create one checksum '
             'file for every input file)')
    group.add_argument(
        '-O', '--overwrite', action='store_true',
        help='override checksum files without asking')
    group.add_argument(
        '--no-missing-checksums', action='store_true',
        help='Don\'t warn on directories without checksum files')

    group = parser.add_argument_group(title="directory selection options")
    group.add_argument(
        '-n', '--number', action='store', default=-1, type=int, metavar='n',
        help='only process this many dirs (after the skipped ones)')
    group.add_argument(
        '-s', '--skip', action='store', default=0, type=int, metavar='n',
        help='skip given number of dirs (to resume an aborted run)')

    group = parser.add_argument_group(title="output options")
    group.add_argument(
        '-q', '--quiet', action='count', default=0,
        help='less progress output (-q: only print directory number, -qq: '
             'print no progress at all, -qqq: suppress warnings about missing '
             'checksum files)')
    group.add_argument(
        '-v', '--verbose', action='store_true',
        help='print the file that is being checked')
    group.add_argument(
        '--no-color', action='store_true', help='don\'t use colours in output')
    parser.add_argument(
        'locations', metavar='dir', type=str, nargs='*',
        help='a list of directories to parse (default is .)')

    parsed_args = parser.parse_args()

    # clean-up of input
    if parsed_args.version:
        print("{} version {}".format(__prog_name__, __prog_version__))
        exit(0)
    if parsed_args.quiet > 0 and parsed_args.verbose:
        print("error: quiet and verbose options cannot be mixed.")
        sys.exit(1)
    if parsed_args.quiet > 3:
        parsed_args.quiet = 3
    if parsed_args.filename != "all":
        parsed_args.filename = os.path.basename(parsed_args.filename)

    # use current dir if no dir to process was given
    if not parsed_args.locations:
        parsed_args.locations.append(CWD)

    Output.progress_with_newline = parsed_args.verbose
    return parsed_args

ARGS = parse_arguments()


class State(object):  # {{{1
    """ Encapsulate al necessary state variables for the recursion. """

    # number of matching directories (i.e. those that contain files to process)
    dircount = 0
    # number of matching directories to skip at the beginning
    skip = 0
    # number of matching directories after which to exit
    limit = 0
    # number of directories skipped due to answer to overwrite question
    skipped_overwrites = 0
    # number of files processed
    hashed_files = 0
    # number of failed md5 checks
    fails = 0
    # number of passed md5 checks
    passes = 0
    # number of files listed in md5, but physically missing
    files_missing = 0
    # number of files that had an entry in an existing md5 file
    found_in_md5 = 0
    # number of files not listed in existing md5 file
    not_in_md5 = 0
    # number of matching directories without md5 file
    md5_missing = 0
    # number of bytes processed during hasing
    total_hashed_bytes = 0

    # answer flags for the question about already existing md5 files
    # overwrite all following md5 collisions
    overwrite_all = False
    # skip all following directories with md5 collisions
    skip_all = False
    # whether a question was asked (which means there was output)
    question_asked = False

    @staticmethod
    def set_from_arguments(arguments):  # {{{2
        """ Set relevant statistics according to main arguments. """

        State.skip = arguments.skip
        State.limit = arguments.number
        State.overwrite_all = arguments.overwrite

State.set_from_arguments(ARGS)


class RecursionException(Exception):  # {{{1

    """ Base class for custom exceptions. """

    pass


def do_hash(path):  # {{{1
    """ Read the given file chunk by chunk and fead that to the digest. """

    # thanks: http://stackoverflow.com/questions/1131220/get-md5-hash-of-big-\
    # files-in-python
    global filenum, filecount
    filenum += 1
    if ARGS.verbose:
        Output.progress("file", filenum, filecount, path)

    md5 = hashlib.md5()
    with open(path, "rb") as infile:
        while True:
            data = infile.read(1048576)  # 1 MiB at a time
            if not data:
                break
            md5.update(data)
            State.total_hashed_bytes += len(data)
    return md5.hexdigest()


def gather_files(path, dirlist):  # {{{1
    """ Build sorted list of directories and files to process.

    Each entry is a tuple (path, size, filelist, checksum filelist). Directory
    paths end with a path separator. The 'size' for directories is the summed
    size of all the files to be hashed in that dir. Directories that don't
    contain relevant files will not be listed, even if any of their
    subdirectories actually do contain such files.

    The resulting list looks like this:
    [
        ("/", 12345, ["/frotz", 12345], []),
        ("/A/B/", 96, [("/A/B/foo", 32), ("/A/B/bar", 64)], ["Checksums.md5"])
    ]
    """

    if not os.path.isdir(path):
        return 0
    if not path.endswith(os.path.sep):
        path += os.path.sep

    totalsize = 0

    # get and categorise directory content {{{2
    content = [item for item in os.listdir(path) if item[0] != "." or ARGS.all]
    dirs = []
    files = []
    for item in content:
        fullpath = path + os.path.sep + item
        if os.path.islink(fullpath) and not ARGS.follow_links:
            continue
        if os.path.isdir(fullpath):
            dirs.append(item)
        elif os.path.isfile(path + os.path.sep + item):
            files.append(item)

    # look for requested (or existing) checksum files {{{2
    if ARGS.filename == "all":
        md5files = [f for f in files if f.lower().endswith(".md5")]
        md5files.sort()
    else:
        md5files = [ARGS.filename] if ARGS.filename in files else []
    # and remove them from the files to be checked
    for md5file in md5files:
        if md5file in files:
            files.remove(md5file)

    # gather relevant list of files in this directory {{{2
    if files and (not dirs or ARGS.force):
        # only process if this dir is not excluded through constraint arguments
        if State.skip == 0 and State.limit != 0:
            files.sort()
            filelist = []
            for item in files:
                fullpath = path + os.path.sep + item
                size = os.path.getsize(fullpath)
                totalsize += size
                filelist.append(item)
            dirlist.append((path, totalsize, filelist, md5files))
            if State.limit != -1:
                State.limit -= 1
        else:
            if State.skip > 0:
                State.skip -= 1

    # recursive part: go through all subdirs {{{2
    dirs.sort()
    for item in dirs:
        if State.limit == 0:
            return totalsize
        totalsize += gather_files(path + item + os.path.sep, dirlist)
    return totalsize


def ask_checksum_overwrite():  # {{{1
    """ A checksum file would be overwritten. Ask how to proceed.

    Return True if to proceed with the current directory. """

    if State.skip_all:
        return False
    if not State.overwrite_all:
        while True:
            State.question_asked = True
            answer = Output.ask(
                ">>> Checksum file exists: (O)verwrite, "
                "o(v)erwrite all, (s)kip, skip al(l), (a)bort? ")
            if answer.lower() in "ovsla":
                break
        if answer == "o":
            return True
        elif answer == "v":
            State.overwrite_all = True
            return True
        elif answer == "a":
            raise RecursionException("aborted")
        elif answer == "l":
            State.skip_all = True
            return False
        elif answer == "s":
            return False
    return True


def ask_delete_incomplete_checksum():  # {{{1
    """ Hashing a dir was interrupted, the file is incomplete. Ask what to do.

    Return True if the file shall be deleted. """

    # put a newline behind the dots
    while True:
        State.question_asked = True
        answer = Output.ask(
            ">>> Delete incomplete checksum file: (y)es, (n)o? ")
        if answer.lower() in "yn":
            break
    return answer.lower() == "y"


def process_files(filenum_width, path, files, checksum_files):  # {{{1
    """ Read checksum files and compare hashes in one directory. """

    global dirnum, filenum
    dirnum += 1
    # progress output for this directory {{{2
    # want to verify checksums, but no checksum file available
    if not ARGS.create and not ARGS.update and not checksum_files:
        if ARGS.quiet < 3 and not ARGS.no_missing_checksums:
            WARN("'{}'".format(
                "." + path[len(CWD):] if path.startswith(CWD) else path),
                msg="No checksum file: ")
        State.md5_missing += 1
        filenum += len(files)
        return 0

    if State.skip_all and ARGS.create and checksum_files:
        Output.progress(
            "dir", dirnum, dircount, "Skipping overwrite in {}".format(
                "." + path[len(CWD):] if path.startswith(CWD) else path))
        State.skipped_overwrites += 1
        filenum += len(files)
        return 0
    else:
        # full output mode: print number of files and name of directory
        if ARGS.quiet == 0:
            Output.progress(
                "dir", dirnum, dircount,
                "Processing {:>{}} files in {}".format(
                    len(files), filenum_width,
                    "." + path[len(CWD):] if path.startswith(CWD) else path))
        # reduced output: only print a dot for each directory
        elif ARGS.quiet == 1:
            Output.progress("dir", dirnum, dircount)

    # the checksum file will be overwritten -> ask how to proceed {{{2
    # TODO: -F all (right now, it asks only for the dir's first file)
    if checksum_files and ARGS.create and os.path.isfile(
            path + checksum_files[0]) and not ask_checksum_overwrite():
        State.skipped_overwrites += 1
        filenum += len(files)
        return 0

    with ChecksumFiles(path, checksum_files) as checksums:
        try:
            old_sums = checksums.entries()
            if ARGS.create:
                files_to_hash = files
            else:
                files_to_hash = set(old_sums.keys())
                files_to_hash.update(files)
                files_to_hash = list(files_to_hash)
                files_to_hash.sort()

            for filename in files_to_hash:
                fullpath = path + filename
                # get hash and check it agains existing hash from checksum file

                # a missing file can only come from a checksum file entry, so
                # no check for ARGS.(create|update) necessary
                if not os.path.isfile(fullpath):
                    WARN("'{}' (listed in '{}')".format(
                        filename,
                        os.path.basename(old_sums[filename][1])
                        if ARGS.quiet == 0 else old_sums[filename][1]),
                        msg=">> file does not exist: ")
                    State.files_missing += 1
                    if ARGS.update:
                        checksums.remove_entry(filename)
                    continue

                if filename not in old_sums.keys():
                    if not ARGS.create:
                        State.not_in_md5 += 1
                        if not ARGS.update:
                            WARN(
                                # full directory path is already printed with
                                # ARGS.quiet == 0, so don't repeat here
                                filename if ARGS.quiet == 0 else fullpath,
                                msg=">> not in any checksum file: ")
                            # nothing more to do in read-only check mode
                            continue
                else:
                    State.found_in_md5 += 1
                    if ARGS.paths:
                        continue
                    if ARGS.update and checksums.file_is_not_newer(fullpath):
                        continue

                checksum = do_hash(fullpath)
                State.hashed_files += 1
                if ARGS.update or ARGS.create:
                    checksums.write_hash(filename, checksum)
                else:
                    match = checksums.verify_hash(filename, checksum)
                    if match:
                        State.passes += 1
                    else:
                        ERR("'{}'{}".format(
                            filename if ARGS.quiet == 0 else fullpath,
                            " (listed in '{}')".format(
                                os.path.basename(old_sums[filename][1]))
                            if ARGS.filename == "all" else ""
                        ), msg=">> checksum error: ")
                        State.fails += 1
        except KeyboardInterrupt:
            if checksums.is_modified() and ARGS.create:
                print("")
                if ask_delete_incomplete_checksum():
                    checksums.delete_checksum_files()
            raise


def human_readable_size(value):  # {{{1
    """ Express the given number of bytes as a binary exponential unit. """

    power = 0
    while value >= 1024 and power <= 4:
        value = value / 1024
        power = power + 1
    return "{:0.1f} {}".format(
        value, ["B", "kiB", "MiB", "GiB", "TiB"][power])


def plural(number, singular_form, plural_form=""):  # {{{1
    """ Return the singular or plural form of a string depending on number. """

    if singular_form[-1] == "y" and not plural_form:
        plural_form = singular_form[0:-1] + "ies"

    if number == 1:
        return singular_form
    elif plural_form:
        return plural_form
    else:
        return singular_form + "s"


def print_results(duration):  # {{{1
    """ Put all the statistics into a nice tabular format to read. """

    # output stuff, left and right column
    stats = []

    stats.append(("DIRECTORIES:", ""))

    stats.append(("  processed", State.dircount))

    if ARGS.skip > 0:
        stats.append(("  after skipping", ARGS.skip))

    if State.skipped_overwrites > 0:
        stats.append(("  skipped for overwriting", State.skipped_overwrites))

    if State.md5_missing > 0:
        stats.append(("  with no checksum file", State.md5_missing))

    stats.append(("FILES:", ""))

    if not ARGS.create:
        stats.append(("  listed in checksum file", State.found_in_md5))

        # number of files that had an entry in an existing md5 file
        if State.not_in_md5 > 0:
            stats.append(("  not in checksum file", State.not_in_md5))

        if State.files_missing > 0:
            stats.append(("  listed, but not found", State.files_missing))

    if not ARGS.paths:
        stats.append(("  hashed", State.hashed_files))

        if not ARGS.create and not ARGS.update:
            if not ARGS.paths:
                stat = ["  checks passed", State.passes]
                if State.passes != 0:
                    stat.append("Green" if State.passes == State.hashed_files
                                else "Yellow")
                stats.append(stat)

                stat = ["  checks failed", State.fails]
                if State.fails > 0:
                    stat.append("Red")
                stats.append(stat)

    if not ARGS.paths:
        stats.append(("VOLUME:", ""))

        if not ARGS.paths:
            stats.append(("  hashed bytes",
                          0 if State.total_hashed_bytes == 0 else
                          "{} ({})".format(
                              State.total_hashed_bytes,
                              human_readable_size(State.total_hashed_bytes))))

        # --paths is fast, we don’t need time stats and total_hashed_bytes == 0
        if not ARGS.paths:
            value = "{:3.1f} seconds".format(duration)
            if State.total_hashed_bytes != 0:
                value += " ({:0.1f} MiB/second)".format(
                    State.total_hashed_bytes / 1048576 / duration
                    if duration != 0 else 0)
            stats.append(("  time elapsed", value))

    # get maximum width of items for both columns
    labelwidth = max(len(stat[0]) for stat in stats)
    valuewidth = math.floor(max(
        0 if stat[1] == 0 or isinstance(stat[1], str) else
        math.log10(stat[1]) for stat in stats)) + 1

    # separation line between process output and result table
    Output.print_separator(labelwidth + valuewidth + 2)

    # print results
    for stat in stats:
        print("{label:{labelwidth}}{colon} ".format(
            label=stat[0],
            labelwidth=labelwidth,
            colon=":" if stat[1] != "" else "",
            ), end="")
        value = "{value:>{valuewidth}}".format(
            value=stat[1], valuewidth=valuewidth)
        if len(stat) == 3:
            Output.print((stat[2], value))
        else:
            print(value)


def main():  # {{{1
    """ This is where everything comes together. """

    # recurse every given directory
    try:
        dirlist = []
        starttime = time.time()
        if not ARGS.quiet:
            OUT("Gathering list of files...")
        for location in ARGS.locations:
            location = os.path.abspath(location)
            if not os.path.exists(location):
                ERR("'" + location + "'", msg=">> does not exist: ")
            else:
                total_size = gather_files(location, dirlist)

        if len(dirlist) == 0:
            WARN("Nothing worth checking found.")
            exit(0)

        global dirnum, dircount, filenum, filecount
        dircount = len(dirlist)
        filecount = sum([len(directory[2]) for directory in dirlist])
        dirnum = 0
        filenum = 0
        # find out how many characters are needed for the file count column
        width = max([len(directory[2]) for directory in dirlist])
        width = math.floor(math.log10(width) + 1)

        if not ARGS.quiet:
            if ARGS.paths or ARGS.update:
                OUT("Checking checksum consistency for "
                    "{} {} in {} {}".format(
                        filecount, plural(filecount, "file"),
                        dircount, plural(dircount, "directory")))
            else:
                OUT("Processing {} {} in {} {} ({})".format(
                    filecount, plural(filecount, "file"),
                    dircount, plural(dircount, "directory"),
                    human_readable_size(total_size)))

        starttime = time.time()
        for directory in dirlist:
            process_files(width, directory[0], directory[2], directory[3])
            State.dircount += 1
    except KeyboardInterrupt:
        Output.clear_progress_text()
        if ARGS.create:
            WARN("\nHashing aborted.")
        else:
            WARN("\nCheck aborted.")
    except RecursionException:
        pass
    finally:
        endtime = time.time()
        duration = endtime - starttime

    print_results(duration)

    if any([
            State.fails, State.files_missing,
            State.not_in_md5, State.md5_missing
            ]):
        exit(1)


if __name__ == "__main__":  # {{{1
    main()
