#!/usr/bin/python3

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
__prog_version__ = "1.4.1"

cwd = os.getcwd()


class Output(object):  # {{{1
    """ Handles printing to the screen and the state of the output. """

    # whether the last thing printed was a progress dot
    dot_last = False
    output_shown = False

    @staticmethod
    def colorstring(color):  # {{{2
        """ Create the terminal escape sequence for the given colour. """

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
        return "\033[{0};{1}m".format(
            "0" if color[0].islower() else "1",
            colors.get(color.lower(), "0"))

    @staticmethod
    def clear_dot():  # {{{2
        """ Print a newline if the last thing printed was a progress dot. """

        if Output.dot_last:
            print("")
            Output.dot_last = False

    @staticmethod
    def print_separator(width):
        """ Print the stats table header only if other output came before. """

        if Output.output_shown:
            print("-" * width)

    @staticmethod
    def dot():  # {{{2
        """ Output a progress dot. """

        print(".", end="")
        sys.stdout.flush()
        Output.dot_last = True
        Output.output_shown = True

    @staticmethod
    def print(*what, file=sys.stdout):  # {{{2
        """ Output the given message. """

        Output.clear_dot()
        for item in what:
            if type(item) is str:
                print(item, end="", file=file)
            else:
                if args.no_color:
                    print(item[1], end="", file=file)
                else:
                    print(Output.colorstring(item[0]) + item[1] + "\033[0;0m",
                          end="", file=file)
        print(file=file)
        file.flush()
        Output.output_shown = True

    @staticmethod
    def error(*arguments, msg=""):  # {{{2
        """ Output a given message as error message. """

        Output.clear_dot()
        if msg != "":
            Output.print(("Red", msg), *arguments, file=sys.stderr)
        else:
            Output.print(("Red", "".join(arguments)), file=sys.stderr)
        Output.output_shown = True

    @staticmethod
    def warn(*arguments, msg=""):  # {{{2
        """ Convenience function: output a given message as error message. """

        Output.clear_dot()
        if msg != "":
            Output.print(("Yellow", msg), *arguments, file=sys.stderr)
        else:
            Output.print(("Yellow", "".join(arguments)), file=sys.stderr)
        Output.output_shown = True

OUT = Output.print
ERR = Output.error
WARN = Output.warn


