#!/usr/bin/env python3

"""
Set up a directory tree, run dh over it, compare the result.

Test data format: a tuple of test cases.
    Each test is a tuple with
    - the arguments to dh as a list for popen,
    - the expected dh exit code,
    - a comment string of what the test is about,
    - a tuple of directory entries
        Each entry is a tuple with
        - whether it shall exist before the dh run
        - whether it shall exist after the dh run
        - the path of the entry starting at test root
        - content of the entry (ignored for directories)
        - an optional fifth item with an age delta in hours for this file

Each test creates the test root directory and sets up the a-priori structure.
Then it runs dh over the directory. Finally, it compares the content of the
directory with the expected files.
"""

import datetime
import os
import subprocess
import sys
import tempfile


if sys.version_info[0] < 3 or sys.version_info[1] < 5:
    print('Need python 3.5 or up.', file=sys.stderr)
    sys.exit(1)

TEST_DATA = (
        (
            [], 0, "empty directory", ()
        ),
        (
            [], 0, "empty directory and subdirectory", (
                (True,  True, 'subdir/', None),
            )
        ),
        (
            [], 0, "simple check with correct checksum", (
                (True, True, 'foo.txt', 'foo\n'),
                (True, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),
        (
            [], 1, "simple check with wrong checksum", (
                (True,  True, 'foo.txt', 'foo\n'),
                (True, True, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
            )
        ),
        (
            ['-c'], 0, "simple creation with one file", (
                (True,  True, 'foo.txt', 'foo\n'),
                (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),
        (
            ['-u'], 0, "simple update with one file without checksum file and one ignored dotfile", (
                (True,  True, '.foo.txt', 'foo\n'),
                (True,  True, 'foo.txt', 'foo\n'),
                (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),
        (
            ['-au'], 0, "simple update with one file without checksum file and one ignored dotfile", (
                (True,  True, '.foo.txt', 'foo\n'),
                (True,  True, 'foo.txt', 'foo\n'),
                (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *.foo.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),
        (
            ['-u'], 0, "simple update with one file older than checksum file", (
                (True,  True, 'foo.txt', 'foo\n', -1),
                (True, False, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
                (False, True, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
            ),
        ),
        (
            ['-u'], 0, "simple update with one file newer than checksum file", (
                (True,  True, 'foo.txt', 'foo\n', +1),
                (True, False, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
                (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),

        (
            ['-u'], 1, "update with one file and two checksum entries", (
                (True,  True, 'foo.txt', 'foo\n', +1),
                (True, False, 'Checksums.md5', 'ffffffffffffffffffffffffffffffff *foo.txt\naaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *bar.txt\n'),
                (False, True, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *bar.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),
        (
            ['-du'], 1, "update with one file, two checksum entries and deletion of unreferenced entry", (
                (True,  True, 'foo.txt', 'foo\n', +1),
                (True, False, 'Checksums.md5', 'ffffffffffffffffffffffffffffffff *foo.txt\naaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *bar.txt\n'),
                (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            )
        ),

        (
            ['-c'], 0, "creation with subdir with one file", (
                (True,  True, 'subdir/', None),
                (True,  True, 'subdir/foo.txt', 'foo\n'),
                (False, True, 'subdir/Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
                (True,  True, 'ignored.txt', ''),
            )
        ),
        (
            ['-cf'], 0, "creation with subdir and with file in root", (
                (True,  True, 'subdir/', None),
                (True,  True, 'subdir/foo.txt', 'foo\n'),
                (False, True, 'subdir/Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
                (True,  True, 'root.txt', 'foo\n'),
                (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *root.txt\n'),
            )
        ),

        (
            ['-u', '-F', 'test.md5'], 0, "simple update with different checksum file name", (
                (True,  True, 'foo.txt', 'foo\n'),
                (True,  True, 'bar.txt', 'foo\n'),
                (False, True, 'test.md5', 'd3b07384d113edec49eaa6238ad5ff00 *bar.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            ),
        ),
        (
            ['-u', '-F', 'all'], 0, "simple update with individual checksum files", (
                (True,  True, 'foo.txt', 'foo\n'),
                (True,  True, 'bar.txt', 'foo\n'),
                (False, True, 'foo.txt.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
                (False, True, 'bar.txt.md5', 'd3b07384d113edec49eaa6238ad5ff00 *bar.txt\n'),
            )
        ),
        (
            ['-u', '-F', 'test.md5'], 0, "simple update with different checksum file name", (
                (True,  True, 'foo.txt', 'foo\n'),
                (True,  True, 'bar.txt', 'foo\n'),
                (False, True, 'test.md5', 'd3b07384d113edec49eaa6238ad5ff00 *bar.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            ),
        ),
)

PASSED = 0
FAILED = 0
# for debugging purposes
SKIP_TESTS = 0
COUNT = len(TEST_DATA) - SKIP_TESTS

TEST_ROOT = tempfile.mkdtemp(prefix='dhtest-') + os.path.sep
DH_PATH = os.getcwd() + os.path.sep + 'dh'
os.chdir(TEST_ROOT)


def get_dirlist(prefix, output):
    """
    Get a recursive list of files and directories.

    :param str prefix: the directory to look at
    :param list output: the list in which to write the result recursively
    :return: the result as a flat list: ['subdir/', 'subdir/file', 'file']
    """

    dirlist = os.listdir(prefix if prefix else '.')
    for entry in dirlist:
        if os.path.isdir(entry):
            output.append(prefix + entry + os.path.sep)
            get_dirlist(output[-1], output)
        else:
            output.append(prefix + entry)


def testing(text):
    """ Write progress string. """
    print(f'{text}...', end='')


def coloured(colour, value, do_colour=True):
    """ Return value within colour codes if do_colour is True. """
    if do_colour:
        return f"\033[1;{colour}m{str(value)}\033[0m"
    return str(value)


def failed(reason):
    """ Write progress string. """
    global FAILED  # pylint: disable=global-statement
    print(f' \033[1;31mFAILED\033[0m: {reason}')
    FAILED += 1


def passed():
    """ Write progress string. """
    global PASSED  # pylint: disable=global-statement
    print(' \033[1;32mPASSED\033[0m')
    PASSED += 1


def clean_up(path):
    """ Delete all test data. """
    for entry in os.listdir(path):
        if os.path.isdir(entry):
            clean_up(entry)
            os.rmdir(entry)
        else:
            os.unlink(entry)


def do_test_case(test_case):
    """ Perform all actions pertaining to a single test case.

    :param test_case: tuple with test data (see definition of TEST_CASE)
    """

    args, exit_code, comment, entries = test_case
    testing(comment)

    # given: create input directory structure
    for entry in entries:
        before, _, filename, content = entry[:4]
        if not before:
            continue
        if filename.endswith('/'):
            os.mkdir(filename)
        else:
            with open(filename, 'w', encoding='utf8') as file:
                file.write(content)
        if len(entry) == 5:
            newtime = datetime.datetime.timestamp(datetime.datetime.now())
            newtime = int((newtime + entry[4] * 3600) * 1000000000)
            os.utime(filename, ns=(newtime, newtime))

    # when: run dh on the test data
    completed = subprocess.run(
            [DH_PATH, '-qqq'] + args,
            capture_output=True,
            check=False, text=True)

    # then: gather the result and compare with expected content
    if exit_code != completed.returncode:
        clean_up(TEST_ROOT)
        failed(f'exit code. Expected={exit_code}, Actual={completed.returncode}')
        return False

    # dictionary mapping filename to file content
    expected = {entry[2]: entry[3] for entry in entries if entry[1]}

    result = []
    get_dirlist('', result)

    # compare the result with the expected data
    if set(expected.keys()) != set(result):
        clean_up(TEST_ROOT)
        failed('directory content')
        return False

    # check file content and clean up in the same loop
    result.reverse()
    for filename in result:
        if filename.endswith(os.path.sep):
            os.rmdir(filename)
        else:
            with open(filename, encoding='utf8') as file:
                content = file.read()
                if content != expected[filename]:
                    clean_up(TEST_ROOT)
                    failed(f'content of file {filename}')
                    return False
            os.unlink(filename)

    passed()
    return True


if __name__ == "__main__":
    for test_data_item in TEST_DATA:
        if SKIP_TESTS > 0:
            SKIP_TESTS -= 1
            continue

        do_test_case(test_data_item)

    os.rmdir(TEST_ROOT)

    print()
    print('Failed test cases:', coloured('31', FAILED, FAILED != 0))
    print('Passed test cases:', coloured('32', f'{PASSED}/{COUNT}', PASSED == COUNT))
