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

# pylint: disable=line-too-long

import argparse
import datetime
import os
import subprocess
import re
import sys
import tempfile

if sys.version_info[0] < 3 or sys.version_info[1] < 5:
    print('Need python 3.5 or up.', file=sys.stderr)
    sys.exit(1)

DH_OUTPUT_KEYS = [
        '  processed',
        '  with no checksum file',
        '  listed in checksum file',
        '  listed, but not found',
        '  not in checksum file',
        '  hashed',
        '  checks passed',
        '  checks failed',
        '  hashed bytes',
        ]

TEST_DATA = (
    (
        [], 0, "empty directory", (),
        (None, None, None, None, None, None, None, None, None,)
    ),
    (
        [], 0, "empty directory and subdirectory", (
            (True, True, 'subdir/', None),
        ),
        (None, None, None, None, None, None, None, None, None,)
    ),
    (
        [], 2, "simple check with missing checksum file", (
            (True, True, 'foo.txt', 'foo\n'),
        ),
        (1, 1, 0, None, None, 0, 0, 0, 0,)
    ),
    (
        ['--no-missing-checksums'], 0, "simple check with missing checksum file, but ignoring that", (
            (True, True, 'foo.txt', 'foo\n'),
        ),
        (1, 1, 0, None, None, 0, 0, 0, 0,)
    ),
    (
        [], 0, "simple check with correct checksum and depth=1", (
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 1, None, None, 1, 1, 0, 4,)
    ),
    (
        [], 0, "simple check with correct checksum and depth=2", (
            (True, True, 'subdir/', None),
            (True, True, 'subdir/subsubdir/', None),
            (True, True, 'subdir/subsubdir/foo.txt', 'foo\n'),
            (True, True, 'subdir/subsubdir/Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 1, None, None, 1, 1, 0, 4,)
    ),
    (
        [], 2, "simple check with wrong checksum", (
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
        ),
        (1, None, 1, None, None, 1, 0, 1, 4,)
    ),
    (
        ['-c'], 0, "simple creation with one file", (
            (True, True, 'foo.txt', 'foo\n'),
            (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, None, None, None, 1, None, None, 4,)
    ),
    (
        ['-u'], 0, "simple update with one file without checksum file and one ignored dotfile", (
            (True, True, '.foo.txt', 'foo\n'),
            (True, True, 'foo.txt', 'foo\n'),
            (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 0, None, 1, 1, None, None, 4,)
    ),
    (
        ['-a', '-u'], 0, "simple update with one file without checksum file and one ignored dotfile", (
            (True, True, '.foo.txt', 'foo\n'),
            (True, True, 'foo.txt', 'foo\n'),
            (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *.foo.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 0, None, 2, 2, None, None, 8,)
    ),
    (
        ['-u'], 0, "simple update with one file older than checksum file", (
            (True, True, 'foo.txt', 'foo\n', -1),
            (True, False, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
            (False, True, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
        ),
        (1, None, 1, None, None, 0, None, None, 0)
    ),
    (
        ['-u'], 0, "simple update with one file newer than checksum file", (
            (True, True, 'foo.txt', 'foo\n', +1),
            (True, False, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *foo.txt\n'),
            (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 1, None, None, 1, None, None, 4)
    ),

    (
        # return code 2, because the missing entry is not deleted
        ['-u'], 2, "update with one file and two checksum entries", (
            (True, True, 'foo.txt', 'foo\n', +1),
            (True, False, 'Checksums.md5', 'ffffffffffffffffffffffffffffffff *foo.txt\naaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *bar.txt\n'),
            (False, True, 'Checksums.md5', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *bar.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 1, 1, None, 1, None, None, 4,)
    ),
    (
        # same test, but now with --delete to remove the unneeded entry
        ['-u', '-d'], 0, "update with one file, two checksum entries and deletion of unreferenced entry", (
            (True, True, 'foo.txt', 'foo\n', +1),
            (True, False, 'Checksums.md5', 'ffffffffffffffffffffffffffffffff *foo.txt\naaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa *bar.txt\n'),
            (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 1, 1, None, 1, None, None, 4,)
    ),

    (
        ['-c'], 0, "creation with subdir with one file", (
            (True, True, 'ignored.txt', ''),
            (True, True, 'subdir/', None),
            (True, True, 'subdir/foo.txt', 'foo\n'),
            (False, True, 'subdir/Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, None, None, None, 1, None, None, 4,)
    ),
    (
        ['-c', '-f'], 0, "creation with subdir and with file in root", (
            (True, True, 'root.txt', 'foo\n'),
            (True, True, 'subdir/', None),
            (True, True, 'subdir/foo.txt', 'foo\n'),
            (False, True, 'subdir/Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            (False, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *root.txt\n'),
        ),
        (2, None, None, None, None, 2, None, None, 8,)
    ),

    (
        ['-u', '-F', 'test.md5'], 0, "simple update with different checksum file name", (
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'bar.txt', 'foo\n'),
            (False, True, 'test.md5', 'd3b07384d113edec49eaa6238ad5ff00 *bar.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 0, None, 2, 2, None, None, 8,)
    ),
    (
        ['-u', '-F', 'all'], 0, "simple update with individual checksum files", (
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'bar.txt', 'foo\n'),
            (False, True, 'foo.txt.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
            (False, True, 'bar.txt.md5', 'd3b07384d113edec49eaa6238ad5ff00 *bar.txt\n'),
        ),
        (1, None, 0, None, 2, 2, None, None, 8,)
    ),
    (
        ['-u', '-F', 'test.md5'], 0, "simple update with different checksum file name", (
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'bar.txt', 'foo\n'),
            (False, True, 'test.md5', 'd3b07384d113edec49eaa6238ad5ff00 *bar.txt\nd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (1, None, 0, None, 2, 2, None, None, 8,)
    ),

    (
        # return code 2, because subdir has no checksum file
        ['-f'], 2, "check with subdir and one checksum file at root, none in subdir", (
            (True, True, 'subdir/', None),
            (True, True, 'subdir/foo.txt', 'foo\n'),
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\nd3b07384d113edec49eaa6238ad5ff00 *subdir/foo.txt\n'),
        ),
        (2, 1, 2, None, None, 2, 2, 0, 8,)
    ),
    (
        # now return code 0, because the missing checksum file is ignored
        ['-f', '--no-missing-checksums'], 0, "check with subdir and one checksum file at root, none in subdir, ignoring that", (
            (True, True, 'subdir/', None),
            (True, True, 'subdir/foo.txt', 'foo\n'),
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\nd3b07384d113edec49eaa6238ad5ff00 *subdir/foo.txt\n'),
        ),
        (2, 1, 2, None, None, 2, 2, 0, 8,)
    ),
    (
        # return code 0 despite globally incomplete checksum, because for its own dir, it is complete (see option -s, to be implemented)
        ['-f', '--no-missing-checksums'], 0, "check with subdir and one incomplete checksum file at root", (
            (True, True, 'subdir/', None),
            (True, True, 'subdir/foo.txt', 'foo\n'),
            (True, True, 'foo.txt', 'foo\n'),
            (True, True, 'Checksums.md5', 'd3b07384d113edec49eaa6238ad5ff00 *foo.txt\n'),
        ),
        (2, 1, 1, None, None, 1, 1, 0, 4,)
    ),

)

PASSED = 0
FAILED = 0

TEST_ROOT = tempfile.mkdtemp(prefix='dhtest-') + os.path.sep
DH_PATH = os.getcwd() + os.path.sep + 'dh'
os.chdir(TEST_ROOT)


def parse_arguments(test_count):
    """ Parse argument that specifies which test case to run. """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-o', action='store_true', dest='output',
        help='show output of dh')
    parser.add_argument(
        '-w', action='store_true', dest='wait',
        help='wait for user confirmation after test setup for manual check')
    parser.add_argument(
        'tests', type=str, nargs='*', default=[],
        help='the test numbers to run. Default: all of them, Molari! ALL OF '
             'THEM! Use a comma- or space-separated list of items. Each item '
             'can be a single number or a range (open-ended or closed).')
    args = parser.parse_args()

    if not args.tests:
        return (args.output, args.wait, range(1, 1 + len(TEST_DATA)))

    cases = []
    for item in args.tests:
        cases.extend(item.split(','))
    result = []
    for num in cases:
        nums = num.split('-')
        if len(nums) == 1:
            try:
                result.append(int(num))
            except ValueError:
                pass
        else:
            if nums[0] == '':
                nums[0] = 1
            if nums[1] == '':
                nums[1] = test_count
            try:
                result.extend(range(int(nums[0]), 1 + int(nums[1])))
            except ValueError:
                pass
    result.sort()
    return (args.output, args.wait, result)


def get_dirlist(prefix, output):
    """
    Get a recursive list of files and directories.

    :param str prefix: the directory to look at
    :param list output: the list in which to write the result recursively
    :return: the result as a flat list: ['subdir/', 'subdir/file', 'file']
    """

    dirlist = os.listdir(prefix if prefix else '.')
    for entry in dirlist:
        if os.path.isdir(prefix + entry):
            output.append(prefix + entry + os.path.sep)
            get_dirlist(output[-1], output)
        else:
            output.append(prefix + entry)


def testing(width, number, text):
    """ Write progress string. """
    print(f'{number:{width}}: {text}...', end='')


def coloured(colour, value, do_colour=True):
    """ Return value within colour codes if do_colour is True. """
    if do_colour:
        return f"\033[1;{colour}m{str(value)}\033[0m"
    return str(value)


def failed(reason, output, stdout):
    """ Write progress string. """
    global FAILED  # pylint: disable=global-statement
    print(f' \033[1;31mFAILED\033[0m: {reason}')
    FAILED += 1

    if output:
        print('Output of dh:')
        print(stdout)


def passed(output, stdout):
    """ Write progress string. """
    global PASSED  # pylint: disable=global-statement
    print(' \033[1;32mPASSED\033[0m')
    PASSED += 1

    if output:
        print('Output of dh:')
        print(stdout)


def clean_up(path):
    """ Delete all test data. """
    for entry in os.listdir(path):
        if os.path.isdir(path + entry):
            clean_up(path + entry + os.path.sep)
            os.rmdir(path + entry)
        else:
            os.unlink(path + entry)


def set_up_dirs(test_case):
    """ Create the file tree specific to a test cast.

    :param test_case: tuple with test data (see definition of TEST_CASE)
    """

    _, _, _, entries, _ = test_case
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


def check_summary(dh_output, expected):
    """ Check whether dh outputs the expected values at the end

    :param str dh_output: stdout of the teset run
    :param list expected: the expected values (see DH_OUTPUT_KEYS)
    """

    result = {key: None for key in DH_OUTPUT_KEYS}
    errorlist = []

    for line in dh_output.split('\n'):
        # remove colour control sequences
        line = re.sub(r'\033\[[01];[0-9]+m', '', line)

        for pattern in DH_OUTPUT_KEYS:
            rem = re.match(f'^{pattern} *: *([0-9]+)( .*)?$', line)
            if rem:
                result[pattern] = int(rem.group(1))

    for key, exp in zip(DH_OUTPUT_KEYS, expected, strict=True):
        if result[key] != exp:
            errorlist.append(f'{key}: expected={exp}, actual={result[key]}')

    return errorlist


def do_test_case(test_case, output, wait):
    """ Perform all actions pertaining to a single test case.

    :param test_case: tuple with test data (see definition of TEST_CASE)
    """

    args, exit_code, _, entries, summary = test_case

    if wait:
        input(f"\nWaiting to run {' '.join([DH_PATH, '-qqq'] + args)} ...")

    # when: run dh on the test data
    completed = subprocess.run(
        [DH_PATH, '-qqq'] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, text=True)

    # then: gather the result and compare with expected content
    if exit_code != completed.returncode:
        failed(f'exit code. Expected={exit_code}, Actual={completed.returncode}',
               output, completed.stdout)
        return False

    result = check_summary(completed.stdout, summary)
    if result:
        failed('summary\n' + '\n'.join(result) + '\n', True, completed.stdout)
        return False

    # dictionary mapping filename to file content
    expected = {entry[2]: entry[3] for entry in entries if entry[1]}

    result = []
    get_dirlist('', result)

    # compare the result with the expected data
    if set(expected.keys()) != set(result):
        failed('directory content', output, completed.stdout)
        return False

    # check file content
    result.reverse()
    for filename in result:
        if not filename.endswith(os.path.sep):
            with open(filename, encoding='utf8') as file:
                content = file.read()
                if content != expected[filename]:
                    failed(f'content of file {filename}',
                           output, completed.stdout)
                    return False

    passed(output, completed.stdout)
    return True


def main():
    """ The main loop. """
    test_count = len(TEST_DATA)
    columns = len(str(test_count))
    do_output, do_wait, case_range = parse_arguments(test_count)

    test_number = 0
    tests_run = 0

    if do_wait:
        print(f"Test directory is '{TEST_ROOT}'")

    for test_data_item in TEST_DATA:
        test_number += 1
        if test_number not in case_range:
            continue

        testing(columns, test_number, test_data_item[2])
        # given
        set_up_dirs(test_data_item)
        # when and then
        do_test_case(test_data_item, do_output, do_wait)
        if do_wait:
            input('Waiting to clean up ...')
        clean_up(TEST_ROOT)
        tests_run += 1

    os.rmdir(TEST_ROOT)

    print()
    print('Failed test cases:', coloured('31', FAILED, FAILED != 0))
    print('Passed test cases:', coloured('32', f'{PASSED}/{tests_run}', PASSED == tests_run))


if __name__ == "__main__":
    main()