class ChecksumFiles(object):  # {{{1
    """ Encapsulate access to checksum files while processing a directory. """

    def __init__(self, path, checksum_files):  # {{{2
        # the directory
        self._path = path
        # the directory
        self._csfiles = [os.path.join(path, cf) for cf in checksum_files]
        # whether each file has its own checksum file
        self._separate = args.filename == 'all'
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

        # read entries in existing checksum files (but not if creating from
        # scratch, then we don't care what's already there)
        if not args.create:
            for cspath in self._csfiles:
                try:
                    for line in open(cspath):
                        filename, md5 = line[34:-1], line[:32]
                        self._entries[filename] = (md5, cspath)
                except OSError as error:
                    ERR("'" + cspath + "'",
                        msg="Could not read checksum file ({0}): ".format(
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
                if args.filename == "all":
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
                                print("{0} *{1}".format(
                                    self._entries[entry][0], entry),
                                      file=csfile)
                    except KeyboardInterrupt:
                        ERR("\nWARNING! Interrupted while rewriting '{0}'\n"
                            "Data loss is possible.".format(cspath))
                        raise
                    except OSError as error:
                        ERR(cspath,
                            msg="Could not write to checksum file ({0}): ". \
                            format(error.args[1]))
                else:
                    os.unlink(cspath)

    def _get_checksum_file(self):  # {{{2
        """ Encapsulate write access to checksum file. """

        try:
            if args.filename != "all" and self._file is None:
                path = os.path.join(self._path, args.filename)
                self._file = open(
                    path, "a" if args.update else "w")
        except OSError as error:
            ERR("'" + path + "'",
                msg="Could not open checksum file for writing ({0}): ".format(
                    error.args[1]))
        return self._file

    def entries(self):  # {{{2
        """ Getter to retrieve all listed checksums. """

        return self._entries

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
            if args.filename == 'all':
                # path is guaranteed to end with "/" (2. stmt in gather_files)
                csfpath = self._path + filename + ".md5"
                # TODO: ask for overwriting here
                with open(csfpath, "a" if args.update else "w") as csfile:
                    print("{0} *{1}".format(checksum, filename), file=csfile)
                if args.update:
                    # record new checksum item for use in self.__del__
                    self._entries[filename] = (checksum, csfpath)
            else:
                print(
                    "{0} *{1}".format(checksum, filename),
                    file=self._get_checksum_file())
                csfpath = self._path + args.filename
                if args.update and self._csfiles:
                    self._entries[filename] = (checksum, csfpath)
                    self._updated_csfiles.add(csfpath)
        except OSError as error:
            ERR(csfpath, msg="Could not write to checksum file ({0}): ".format(
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
             'use any *.md5 file in checking mode)')
    group.add_argument(
        '-o', '--overwrite', action='store_true',
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
        help='less progress output (-q: print dots instead of paths, -qq: '
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
        print("{0} version {1}".format(__prog_name__, __prog_version__))
        exit(0)
    if parsed_args.quiet > 3:
        parsed_args.quiet = 3
    if parsed_args.filename != "all":
        parsed_args.filename = os.path.basename(parsed_args.filename)

    # use current dir if no dir to process was given
    if not parsed_args.locations:
        parsed_args.locations.append(cwd)

    return parsed_args

args = parse_arguments()


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
    def set_from_arguments(arguments):
        """ Set relevant statistics according to main arguments. """

        State.skip = arguments.skip
        State.limit = arguments.number
        State.overwrite_all = args.overwrite

State.set_from_arguments(args)


class RecursionException(Exception):  # {{{1

    """ Base class for custom exceptions. """

    pass


def do_hash(path):  # {{{1
    """ Read the given file chunk by chunk and fead that to the digest. """

    # thanks: http://stackoverflow.com/questions/1131220/get-md5-hash-of-big-\
    # files-in-python
    if args.verbose:
        OUT("Hashing '{0}'".format(path))

    md5 = hashlib.md5()
    with open(path, "rb") as infile:
        while True:
            data = infile.read(1048576)  # 1 MiB at a time
            if not data:
                break
            md5.update(data)
    State.total_hashed_bytes += os.path.getsize(path)
    return md5.hexdigest()


def gather_files(path, dirlist):  # {{{1
    """ Build sorted list of directories and files to process.

    Each entry is a tuple (path, size, filelist, checksum filelist). Directory
    paths end with a path separator. The 'size' for directories is the summed
    size of all the files to be hashed in that dir. Directories that don't
    contain relevant files will not be listed, even if any of their
    subdirectories actually do contain some.

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
    content = [item for item in os.listdir(path) if item[0] != "." or args.all]
    dirs = []
    files = []
    for item in content:
        fullpath = path + os.path.sep + item
        if os.path.islink(fullpath) and not args.follow_links:
            continue
        if os.path.isdir(fullpath):
            dirs.append(item)
        elif os.path.isfile(path + os.path.sep + item):
            files.append(item)

    # look for requested (or existing) checksum files {{{2
    if args.filename == "all":
        md5files = [f for f in files if f.lower().endswith(".md5")]
        md5files.sort()
    else:
        md5files = [args.filename] if args.filename in files else []
    # and remove them from the files to be checked
    for md5file in md5files:
        if md5file in files:
            files.remove(md5file)

    # gather relevant list of files in this directory {{{2
    if files and (not dirs or args.force):
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


def ask_checksum_overwrite():
    """ A checksum file would be overwritten. Ask how to proceed.

    Return True if proceed with the current directory. """

    if State.skip_all:
        return False
    if not State.overwrite_all:
        # put a newline behind the dots
        Output.clear_dot()
        while True:
            State.question_asked = True
            answer = input(
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


def process_files(filenum_width, path, files, checksum_files):  # {{{1
    """ Read checksum files and compare hashes in one directory. """

    # progress output for this directory {{{2
    # want to verify checksums, but no checksum file available
    if not args.create and not args.update and not checksum_files:
        if args.quiet < 3 and not args.no_missing_checksums:
            WARN("'{0}'".format(
                path[len(cwd):] if path.startswith(cwd) else path),
                 msg="No checksum file: ")
        State.md5_missing += 1
        return 0

    if State.skip_all and args.create and checksum_files:
        OUT("Skipping overwrite in {0}".format(
            "." + path[len(cwd):] if path.startswith(cwd) else path))
        State.skipped_overwrites += 1
        return 0
    else:
        # full output mode: print number of files and name of directory
        if args.quiet == 0:
            OUT("Processing {0:>{1}} files in {2}".format(
                len(files), filenum_width,
                "." + path[len(cwd):] if path.startswith(cwd) else path))
        # reduced output: only print a dot for each directory
        elif args.quiet == 1:
            Output.dot()

    # the checksum file will be overwritten -> ask how to proceed {{{2
    # TODO: -F all (right now, it asks only for the dir's first file)
    if checksum_files and args.create and os.path.isfile(
            path + checksum_files[0]) and not ask_checksum_overwrite():
        State.skipped_overwrites += 1
        return 0

    with ChecksumFiles(path, checksum_files) as checksums:
        old_sums = checksums.entries()
        if args.create:
            files_to_hash = files
        else:
            files_to_hash = set(old_sums.keys())
            files_to_hash.update(files)
            files_to_hash = list(files_to_hash)
            files_to_hash.sort()

        for filename in files_to_hash:
            fullpath = path + filename
            # get hash and check it agains existing hash from checksum file

            # a missing file can only come from a checksum file entry, so no
            # check for args.(create|update) necessary
            if not os.path.isfile(fullpath):
                WARN("'{0}' (listed in '{1}')".format(
                    filename,
                    os.path.basename(old_sums[filename][1]) if args.quiet == 0
                    else old_sums[filename][1]),
                     msg=">> file does not exist: ")
                State.files_missing += 1
                if args.update:
                    checksums.remove_entry(filename)
                continue

            if not filename in old_sums.keys():
                if not args.create:
                    State.not_in_md5 += 1
                    if not args.update:
                        WARN('{0}'.format(
                            # full directory path is already printed with
                            # args.quiet == 0, so don't repeat here
                            filename if args.quiet == 0 else fullpath),
                             msg=">> not in any checksum file: ")
                        # nothing more to do in read-only check mode
                        continue
            else:
                State.found_in_md5 += 1
                if args.paths or args.update:
                    continue

            checksum = do_hash(fullpath)
            State.hashed_files += 1
            if args.update or args.create:
                checksums.write_hash(filename, checksum)
            else:
                match = checksums.verify_hash(filename, checksum)
                if match:
                    State.passes += 1
                else:
                    ERR("'{0}'{1}".format(
                        filename if args.quiet == 0 else fullpath,
                        " (listed in '{0}')".format(
                            os.path.basename(old_sums[filename][1]))
                        if args.filename == "all" else ""
                    ), msg=">> checksum error: ")
                    State.fails += 1


def human_readable_size(value):  # {{{1
    """ Express the given number of bytes as a binary exponential unit. """

    power = 0
    while value >= 1024 and power <= 4:
        value = value / 1024
        power = power + 1
    return "{0:0.1f} {1}".format(
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

    if args.skip > 0:
        stats.append(("  after skipping", args.skip))

    if State.skipped_overwrites > 0:
        stats.append(("  skipped for overwriting", State.skipped_overwrites))

    if State.md5_missing > 0:
        stats.append(("  with no checksum file", State.md5_missing))

    stats.append(("FILES:", ""))

    if not args.create:
        stats.append(("  listed in checksum file", State.found_in_md5))

        # number of files that had an entry in an existing md5 file
        if State.not_in_md5 > 0:
            stats.append(("  not in checksum file", State.not_in_md5))

        if State.files_missing > 0:
            stats.append(("  listed, but not found", State.files_missing))

    if not args.paths:
        stats.append(("  hashed", State.hashed_files))

        if not args.create and not args.update:
            if not args.paths:
                stat = ["  checks passed", State.passes]
                if State.passes != 0:
                    stat.append("Green" if State.passes == State.hashed_files
                                else "Yellow")
                stats.append(stat)

                stat = ["  checks failed", State.fails]
                if State.fails > 0:
                    stat.append("Red")
                stats.append(stat)

    if not args.paths:
        stats.append(("VOLUME:", ""))

        if not args.paths:
            stats.append(("  hashed bytes",
                          0 if State.total_hashed_bytes == 0 else
                          "{0} ({1})".format(
                              State.total_hashed_bytes,
                              human_readable_size(State.total_hashed_bytes))))

        # --paths is fast, we don’t need time stats and total_hashed_bytes == 0
        if not args.paths:
            value = "{0:3.1f} seconds".format(duration)
            if State.total_hashed_bytes != 0:
                value += " ({0:0.1f} MiB/second)".format(
                    State.total_hashed_bytes / 1048576 / duration \
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
        print("{label:{labelwidth}}: ".format(
            label=stat[0], labelwidth=labelwidth), end="")
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
        if not args.quiet:
            OUT("Gathering list of files...")
        for location in args.locations:
            location = os.path.abspath(location)
            if not os.path.exists(location):
                ERR("'" + location + "'", msg=">> does not exist: ")
            else:
                total_size = gather_files(location, dirlist)

        if len(dirlist) == 0:
            WARN("Nothing worth checking found.")
            exit(0)

        # find out how many characters are needed for the file count column
        width = max([len(directory[2]) for directory in dirlist])
        width = math.floor(math.log10(width) + 1)
        filecount = sum([len(directory[2]) for directory in dirlist])

        if not args.quiet:
            if args.paths or args.update:
                OUT("Checking consistency between checksum files and "
                    "{0} {1} in {2} {3}".format(
                        filecount, plural(filecount, "file"),
                        len(dirlist), plural(len(dirlist), "directory")))
            else:
                OUT("Hashing {0} {1} in {2} {3} ({4})".format(
                    filecount, plural(filecount, "file"),
                    len(dirlist), plural(len(dirlist), "directory"),
                    human_readable_size(total_size)))

        starttime = time.time()
        for directory in dirlist:
            process_files(width, directory[0], directory[2], directory[3])
            State.dircount += 1
    except KeyboardInterrupt:
        if args.create:
            WARN("\nHashing aborted.")
        else:
            WARN("\nCheck aborted.")
    except RecursionException:
        pass
    finally:
        endtime = time.time()
        duration = endtime - starttime

    Output.clear_dot()
    print_results(duration)

    if any([
            State.fails, State.files_missing,
            State.not_in_md5, State.md5_missing
        ]):
        exit(1)


if __name__ == "__main__":  # {{{1
    main()
